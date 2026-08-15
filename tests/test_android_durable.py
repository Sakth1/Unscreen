import contextlib
import os
import sqlite3
import sys
import tempfile
import types
from unittest.mock import MagicMock, patch

from core.storage import Storage
from core.storage.android_durable import (
    BACKUP_DIR,
    BACKUP_FILE,
    AndroidDurableBackup,
    describe,
)


def _make_db(tmp_path) -> str:
    path = str(tmp_path / "data.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (k TEXT, v TEXT)")
    conn.execute("INSERT INTO items VALUES ('a', '1')")
    conn.commit()
    conn.close()
    return path


def _make_db_bytes() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (k TEXT, v TEXT)")
    conn.execute("INSERT INTO items VALUES ('restored', 'yes')")
    conn.commit()
    conn.close()
    with open(path, "rb") as f:
        data = f.read()
    os.unlink(path)
    return data


class FakeOutputStream:
    def __init__(self):
        self.chunks: list[bytes] = []

    def write(self, data, offset, length):
        self.chunks.append(bytes(data[offset : offset + length]))

    def flush(self):
        pass

    def close(self):
        pass


class FakeInputStream:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, buf):
        if self._pos >= len(self._data):
            return -1
        n = min(len(buf), len(self._data) - self._pos)
        buf[:n] = self._data[self._pos : self._pos + n]
        self._pos += n
        return n

    def close(self):
        pass


class FakeContentValues:
    def __init__(self):
        self.values: dict = {}

    def put(self, key, value):
        self.values[key] = value


class FakeCursor:
    def __init__(self, rows: list[tuple], columns: list[str]):
        self._rows = rows
        self._columns = columns
        self._pos = -1

    def moveToFirst(self):
        self._pos = 0
        return bool(self._rows)

    def getColumnIndex(self, name):
        return self._columns.index(name)

    def getInt(self, index):
        return self._rows[self._pos][index]

    def close(self):
        pass


def _android_bridge(
    sdk_int: int = 34,
    existing_uri: bool = False,
    backup_bytes: bytes = b"",
):
    jnius = types.ModuleType("jnius")
    resolver = MagicMock()
    activity = MagicMock()
    activity.getContentResolver.return_value = resolver
    upload_stream = FakeOutputStream()
    resolver.openOutputStream.return_value = upload_stream
    resolver.openInputStream.return_value = FakeInputStream(backup_bytes)
    resolver.insert.return_value = "content://media/external_primary/downloads/1"
    resolver.query.return_value = FakeCursor(
        rows=[(42,)] if existing_uri else [], columns=["_id"]
    )
    resolver.delete.return_value = 1

    downloads = MagicMock()
    downloads.EXTERNAL_CONTENT_URI = "content://media/external_primary/downloads"
    downloads.DISPLAY_NAME = "_display_name"
    downloads.MIME_TYPE = "mime_type"
    downloads.RELATIVE_PATH = "relative_path"
    downloads.IS_PENDING = "is_pending"

    classes = {
        "android.os.Build$VERSION": types.SimpleNamespace(SDK_INT=sdk_int),
        "android.provider.MediaStore$Downloads": downloads,
        "android.provider.MediaStore": MagicMock(),
        "android.content.ContentValues": FakeContentValues,
        "android.net.Uri": types.SimpleNamespace(
            withAppendedPath=lambda base, item: f"{base}/{item}"
        ),
    }
    jnius.autoclass = lambda name: classes[name]
    return jnius, activity, resolver, upload_stream


@contextlib.contextmanager
def _android_env(
    backup: AndroidDurableBackup,
    sdk_int: int = 34,
    existing_uri: bool = False,
):
    jnius, activity, resolver, upload_stream = _android_bridge(
        sdk_int=sdk_int, existing_uri=existing_uri, backup_bytes=_make_db_bytes()
    )
    with (
        patch.dict(sys.modules, {"jnius": jnius}),
        patch("core.storage.android_durable.get_activity", return_value=activity),
    ):
        yield backup, resolver, upload_stream


class TestDescribe:
    def test_describe_includes_dir_and_file(self):
        assert describe() == "Download/Unscreen-data-backup/unscreen.db"
        assert BACKUP_FILE == "unscreen.db"
        assert BACKUP_DIR == "Download/Unscreen-data-backup"


class TestSync:
    def test_uploads_snapshot_to_downloads(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db)) as (backup, resolver, stream):
            assert backup.sync(force=True) is True
            resolver.insert.assert_called_once()
            collection, values = resolver.insert.call_args.args
            assert collection == "content://media/external_primary/downloads"
            assert values.values["_display_name"] == "unscreen.db"
            assert values.values["relative_path"] == "Download/Unscreen-data-backup/"
            assert values.values["is_pending"] == 0
            data = b"".join(stream.chunks)
            fd, snapshot_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                with open(snapshot_path, "wb") as f:
                    f.write(data)
                conn = sqlite3.connect(snapshot_path)
                row = conn.execute("SELECT k, v FROM items").fetchone()
                conn.close()
                assert row == ("a", "1")
            finally:
                os.unlink(snapshot_path)

    def test_updates_existing_row_instead_of_inserting(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db), existing_uri=True) as (
            backup,
            resolver,
            _,
        ):
            assert backup.sync(force=True) is True
            resolver.insert.assert_not_called()
            assert resolver.update.call_count == 2
            first_uri = resolver.update.call_args_list[0].args[0]
            second_uri = resolver.update.call_args_list[1].args[0]
            assert first_uri is second_uri
            assert resolver.update.call_args_list[1].args[1].values["is_pending"] == 0

    def test_throttles_without_force(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db)) as (backup, resolver, _):
            assert backup.sync() is True
            resolver.insert.reset_mock()
            assert backup.sync() is False
            resolver.insert.assert_not_called()

    def test_force_bypasses_throttle(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db)) as (backup, resolver, _):
            assert backup.sync() is True
            resolver.insert.reset_mock()
            assert backup.sync(force=True) is True
            resolver.insert.assert_called_once()

    def test_skips_below_api_29(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db), sdk_int=28) as (
            backup,
            resolver,
            _,
        ):
            assert backup.is_available() is False
            assert backup.sync(force=True) is False
            resolver.insert.assert_not_called()
            resolver.query.assert_not_called()

    def test_skips_missing_db_file(self, tmp_path):
        with _android_env(AndroidDurableBackup(str(tmp_path / "nope.db"))) as (
            backup,
            resolver,
            _,
        ):
            assert backup.sync(force=True) is False
            resolver.insert.assert_not_called()

    def test_failure_is_silent(self, tmp_path):
        db = _make_db(tmp_path)
        jnius, activity, resolver, _ = _android_bridge()
        resolver.insert.side_effect = RuntimeError("MediaStore boom")
        backup = AndroidDurableBackup(db)
        with (
            patch.dict(sys.modules, {"jnius": jnius}),
            patch("core.storage.android_durable.get_activity", return_value=activity),
        ):
            assert backup.sync(force=True) is False

    def test_without_jnius_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        backup = AndroidDurableBackup(db)
        with patch.dict(sys.modules, {"jnius": None}):
            assert backup.is_available() is False
            assert backup.sync(force=True) is False
            assert backup.restore_if_present() is False


class TestRestore:
    def test_restores_backup_into_db_path(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        with _android_env(AndroidDurableBackup(db_path), existing_uri=True) as (
            backup,
            resolver,
            _,
        ):
            assert backup.restore_if_present() is True
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT k, v FROM items").fetchone()
            conn.close()
            assert row == ("restored", "yes")

    def test_restore_without_backup_does_nothing(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        with _android_env(AndroidDurableBackup(db_path), existing_uri=False) as (
            backup,
            resolver,
            _,
        ):
            assert backup.restore_if_present() is False
            assert not os.path.exists(db_path)

    def test_restore_failure_is_silent(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        jnius, activity, resolver, _ = _android_bridge(existing_uri=True)
        resolver.openInputStream.side_effect = RuntimeError("read boom")
        backup = AndroidDurableBackup(db_path)
        with (
            patch.dict(sys.modules, {"jnius": jnius}),
            patch("core.storage.android_durable.get_activity", return_value=activity),
        ):
            assert backup.restore_if_present() is False
        assert not os.path.exists(db_path)

    def test_restore_skips_below_api_29(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        with _android_env(
            AndroidDurableBackup(db_path), sdk_int=28, existing_uri=True
        ) as (backup, resolver, _):
            assert backup.restore_if_present() is False
            resolver.query.assert_not_called()


class TestDelete:
    def test_deletes_backup_file(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db), existing_uri=True) as (
            backup,
            resolver,
            _,
        ):
            assert backup.delete() is True
            resolver.delete.assert_called_once()

    def test_delete_without_backup_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db), existing_uri=False) as (
            backup,
            resolver,
            _,
        ):
            assert backup.delete() is False
            resolver.delete.assert_not_called()

    def test_delete_failure_is_silent(self, tmp_path):
        db = _make_db(tmp_path)
        jnius, activity, resolver, _ = _android_bridge(existing_uri=True)
        resolver.delete.side_effect = RuntimeError("delete boom")
        backup = AndroidDurableBackup(db)
        with (
            patch.dict(sys.modules, {"jnius": jnius}),
            patch("core.storage.android_durable.get_activity", return_value=activity),
        ):
            assert backup.delete() is False

    def test_delete_skips_below_api_29(self, tmp_path):
        db = _make_db(tmp_path)
        with _android_env(AndroidDurableBackup(db), sdk_int=28) as (
            backup,
            resolver,
            _,
        ):
            assert backup.delete() is False
            resolver.query.assert_not_called()


class TestStorageIntegration:
    def test_sync_noop_off_android(self, tmp_path):
        storage = Storage(db_path=str(tmp_path / "data.db"))
        try:
            assert storage.sync_durable_backup(force=True) is False
        finally:
            storage.close()

    def test_restores_durable_backup_on_fresh_android_db(self, tmp_path):
        db_path = str(tmp_path / "data.db")
        jnius, activity, resolver, _ = _android_bridge(
            existing_uri=True, backup_bytes=_make_db_bytes()
        )
        with (
            patch("core.storage.is_android", return_value=True),
            patch.dict(sys.modules, {"jnius": jnius}),
            patch("core.storage.android_durable.get_activity", return_value=activity),
        ):
            storage = Storage(db_path=db_path)
            try:
                conn = sqlite3.connect(db_path)
                restored = conn.execute(
                    "SELECT v FROM items WHERE k='restored'"
                ).fetchone()
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                conn.close()
                assert restored == ("yes",)
                assert "raw_events" in tables
            finally:
                storage.close()

    def test_no_restore_when_db_already_exists(self, tmp_path):
        db_path = _make_db(tmp_path)
        jnius, activity, resolver, _ = _android_bridge(
            existing_uri=True, backup_bytes=_make_db_bytes()
        )
        with (
            patch("core.storage.is_android", return_value=True),
            patch.dict(sys.modules, {"jnius": jnius}),
            patch("core.storage.android_durable.get_activity", return_value=activity),
        ):
            storage = Storage(db_path=db_path)
            try:
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT k, v FROM items").fetchone()
                conn.close()
                assert row == ("a", "1")
            finally:
                storage.close()
        resolver.openInputStream.assert_not_called()
