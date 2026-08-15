import logging
import os
import sqlite3
import tempfile
import time

from utils.android import get_activity

logger = logging.getLogger(__name__)

BACKUP_DIR = "Download/Unscreen-data-backup"
BACKUP_FILE = "unscreen.db"
MIN_SYNC_INTERVAL_S = 60.0
_CHUNK_SIZE = 65536

_CLASS_NAMES = (
    "android.os.Build$VERSION",
    "android.provider.MediaStore$Downloads",
    "android.provider.MediaStore",
    "android.content.ContentValues",
    "android.net.Uri",
)


def describe() -> str:
    return f"{BACKUP_DIR}/{BACKUP_FILE}"


class AndroidDurableBackup:
    """Keep a consistent copy of the SQLite DB in user-visible shared storage.

    The copy lives in the MediaStore Downloads collection (API 29+), where it
    survives an app uninstall and stays visible to and deletable by the user —
    the same "data survives by default, user may remove it" contract Windows
    has. No permissions are needed for files the app contributes itself.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._last_sync_at = 0.0
        self._loaded_classes: dict | None = None

    def is_available(self) -> bool:
        classes = self._classes()
        return classes is not None and classes["VERSION"].SDK_INT >= 29

    def sync(self, force: bool = False) -> bool:
        if not self.is_available():
            return False
        if not force:
            now = time.monotonic()
            if now - self._last_sync_at < MIN_SYNC_INTERVAL_S:
                return False
            self._last_sync_at = now
        try:
            snapshot = self._snapshot_to_tmp()
            if snapshot is None:
                return False
            resolver = get_activity().getContentResolver()
            uri = self._find_backup_uri(resolver)
            size = os.path.getsize(snapshot)
            try:
                self._upload_snapshot(resolver, uri, snapshot)
            finally:
                os.unlink(snapshot)
            logger.info(
                "Durable Android backup synced to %s (%d bytes)",
                describe(),
                size,
            )
            return True
        except Exception:
            logger.exception("Durable Android backup sync failed")
            return False

    def restore_if_present(self) -> bool:
        if not self.is_available():
            return False
        try:
            resolver = get_activity().getContentResolver()
            uri = self._find_backup_uri(resolver)
            if uri is None:
                return False
            stream = resolver.openInputStream(uri)
            tmp = f"{self._db_path}.restore-tmp"
            try:
                with open(tmp, "wb") as out:
                    buffer = bytearray(_CHUNK_SIZE)
                    while True:
                        n = stream.read(buffer)
                        if n <= 0:
                            break
                        out.write(bytes(buffer[:n]))
            finally:
                stream.close()
            os.replace(tmp, self._db_path)
            logger.info("Restored durable Android backup into %s", self._db_path)
            return True
        except Exception:
            logger.exception("Durable Android backup restore failed")
            return False

    def _classes(self) -> dict | None:
        if self._loaded_classes is not None:
            return self._loaded_classes
        try:
            from jnius import autoclass  # type: ignore
        except ImportError:
            logger.debug("pyjnius not available — durable Android backup disabled")
            return None
        loaded: dict = {}
        try:
            for class_name in _CLASS_NAMES:
                key = class_name.split(".")[-1].split("$")[-1]
                loaded[key] = autoclass(class_name)
        except Exception:
            logger.exception("jnius bridge classes failed to load")
            return None
        self._loaded_classes = loaded
        return loaded

    def _snapshot_to_tmp(self) -> str | None:
        if not os.path.exists(self._db_path) or os.path.getsize(self._db_path) == 0:
            return None
        fd, tmp = tempfile.mkstemp(prefix="unscreen-backup-", suffix=".db")
        os.close(fd)
        os.unlink(tmp)
        try:
            src = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                src.execute(f"VACUUM INTO '{tmp}'")
            finally:
                src.close()
        except Exception:
            logger.warning(
                "VACUUM INTO failed for durable backup — falling back to raw copy",
                exc_info=True,
            )
            try:
                with open(self._db_path, "rb") as src, open(tmp, "wb") as dst:
                    while True:
                        chunk = src.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        dst.write(chunk)
            except Exception:
                logger.exception("Raw DB copy for durable backup failed")
                os.unlink(tmp)
                return None
        return tmp

    def _find_backup_uri(self, resolver) -> object | None:
        classes = self._classes()
        if classes is None:
            return None
        Downloads = classes["Downloads"]
        cursor = resolver.query(
            Downloads.EXTERNAL_CONTENT_URI,
            ["_id"],
            "_display_name = ? AND relative_path = ?",
            [BACKUP_FILE, f"{BACKUP_DIR}/"],
            None,
        )
        try:
            if cursor.moveToFirst():
                index = cursor.getColumnIndex("_id")
                Uri = classes["Uri"]
                return Uri.withAppendedPath(
                    Downloads.EXTERNAL_CONTENT_URI, str(cursor.getInt(index))
                )
            return None
        finally:
            cursor.close()

    def _upload_snapshot(self, resolver, uri: object, snapshot: str) -> None:
        classes = self._classes()
        if classes is None:
            raise RuntimeError("jnius bridge classes unavailable")
        Downloads = classes["Downloads"]
        ContentValues = classes["ContentValues"]
        values = ContentValues()
        values.put(Downloads.DISPLAY_NAME, BACKUP_FILE)
        values.put(Downloads.MIME_TYPE, "application/octet-stream")
        values.put(Downloads.RELATIVE_PATH, f"{BACKUP_DIR}/")
        values.put(Downloads.IS_PENDING, 1)
        if uri is None:
            uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
            if uri is None:
                raise RuntimeError("MediaStore insert returned no URI")
        else:
            resolver.update(uri, values, None, None)
        stream = resolver.openOutputStream(uri, "w")
        try:
            with open(snapshot, "rb") as src:
                while True:
                    chunk = src.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    stream.write(chunk, 0, len(chunk))
        finally:
            stream.flush()
            stream.close()
        values.put(Downloads.IS_PENDING, 0)
        resolver.update(uri, values, None, None)
