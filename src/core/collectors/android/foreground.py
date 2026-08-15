import logging

from core.collectors.android.package_resolver import resolve as resolve_package
from core.collectors.android.usage_stats import (
    _EVENT_TYPE_RESUMED,
    check_usage_stats_permission,
    is_screen_on,
    query_usage_events,
    query_usage_stats,
)
from core.models import Tick, WatcherConfig
from utils.time_utils import day_start_ms, get_current_time_ms

logger = logging.getLogger(__name__)

_EVENT_WINDOW_OVERLAP_MS = 120_000
_EVENT_END_BUFFER_MS = 2_000


class AndroidForegroundWatcher:
    def __init__(self, config: WatcherConfig | None = None):
        self.config = config or WatcherConfig(
            name="android_foreground",
            interval_s=10.0,
            enabled=True,
        )
        self._current_app: str | None = None
        self._last_tick_ms: int | None = None
        self._initialized = False
        self._permission_lost = False

    async def tick(self) -> Tick | None:
        now_ms = get_current_time_ms()

        if not check_usage_stats_permission():
            if not self._permission_lost:
                logger.warning(
                    "Usage Stats permission lost — pausing foreground watcher"
                )
                self._permission_lost = True
                self._current_app = None
                self._last_tick_ms = None
                self._initialized = False
            return None

        if self._permission_lost:
            logger.info("Usage Stats permission restored — resuming foreground watcher")
            self._permission_lost = False

        if not self._initialized:
            return self._initialize(now_ms)

        return self._check_transition(now_ms)

    def _initialize(self, now_ms: int) -> Tick | None:
        app = self._resolve_current_foreground(now_ms)
        if app is None:
            start_of_day_ms = day_start_ms(now_ms)
            stats = query_usage_stats(start_of_day_ms, now_ms)
            if stats:
                top_pkg = max(stats, key=lambda p: stats[p]["total_time_foreground_ms"])
                app = top_pkg

        if app:
            self._current_app = app
            self._initialized = True
            self._last_tick_ms = now_ms
            logger.info("foreground initialized: %s", resolve_package(app))
        return None

    def _check_transition(self, now_ms: int) -> Tick | None:
        app = self._resolve_current_foreground(now_ms)
        self._last_tick_ms = now_ms

        if app is None:
            if is_screen_on() and self._current_app is not None:
                logger.debug(
                    "foreground [stale]: %s (screen on, no events)", self._current_app
                )
                return None
            logger.info("foreground [idle]: no app activity")
            return None

        if app == self._current_app:
            return None

        logger.info(
            "foreground transition: %s -> %s",
            resolve_package(self._current_app) if self._current_app else "(none)",
            resolve_package(app),
        )
        self._current_app = app
        return Tick(
            watcher="android_foreground",
            data={
                "package": app,
                "app_name": resolve_package(app),
            },
        )

    def _resolve_current_foreground(self, now_ms: int) -> str | None:
        begin_ms = (
            self._last_tick_ms - _EVENT_WINDOW_OVERLAP_MS
            if self._last_tick_ms is not None
            else now_ms - _EVENT_WINDOW_OVERLAP_MS
        )
        end_ms = now_ms - _EVENT_END_BUFFER_MS
        if end_ms <= begin_ms:
            return self._current_app

        events = query_usage_events(begin_ms, end_ms)
        if not events:
            return None

        for ev in reversed(events):
            if ev["event_type"] == _EVENT_TYPE_RESUMED:
                return ev["package_name"]
        return None
