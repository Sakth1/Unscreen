import hashlib
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
from utils.time_utils import utc_timestamp

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 8

# Pre-v8 databases are structurally incompatible with the current schema
# (16-byte BLOB hash, sessions table without event_id, no status_sessions).
# The app is early stage, so rather than migrating we wipe and recreate
# fresh.
WIPE_BELOW_VERSION = SCHEMA_VERSION


def _db_path() -> str:
    return os.path.join(get_data_dir(), "data.db")


def _schema_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "schemas")


def _payload_hash(payload: dict) -> int:
    """8-byte blake2b of the canonical payload JSON, as a positive INTEGER.

    Halves the v7 storage cost (16-byte BLOB + index leaf). The digest is
    deterministic across processes and platforms, which is what the dedup
    identity needs; 64 bits of collision space is ample for dedup at
    personal scale.
    """
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    digest = hashlib.blake2b(canonical.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


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
        self._durable_backup = None
        self._device_fk: int | None = None
        self._event_type_ids: dict[str, int] = {}
        self._source_ids: dict[str, int] = {}
        if (
            self._platform == "android"
            and path != ":memory:"
            and not os.path.exists(path)
        ):
            self._restore_durable_backup()
        try:
            self._conn = self._connect()
            self._run_migrations()
            self._register_device()
            self._device_fk = self._resolve_device_fk(self._device_id)
            self.close_orphaned_sessions(utc_timestamp())
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
        durable = ""
        if self._platform == "android":
            from core.storage.android_durable import describe

            durable = f" android_durable_backup={describe()}"
        logger.info(
            "Storage initialized: db=%s dir=%s UNSCREEN_DATA_DIR=%s "
            "platform=%s device_id=%s journal=%s user_version=%d "
            "raw_events=%d size_bytes=%d%s",
            self._path,
            get_data_dir(),
            os.environ.get("UNSCREEN_DATA_DIR") or "unset",
            self._platform,
            self._device_id,
            journal,
            version,
            count,
            size,
            durable,
        )

    def _restore_durable_backup(self) -> None:
        try:
            from core.storage.android_durable import AndroidDurableBackup, describe

            backup = AndroidDurableBackup(self._path)
            if backup.is_available() and backup.restore_if_present():
                logger.info("Restored durable Android backup from %s", describe())
        except Exception:
            logger.exception("Durable Android backup restore at startup failed")

    def sync_durable_backup(self, force: bool = False) -> bool:
        if self._platform != "android":
            return False
        if self._durable_backup is None:
            from core.storage.android_durable import AndroidDurableBackup

            self._durable_backup = AndroidDurableBackup(self._path)
        return self._durable_backup.sync(force=force)

    @property
    def db_path(self) -> str:
        return self._path

    @property
    def device_id(self) -> str:
        return self._device_id

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
        self._device_fk = self._resolve_device_fk(self._device_id)
        self._run_startup_health_checks()

    def _run_migrations(self) -> None:
        current_version = self._conn.execute("PRAGMA user_version").fetchone()[0] or 0

        if current_version == SCHEMA_VERSION:
            return

        if current_version == 0:
            logger.info("Fresh database — creating schema v%d", SCHEMA_VERSION)
            self._create_schema()
        else:
            logger.warning(
                "Database schema v%d predates v%d — wiping all data and "
                "recreating (early-stage policy: no data migration)",
                current_version,
                WIPE_BELOW_VERSION,
            )
            self._wipe_and_recreate()

    def _create_schema(self) -> None:
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
        logger.info("Schema created: v%d", SCHEMA_VERSION)

    def _wipe_and_recreate(self) -> None:
        self._conn.close()
        for suffix in ("", "-wal", "-shm"):
            self._remove_or_quarantine(f"{self._path}{suffix}")
        if self._platform == "android":
            try:
                from core.storage.android_durable import AndroidDurableBackup

                if AndroidDurableBackup(self._path).delete():
                    logger.info("Deleted durable Android backup during schema wipe")
            except Exception:
                logger.exception(
                    "Failed to delete durable Android backup during schema wipe"
                )
        self._conn = self._connect()
        self._create_schema()

    def _remove_or_quarantine(self, path: str) -> None:
        """Delete *path*, quarantining it when deletion keeps failing.

        A stale handle from a previous instance (or a brief antivirus scan)
        can block ``os.remove``; the file is then renamed aside instead of
        aborting the wipe. If even the rename fails the wipe raises a
        human-readable :class:`RuntimeError` instead of leaking a cryptic
        ``PermissionError``.
        """
        if not os.path.exists(path):
            return
        try:
            for attempt in range(3):
                try:
                    os.remove(path)
                    return
                except OSError:
                    if attempt >= 2:
                        raise
                    time.sleep(0.2)
        except OSError:
            quarantined = self._quarantine_file(path)
            if quarantined is not None:
                logger.warning(
                    "Quarantined locked database file %s -> %s", path, quarantined
                )
                return
            raise RuntimeError(
                f"Database wipe failed: could not remove or quarantine {path}. "
                "Close every other Unscreen instance and try again."
            ) from None

    def _quarantine_file(self, path: str) -> str | None:
        """Rename *path* aside so the schema wipe can proceed."""
        base = f"{path}.quarantined-{int(time.time())}"
        for attempt in range(10):
            target = base if attempt == 0 else f"{base}-{attempt}"
            if os.path.exists(target):
                continue
            try:
                os.rename(path, target)
            except OSError:
                return None
            return target
        return None

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

    def _resolve_device_fk(self, device_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row is not None:
            return row[0]
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR IGNORE INTO devices
               (device_id, hostname, platform, first_seen, is_current)
               VALUES (?, NULL, ?, ?, 0)""",
            (device_id, self._platform, now),
        )
        row = self._conn.execute(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        return row[0] if row is not None else None

    def _resolve_name_fk(self, table: str, name: str, cache: dict[str, int]) -> int:
        fk = cache.get(name)
        if fk is not None:
            return fk
        self._conn.execute(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (name,))
        row = self._conn.execute(
            f"SELECT id FROM {table} WHERE name = ?", (name,)
        ).fetchone()
        fk = row[0]
        cache[name] = fk
        return fk

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
        timestamp: int,
        payload: dict,
        source: str,
    ) -> int:
        logger.debug(
            "Writing event: type=%s timestamp=%d source=%s payload=%s",
            event_type,
            timestamp,
            source,
            json.dumps(payload)[:200],
        )
        event_type_fk = self._resolve_name_fk(
            "event_types", event_type, self._event_type_ids
        )
        source_fk = self._resolve_name_fk("sources", source, self._source_ids)
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        self._conn.execute(
            """INSERT INTO raw_events
               (device_fk, event_type_fk, source_fk, timestamp, collected_at,
                payload, payload_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                self._device_fk,
                event_type_fk,
                source_fk,
                timestamp,
                utc_timestamp(),
                payload_json,
                _payload_hash(payload),
            ),
        )
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_raw_events(
        self,
        event_type: str | None = None,
        source: str | None = None,
        device_id: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int | None = None,
        desc: bool = False,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if event_type:
            filters.append("et.name = ?")
            params.append(event_type)
        if source:
            filters.append("s.name = ?")
            params.append(source)
        if device_id:
            filters.append("d.device_id = ?")
            params.append(device_id)
        if since is not None:
            filters.append("e.timestamp >= ?")
            params.append(since)
        if until is not None:
            filters.append("e.timestamp <= ?")
            params.append(until)

        sql = (
            "SELECT e.id, d.device_id, d.platform, et.name, e.timestamp,"
            " e.collected_at, e.payload, s.name"
            " FROM raw_events e"
            " JOIN devices d ON d.id = e.device_fk"
            " JOIN event_types et ON et.id = e.event_type_fk"
            " JOIN sources s ON s.id = e.source_fk"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += (
            " ORDER BY e.timestamp DESC, e.id DESC"
            if desc
            else " ORDER BY e.timestamp ASC, e.id ASC"
        )
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

    def open_app_session(
        self, event_id: int, start_ts: int, app_key: str, payload: dict
    ) -> int:
        """Insert one open app session row owned by ``event_id`` (the
        foreground_transition that started the block). Returns the rowid.
        The session stays open until ``close_app_session()``."""
        self._conn.execute(
            """INSERT INTO app_sessions
               (device_fk, event_id, start_ts, app_key, payload)
               VALUES (?, ?, ?, ?, ?)""",
            (
                self._device_fk,
                event_id,
                start_ts,
                app_key,
                json.dumps(payload),
            ),
        )
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def close_app_session(self, event_id: int, end_ts: int) -> int | None:
        """Close the open app session owned by ``event_id`` at ``end_ts``.

        Sets ``end_ts`` and ``duration_s = (end_ts - start_ts) / 1000``.
        Returns the closed session's rowid, or ``None`` when the session
        was already closed or does not exist.
        """
        cur = self._conn.execute(
            """UPDATE app_sessions
               SET end_ts = ?, duration_s = (? - start_ts) / 1000.0
               WHERE device_fk = ? AND event_id = ? AND end_ts IS NULL""",
            (end_ts, end_ts, self._device_fk, event_id),
        )
        if cur.rowcount == 0:
            return None
        row = self._conn.execute(
            "SELECT id FROM app_sessions WHERE device_fk = ? AND event_id = ?",
            (self._device_fk, event_id),
        ).fetchone()
        return row[0] if row is not None else None

    def close_orphaned_sessions(self, end_ts: int) -> int:
        """Close every open session/block left behind by a dead instance.

        The previous process may have exited without finalizing (window
        closed, killed, crashed, update relaunch): its open ``app_sessions``
        and ``status_sessions`` rows stay half-open (``end_ts IS NULL``) and
        are silently excluded from every duration aggregation. Called once
        at startup; each orphan is closed at ``end_ts`` and its
        ``url_visits.session_id`` is backfilled. Returns the number of app
        sessions closed.
        """
        open_sessions = self._conn.execute(
            "SELECT id, event_id FROM app_sessions"
            " WHERE device_fk = ? AND end_ts IS NULL",
            (self._device_fk,),
        ).fetchall()
        for session_id, event_id in open_sessions:
            self._conn.execute(
                """UPDATE app_sessions
                   SET end_ts = ?, duration_s = (? - start_ts) / 1000.0
                   WHERE id = ?""",
                (end_ts, end_ts, session_id),
            )
            self.backfill_url_sessions_for_event(event_id, session_id)
        self._conn.execute(
            """UPDATE status_sessions
               SET end_ts = ?, duration_s = (? - start_ts) / 1000.0
               WHERE device_fk = ? AND end_ts IS NULL""",
            (end_ts, end_ts, self._device_fk),
        )
        if open_sessions:
            logger.warning(
                "Closed %d orphaned app session(s) left by a previous instance",
                len(open_sessions),
            )
        return len(open_sessions)

    def replace_device_sessions(
        self,
        device_id: str,
        app_sessions: list[dict],
        status_sessions: list[dict],
    ) -> None:
        """Replace every derived session row for ``device_id``.

        Delete-and-rebuild in one transaction — sessions are a derived
        view (ADR-0002), so wiping and re-inserting per device is
        idempotent. ``app_sessions`` rows need ``event_id``, ``start_ts``,
        ``end_ts`` (or None), ``duration_s`` (or None), ``app_key`` and
        ``payload``; ``status_sessions`` rows need the same plus
        ``status`` instead of ``app_key``. On any failure the transaction
        rolls back, leaving the previous rows intact.
        """
        device_fk = self._resolve_device_fk(device_id)
        if device_fk is None:
            raise ValueError(f"Unknown device: {device_id}")
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(
                "DELETE FROM app_sessions WHERE device_fk = ?", (device_fk,)
            )
            self._conn.execute(
                "DELETE FROM status_sessions WHERE device_fk = ?", (device_fk,)
            )
            for row in app_sessions:
                self._conn.execute(
                    """INSERT INTO app_sessions
                       (device_fk, event_id, start_ts, end_ts, duration_s,
                        app_key, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device_fk,
                        row["event_id"],
                        row["start_ts"],
                        row["end_ts"],
                        row["duration_s"],
                        row["app_key"],
                        json.dumps(row["payload"], sort_keys=True, ensure_ascii=True),
                    ),
                )
            for row in status_sessions:
                self._conn.execute(
                    """INSERT INTO status_sessions
                       (device_fk, event_id, start_ts, end_ts, duration_s,
                        status, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device_fk,
                        row["event_id"],
                        row["start_ts"],
                        row["end_ts"],
                        row["duration_s"],
                        row["status"],
                        json.dumps(row["payload"], sort_keys=True, ensure_ascii=True),
                    ),
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def list_devices(self) -> list[dict]:
        """Return every registered device, oldest first."""
        return [
            {
                "id": r[0],
                "device_id": r[1],
                "hostname": r[2],
                "platform": r[3],
                "first_seen": r[4],
                "last_seen": r[5],
                "is_current": bool(r[6]),
            }
            for r in self._conn.execute(
                "SELECT id, device_id, hostname, platform, first_seen, last_seen,"
                " is_current FROM devices ORDER BY first_seen"
            )
        ]

    def get_app_sessions(
        self,
        app_key: str | None = None,
        device_id: str | None = None,
        since: int | None = None,
        until: int | None = None,
        platform: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if app_key:
            filters.append("s.app_key = ?")
            params.append(app_key)
        if device_id:
            filters.append("d.device_id = ?")
            params.append(device_id)
        if since is not None:
            filters.append("s.start_ts >= ?")
            params.append(since)
        if until is not None:
            filters.append("COALESCE(s.end_ts, s.start_ts) <= ?")
            params.append(until)
        if platform:
            filters.append("d.platform = ?")
            params.append(platform)

        sql = (
            "SELECT s.id, d.device_id, d.platform, s.event_id, s.start_ts,"
            " s.end_ts, s.duration_s, s.app_key, s.payload"
            " FROM app_sessions s"
            " JOIN devices d ON d.id = s.device_fk"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY s.start_ts ASC"
        if limit is not None:
            sql += f" LIMIT {limit}"

        return [
            {
                "id": r[0],
                "device_id": r[1],
                "platform": r[2],
                "event_id": r[3],
                "start_ts": r[4],
                "end_ts": r[5],
                "duration_s": r[6],
                "app_key": r[7],
                "payload": json.loads(r[8]),
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def get_app_session_totals(
        self,
        since_ms: int,
        until_ms: int,
        device_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Aggregate closed app-session durations in ``[since_ms, until_ms)``.

        Sessions are assigned to the range by their ``start_ts`` (a session
        crossing a boundary counts entirely on its start day). Open sessions
        (``duration_s`` NULL) are excluded. Returns rows grouped by
        ``(app_key, payload)`` sorted by total duration descending; each row
        carries ``grand_total_s`` — the sum over *all* groups in range
        (before ``LIMIT``) — so callers can compute share percentages
        against the full range, not just the top-N slice.
        """
        filters: list[str] = [
            "s.start_ts >= ?",
            "s.start_ts < ?",
            "s.duration_s IS NOT NULL",
        ]
        params: list = [since_ms, until_ms]
        if device_id:
            filters.append("d.device_id = ?")
            params.append(device_id)

        sql = (
            "SELECT s.app_key, s.payload, SUM(s.duration_s) AS total_s,"
            " SUM(SUM(s.duration_s)) OVER () AS grand_total_s"
            " FROM app_sessions s"
            " JOIN devices d ON d.id = s.device_fk"
            " WHERE "
            + " AND ".join(filters)
            + " GROUP BY s.app_key, s.payload"
            + " ORDER BY total_s DESC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        return [
            {
                "app_key": r[0],
                "payload": json.loads(r[1]),
                "total_s": float(r[2]),
                "grand_total_s": float(r[3]),
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def open_status_session(
        self, event_id: int, start_ts: int, status: str, payload: dict
    ) -> int:
        """Insert one open status block row owned by ``event_id`` (the
        idle_transition that started the block). Returns the rowid. The
        block stays open until ``close_status_session()``."""
        self._conn.execute(
            """INSERT INTO status_sessions
               (device_fk, event_id, start_ts, status, payload)
               VALUES (?, ?, ?, ?, ?)""",
            (
                self._device_fk,
                event_id,
                start_ts,
                status,
                json.dumps(payload),
            ),
        )
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def close_status_session(self, event_id: int, end_ts: int) -> int | None:
        """Close the open status block owned by ``event_id`` at ``end_ts``.

        Sets ``end_ts`` and ``duration_s = (end_ts - start_ts) / 1000``.
        Returns the closed block's rowid, or ``None`` when the block was
        already closed or does not exist.
        """
        cur = self._conn.execute(
            """UPDATE status_sessions
               SET end_ts = ?, duration_s = (? - start_ts) / 1000.0
               WHERE device_fk = ? AND event_id = ? AND end_ts IS NULL""",
            (end_ts, end_ts, self._device_fk, event_id),
        )
        if cur.rowcount == 0:
            return None
        row = self._conn.execute(
            "SELECT id FROM status_sessions WHERE device_fk = ? AND event_id = ?",
            (self._device_fk, event_id),
        ).fetchone()
        return row[0] if row is not None else None

    def get_status_sessions(
        self,
        status: str | None = None,
        device_id: str | None = None,
        since: int | None = None,
        until: int | None = None,
        platform: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if status:
            filters.append("s.status = ?")
            params.append(status)
        if device_id:
            filters.append("d.device_id = ?")
            params.append(device_id)
        if since is not None:
            filters.append("s.start_ts >= ?")
            params.append(since)
        if until is not None:
            filters.append("COALESCE(s.end_ts, s.start_ts) <= ?")
            params.append(until)
        if platform:
            filters.append("d.platform = ?")
            params.append(platform)

        sql = (
            "SELECT s.id, d.device_id, d.platform, s.event_id, s.start_ts,"
            " s.end_ts, s.duration_s, s.status, s.payload"
            " FROM status_sessions s"
            " JOIN devices d ON d.id = s.device_fk"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY s.start_ts ASC"
        if limit is not None:
            sql += f" LIMIT {limit}"

        return [
            {
                "id": r[0],
                "device_id": r[1],
                "platform": r[2],
                "event_id": r[3],
                "start_ts": r[4],
                "end_ts": r[5],
                "duration_s": r[6],
                "status": r[7],
                "payload": json.loads(r[8]),
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def write_url_visit(
        self,
        url: str,
        seen_at: int,
        event_id: int,
        browser: str | None = None,
        extraction_method: str | None = None,
        confidence: str = "high",
        scheme: str | None = None,
        host: str | None = None,
        domain: str | None = None,
        path: str | None = None,
        is_trackable: bool = True,
    ) -> int:
        """Record a URL visit. Returns the new rowid, or 0 when the visit
        duplicates an existing (device_fk, event_id, url) row — e.g. the same
        URL revisited within one browser session — in which case it is
        silently skipped."""
        before = self._conn.total_changes
        self._conn.execute(
            """INSERT OR IGNORE INTO url_visits
               (device_fk, event_id, url, browser, seen_at, collected_at,
                extraction_method, confidence, scheme, host, domain, path,
                is_trackable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self._device_fk,
                event_id,
                url,
                browser,
                seen_at,
                utc_timestamp(),
                extraction_method,
                confidence,
                scheme,
                host,
                domain,
                path,
                int(is_trackable),
            ),
        )
        if self._conn.total_changes == before:
            logger.debug("Skipped duplicate url_visit: event=%d url=%s", event_id, url)
            return 0
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_url_visits(
        self,
        device_id: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []

        if device_id:
            filters.append("d.device_id = ?")
            params.append(device_id)
        if since is not None:
            filters.append("u.seen_at >= ?")
            params.append(since)
        if until is not None:
            filters.append("u.seen_at <= ?")
            params.append(until)

        sql = (
            "SELECT u.id, d.device_id, u.event_id, u.session_id, u.url, u.browser,"
            " u.scheme, u.host, u.domain, u.path, u.extraction_method,"
            " u.confidence, u.is_trackable, u.seen_at, u.collected_at"
            " FROM url_visits u"
            " JOIN devices d ON d.id = u.device_fk"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY u.seen_at ASC"
        if limit is not None:
            sql += f" LIMIT {limit}"

        return [
            {
                "id": r[0],
                "device_id": r[1],
                "event_id": r[2],
                "session_id": r[3],
                "url": r[4],
                "browser": r[5],
                "scheme": r[6],
                "host": r[7],
                "domain": r[8],
                "path": r[9],
                "extraction_method": r[10],
                "confidence": r[11],
                "is_trackable": bool(r[12]),
                "seen_at": r[13],
                "collected_at": r[14],
            }
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def backfill_url_sessions_for_event(self, event_id: int, session_id: int) -> None:
        """Stamp ``url_visits.session_id`` for every visit owned by
        ``event_id`` that does not have one yet."""
        self._conn.execute(
            "UPDATE url_visits SET session_id = ? WHERE event_id = ? AND session_id IS NULL",
            (session_id, event_id),
        )

    def count_events(
        self,
        event_type: str | None = None,
        device_id: str | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> int:
        """Count raw events matching the filters (cheap COUNT(*))."""
        filters: list[str] = []
        params: list = []

        if event_type:
            filters.append("et.name = ?")
            params.append(event_type)
        if device_id:
            filters.append("e.device_fk = (SELECT id FROM devices WHERE device_id = ?)")
            params.append(device_id)
        if since is not None:
            filters.append("e.timestamp >= ?")
            params.append(since)
        if until is not None:
            filters.append("e.timestamp <= ?")
            params.append(until)

        sql = (
            "SELECT COUNT(*) FROM raw_events e"
            " JOIN event_types et ON et.id = e.event_type_fk"
        )
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def get_latest_battery(self) -> dict | None:
        row = self._conn.execute(
            "SELECT payload FROM raw_events WHERE event_type_fk = ?"
            " ORDER BY timestamp DESC LIMIT 1",
            (
                self._resolve_name_fk(
                    "event_types", "power_change", self._event_type_ids
                ),
            ),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def clear_all_data(self) -> None:
        self._conn.execute("DELETE FROM url_visits")
        self._conn.execute("DELETE FROM raw_events")
        self._conn.execute("DELETE FROM app_sessions")
        self._conn.execute("DELETE FROM status_sessions")
        self._conn.execute("DELETE FROM sync_cursors")
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
