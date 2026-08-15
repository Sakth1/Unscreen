import logging

from core.collectors.android.usage_stats import get_battery_info
from core.models import Tick, WatcherConfig

logger = logging.getLogger(__name__)


class AndroidPowerWatcher:
    def __init__(self, config: WatcherConfig | None = None):
        self.config = config or WatcherConfig(
            name="android_power",
            interval_s=60.0,
            enabled=True,
        )

    async def tick(self) -> Tick | None:
        info = get_battery_info()
        return Tick(
            watcher="android_power",
            data={
                "battery_pct": info["battery_pct"],
                "charging": info["charging"],
            },
        )
