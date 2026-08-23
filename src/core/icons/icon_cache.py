"""SQLite-backed icon cache for the dashboard.

Stores resolved PNG bytes keyed by ``app_key`` with a timestamp for
time-based eviction (entries older than 30 days are re-resolved on
the next dashboard pass). Fingerprints detect staleness so an app
update or move triggers immediate re-extraction.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: Default maximum age for cache entries (seconds).
_MAX_AGE_S = 30 * 24 * 60 * 60  # 30 days


class IconCache:
    """Thin wrapper around the ``app_icons`` SQLite table."""

    def __init__(self, conn):
        """
        Parameters
        ----------
        conn : sqlite3.Connection
            The database connection (owned by ``Storage``).
        """
        self._conn = conn

    def get(self, app_key: str) -> bytes | None:
        """Return cached PNG bytes if the entry exists and is新鲜 (< 30 days).

        Returns ``None`` when the entry is missing, stale, or corrupted.
        """
        try:
            row = self._conn.execute(
                "SELECT png, updated_at FROM app_icons WHERE app_key = ?",
                (app_key,),
            ).fetchone()
            if row is None:
                return None
            png, updated_at = row
            if (time.time() * 1000) - updated_at > _MAX_AGE_S * 1000:
                return None
            return bytes(png)
        except Exception:
            logger.debug("IconCache.get failed for %s", app_key, exc_info=True)
            return None

    def put(
        self,
        app_key: str,
        source: str,
        fingerprint: str,
        png: bytes,
        width: int,
    ) -> None:
        """Upsert an icon entry with the current timestamp."""
        try:
            now_ms = int(time.time() * 1000)
            self._conn.execute(
                """INSERT INTO app_icons (app_key, source, fingerprint, png, width, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(app_key) DO UPDATE SET
                     source = excluded.source,
                     fingerprint = excluded.fingerprint,
                     png = excluded.png,
                     width = excluded.width,
                     updated_at = excluded.updated_at""",
                (app_key, source, fingerprint, png, width, now_ms),
            )
        except Exception:
            logger.debug("IconCache.put failed for %s", app_key, exc_info=True)

    def invalidate(self, app_key: str) -> None:
        """Remove a single cache entry."""
        try:
            self._conn.execute("DELETE FROM app_icons WHERE app_key = ?", (app_key,))
        except Exception:
            logger.debug(
                "IconCache.invalidate failed for %s", app_key, exc_info=True
            )

    def evict_expired(self, max_age_days: int = 30) -> int:
        """Remove entries older than *max_age_days*. Returns count evicted."""
        try:
            cutoff_ms = int((time.time() - max_age_days * 86400) * 1000)
            cur = self._conn.execute(
                "DELETE FROM app_icons WHERE updated_at < ?", (cutoff_ms,)
            )
            return cur.rowcount
        except Exception:
            logger.debug("IconCache.evict_expired failed", exc_info=True)
            return 0

    def fingerprint_changed(self, app_key: str, fingerprint: str) -> bool:
        """Whether the stored fingerprint differs (app updated/moved)."""
        try:
            row = self._conn.execute(
                "SELECT fingerprint FROM app_icons WHERE app_key = ?",
                (app_key,),
            ).fetchone()
            if row is None:
                return True
            return row[0] != fingerprint
        except Exception:
            return True
