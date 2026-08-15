import logging

from core.collectors.android.afk import AndroidAfkWatcher
from core.collectors.android.app_usage import AndroidAppUsageWatcher
from core.collectors.android.foreground import AndroidForegroundWatcher
from core.collectors.android.power import AndroidPowerWatcher
from core.collectors.base import Watcher
from core.config_manager import ConfigManager
from core.models import WatcherConfig

logger = logging.getLogger(__name__)

_DEFAULT_INTERVALS: dict[str, float] = {
    "android_foreground": 10.0,
    "android_app_usage": 60.0,
    "android_afk": 5.0,
    "android_power": 60.0,
}


class AndroidRuntime:
    def __init__(self, config: ConfigManager):
        self._config = config

    def create_watchers(self) -> list[Watcher]:
        enabled = self._config.watchers_enabled
        all_watchers: dict[str, type[Watcher]] = {
            "android_foreground": AndroidForegroundWatcher,
            "android_app_usage": AndroidAppUsageWatcher,
            "android_afk": AndroidAfkWatcher,
            "android_power": AndroidPowerWatcher,
        }

        if not any(name in enabled for name in all_watchers):
            enabled = list(all_watchers.keys())
            logger.info("No Android watchers in config, using defaults: %s", enabled)

        watchers: list[Watcher] = []
        for name, cls in all_watchers.items():
            if name in enabled:
                interval = self._config.get_interval(
                    name, _DEFAULT_INTERVALS.get(name, 60.0)
                )
                wc = WatcherConfig(name=name, interval_s=interval, enabled=True)
                watchers.append(cls(wc))
        logger.info("Created %d Android watchers", len(watchers))
        return watchers

    def shutdown(self) -> None:
        logger.info("Android runtime shutdown")
