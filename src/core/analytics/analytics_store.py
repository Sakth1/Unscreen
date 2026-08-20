"""Per-app usage totals over configurable time ranges.

``AnalyticsStore`` reads the derived ``app_sessions`` table (see
``core.storage``) and answers the milestone #26 question: how much time
did I spend in each app today / this week? All ranges are local-time
boundaries; sessions are assigned to a range by their ``start_ts`` and
open sessions (no ``end_ts`` yet) are excluded until closed.

The store is platform-agnostic by design (ADR-0004): the same SQLite
query path runs on Windows and Android.
"""

import datetime
import logging
from dataclasses import dataclass

from core.collectors.windows.browser import BROWSER_PROCESSES
from utils.time_utils import get_current_time_ms, week_start_ms

logger = logging.getLogger(__name__)

ALL_DEVICES = "*"

_MS_PER_S = 1000


@dataclass(frozen=True)
class AppTotal:
    """Total tracked time for one app within a range."""

    app_key: str
    app_name: str
    total_s: float
    share_pct: float


def _local_midnight_ms(day: datetime.date) -> int:
    local_midnight = datetime.datetime(day.year, day.month, day.day)
    return int(local_midnight.timestamp() * _MS_PER_S)


def _app_name(app_key: str, payload: dict | None) -> str:
    """Best-effort display name for an app, per platform payload."""
    name = (payload or {}).get("app_name") or (payload or {}).get("app") or app_key
    friendly = BROWSER_PROCESSES.get(name.lower())
    if friendly:
        return friendly
    if name.lower().endswith(".exe"):
        return name[:-4]
    return name


class AnalyticsStore:
    """Aggregate app-session durations over local-time ranges."""

    def __init__(self, storage):
        self._storage = storage

    def totals(
        self,
        since_ms: int,
        until_ms: int,
        device_id: str | None = None,
        limit: int | None = None,
    ) -> list[AppTotal]:
        """Per-app totals for ``[since_ms, until_ms)``, duration descending.

        ``device_id`` defaults to the current device; pass ``ALL_DEVICES``
        to aggregate across every device. ``limit`` caps the returned rows
        (top-N); ``share_pct`` is still computed against the full range.
        Returns an empty list when the range has no tracked time.
        """
        scope = (
            None if device_id == ALL_DEVICES else device_id or self._storage.device_id
        )
        rows = self._storage.get_app_session_totals(
            since_ms, until_ms, device_id=scope, limit=limit
        )
        if not rows:
            return []
        grand_total = rows[0]["grand_total_s"]
        if grand_total <= 0:
            return []
        return [
            AppTotal(
                app_key=row["app_key"],
                app_name=_app_name(row["app_key"], row["payload"]),
                total_s=row["total_s"],
                share_pct=round(row["total_s"] / grand_total * 100.0, 1),
            )
            for row in rows
        ]

    def daily_totals(
        self,
        day: datetime.date | None = None,
        device_id: str | None = None,
        limit: int | None = None,
    ) -> list[AppTotal]:
        """Per-app totals for a local day (default: today)."""
        day = day or datetime.date.today()
        start = _local_midnight_ms(day)
        end = _local_midnight_ms(day + datetime.timedelta(days=1))
        return self.totals(start, end, device_id=device_id, limit=limit)

    def weekly_totals(
        self,
        day: datetime.date | None = None,
        device_id: str | None = None,
        limit: int | None = None,
    ) -> list[AppTotal]:
        """Per-app totals for the ISO week (Mon-Sun) containing ``day``.

        Defaults to the current week.
        """
        if day is None:
            start = week_start_ms(get_current_time_ms())
            end = start + 7 * 24 * 60 * 60 * _MS_PER_S
        else:
            monday = day - datetime.timedelta(days=day.weekday())
            start = _local_midnight_ms(monday)
            end = _local_midnight_ms(monday + datetime.timedelta(days=7))
        return self.totals(start, end, device_id=device_id, limit=limit)
