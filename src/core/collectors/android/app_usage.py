import logging

from core.collectors.android.usage_stats import (
    check_usage_stats_permission,
    query_usage_stats,
)
from core.models import Tick, WatcherConfig
from utils.time_utils import day_start_ms, get_current_time_ms

logger = logging.getLogger(__name__)

_MS_PER_S = 1000


class AndroidAppUsageWatcher:
    def __init__(self, config: WatcherConfig | None = None):
        self.config = config or WatcherConfig(
            name="android_app_usage",
            interval_s=60.0,
            enabled=True,
        )
        self._last_foreground_ms: dict[str, int] = {}
        self._permission_lost = False

    async def tick(self) -> Tick | None:
        now_ms = get_current_time_ms()

        if not check_usage_stats_permission():
            if not self._permission_lost:
                logger.warning(
                    "Usage Stats permission lost — pausing app usage watcher"
                )
                self._permission_lost = True
                self._last_foreground_ms.clear()
            return None

        if self._permission_lost:
            logger.info("Usage Stats permission restored — resuming app usage watcher")
            self._permission_lost = False

        if not self._last_foreground_ms:
            return self._initialize(now_ms)

        return self._emit_intervals(now_ms)

    def _initialize(self, now_ms: int) -> Tick | None:
        start_of_day_ms = day_start_ms(now_ms)
        stats = query_usage_stats(start_of_day_ms, now_ms)
        if stats:
            for pkg, stat in stats.items():
                self._last_foreground_ms[pkg] = stat["total_time_foreground_ms"]
            logger.info("app_usage initialized: %d packages", len(stats))
        return None

    def _emit_intervals(self, now_ms: int) -> Tick | None:
        start_of_day_ms = day_start_ms(now_ms)
        stats = query_usage_stats(start_of_day_ms, now_ms)
        if not stats:
            return None

        stale = self._last_foreground_ms.keys() - stats.keys()
        for pkg in stale:
            del self._last_foreground_ms[pkg]

        intervals = []
        for pkg, stat in stats.items():
            current = stat["total_time_foreground_ms"]
            last = self._last_foreground_ms.get(pkg, 0)
            delta_ms = current - last
            if delta_ms > 0:
                intervals.append(
                    {
                        "package": pkg,
                        "duration_ms": delta_ms,
                        "duration_s": round(delta_ms / _MS_PER_S, 2),
                        "app_name": stat["app_name"],
                    }
                )
                self._last_foreground_ms[pkg] = current

        if not intervals:
            return None

        intervals.sort(key=lambda x: x["duration_ms"], reverse=True)
        logger.info(
            "app_usage [%ds]: %s",
            int(self.config.interval_s),
            ", ".join(f"{i['app_name']} {i['duration_s']}s" for i in intervals),
        )

        return Tick(
            watcher="android_app_usage",
            data={"intervals": intervals},
        )
