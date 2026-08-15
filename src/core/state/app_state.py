"""Singleton holder for everything about the runtime app state.

Reads go straight to public attributes. Mutations go through the ``set_*`` /
``record_*`` methods, which update the value and notify observers registered
with :meth:`AppState.on_change` for the corresponding key. This keeps a single
place where runtime facts live and lets the UI subscribe to what it renders.
"""

from __future__ import annotations

import logging
import platform as _platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from core import device_identity
from core.models import Tick
from core.update_checker import UpdateInfo
from UI.layout.models import AppLayout
from utils.paths import get_data_dir
from utils.platform import OSType, detect_os, is_packaged
from utils.versions import get_current_version

logger = logging.getLogger(__name__)

# ── Observer keys ────────────────────────────────────────────────────────────

KEY_OS_TYPE = "os_type"
KEY_PLATFORM_NAME = "platform_name"
KEY_IS_PACKAGED = "is_packaged"
KEY_APP_VERSION = "app_version"
KEY_DEVICE_ID = "device_id"
KEY_DATA_DIR = "data_dir"
KEY_COLLECTION_RUNNING = "collection_running"
KEY_COLLECTION_PAUSED = "collection_paused"
KEY_COLLECTION_AUTO_PAUSED = "collection_auto_paused"
KEY_COLLECTION_STARTED_AT = "collection_started_at"
KEY_WATCHER_HEALTH = "watcher_health"
KEY_LAST_TICKS = "last_ticks"
KEY_LAYOUT = "layout"
KEY_ROUTE = "route"
KEY_UPDATE_STATUS = "update_status"
KEY_UPDATE_INFO = "update_info"
KEY_UPDATE_PROGRESS = "update_progress"
KEY_UPDATE_ERROR = "update_error"


class UpdateStatus(Enum):
    IDLE = "idle"
    CHECKING = "checking"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    READY = "ready"
    APPLYING = "applying"
    FAILED = "failed"


@dataclass
class WatcherHealth:
    """Per-watcher runtime health snapshot."""

    name: str = ""
    failures: int = 0
    paused: bool = False
    last_tick_at: datetime | None = None
    last_error: str | None = None


class AppState:
    """Container for all runtime app state (environment, collection, UI, update).

    Public attributes are the read surface; ``set_*`` / ``record_*`` methods
    are the mutation surface and notify subscribers on change. Setting a value
    equal to the current one does not re-notify.
    """

    def __init__(self) -> None:
        self._observers: dict[str, list[Callable[[str], None]]] = {}
        #: Environment (set once at construction).
        self.os_type = OSType.UNKNOWN
        self.platform_name = ""
        self.is_packaged = False
        self.app_version = ""
        self.device_id = ""
        self.data_dir = ""
        #: Collection runtime.
        self.collection_running = False
        self.collection_paused = False
        self.collection_auto_paused = False
        self.collection_started_at: datetime | None = None
        self.watcher_health: dict[str, WatcherHealth] = {}
        self.last_ticks: dict[str, Tick] = {}
        #: UI state.
        self.layout: AppLayout | None = None
        self.current_route = "/dashboard"
        #: Update state.
        self.update_status = UpdateStatus.IDLE
        self.update_info: UpdateInfo | None = None
        self.update_progress: tuple[int, int | None] | None = None
        self.update_error: str | None = None

        self.snapshot_environment()

    # ── Observers ────────────────────────────────────────────────────────────

    def on_change(self, key: str, callback: Callable[[str], None]) -> None:
        """Register ``callback`` to fire with ``key`` on every change to it."""
        self._observers.setdefault(key, []).append(callback)

    def unsubscribe(self, key: str, callback: Callable[[str], None]) -> None:
        callbacks = self._observers.get(key)
        if callbacks and callback in callbacks:
            callbacks.remove(callback)

    def _notify(self, key: str) -> None:
        for cb in list(self._observers.get(key, ())):
            try:
                cb(key)
            except Exception:
                logger.exception("AppState observer failed for key=%s", key)

    def _update(self, key: str, attr: str, value) -> None:
        if getattr(self, attr) == value:
            return
        setattr(self, attr, value)
        self._notify(key)

    # ── Environment ──────────────────────────────────────────────────────────

    def snapshot_environment(self) -> None:
        """Re-read platform/device facts into state (called at construction)."""
        self._update(KEY_OS_TYPE, "os_type", detect_os())
        self._update(KEY_PLATFORM_NAME, "platform_name", _platform.system())
        self._update(KEY_IS_PACKAGED, "is_packaged", is_packaged())
        self._update(KEY_APP_VERSION, "app_version", get_current_version())
        self._update(KEY_DEVICE_ID, "device_id", device_identity.get_device_id())
        self._update(KEY_DATA_DIR, "data_dir", get_data_dir())

    # ── Collection runtime ───────────────────────────────────────────────────

    def set_collection_running(self, running: bool) -> None:
        self._update(KEY_COLLECTION_RUNNING, "collection_running", running)

    def set_collection_paused(self, paused: bool) -> None:
        self._update(KEY_COLLECTION_PAUSED, "collection_paused", paused)

    def set_collection_auto_paused(self, auto_paused: bool) -> None:
        self._update(KEY_COLLECTION_AUTO_PAUSED, "collection_auto_paused", auto_paused)

    def set_collection_started_at(self, started_at: datetime | None = None) -> None:
        self._update(
            KEY_COLLECTION_STARTED_AT,
            "collection_started_at",
            started_at or datetime.now(timezone.utc),
        )

    def set_os_type(self, os_type: OSType) -> None:
        self._update(KEY_OS_TYPE, "os_type", os_type)

    def ensure_watcher(self, name: str) -> None:
        """Register a watcher in :attr:`watcher_health` (fresh entry)."""
        self.watcher_health[name] = WatcherHealth(name=name)
        self._notify(KEY_WATCHER_HEALTH)

    def set_watcher_health(
        self,
        name: str,
        failures: int | None = None,
        paused: bool | None = None,
        last_error: str | None = None,
    ) -> None:
        """Update one watcher's health; missing fields keep current values."""
        health = self.watcher_health.get(name)
        if health is None:
            self.watcher_health[name] = WatcherHealth(
                name=name,
                failures=failures if failures is not None else 0,
                paused=paused if paused is not None else False,
                last_error=last_error,
            )
            self._notify(KEY_WATCHER_HEALTH)
            return
        changed = False
        if failures is not None and health.failures != failures:
            health.failures = failures
            changed = True
        if paused is not None and health.paused != paused:
            health.paused = paused
            changed = True
        if last_error is not None and health.last_error != last_error:
            health.last_error = last_error
            changed = True
        if changed:
            self._notify(KEY_WATCHER_HEALTH)

    def record_tick(self, tick: Tick) -> None:
        """Record a successful tick: latest snapshot + health heartbeat."""
        self.last_ticks[tick.watcher] = tick
        self._notify(KEY_LAST_TICKS)
        health = self.watcher_health.get(tick.watcher)
        if health is None:
            health = WatcherHealth(name=tick.watcher)
            self.watcher_health[tick.watcher] = health
        changed = health.failures != 0 or health.last_tick_at != tick.timestamp
        health.failures = 0
        health.last_tick_at = tick.timestamp
        if changed:
            self._notify(KEY_WATCHER_HEALTH)

    # ── UI state ─────────────────────────────────────────────────────────────

    def set_layout(self, layout: AppLayout | None) -> None:
        self._update(KEY_LAYOUT, "layout", layout)

    def set_route(self, route: str) -> None:
        self._update(KEY_ROUTE, "current_route", route)

    # ── Update state ─────────────────────────────────────────────────────────

    def set_update_status(self, status: UpdateStatus) -> None:
        self._update(KEY_UPDATE_STATUS, "update_status", status)

    def set_update_info(self, info: UpdateInfo | None) -> None:
        self._update(KEY_UPDATE_INFO, "update_info", info)

    def set_update_progress(self, progress: tuple[int, int | None] | None) -> None:
        self._update(KEY_UPDATE_PROGRESS, "update_progress", progress)

    def set_update_error(self, error: str | None) -> None:
        self._update(KEY_UPDATE_ERROR, "update_error", error)


_state: AppState | None = None


def get_app_state() -> AppState:
    """Return the process-wide :class:`AppState` instance."""
    global _state
    if _state is None:
        _state = AppState()
    return _state


def reset_app_state() -> AppState:
    """Replace the singleton with a fresh instance (used by tests)."""
    global _state
    _state = AppState()
    return _state
