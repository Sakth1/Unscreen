import logging

from core.collectors.android.usage_stats import (
    _EVENT_TYPE_RESUMED,
    check_usage_stats_permission,
    is_screen_on,
    query_usage_events,
)
from core.models import Tick, WatcherConfig
from utils.time_utils import get_current_time_ms

logger = logging.getLogger(__name__)

_EVENT_LOOKBACK_SECONDS = 300


class AndroidAfkWatcher:
    def __init__(self, config: WatcherConfig | None = None):
        self.config = config or WatcherConfig(
            name="android_afk",
            interval_s=5.0,
            enabled=True,
        )
        self._permission_lost = False
        self._last_event_time_ms: int | None = None

    async def tick(self) -> Tick | None:
        now_ms = get_current_time_ms()

        screen_on = is_screen_on()
        if not screen_on:
            return Tick(
                watcher="android_afk",
                data={
                    "present": False,
                    "screen_on": False,
                    "seconds_since_last_event": None,
                },
            )

        if not check_usage_stats_permission():
            if not self._permission_lost:
                logger.warning(
                    "Usage Stats permission lost — user presence fallback to screen state"
                )
                self._permission_lost = True
            return Tick(
                watcher="android_afk",
                data={
                    "present": True,
                    "screen_on": True,
                    "seconds_since_last_event": None,
                },
            )

        if self._permission_lost:
            logger.info(
                "Usage Stats permission restored — user presence watcher resumed"
            )
            self._permission_lost = False

        present, seconds_since = self._check_presence(now_ms)

        return Tick(
            watcher="android_afk",
            data={
                "present": present,
                "screen_on": True,
                "seconds_since_last_event": seconds_since,
            },
        )

    def _check_presence(self, now_ms: int) -> tuple[bool, float | None]:
        lookback_ms = _EVENT_LOOKBACK_SECONDS * 1000
        events = query_usage_events(now_ms - lookback_ms, now_ms)
        for ev in events:
            if ev["event_type"] == _EVENT_TYPE_RESUMED:
                event_time_ms = ev.get("time_stamp_ms", now_ms)
                self._last_event_time_ms = event_time_ms
                seconds_since = (
                    round((now_ms - event_time_ms) / 1000.0, 1)
                    if self._last_event_time_ms
                    else None
                )
                return True, seconds_since

        if self._last_event_time_ms is not None:
            seconds_since = round((now_ms - self._last_event_time_ms) / 1000.0, 1)
            return False, seconds_since
        return False, None
