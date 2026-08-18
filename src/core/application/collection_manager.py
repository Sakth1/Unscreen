import asyncio
import contextlib
import logging
from collections.abc import Callable

from core.config_manager import ConfigManager
from core.event_bus import TickBus
from core.models import Tick
from core.scheduler import Scheduler
from core.state.app_state import get_app_state
from core.storage import Storage
from utils.platform import OSType, detect_os
from utils.time_utils import utc_timestamp

logger = logging.getLogger(__name__)


class _EventBridge:
    def __init__(self, storage: Storage, platform: str):
        self._storage = storage
        self._platform = platform
        self._last_app: dict[str, str | None] = {}
        self._last_event_id: dict[str, int | None] = {}

    def __call__(self, tick: Tick) -> None:
        ts = int(round(tick.timestamp.timestamp() * 1000))
        watcher = tick.watcher
        data = tick.data

        event_type = _watcher_to_event_type(watcher)
        if event_type is None:
            return

        if event_type == "app_usage_interval":
            for interval in data.get("intervals") or []:
                self._storage.write_event(
                    event_type=event_type,
                    timestamp=ts,
                    payload=interval,
                    source=watcher,
                )
            return

        if event_type == "foreground_transition":
            app_key = data.get("app") or data.get("package", "unknown")
            url_visit = data.get("url_visit")
            if app_key == self._last_app.get(watcher):
                event_id = self._last_event_id.get(watcher)
                if url_visit and event_id is not None:
                    self._write_url_visit(watcher, url_visit, ts, event_id)
                return
            prev_event_id = self._last_event_id.get(watcher)
            self._last_app[watcher] = app_key
            payload = {k: v for k, v in data.items() if k != "url_visit"}
            event_id = self._storage.write_event(
                event_type=event_type,
                timestamp=ts,
                payload=payload,
                source=watcher,
            )
            self._last_event_id[watcher] = event_id
            if url_visit:
                self._write_url_visit(watcher, url_visit, ts, event_id)
            if self._platform == "windows":
                self._produce_app_session(prev_event_id, event_id, ts, app_key, payload)
            return

        if event_type == "idle_transition":
            prev_event_id = self._last_event_id.get(watcher)
            event_id = self._storage.write_event(
                event_type=event_type,
                timestamp=ts,
                payload=data,
                source=watcher,
            )
            self._last_event_id[watcher] = event_id
            if self._platform == "windows":
                self._produce_status_session(prev_event_id, event_id, ts, data)
            return

        event_id = self._storage.write_event(
            event_type=event_type,
            timestamp=ts,
            payload=data,
            source=watcher,
        )
        self._last_event_id[watcher] = event_id

    def _produce_status_session(
        self,
        prev_event_id: int | None,
        event_id: int,
        start_ts: int,
        payload: dict,
    ) -> None:
        """Write-time status block production (Windows).

        Every idle_transition entry (including 'active') opens its own
        status_sessions row and closes the previous one at this entry's
        start timestamp — one block per status, half-open intervals.
        """
        try:
            if prev_event_id is not None:
                self._storage.close_status_session(prev_event_id, start_ts)
            status = payload.get("status", "active")
            self._storage.open_status_session(event_id, start_ts, status, payload)
        except Exception:
            logger.exception(
                "Failed to produce status session for event %d (status=%s)",
                event_id,
                payload.get("status"),
            )

    def _produce_app_session(
        self,
        prev_event_id: int | None,
        event_id: int,
        start_ts: int,
        app_key: str,
        payload: dict,
    ) -> None:
        """Write-time app session production (Windows).

        The new foreground_transition closes the previous session at its
        own start timestamp (half-open [start, next_start)) and opens the
        next one. The closed session's url_visits get their session_id
        backfilled.
        """
        try:
            if prev_event_id is not None:
                session_id = self._storage.close_app_session(prev_event_id, start_ts)
                if session_id is not None:
                    self._storage.backfill_url_sessions_for_event(
                        prev_event_id, session_id
                    )
            self._storage.open_app_session(event_id, start_ts, app_key, payload)
        except Exception:
            logger.exception(
                "Failed to produce app session for event %d (app=%s)",
                event_id,
                app_key,
            )

    def finalize_open_sessions(self, end_ts: int) -> None:
        """Close every still-open session/block at ``end_ts`` (collection stop)."""
        if self._platform != "windows":
            return
        app_event_id = self._last_event_id.get("foreground")
        if app_event_id is not None:
            try:
                session_id = self._storage.close_app_session(app_event_id, end_ts)
                if session_id is not None:
                    self._storage.backfill_url_sessions_for_event(
                        app_event_id, session_id
                    )
            except Exception:
                logger.exception(
                    "Failed to close open app session for event %d at stop",
                    app_event_id,
                )
        status_event_id = self._last_event_id.get("afk")
        if status_event_id is not None:
            try:
                self._storage.close_status_session(status_event_id, end_ts)
            except Exception:
                logger.exception(
                    "Failed to close open status block for event %d at stop",
                    status_event_id,
                )

    def _write_url_visit(
        self, watcher: str, url_visit: dict, ts: int, event_id: int
    ) -> None:
        try:
            self._storage.write_url_visit(
                url=url_visit["url"],
                seen_at=ts,
                event_id=event_id,
                browser=url_visit.get("browser"),
                extraction_method=url_visit.get("extraction_method"),
                confidence=url_visit.get("confidence", "high"),
                scheme=url_visit.get("scheme"),
                host=url_visit.get("host"),
                domain=url_visit.get("domain"),
                path=url_visit.get("path"),
                is_trackable=url_visit.get("is_trackable", True),
            )
        except Exception:
            logger.exception(
                "Failed to write url_visit for watcher %s (event %d, url %s)",
                watcher,
                event_id,
                url_visit.get("url"),
            )


def _watcher_to_event_type(watcher: str) -> str | None:
    mapping = {
        "foreground": "foreground_transition",
        "android_foreground": "foreground_transition",
        "android_app_usage": "app_usage_interval",
        "afk": "idle_transition",
        "android_afk": "user_presence",
        "power": "power_change",
        "android_power": "power_change",
    }
    return mapping.get(watcher)


class CollectionManager:
    def __init__(self, config: ConfigManager | None = None):
        self._config = config or ConfigManager()
        self._config.load()
        self._bus = TickBus()
        self._scheduler = Scheduler(self._bus)
        self._storage = Storage()
        self._app_state = get_app_state()
        self._runtime = None
        self._system_type = OSType.UNKNOWN
        self._running = False
        self._auto_paused = False
        self._screen_monitor_task: asyncio.Task | None = None
        self._health_monitor_task: asyncio.Task | None = None
        self._on_pause_changed = None
        self._event_bridge = _EventBridge(
            self._storage, "windows" if detect_os() == OSType.WINDOWS else "android"
        )

    def _create_runtime(self):
        match self._system_type:
            case OSType.WINDOWS:
                from core.collectors.windows.runtime import WindowsRuntime

                return WindowsRuntime(self._config, storage=self._storage)
            case OSType.ANDROID:
                from core.collectors.android.runtime import AndroidRuntime

                return AndroidRuntime(self._config)
            case _:
                raise RuntimeError(f"Unsupported platform: {self._system_type}")

    async def start(self) -> None:
        self._system_type = detect_os()
        logger.info("Detected platform: %s", self._system_type)
        self._app_state.set_os_type(self._system_type)

        self._bus.subscribe(self._event_bridge)
        self._bus.subscribe(self._on_tick_state)

        self._runtime = self._create_runtime()

        watchers = self._runtime.create_watchers()
        for w in watchers:
            self._scheduler.register(w)
            self._app_state.ensure_watcher(w.config.name)
            logger.info("Registered watcher: %s", w.config.name)

        await self._scheduler.start()
        self._running = True
        self._app_state.set_collection_running(True)
        self._app_state.set_collection_started_at()

        if not self._config.collection_enabled:
            self._scheduler.pause()
            self._app_state.set_collection_paused(True)
            logger.info("Collection started in paused state (from saved config)")

        self._health_monitor_task = asyncio.create_task(self._run_health_monitor())
        logger.info("Health monitor started")

        if self._system_type == OSType.ANDROID:
            from core.application.session_reconstructor import rebuild_sessions

            self._screen_monitor_task = asyncio.create_task(
                self._monitor_screen_state()
            )
            logger.info("Screen state monitor started")
            try:
                rebuild_sessions(self._storage, self._storage.device_id)
            except Exception:
                logger.exception(
                    "Failed to rebuild sessions at startup — sessions may lag events"
                )

        logger.info(
            "Collection started — events will be written to %s",
            self._storage.db_path,
        )

    async def restart(self) -> None:
        """Stop collection and rebuild watchers from the current config.

        Used by settings to apply watcher enable/disable and interval
        changes without restarting the app. No-op when not running.
        """
        if not self._running:
            logger.info("Collection not running; restart skipped")
            return
        logger.info("Restarting collection with current config")
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        self._running = False
        self._auto_paused = False
        self._app_state.set_collection_running(False)
        self._app_state.set_collection_paused(False)
        self._app_state.set_collection_auto_paused(False)
        if self._health_monitor_task:
            self._health_monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_monitor_task
            self._health_monitor_task = None
        if self._screen_monitor_task:
            self._screen_monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._screen_monitor_task
            self._screen_monitor_task = None
        await self._scheduler.stop()
        self._event_bridge.finalize_open_sessions(utc_timestamp())
        if self._system_type == OSType.ANDROID:
            from core.application.session_reconstructor import rebuild_sessions

            try:
                rebuild_sessions(self._storage, self._storage.device_id)
            except Exception:
                logger.exception("Failed to rebuild sessions at stop")
        self._storage.sync_durable_backup(force=True)
        if self._runtime:
            self._runtime.shutdown()
        self._bus.unsubscribe(self._event_bridge)
        self._bus.unsubscribe(self._on_tick_state)
        logger.info("Collection stopped")

    def pause(self) -> None:
        if self._scheduler.is_paused:
            return
        self._auto_paused = False
        self._app_state.set_collection_auto_paused(False)
        self._set_paused(True)

    def resume(self) -> None:
        if not self._scheduler.is_paused:
            return
        self._auto_paused = False
        self._app_state.set_collection_auto_paused(False)
        self._set_paused(False)

    @property
    def is_paused(self) -> bool:
        return self._scheduler.is_paused

    def _set_paused(self, paused: bool) -> None:
        if paused:
            self._config.collection_enabled = False
            self._config.save()
            self._scheduler.pause()
        else:
            self._config.collection_enabled = True
            self._config.save()
            self._scheduler.resume()
        self._app_state.set_collection_paused(paused)
        if self._on_pause_changed:
            self._on_pause_changed(paused)

    def _on_tick_state(self, tick: Tick) -> None:
        """Mirror each successful tick into app state for live UI reads."""
        self._app_state.set_watcher_health(
            tick.watcher,
            paused=tick.watcher in self._scheduler.paused_watchers,
        )
        self._app_state.record_tick(tick)

    async def _monitor_screen_state(self, interval: float = 5.0) -> None:
        from core.collectors.android.usage_stats import is_screen_on

        was_on = is_screen_on()
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            now_on = is_screen_on()
            if was_on and not now_on:
                self._auto_paused = True
                self._app_state.set_collection_auto_paused(True)
                self._set_paused(True)
                self._storage.write_event(
                    "screen_state_change",
                    utc_timestamp(),
                    {"screen_on": False},
                    "screen_monitor",
                )
                logger.info("Screen turned off — collection auto-paused")
            elif not was_on and now_on and self._auto_paused:
                self._auto_paused = False
                self._app_state.set_collection_auto_paused(False)
                self._set_paused(False)
                self._storage.write_event(
                    "screen_state_change",
                    utc_timestamp(),
                    {"screen_on": True},
                    "screen_monitor",
                )
                logger.info("Screen turned on — collection auto-resumed")
            was_on = now_on

    async def _run_health_monitor(self, interval: float = 3600.0) -> None:
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            result = self._storage.check_integrity()
            if not result["ok"]:
                logger.error("Periodic integrity check FAILED: %s", result["message"])
            vac = self._storage.auto_vacuum()
            if vac["vacuumed"]:
                logger.info("Periodic auto-vacuum completed: %s", vac["message"])
            self._storage.sync_durable_backup()

    @property
    def bus(self) -> TickBus:
        return self._bus

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def system_type(self) -> OSType:
        return self._system_type

    @property
    def storage(self) -> Storage:
        return self._storage

    @property
    def config(self) -> ConfigManager:
        return self._config

    def clear_all_data(self) -> None:
        self._storage.clear_all_data()

    @property
    def on_pause_changed(self) -> Callable[[bool], None] | None:
        return self._on_pause_changed

    @on_pause_changed.setter
    def on_pause_changed(self, callback: Callable[[bool], None] | None) -> None:
        self._on_pause_changed = callback
