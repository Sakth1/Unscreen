from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from core.state.app_state import (
    KEY_LAST_TICKS,
    KEY_LAYOUT,
    KEY_ROUTE,
    KEY_WATCHER_HEALTH,
    UpdateStatus,
    get_app_state,
    reset_app_state,
)
from core.update_checker import UpdateInfo
from UI.layout.layout_resolver import app_layout_resolver
from utils.platform import OSType


class TestSingleton:
    def test_get_returns_same_instance(self):
        assert get_app_state() is get_app_state()

    def test_reset_returns_fresh_instance(self):
        first = get_app_state()
        second = reset_app_state()
        assert second is not first
        assert get_app_state() is second

    def test_reset_clears_values(self):
        state = get_app_state()
        state.set_route("/timeline")
        state.set_collection_running(True)
        reset_app_state()
        fresh = get_app_state()
        assert fresh.current_route == "/dashboard"
        assert fresh.collection_running is False


class TestEnvironment:
    def test_environment_populated(self, monkeypatch):
        monkeypatch.setattr("core.state.app_state.detect_os", lambda: OSType.ANDROID)
        monkeypatch.setattr("core.state.app_state.is_packaged", lambda: True)
        monkeypatch.setattr("core.state.app_state.get_current_version", lambda: "9.9.9")
        monkeypatch.setattr("core.state.app_state.get_data_dir", lambda: "/fake/data")
        state = reset_app_state()
        assert state.os_type is OSType.ANDROID
        assert state.platform_name
        assert state.is_packaged is True
        assert state.app_version == "9.9.9"
        assert state.device_id == "00000000-0000-0000-0000-000000000001"
        assert state.data_dir == "/fake/data"


class TestObservers:
    def test_on_change_fires_with_key(self):
        state = get_app_state()
        fired = []
        state.on_change(KEY_LAYOUT, lambda key: fired.append(key))
        state.set_layout(app_layout_resolver(1200, 800))
        assert fired == [KEY_LAYOUT]

    def test_unsubscribe_stops_firing(self):
        state = get_app_state()
        fired = []

        def cb(key):
            fired.append(key)

        state.on_change(KEY_ROUTE, cb)
        state.unsubscribe(KEY_ROUTE, cb)
        state.set_route("/timeline")
        assert fired == []

    def test_no_notify_when_value_unchanged(self):
        state = get_app_state()
        fired = []
        state.on_change(KEY_ROUTE, lambda key: fired.append(key))
        state.set_route("/dashboard")
        state.set_route("/dashboard")
        assert fired == []
        state.set_route("/timeline")
        state.set_route("/timeline")
        assert fired == [KEY_ROUTE]

    def test_observer_error_swallowed(self):
        state = get_app_state()

        def boom(key):
            raise RuntimeError("observer exploded")

        state.on_change(KEY_ROUTE, boom)
        state.set_route("/timeline")
        assert state.current_route == "/timeline"

    def test_observers_isolated_per_key(self):
        state = get_app_state()
        fired = []
        state.on_change(KEY_ROUTE, lambda key: fired.append(key))
        state.set_collection_running(True)
        assert fired == []


class TestCollectionState:
    def test_collection_lifecycle_state(self):
        state = get_app_state()
        state.set_collection_running(True)
        assert state.collection_running is True
        started_at = datetime(2026, 8, 5, tzinfo=timezone.utc)
        state.set_collection_started_at(started_at)
        assert state.collection_started_at == started_at
        state.set_collection_paused(True)
        state.set_collection_auto_paused(True)
        assert state.collection_paused is True
        assert state.collection_auto_paused is True
        state.set_collection_running(False)
        state.set_collection_paused(False)
        state.set_collection_auto_paused(False)
        assert not any(
            [
                state.collection_running,
                state.collection_paused,
                state.collection_auto_paused,
            ]
        )


class TestWatcherHealth:
    def test_set_watcher_health_upserts(self):
        state = get_app_state()
        state.set_watcher_health("foreground", failures=3, last_error="boom")
        health = state.watcher_health["foreground"]
        assert health.name == "foreground"
        assert health.failures == 3
        assert health.last_error == "boom"
        state.set_watcher_health("foreground", paused=True)
        assert state.watcher_health["foreground"].paused is True
        assert state.watcher_health["foreground"].failures == 3

    def test_ensure_watcher_registers_fresh_entry(self):
        state = get_app_state()
        fired = []
        state.on_change(KEY_WATCHER_HEALTH, lambda key: fired.append(key))
        state.ensure_watcher("power")
        assert state.watcher_health["power"].name == "power"
        assert state.watcher_health["power"].failures == 0
        assert fired == [KEY_WATCHER_HEALTH]

    def test_record_tick_updates_last_ticks_and_health(self, make_tick):
        state = get_app_state()
        fired = []
        state.on_change(KEY_LAST_TICKS, lambda key: fired.append(key))
        tick = make_tick(watcher="afk", data={"status": "active"})
        state.record_tick(tick)
        assert state.last_ticks["afk"] is tick
        assert state.watcher_health["afk"].last_tick_at == tick.timestamp
        assert state.watcher_health["afk"].failures == 0
        assert fired == [KEY_LAST_TICKS]


class TestUIState:
    def test_layout_and_route(self):
        state = get_app_state()
        layout = app_layout_resolver(500, 800)
        state.set_layout(layout)
        assert state.layout is layout
        state.set_route("/settings")
        assert state.current_route == "/settings"


class TestUpdateState:
    def _make_update_info(self) -> UpdateInfo:
        return UpdateInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            release_notes="",
            published_at="",
            prerelease=False,
            html_url="https://example.invalid/releases/v1.0.0",
        )

    def test_update_state_transitions(self):
        state = get_app_state()
        info = self._make_update_info()
        state.set_update_status(UpdateStatus.DOWNLOADING)
        state.set_update_info(info)
        state.set_update_progress((100, 1000))
        assert state.update_status is UpdateStatus.DOWNLOADING
        assert state.update_info is info
        assert state.update_progress == (100, 1000)
        state.set_update_status(UpdateStatus.FAILED)
        state.set_update_error("network down")
        assert state.update_status is UpdateStatus.FAILED
        assert state.update_error == "network down"


class TestCollectionManagerWiring:
    async def test_start_stop_updates_state(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        runtime = MagicMock()
        runtime.create_watchers.return_value = []
        with patch.object(cm, "_create_runtime", return_value=runtime):
            await cm.start()
        try:
            state = get_app_state()
            assert state.collection_running is True
            assert state.collection_started_at is not None
        finally:
            await cm.stop()
        assert get_app_state().collection_running is False

    async def test_pause_resume_updates_state(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm.pause()
        assert get_app_state().collection_paused is True
        cm.resume()
        assert get_app_state().collection_paused is False

    async def test_tick_bridge_updates_state(self, make_tick):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        tick = make_tick(watcher="afk", data={"status": "active"})
        cm._on_tick_state(tick)
        state = get_app_state()
        assert state.last_ticks["afk"] is tick
        assert state.watcher_health["afk"].last_tick_at == tick.timestamp

    async def test_tick_bridge_records_paused_watcher(self, make_tick):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._scheduler._paused_watchers.add("afk")
        tick = make_tick(watcher="afk")
        cm._on_tick_state(tick)
        assert get_app_state().watcher_health["afk"].paused is True


class TestRouteManagerWiring:
    def test_navigate_updates_state(self):
        from UI.layout.models import NavigationDestination
        from UI.routing import RouteManager

        page = MagicMock()
        container = MagicMock()
        views = {
            "/dashboard": object(),
            "/timeline": object(),
        }
        destinations = [
            NavigationDestination(route, "Label " + route, "HOME", view)
            for route, view in views.items()
        ]
        rm = RouteManager(page, container, destinations)
        rm.navigate("/timeline")
        assert get_app_state().current_route == "/timeline"
        assert container.update.called
