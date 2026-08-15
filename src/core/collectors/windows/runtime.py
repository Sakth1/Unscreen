import logging
from typing import cast

from core.collectors.base import Watcher
from core.collectors.windows.afk import AfkWatcher
from core.collectors.windows.foreground import ForegroundWatcher
from core.collectors.windows.power import PowerWatcher
from core.config_manager import ConfigManager
from core.models import WatcherConfig
from core.storage import Storage

logger = logging.getLogger(__name__)

_DEFAULT_INTERVALS: dict[str, float] = {
    "foreground": 2.0,
    "afk": 5.0,
    "power": 60.0,
}


class WindowsRuntime:
    def __init__(self, config: ConfigManager, storage: Storage | None = None):
        self._config = config
        self._storage = storage

    def create_watchers(self) -> list[Watcher]:
        enabled = self._config.watchers_enabled
        all_watchers = {
            "foreground": ForegroundWatcher,
            "afk": AfkWatcher,
            "power": PowerWatcher,
        }
        watchers: list[Watcher] = []
        for name, cls in all_watchers.items():
            if name in enabled:
                interval = self._config.get_interval(name, _DEFAULT_INTERVALS[name])
                wc = WatcherConfig(name=name, interval_s=interval, enabled=True)
                if name == "foreground":
                    fw = cast(type[ForegroundWatcher], cls)
                    watchers.append(
                        fw(wc, app_config=self._config, storage=self._storage)
                    )
                elif name == "afk":
                    aw = cast(type[AfkWatcher], cls)
                    watchers.append(aw(wc, app_config=self._config))
                else:
                    watchers.append(cls(wc))
        logger.info("Created %d Windows watchers: %s", len(watchers), enabled)
        return watchers

    def shutdown(self) -> None:
        logger.info("Windows runtime shutdown")
