import sqlite3
from datetime import datetime, timezone

from core.device_identity import get_device_id
from core.storage import SCHEMA_VERSION, Storage

T0 = datetime(2026, 7, 19, tzinfo=timezone.utc)

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


def _seed_version(db_path: str, version: int, platform: str = "windows") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    core_sql, plat_sql = _SCHEMAS[version]
    sid = _short_id()
    _exec_sql(conn, core_sql, sid)
    _exec_sql(conn, plat_sql, sid)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.close()


def _assert_schema_v6(conn) -> None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for tbl in ("devices", "raw_events", "sessions", "url_visits"):
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
        "idx_raw_events_type_ts",
        "idx_raw_events_device_ts",
        "idx_sessions_device_app",
        "idx_sessions_ts",
        "idx_url_visits_device_seen",
        "idx_url_visits_device_domain",
        "idx_url_visits_event",
        "idx_url_visits_session",
    ):
        assert idx in indexes, f"Missing index {idx}"


class TestWriteEvent:
    def test_writes_event_to_raw_events(self, in_memory_db, make_tick):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=T0.timestamp(),
            payload={"app": "Code.exe"},
            source="foreground",
        )
        rows = in_memory_db._conn.execute("SELECT * FROM raw_events").fetchall()
        assert len(rows) == 1
        assert rows[0][3] == "foreground_transition"

    def test_get_raw_events_returns_event(self, in_memory_db, make_tick):
        ts = T0.timestamp()
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=ts,
            payload={"app": "Code.exe"},
            source="foreground",
        )
        results = in_memory_db.get_raw_events()
        assert len(results) == 1
        assert results[0]["event_type"] == "foreground_transition"
        assert results[0]["payload"]["app"] == "Code.exe"

    def test_get_raw_events_filtered(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={},
            source="fg",
        )
        in_memory_db.write_event(
            event_type="power_change",
            timestamp=1100.0,
            payload={},
            source="pwr",
        )
        fg_events = in_memory_db.get_raw_events(event_type="foreground_transition")
        assert len(fg_events) == 1
        assert fg_events[0]["event_type"] == "foreground_transition"

    def test_get_raw_events_since_until(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={},
            source="fg",
        )
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1100.0,
            payload={},
            source="fg",
        )
        results = in_memory_db.get_raw_events(since=1050.0, until=1150.0)
        assert len(results) == 1
        assert results[0]["timestamp"] == 1100.0

    def test_get_raw_events_desc(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={},
            source="fg",
        )
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1100.0,
            payload={},
            source="fg",
        )
        results = in_memory_db.get_raw_events(desc=True)
        assert results[0]["timestamp"] > results[1]["timestamp"]

    def test_count_events_all(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={},
            source="fg",
        )
        in_memory_db.write_event(
            event_type="power_change",
            timestamp=1100.0,
            payload={},
            source="pwr",
        )
        assert in_memory_db.count_events() == 2

    def test_count_events_filtered(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={},
            source="fg",
        )
        in_memory_db.write_event(
            event_type="power_change",
            timestamp=1100.0,
            payload={},
            source="pwr",
        )
        in_memory_db.write_event(
            event_type="power_change",
            timestamp=1200.0,
            payload={},
            source="pwr",
        )
        assert in_memory_db.count_events(event_type="power_change") == 2
        assert in_memory_db.count_events(since=1050.0, until=1150.0) == 1
        assert in_memory_db.count_events(event_type="nope") == 0

    def test_get_raw_events_limit(self, in_memory_db):
        for i in range(10):
            in_memory_db.write_event(
                event_type="foreground_transition",
                timestamp=float(1000 + i),
                payload={},
                source="fg",
            )
        results = in_memory_db.get_raw_events(limit=3)
        assert len(results) == 3

    def test_clear_all_data_clears_raw_events(self, in_memory_db):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={},
            source="fg",
        )
        in_memory_db.clear_all_data()
        assert len(in_memory_db.get_raw_events()) == 0

    def test_clear_all_data_clears_sessions(self, in_memory_db):
        in_memory_db.write_canonical_session(
            {
                "device_id": "test",
                "platform": "windows",
                "start_ts": 1000.0,
                "end_ts": 1100.0,
                "duration_s": 100.0,
                "app_key": "Code.exe",
                "payload": {},
                "session_type": "foreground",
            }
        )
        in_memory_db.clear_all_data()
        assert len(in_memory_db.get_canonical_sessions()) == 0


class TestCanonicalSessions:
    def test_write_and_read_session(self, in_memory_db):
        in_memory_db.write_canonical_session(
            {
                "device_id": "test",
                "platform": "windows",
                "start_ts": 1000.0,
                "end_ts": 1100.0,
                "duration_s": 100.0,
                "app_key": "Code.exe",
                "payload": {"title": "main.py"},
                "session_type": "foreground",
            }
        )
        results = in_memory_db.get_canonical_sessions()
        assert len(results) == 1
        assert results[0]["app_key"] == "Code.exe"
        assert results[0]["payload"]["title"] == "main.py"

    def test_get_canonical_sessions_filtered(self, in_memory_db):
        for app in ["Code.exe", "brave.exe"]:
            in_memory_db.write_canonical_session(
                {
                    "device_id": "test",
                    "platform": "windows",
                    "start_ts": 1000.0,
                    "end_ts": 1100.0,
                    "duration_s": 100.0,
                    "app_key": app,
                    "payload": {},
                    "session_type": "foreground",
                }
            )
        results = in_memory_db.get_canonical_sessions(app_key="Code.exe")
        assert len(results) == 1


class TestUrlVisits:
    def test_write_and_get_url_visit(self, in_memory_db):
        import time

        seen_at = time.time()
        visit_id = in_memory_db.write_url_visit(
            url="https://github.com/user/repo",
            seen_at=seen_at,
            extraction_method="uia",
            confidence="high",
            scheme="https",
            host="github.com",
            domain="github.com",
            path="/user/repo",
            is_trackable=True,
        )
        assert visit_id > 0

        visits = in_memory_db.get_url_visits()
        assert len(visits) == 1
        v = visits[0]
        assert v["url"] == "https://github.com/user/repo"
        assert v["host"] == "github.com"
        assert v["domain"] == "github.com"
        assert v["extraction_method"] == "uia"
        assert v["confidence"] == "high"
        assert v["is_trackable"] is True

    def test_get_url_visits_filtered_by_device(self, in_memory_db):
        import time

        in_memory_db.write_url_visit(url="https://a.com", seen_at=time.time())
        result = in_memory_db.get_url_visits(device_id="nonexistent")
        assert len(result) == 0

    def test_get_url_visits_filtered_by_time(self, in_memory_db):
        in_memory_db.write_url_visit(url="https://a.com", seen_at=1000.0)
        in_memory_db.write_url_visit(url="https://b.com", seen_at=1100.0)

        results = in_memory_db.get_url_visits(since=1050.0, until=1150.0)
        assert len(results) == 1
        assert results[0]["url"] == "https://b.com"

    def test_get_url_visits_limit(self, in_memory_db):
        import time

        for i in range(5):
            in_memory_db.write_url_visit(url=f"https://x.com/{i}", seen_at=time.time())
        results = in_memory_db.get_url_visits(limit=2)
        assert len(results) == 2

    def test_clear_all_data_clears_url_visits(self, in_memory_db):
        import time

        in_memory_db.write_url_visit(url="https://a.com", seen_at=time.time())
        in_memory_db.clear_all_data()
        assert len(in_memory_db.get_url_visits()) == 0

    def test_backfill_event_id(self, in_memory_db, make_tick):
        import time

        visit_id = in_memory_db.write_url_visit(
            url="https://a.com", seen_at=time.time()
        )
        event_id = in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=time.time(),
            payload={"app": "brave.exe"},
            source="foreground",
        )
        in_memory_db.backfill_url_event_id(visit_id, event_id)
        visits = in_memory_db.get_url_visits()
        assert visits[0]["event_id"] == event_id

    def test_backfill_session_id(self, in_memory_db):
        import time

        visit_id = in_memory_db.write_url_visit(
            url="https://a.com", seen_at=time.time()
        )
        sess_id = in_memory_db.write_canonical_session(
            {
                "device_id": "test",
                "platform": "windows",
                "start_ts": 1000.0,
                "end_ts": 1100.0,
                "duration_s": 100.0,
                "app_key": "brave.exe",
                "payload": {},
                "session_type": "foreground",
            }
        )
        in_memory_db.backfill_url_session_id(visit_id, sess_id)
        visits = in_memory_db.get_url_visits()
        assert visits[0]["session_id"] == sess_id


class TestSchemaMigration:
    def test_migration_sets_version(self, tmp_path, make_tick):
        from core.storage import Storage

        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        storage.close()

    def test_migration_skipped_when_up_to_date(self, tmp_path, make_tick):
        from core.storage import Storage

        db = str(tmp_path / "test.db")
        storage1 = Storage(db_path=db)
        assert storage1._conn.execute("PRAGMA user_version").fetchone()[0] == 6

        storage1.write_event(
            event_type="foreground_transition",
            timestamp=1000.0,
            payload={"app": "Code.exe"},
            source="foreground",
        )
        assert len(storage1.get_raw_events()) == 1
        storage1.close()

        storage2 = Storage(db_path=db)
        assert storage2._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        events = storage2.get_raw_events()
        assert len(events) == 1
        assert events[0]["payload"]["app"] == "Code.exe"
        storage2.close()

    def test_migration_is_idempotent(self, tmp_path, make_tick):
        from core.storage import Storage

        db = str(tmp_path / "test.db")
        for i in range(5):
            storage = Storage(db_path=db)
            assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
            if i == 0:
                storage.write_event(
                    event_type="foreground_transition",
                    timestamp=1000.0,
                    payload={"app": "Code.exe"},
                    source="foreground",
                )
            storage.close()

        final = Storage(db_path=db)
        events = final.get_raw_events()
        assert len(events) == 1
        final.close()

    def test_upgrade_from_v5_creates_url_visits(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 5)
        storage = Storage(db_path=db)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        _assert_schema_v6(storage._conn)
        storage.close()

    def test_upgrade_from_v5_preserves_data(self, tmp_path):
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
        conn.execute(
            "INSERT INTO sessions (device_id, platform, start_ts, end_ts, duration_s, app_key, payload, session_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (did, "windows", 1000.0, 1100.0, 100.0, "Code.exe", "{}", "foreground"),
        )
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        conn.close()

        storage = Storage(db_path=db)
        events = storage.get_raw_events()
        assert len(events) == 1
        assert events[0]["payload"]["app"] == "Code.exe"
        sessions = storage.get_canonical_sessions()
        assert len(sessions) == 1
        assert sessions[0]["app_key"] == "Code.exe"
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        storage.close()

    def test_upgrade_from_v2_drops_legacy_tables(self, tmp_path):
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
            }, f"{legacy} should exist before migration"
        conn.close()

        storage = Storage(db_path=db)
        _assert_schema_v6(storage._conn)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        storage.close()

    def test_upgrade_from_v1_creates_all_tables(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 1)
        conn = sqlite3.connect(db)
        assert f"events_{_short_id()}" in {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }, "events_{short_id} should exist before migration"
        conn.close()

        storage = Storage(db_path=db)
        _assert_schema_v6(storage._conn)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        storage.close()

    def test_schema_integrity_after_migration(self, tmp_path):
        db = str(tmp_path / "test.db")
        storage = Storage(db_path=db)
        _assert_schema_v6(storage._conn)
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        storage.close()

    def test_migration_on_memory_db(self):
        storage = Storage(db_path=":memory:")
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        _assert_schema_v6(storage._conn)
        storage.close()

    def test_interrupted_migration_recovery(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_version(db, 5)
        conn = sqlite3.connect(db)
        # simulate crash after partial DDL: url_visits table created but indexes not yet built
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
        assert storage._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        _assert_schema_v6(storage._conn)
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
