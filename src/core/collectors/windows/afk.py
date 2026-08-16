import ctypes
import logging

from core.config_manager import ConfigManager
from core.models import Tick, WatcherConfig

logger = logging.getLogger(__name__)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_kernel32.GetTickCount64.restype = ctypes.c_ulonglong


def _idle_seconds() -> float:
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        tick64 = _kernel32.GetTickCount64()
        tick_lower32 = tick64 & 0xFFFFFFFF
        diff_ms = (
            tick_lower32 - lii.dwTime
            if tick_lower32 >= lii.dwTime
            else 4294967296 - lii.dwTime + tick_lower32
        )
        return diff_ms / 1000.0
    except Exception:
        logger.debug("Failed to read idle time, defaulting to 0")
        return 0.0


class AfkWatcher:
    def __init__(
        self,
        config: WatcherConfig | None = None,
        app_config: ConfigManager | None = None,
    ):
        self.config = config or WatcherConfig(
            name="afk",
            interval_s=5.0,
            enabled=True,
        )
        self._app_config = app_config
        self._last_status: str | None = None

    async def tick(self) -> Tick | None:
        """Poll idle time; emit a tick only when the status changed.

        The idle/away state machine is write-on-change: a tick carrying the
        same ``status`` as the previous one is dropped (returns ``None``),
        so `idle_transition` rows mark block starts instead of a 5 s
        heartbeat. The first tick after startup always emits — entering a
        state is a change.
        """
        try:
            idle = _idle_seconds()
            idle_threshold = (
                self._app_config.afk_idle_threshold_s if self._app_config else 60.0
            )
            away_threshold = (
                self._app_config.afk_away_threshold_s if self._app_config else 300.0
            )
            status = "active"
            if idle > away_threshold:
                status = "away"
            elif idle > idle_threshold:
                status = "idle"
        except Exception:
            logger.exception("AfkWatcher tick failed")
            status = "active"
            idle = 0.0

        if status == self._last_status:
            return None
        self._last_status = status
        return Tick(
            watcher="afk",
            data={
                "status": status,
                "idle_seconds": idle,
            },
        )
