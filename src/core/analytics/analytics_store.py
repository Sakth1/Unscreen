"""Per-app usage totals over configurable time ranges.

``AnalyticsStore`` reads the derived ``app_sessions`` table (see
``core.storage``) and answers the milestone #26 question: how much time
did I spend in each app today / this week? All ranges are local-time
boundaries; sessions are assigned to a range by their ``start_ts`` and
open sessions (no ``end_ts`` yet) are counted up to the moment the query
runs — an in-progress session is real usage, not silence.

System apps (launcher, shell, IMEs — see ``core.application.system_apps``)
are excluded from the totals by default (F6) so shares reflect real usage;
``hidden_app_keys`` extends the curated list. Browser sessions are
bucketed by normalized site (F8): recognized sites get their own entry
(YouTube, GitHub, ...), everything else merges into a general "Browser"
entry. The store is platform-agnostic by design (ADR-0004): the same
SQLite query path runs on Windows and Android.
"""

import datetime
import logging
import re
from dataclasses import dataclass

from core.application.system_apps import (
    PLATFORM_ANDROID,
    PLATFORM_WINDOWS,
    effective_system_keys,
)
from core.collectors.windows.browser import (
    BROWSER_PROCESSES,
    DOMAIN_KEYWORDS,
    SITE_NAMES,
)
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


def _is_browser_session(app_key: str, payload: dict | None) -> bool:
    """True when the session belongs to a tracked browser process (F8)."""
    candidate = (payload or {}).get("app") or app_key
    return candidate.lower() in BROWSER_PROCESSES


def _browser_site(payload: dict | None) -> str | None:
    """The normalized site name a browser session was on, if recognizable.

    Matches the page title (e.g. "YouTube — Brave") against the curated
    ``DOMAIN_KEYWORDS``; returns the site display name ("YouTube") or
    ``None`` when the title names no known site — such sessions roll into
    the general "Browser" bucket.
    """
    title = (payload or {}).get("title") or ""
    if not title:
        return None
    lower = title.lower()
    for pattern, domain in DOMAIN_KEYWORDS:
        if re.search(pattern, lower):
            return SITE_NAMES.get(domain, domain)
    return None


def _display_bucket(app_key: str, payload: dict | None) -> tuple[str, str]:
    """(bucket key, display name) for one app-session group.

    Browser sessions split by their normalized site (YouTube, GitHub, ...)
    so known sites get their own entry; unrecognized browsing is grouped
    by browser identity (Brave, Chrome, ...) instead of a generic catch-all.
    Other apps bucket by app key with the resolved display name.
    """
    if _is_browser_session(app_key, payload):
        site = _browser_site(payload)
        if site:
            return f"browser:{site.lower()}", site
        candidate = (payload or {}).get("app") or app_key
        browser_name = BROWSER_PROCESSES.get(candidate.lower(), "Browser")
        return f"browser:{candidate.lower()}", browser_name
    return app_key, _app_name(app_key, payload)


class AnalyticsStore:
    """Aggregate app-session durations over local-time ranges."""

    def __init__(
        self,
        storage,
        exclude_system_apps: bool = True,
        hidden_app_keys: tuple[str, ...] = (),
    ):
        self._storage = storage
        self._exclude_system_apps = exclude_system_apps
        self._hidden_app_keys = tuple(hidden_app_keys)

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
        System apps are excluded by default (F6); ``share_pct`` reflects
        the visible (post-filter) total. Browser sessions are bucketed by
        their normalized site (F8): known sites (YouTube, GitHub, ...) get
        their own entry and everything else merges into a general
        "Browser" entry, so a browser never fragments across page titles.
        Returns an empty list when the range has no tracked time.
        """
        scope = (
            None if device_id == ALL_DEVICES else device_id or self._storage.device_id
        )
        exclude_keys = None
        if self._exclude_system_apps:
            # Both platform sets are disjoint in practice, so the union is
            # safe for a single-device scope and for ALL_DEVICES alike.
            exclude_keys = effective_system_keys(
                (PLATFORM_ANDROID, PLATFORM_WINDOWS), self._hidden_app_keys
            )
        rows = self._storage.get_app_session_totals(
            since_ms,
            until_ms,
            device_id=scope,
            limit=None,
            now_ms=get_current_time_ms(),
            exclude_keys=exclude_keys,
        )
        if not rows:
            return []

        # Merge the (app_key, payload) groups into display buckets so the
        # browser fragmentation (one row per page title) collapses into
        # per-site entries; the top-N limit applies after the merge.
        buckets: dict[tuple[str, str], float] = {}
        grand_total = 0.0
        for row in rows:
            bucket = _display_bucket(row["app_key"], row["payload"])
            buckets[bucket] = buckets.get(bucket, 0.0) + row["total_s"]
            grand_total += row["total_s"]
        if grand_total <= 0:
            return []
        totals = [
            AppTotal(
                app_key=bucket_key,
                app_name=display_name,
                total_s=total,
                share_pct=round(total / grand_total * 100.0, 1),
            )
            for (bucket_key, display_name), total in buckets.items()
        ]
        totals.sort(key=lambda t: t.total_s, reverse=True)
        if limit is not None:
            totals = totals[:limit]
        return totals

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
