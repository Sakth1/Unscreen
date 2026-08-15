import json
import logging
import os
import platform
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from core.device_identity import get_device_id
from utils.paths import get_data_dir
from utils.platform import is_android

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 6


def _db_path() -> str:
    return os.path.join(get_data_dir(), "data.db")


def _schema_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "schemas")


class Storage:
    _TEST_DEVICE_ID = "00000000-0000-0000-0000-000000000001"

    def __init__(self, db_path: str | None = None):
        self._device_id = get_device_id()
        self._short_id = self._device_id[:8]
        self._platform = "android" if is_android() else platform.system().lower()

        path = db_path or _db_path()
        if self._device_id == self._TEST_DEVICE_ID and path != ":memory:":
            logger.warning(
                "Test device ID used with file-based DB at %s — this may contaminate production data",
                path,
            )

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._path = path
        try:
            self._conn = self._connect()
            self._run_migrations()
            self._register_device()
            self._run_startup_health_checks()
            self._log_storage_info()
        except sqlite3.DatabaseError as exc:
            if path == ":memory:":
                raise
            logger.critical(
                "Database %s is not usable (%s) — quarantining and rebuilding",
                path,
                exc,
            )
            self._rebuild_corrupt_database()

    def _log_storage_info(self) -> None:
        try:
            journal = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            count = self._conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            logger.error("Could not read storage info: %s", exc)
            return
        size = os.path.getsize(self._path) if os.path.exists(self._path) else 0
        logger.info(
            "Storage initialized: db=%s dir=%s UNSCREEN_DATA_DIR=%s "
            "platform=%s device_id=%s journal=%s user_version=%d "
            "raw_events=%d size_bytes=%d",
            self._path,
            get_data_dir(),
            os.environ.get("UNSCREEN_DATA_DIR") or "unset",
            self._platform,
            self._device_id,
            journal,
            version,
            count,
            size,
        )

    @property
    def db_path(self) -> str:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = None
        try:
            conn = sqlite3.connect(
                self._path, check_same_thread=False, isolation_level=None
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn
        except Exception:
            if conn is not None:
                conn.close()
            raise

    def _rebuild_corrupt_database(self) -> None:
        """Quarantine a corrupt DB file and start over with a fresh one."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
        quarantine = f"{self._path}.corrupt-{int(time.time())}"
        os.replace(self._path, quarantine)
        for suffix in ("-wal", "-shm"):
            journal = f"{self._path}{suffix}"
            if os.path.exists(journal):
                os.replace(journal, f"{quarantine}{suffix}")
        self._conn = self._connect()
        self._run_migrations()
        self._register_device()
        self._run_startup_health_checks()

    def _run_migrations(self) -> None:
        cursor = self._conn.execute("PRAGMA user_version")
        current_version = cursor.fetchall()[0][0] or 0

        if current_version < SCHEMA_VERSION:
            logger.info("Migrating schema v%d -> v%d", current_version, SCHEMA_VERSION)

            core_sql = Path(_schema_dir(), "core.sql").read_text()
            for stmt in core_sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._conn.execute(stmt)

            platform_sql = Path(_schema_dir(), f"{self._platform}.sql").read_text()
            platform_sql = platform_sql.replace("{short_id}", self._short_id)
            for stmt in platform_sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._conn.execute(stmt)

            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            logger.info("Schema migration complete")

            cols = {
                r[1]
                for r in self._conn.execute("PRAGMA table_info(raw_events)").fetchall()
            }
            if "tick_uuid" in cols:
                self._conn.execute("ALTER TABLE raw_events DROP COLUMN tick_uuid")
                logger.info("Dropped orphaned tick_uuid column from raw_events")

    def _register_device(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR IGNORE INTO devices
               (device_id, hostname, platform, first_seen, is_current)
               VALUES (?, ?, ?, ?, 1)""",
            (self._device_id, platform.node(), self._platform, now),
        )
        self._conn.execute(
            "UPDATE devices SET last_seen = ? WHERE device_id = ?",
            (now, self._device_id),
        )

    def _run_startup_health_checks(self) -> None:
        result = self.check_integrity()
        if not result["ok"]:
            logger.warning("Database integrity check FAILED: %s", result["message"])

        vac = self.auto_vacuum()
        if vac["vacuumed"]:
            logger.info("Database vacuumed: %s", vac["message"])

    def check_integrity(self) -> dict:
        if self._path == ":memory:":
            return {"ok": True, "message": "skipped (in-memory)"}
        try:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            ok = row[0] == "ok"
            return {"ok": ok, "message": row[0]}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def auto_vacuum(
        self, waste_pct_threshold: float = 20.0, min_size_mb: float = 10.0
    ) -> dict:
        if self._path == ":memory:":
            return {"vacuumed": False, "size_mb": 0.0, "message": "skipped (in-memory)"}
        try:
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
            page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
            freelist_count = self._conn.execute("PRAGMA freelist_count").fetchone()[0]

            total_mb = page_count * page_size / (1024 * 1024)
            waste_mb = freelist_count * page_size / (1024 * 1024)
            waste_pct = (waste_mb / total_mb * 100) if total_mb > 0 else 0.0

            if waste_pct > waste_pct_threshold and total_mb > min_size_mb:
                self._conn.execute("VACUUM")
                new_page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
                new_total_mb = new_page_count * page_size / (1024 * 1024)
                return {
                    "vacuumed": True,
                    "size_mb": total_mb,
                    "waste_pct": waste_pct,
                    "new_size_mb": new_total_mb,
                    "message": f"vacuumed {total_mb:.1f}MB -> {new_total_mb:.1f}MB (waste was {waste_pct:.1f}%)",
                }

            return {
                "vacuumed": False,
                "size_mb": total_mb,
                "waste_pct": waste_pct,
                "message": f"{total_mb:.1f}MB, {waste_pct:.1f}% waste (threshold: {waste_pct_threshold:.0f}%)",
            }
        except Exception as e:
            return {"vacuumed": False, "size_mb": 0.0, "message": f"check failed: {e}"}

    def write_event(
        self,
        event_type: str,
        timestamp: float,
        payload: dict,
        source: str,
    ) -> int:
        logger.debug(
            "Writing event: type=%s timestamp=%.3f source=%s payload=%s",
            event_type,
            timestamp,
            source,
            json.dumps(payload)[:200],
        )
        self._conn.execute(
            """INSERT INTO raw_events
               (device_id, platform, event_type, timestamp, collected_at, payload, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                self._device_id,
                self._platform,
                event_type,
                timestamp,
                datetime.now(timezone.utc).timestamp(),
                json.dumps(payload),
                source,
            ),
        )
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_raw_events(
        self,
        event_type: str | None = None,
        source: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int | None = None,
        desc: bool = False,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if event_type:
            filters.append("event_type = ?")
            params.append(event_type)
        if source:
            filters.append("source = ?")
            params.append(source)
        if since is not None:
            filters.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            filters.append("timestamp <= ?")
            params.append(until)

        sql = "SELECT id, device_id, platform, event_type, timestamp, collected_at, payload, source FROM raw_events"
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY timestamp DESC" if desc else " ORDER BY timestamp ASC"
        if limit is not None:
            sql += f" LIMIT {limit}"

        return [
            {
                "id": r[0],
                "device_id": r[1],
                "platform": r[2],
                "event_type": r[3],
                "timestamp": r[4],
                "collected_at": r[5],
                "payload": json.loads(r[6]),
                "source": r[7],
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def write_canonical_session(self, session: dict) -> int:
        self._conn.execute(
            """INSERT INTO sessions
               (device_id, platform, start_ts, end_ts, duration_s, app_key, payload, session_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session["device_id"],
                session["platform"],
                session["start_ts"],
                session.get("end_ts"),
                session.get("duration_s"),
                session["app_key"],
                json.dumps(session["payload"]),
                session.get("session_type", "foreground"),
            ),
        )
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_canonical_sessions(
        self,
        app_key: str | None = None,
        device_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        platform: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if app_key:
            filters.append("app_key = ?")
            params.append(app_key)
        if device_id:
            filters.append("device_id = ?")
            params.append(device_id)
        if since is not None:
            filters.append("start_ts >= ?")
            params.append(since)
        if until is not None:
            filters.append("COALESCE(end_ts, start_ts) <= ?")
            params.append(until)
        if platform:
            filters.append("platform = ?")
            params.append(platform)

        sql = (
            "SELECT id, device_id, platform, start_ts, end_ts,"
            "duration_s,app_key, payload, session_type FROM sessions"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY start_ts ASC"
        if limit is not None:
            sql += f" LIMIT {limit}"

        return [
            {
                "id": r[0],
                "device_id": r[1],
                "platform": r[2],
                "start_ts": r[3],
                "end_ts": r[4],
                "duration_s": r[5],
                "app_key": r[6],
                "payload": json.loads(r[7]),
                "session_type": r[8],
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def write_url_visit(
        self,
        url: str,
        seen_at: float,
        extraction_method: str | None = None,
        confidence: str = "high",
        scheme: str | None = None,
        host: str | None = None,
        domain: str | None = None,
        path: str | None = None,
        is_trackable: bool = True,
    ) -> int:
        self._conn.execute(
            """INSERT INTO url_visits
               (device_id, url, seen_at, collected_at, extraction_method, confidence,
                scheme, host, domain, path, is_trackable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self._device_id,
                url,
                seen_at,
                datetime.now(timezone.utc).timestamp(),
                extraction_method,
                confidence,
                scheme,
                host,
                domain,
                path,
                int(is_trackable),
            ),
        )
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_url_visits(
        self,
        device_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if device_id:
            filters.append("device_id = ?")
            params.append(device_id)
        if since is not None:
            filters.append("seen_at >= ?")
            params.append(since)
        if until is not None:
            filters.append("seen_at <= ?")
            params.append(until)

        sql = (
            "SELECT id, device_id, event_id, session_id, url, scheme, host, domain, path, "
            "extraction_method, confidence, is_trackable, seen_at, collected_at FROM url_visits"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY seen_at ASC"
        if limit is not None:
            sql += f" LIMIT {limit}"

        return [
            {
                "id": r[0],
                "device_id": r[1],
                "event_id": r[2],
                "session_id": r[3],
                "url": r[4],
                "scheme": r[5],
                "host": r[6],
                "domain": r[7],
                "path": r[8],
                "extraction_method": r[9],
                "confidence": r[10],
                "is_trackable": bool(r[11]),
                "seen_at": r[12],
                "collected_at": r[13],
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def backfill_url_event_id(self, url_visit_id: int, event_id: int) -> None:
        self._conn.execute(
            "UPDATE url_visits SET event_id = ? WHERE id = ?",
            (event_id, url_visit_id),
        )

    def backfill_url_session_id(self, url_visit_id: int, session_id: int) -> None:
        self._conn.execute(
            "UPDATE url_visits SET session_id = ? WHERE id = ?",
            (session_id, url_visit_id),
        )

    def get_today_seconds(self) -> float:
        today_start = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        row = self._conn.execute(
            "SELECT COALESCE(SUM(duration_s), 0) FROM sessions WHERE start_ts >= ? AND duration_s IS NOT NULL",
            (today_start,),
        ).fetchone()
        return float(row[0])

    def count_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> int:
        """Count raw events matching the filters (cheap COUNT(*))."""
        filters: list[str] = []
        params: list = []

        if event_type:
            filters.append("event_type = ?")
            params.append(event_type)
        if since is not None:
            filters.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            filters.append("timestamp <= ?")
            params.append(until)

        sql = "SELECT COUNT(*) FROM raw_events"
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def get_latest_battery(self) -> dict | None:
        row = self._conn.execute(
            "SELECT payload FROM raw_events WHERE event_type = ? ORDER BY timestamp DESC LIMIT 1",
            ("power_change",),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def get_today_top_apps(self, limit: int = 5) -> list[dict]:
        today_start = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        rows = self._conn.execute(
            """SELECT app_key, SUM(duration_s) as total_s
               FROM sessions
               WHERE start_ts >= ? AND duration_s IS NOT NULL
               GROUP BY app_key
               ORDER BY total_s DESC
               LIMIT ?""",
            (today_start, limit),
        ).fetchall()
        return [{"app_key": r[0], "duration_s": r[1]} for r in rows]

    def clear_all_data(self) -> None:
        self._conn.execute("DELETE FROM url_visits")
        self._conn.execute("DELETE FROM raw_events")
        self._conn.execute("DELETE FROM sessions")
        for suffix in ("events_", "observations_", "sessions_"):
            legacy = f"{suffix}{self._short_id}"
            self._conn.execute(f"DROP TABLE IF EXISTS {legacy}")
        self._conn.execute(
            "UPDATE devices SET first_seen = ? WHERE device_id = ?",
            (datetime.now(timezone.utc).isoformat(), self._device_id),
        )
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.execute("VACUUM")
        logger.warning("All data cleared for device %s", self._short_id)

    def close(self) -> None:
        self._conn.close()
