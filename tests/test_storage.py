import sqlite3
from datetime import datetime, timezone

import pytest

from core.device_identity import get_device_id
from core.storage import SCHEMA_VERSION, Storage

T0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
T0_MS = int(T0.timestamp() * 1000)

_CORE_V1 = """\
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    hostname    TEXT,
    platform    TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT,
    is_current  INTEGER DEFAULT 0
);
"""

_PLAT_V1 = """\
CREATE TABLE IF NOT EXISTS events_{short_id} (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher    TEXT NOT NULL,
    timestamp  REAL NOT NULL,
    duration   REAL DEFAULT 0,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{short_id}_watcher_ts
    ON events_{short_id}(watcher, timestamp);
"""

_PLAT_V2 = _PLAT_V1 + """\
CREATE TABLE IF NOT EXISTS observations_{short_id} (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher    TEXT NOT NULL,
    timestamp  REAL NOT NULL,
    data       TEXT NOT NULL,
    obs_type   TEXT DEFAULT 'snapshot'
);
CREATE INDEX IF NOT EXISTS idx_obs_{short_id}_watcher_ts
    ON observations_{short_id}(watcher, timestamp);
CREATE TABLE IF NOT EXISTS sessions_{short_id} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher     TEXT NOT NULL,
    start_ts    REAL NOT NULL,
    end_ts      REAL,
    duration_s  REAL,
    app_key     TEXT NOT NULL,
    data        TEXT NOT NULL,
    source      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ses_{short_id}_app_ts
    ON sessions_{short_id}(app_key, start_ts);
"""

_CORE_V5 = """\
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    hostname    TEXT,
    platform    TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT,
    is_current  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS raw_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    platform      TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    timestamp     REAL NOT NULL,
    collected_at  REAL NOT NULL,
    payload       TEXT NOT NULL,
    source        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_events_type_ts
    ON raw_events(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_raw_events_device_ts
    ON raw_events(device_id, timestamp);
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    platform      TEXT NOT NULL,
    start_ts      REAL NOT NULL,
    end_ts        REAL,
    duration_s    REAL,
    app_key       TEXT NOT NULL,
    payload       TEXT NOT NULL,
    session_type  TEXT DEFAULT 'foreground'
);
CREATE INDEX IF NOT EXISTS idx_sessions_device_app
    ON sessions(device_id, app_key, start_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_ts
    ON sessions(device_id, start_ts);
"""

_PLAT_V5 = """\
DROP TABLE IF EXISTS events_{short_id};
DROP TABLE IF EXISTS observations_{short_id};
DROP TABLE IF EXISTS sessions_{short_id};
"""


def _short_id() -> str:
    return get_device_id()[:8]


def _exec_sql(conn, sql: str, sid: str) -> None:
    for stmt in sql.replace("{short_id}", sid).split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


_SCHEMAS = {
    1: (_CORE_V1, _PLAT_V1),
    2: (_CORE_V1, _PLAT_V2),
    5: (_CORE_V5, _PLAT_V5),
}


def _seed_version(db_path: str, version: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    core_sql, plat_sql = _SCHEMAS[version]
    sid = _short_id()
    _exec_sql(conn, core_sql, sid)
    _exec_sql(conn, plat_sql, sid)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.close()


def _assert_schema_v8(conn) -> None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for tbl in (
        "devices",
        "event_types",
        "sources",
        "raw_events",
        "app_sessions",
        "status_sessions",
        "url_visits",
        "sync_cursors",
    ):
        assert tbl in tables, f"Missing table {tbl}"
    sid = _short_id()
    for legacy in (f"events_{sid}", f"observations_{sid}", f"sessions_{sid}"):
        assert legacy not in tables, f"Legacy table {legacy} should have been dropped"
    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        if not r[0].startswith("sqlite_")
    }
    for idx in (
        "uq_raw_events_identity",
        "idx_raw_events_device_ts",
        "idx_raw_events_type_ts",
        "uq_app_sessions_identity",
        "idx_app_sessions_device_app",
        "idx_app_sessions_ts",
        "uq_status_sessions_identity",
        "idx_status_sessions_ts",
        "idx_status_sessions_status",
        "uq_url_visits_identity",
        "idx_url_visits_device_seen",
        "idx_url_visits_device_domain",
        "idx_url_visits_event",
        "idx_url_visits_session",
    ):
        assert idx in indexes, f"Missing index {idx}"


def _write_fg_event(db: Storage, ts: int = T0_MS, payload: dict | None = None) -> int:
    return db.write_event(
        event_type="foreground_transition",
        timestamp=ts,
        payload=payload if payload is not None else {"app": "Code.exe"},
        source="foreground",
    )


class TestWriteEvent:
    def test_writes_event_to_raw_events(self, in_memory_db, make_tick):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=T0_MS,
            payload={"app": "Code.exe"},
            source="foreground",
        )
        rows = in_memory_db._conn.execute("SELECT * FROM raw_events").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == in_memory_db._device_fk
        assert rows[0][2] == in_memory_db._resolve_name_fk(
            "event_types", "foreground_transition", in_memory_db._event_type_ids
        )
        assert rows[0][4] == T0_MS
        assert isinstance(rows[0][7], int)  # payload_hash INTEGER
        assert -(2**63) <= rows[0][7] < 2**63

    def test_get_raw_events_returns_event(self, in_memory_db, make_tick):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=T0_MS,
            payload={"app": "Code.exe"},
            source="foreground",
        )
        results = in_memory_db.get_raw_events()
        assert len(results) == 1
        assert results[0]["event_type"] == "foreground_transition"
        assert results[0]["payload"]["app"] == "Code.exe"
        assert results[0]["device_id"] == in_memory_db._device_id
        assert results[0]["platform"] == in_memory_db._platform

    def test_get_raw_events_filtered(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=1000)
        in_memory_db.write_event(
            event_type="power_change", timestamp=1100, payload={}, source="pwr"
        )
        fg_events = in_memory_db.get_raw_events(event_type="foreground_transition")
        assert len(fg_events) == 1
        assert fg_events[0]["event_type"] == "foreground_transition"

    def test_get_raw_events_since_until(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=1000)
        _write_fg_event(in_memory_db, ts=1100)
        results = in_memory_db.get_raw_events(since=1050, until=1150)
        assert len(results) == 1
        assert results[0]["timestamp"] == 1100

    def test_get_raw_events_desc(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=1000)
        _write_fg_event(in_memory_db, ts=1100)
        results = in_memory_db.get_raw_events(desc=True)
        assert results[0]["timestamp"] > results[1]["timestamp"]

    def test_count_events_all(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=1000)
        in_memory_db.write_event(
            event_type="power_change", timestamp=1100, payload={}, source="pwr"
        )
        assert in_memory_db.count_events() == 2

    def test_count_events_filtered(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=1000)
        for ts in (1100, 1200):
            in_memory_db.write_event(
                event_type="power_change", timestamp=ts, payload={}, source="pwr"
            )
        assert in_memory_db.count_events(event_type="power_change") == 2
        assert in_memory_db.count_events(since=1050, until=1150) == 1
        assert in_memory_db.count_events(event_type="nope") == 0

    def test_get_raw_events_limit(self, in_memory_db):
        for i in range(10):
            _write_fg_event(in_memory_db, ts=1000 + i)
        results = in_memory_db.get_raw_events(limit=3)
        assert len(results) == 3

    def test_clear_all_data_clears_raw_events(self, in_memory_db):
        _write_fg_event(in_memory_db)
        in_memory_db.clear_all_data()
        assert len(in_memory_db.get_raw_events()) == 0

    def test_clear_all_data_clears_sessions(self, in_memory_db):
        in_memory_db.open_app_session(
            event_id=1, start_ts=1000, app_key="Code.exe", payload={}
        )
        in_memory_db.open_status_session(
            event_id=2, start_ts=1000, status="idle", payload={}
        )
        in_memory_db.clear_all_data()
        assert len(in_memory_db.get_app_sessions()) == 0
        assert len(in_memory_db.get_status_sessions()) == 0

    def test_clear_all_data_clears_sync_cursors(self, in_memory_db):
        in_memory_db._conn.execute(
            "INSERT INTO sync_cursors (remote_device_id, last_synced_at) VALUES (?, ?)",
            ("remote-1", 1000),
        )
        in_memory_db.clear_all_data()
        assert (
            in_memory_db._conn.execute("SELECT COUNT(*) FROM sync_cursors").fetchone()[
                0
            ]
            == 0
        )

    def test_dictionary_tables_are_shared(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition", timestamp=1000, payload={}, source="fg"
        )
        in_memory_db.write_event(
            event_type="foreground_transition", timestamp=1100, payload={}, source="fg"
        )
        count = in_memory_db._conn.execute(
            "SELECT COUNT(*) FROM event_types WHERE name = 'foreground_transition'"
        ).fetchone()[0]
        assert count == 1


class TestEventDedup:
    def test_identical_event_raises_integrity_error(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=T0_MS)
        with pytest.raises(sqlite3.IntegrityError):
            _write_fg_event(in_memory_db, ts=T0_MS)
        assert len(in_memory_db.get_raw_events()) == 1

    def test_same_timestamp_distinct_payloads_are_allowed(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=T0_MS, payload={"app": "Code.exe"})
        _write_fg_event(in_memory_db, ts=T0_MS, payload={"app": "brave.exe"})
        assert len(in_memory_db.get_raw_events()) == 2

    def test_same_payload_distinct_timestamps_are_allowed(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=1000)
        _write_fg_event(in_memory_db, ts=1001)
        assert len(in_memory_db.get_raw_events()) == 2

    def test_different_event_type_same_payload_hash_allowed(self, in_memory_db):
        _write_fg_event(in_memory_db, ts=T0_MS, payload={"app": "Code.exe"})
        in_memory_db.write_event(
            event_type="power_change",
            timestamp=T0_MS,
            payload={"app": "Code.exe"},
            source="power",
        )
        assert len(in_memory_db.get_raw_events()) == 2


class TestAppSessions:
    def _open(self, db: Storage, event_id: int = 1, start_ts: int = 1000):
        return db.open_app_session(
            event_id=event_id,
            start_ts=start_ts,
            app_key="Code.exe",
            payload={"title": "main.py"},
        )

    def test_open_session_has_no_end(self, in_memory_db):
        self._open(in_memory_db)
        results = in_memory_db.get_app_sessions()
        assert len(results) == 1
        assert results[0]["app_key"] == "Code.exe"
        assert results[0]["payload"]["title"] == "main.py"
        assert results[0]["event_id"] == 1
        assert results[0]["end_ts"] is None
        assert results[0]["duration_s"] is None

    def test_close_session_sets_duration(self, in_memory_db):
        self._open(in_memory_db, start_ts=1000)
        assert in_memory_db.close_app_session(event_id=1, end_ts=1500) is True
        results = in_memory_db.get_app_sessions()
        assert results[0]["end_ts"] == 1500
        assert results[0]["duration_s"] == 0.5

    def test_close_session_is_idempotent(self, in_memory_db):
        self._open(in_memory_db, start_ts=1000)
        assert in_memory_db.close_app_session(event_id=1, end_ts=1500) is True
        assert in_memory_db.close_app_session(event_id=1, end_ts=2000) is False
        results = in_memory_db.get_app_sessions()
        assert results[0]["end_ts"] == 1500

    def test_close_unknown_session_returns_false(self, in_memory_db):
        assert in_memory_db.close_app_session(event_id=99, end_ts=1500) is False

    def test_get_app_sessions_filtered(self, in_memory_db):
        self._open(in_memory_db, event_id=1, start_ts=1000)
        self._open(in_memory_db, event_id=2, start_ts=1100)
        in_memory_db.close_app_session(event_id=1, end_ts=1050)
        results = in_memory_db.get_app_sessions(app_key="Code.exe")
        assert len(results) == 2
        results = in_memory_db.get_app_sessions(since=1050)
        assert len(results) == 1
        assert results[0]["event_id"] == 2
        results = in_memory_db.get_app_sessions(until=1050)
        assert len(results) == 1
        assert results[0]["event_id"] == 1
        results = in_memory_db.get_app_sessions(limit=1)
        assert len(results) == 1


class TestStatusSessions:
    def _open(self, db: Storage, event_id: int = 1, start_ts: int = 1000):
        return db.open_status_session(
            event_id=event_id,
            start_ts=start_ts,
            status="idle",
            payload={"idle_seconds": 61.0},
        )

    def test_open_status_has_no_end(self, in_memory_db):
        self._open(in_memory_db)
        results = in_memory_db.get_status_sessions()
        assert len(results) == 1
        assert results[0]["status"] == "idle"
        assert results[0]["payload"]["idle_seconds"] == 61.0
        assert results[0]["event_id"] == 1
        assert results[0]["end_ts"] is None
        assert results[0]["duration_s"] is None

    def test_close_status_sets_duration(self, in_memory_db):
        self._open(in_memory_db, start_ts=1000)
        assert in_memory_db.close_status_session(event_id=1, end_ts=1300) is True
        results = in_memory_db.get_status_sessions()
        assert results[0]["end_ts"] == 1300
        assert results[0]["duration_s"] == 0.3

    def test_close_status_is_idempotent(self, in_memory_db):
        self._open(in_memory_db, start_ts=1000)
        assert in_memory_db.close_status_session(event_id=1, end_ts=1300) is True
        assert in_memory_db.close_status_session(event_id=1, end_ts=2000) is False

    def test_get_status_sessions_filtered_by_status(self, in_memory_db):
        self._open(in_memory_db, event_id=1, start_ts=1000)
        in_memory_db.open_status_session(
            event_id=2, start_ts=1200, status="active", payload={}
        )
        results = in_memory_db.get_status_sessions(status="idle")
        assert len(results) == 1
        assert results[0]["event_id"] == 1


class TestUrlVisits:
    def _write_visit(
        self,
        db: Storage,
        url: str = "https://github.com/user/repo",
        seen_at: int = T0_MS,
    ) -> int:
        event_id = _write_fg_event(db, ts=seen_at)
        return db.write_url_visit(
            url=url,
            seen_at=seen_at,
            event_id=event_id,
            browser="brave",
            extraction_method="uia",
            confidence="high",
            scheme="https",
            host="github.com",
            domain="github.com",
            path="/user/repo",
            is_trackable=True,
        )

    def test_write_and_get_url_visit(self, in_memory_db):
        visit_id = self._write_visit(in_memory_db)
        assert visit_id > 0

        visits = in_memory_db.get_url_visits()
        assert len(visits) == 1
        v = visits[0]
        assert v["url"] == "https://github.com/user/repo"
        assert v["browser"] == "brave"
        assert v["host"] == "github.com"
        assert v["domain"] == "github.com"
        assert v["extraction_method"] == "uia"
        assert v["confidence"] == "high"
        assert v["is_trackable"] is True

    def test_event_id_is_populated_at_write_time(self, in_memory_db):
        event_id = _write_fg_event(in_memory_db)
        in_memory_db.write_url_visit(
            url="https://a.com", seen_at=T0_MS, event_id=event_id
        )
        visits = in_memory_db.get_url_visits()
        assert visits[0]["event_id"] == event_id

    def test_get_url_visits_filtered_by_device(self, in_memory_db):
        self._write_visit(in_memory_db)
        result = in_memory_db.get_url_visits(device_id="nonexistent")
        assert len(result) == 0

    def test_get_url_visits_filtered_by_time(self, in_memory_db):
        self._write_visit(in_memory_db, url="https://a.com", seen_at=1000)
        self._write_visit(in_memory_db, url="https://b.com", seen_at=1100)

        results = in_memory_db.get_url_visits(since=1050, until=1150)
        assert len(results) == 1
        assert results[0]["url"] == "https://b.com"

    def test_get_url_visits_limit(self, in_memory_db):
        for i in range(5):
            self._write_visit(in_memory_db, url=f"https://x.com/{i}", seen_at=T0_MS + i)
        results = in_memory_db.get_url_visits(limit=2)
        assert len(results) == 2

    def test_duplicate_url_in_same_event_is_skipped(self, in_memory_db):
        event_id = _write_fg_event(in_memory_db)
        first = in_memory_db.write_url_visit(
            url="https://a.com", seen_at=T0_MS, event_id=event_id
        )
        second = in_memory_db.write_url_visit(
            url="https://a.com", seen_at=T0_MS + 10, event_id=event_id
        )
        assert first > 0
        assert second == 0
        assert len(in_memory_db.get_url_visits()) == 1

    def test_same_url_different_event_allowed(self, in_memory_db):
        self._write_visit(in_memory_db, url="https://a.com", seen_at=T0_MS)
        self._write_visit(in_memory_db, url="https://a.com", seen_at=T0_MS + 1)
        assert len(in_memory_db.get_url_visits()) == 2

    def test_clear_all_data_clears_url_visits(self, in_memory_db):
        self._write_visit(in_memory_db)
        in_memory_db.clear_all_data()
        assert len(in_memory_db.get_url_visits()) == 0

    def test_backfill_session_id(self, in_memory_db):
        visit_id = self._write_visit(in_memory_db)
        sess_id = in_memory_db.open_app_session(
            event_id=1, start_ts=1000, app_key="brave.exe", payload={}
        )
        in_memory_db.backfill_url_session_id(visit_id, sess_id)
        visits = in_memory_db.get_url_visits()
        assert visits[0]["session_id"] == sess_id


class TestSchemaMigration:
    def test_fresh_db_gets_current_version(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        storage.close()

    def test_migration_skipped_when_up_to_date(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage1 = Storage(db_path=db)
        _write_fg_event(storage1)
        assert len(storage1.get_raw_events()) == 1
        storage1.close()

        storage2 = Storage(db_path=db)
        assert storage2._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        events = storage2.get_raw_events()
        assert len(events) == 1
        assert events[0]["payload"]["app"] == "Code.exe"
        storage2.close()

    def test_migration_is_idempotent(self, tmp_path):
        db = str(tmp_path / "test.db")
        for i in range(5):
            storage = Storage(db_path=db)
            assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
            if i == 0:
                _write_fg_event(storage)
            storage.close()

        final = Storage(db_path=db)
        events = final.get_raw_events()
        assert len(events) == 1
        final.close()

    def test_v5_db_is_wiped_and_recreated(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 5)
        conn = sqlite3.connect(db)
        did = get_device_id()
        conn.execute(
            "INSERT INTO raw_events (device_id, platform, event_type, timestamp, collected_at, payload, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                did,
                "windows",
                "foreground_transition",
                1000.0,
                1000.0,
                '{"app":"Code.exe"}',
                "foreground",
            ),
        )
        conn.commit()
        conn.close()

        storage = Storage(db_path=db)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        _assert_schema_v8(storage._conn)
        assert len(storage.get_raw_events()) == 0, "pre-v7 data must be wiped"
        storage.close()

    def test_v2_db_is_wiped_and_recreated(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 2)
        conn = sqlite3.connect(db)
        for legacy in (
            f"events_{_short_id()}",
            f"observations_{_short_id()}",
            f"sessions_{_short_id()}",
        ):
            assert legacy in {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }, f"{legacy} should exist before wipe"
        conn.close()

        storage = Storage(db_path=db)
        _assert_schema_v8(storage._conn)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        storage.close()

    def test_v1_db_is_wiped_and_recreated(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 1)
        conn = sqlite3.connect(db)
        assert f"events_{_short_id()}" in {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }, "events_{short_id} should exist before wipe"
        conn.close()

        storage = Storage(db_path=db)
        _assert_schema_v8(storage._conn)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        storage.close()

    def test_interrupted_migration_recovery(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 5)
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE IF NOT EXISTS url_visits (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id        TEXT    NOT NULL,
            event_id         INTEGER REFERENCES raw_events(id),
            session_id       INTEGER REFERENCES sessions(id),
            url              TEXT    NOT NULL,
            scheme           TEXT,
            host             TEXT,
            domain           TEXT,
            path             TEXT,
            extraction_method TEXT,
            confidence        TEXT DEFAULT 'high',
            is_trackable      INTEGER DEFAULT 1,
            seen_at          REAL    NOT NULL,
            collected_at     REAL    NOT NULL
        )""")
        conn.execute("PRAGMA user_version = 5")
        conn.close()

        storage = Storage(db_path=db)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        _assert_schema_v8(storage._conn)
        storage.close()

    def test_wipe_deletes_durable_backup(self, tmp_path, monkeypatch):
        db = str(tmp_path / "test.db")
        _seed_version(db, 5)
        deleted: list[str] = []

        class FakeBackup:
            def __init__(self, db_path: str):
                self._db_path = db_path

            def is_available(self) -> bool:
                return True

            def restore_if_present(self) -> bool:
                return False

            def delete(self) -> bool:
                deleted.append(self._db_path)
                return True

        monkeypatch.setattr(
            "core.storage.android_durable.AndroidDurableBackup", FakeBackup
        )
        monkeypatch.setattr("core.storage.is_android", lambda: True)

        storage = Storage(db_path=db)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert deleted == [db]
        storage.close()

    def test_schema_integrity_after_migration(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        _assert_schema_v8(storage._conn)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        storage.close()

    def test_migration_on_memory_db(self):
        storage = Storage(db_path=":memory:")
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 8
        _assert_schema_v8(storage._conn)
        storage.close()


class TestStorageHealthCheck:
    def test_integrity_ok_on_healthy_db(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        result = storage.check_integrity()
        assert result["ok"] is True
        storage.close()

    def test_integrity_ok_on_memory_db(self, in_memory_db):
        result = in_memory_db.check_integrity()
        assert result["ok"] is True
        assert "in-memory" in result["message"]

    def test_auto_vacuum_skipped_on_memory_db(self, in_memory_db):
        result = in_memory_db.auto_vacuum()
        assert result["vacuumed"] is False
        assert "in-memory" in result["message"]

    def test_auto_vacuum_no_waste_on_fresh_db(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        result = storage.auto_vacuum()
        assert result["vacuumed"] is False
        assert result["waste_pct"] == 0.0
        storage.close()

    def test_auto_vacuum_clears_freelist_pages(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        storage._conn.execute(
            "CREATE TABLE tmp_test (id INTEGER PRIMARY KEY, data TEXT)"
        )
        for _i in range(100):
            storage._conn.execute("INSERT INTO tmp_test (data) VALUES ('x')")
        storage._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        freelist_before = storage._conn.execute("PRAGMA freelist_count").fetchone()[0]
        assert freelist_before == 0, "Freelist should be empty before DROP"

        storage._conn.execute("DROP TABLE tmp_test")
        freelist_before = storage._conn.execute("PRAGMA freelist_count").fetchone()[0]
        assert freelist_before > 0, "DROP TABLE should create freelist pages"

        result = storage.auto_vacuum(waste_pct_threshold=0.0, min_size_mb=0.0)
        assert result["vacuumed"] is True

        after_freelist = storage._conn.execute("PRAGMA freelist_count").fetchone()[0]
        assert after_freelist == 0
        storage.close()

    def test_auto_vacuum_skips_below_threshold(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        result = storage.auto_vacuum(waste_pct_threshold=99.0)
        assert result["vacuumed"] is False
        storage.close()

    def test_startup_checks_do_not_crash(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        assert storage._conn is not None
        storage.close()


class TestStorageInitRegression:
    """Regression tests for CI-specific issues that were missed locally."""

    def test_fresh_memory_db_initialises_without_error(self):
        storage = Storage(db_path=":memory:")
        assert (
            storage._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        )
        storage.close()

    def test_fresh_file_db_initialises_without_error(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        storage = Storage(db_path=db)
        assert (
            storage._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        )
        storage.close()
