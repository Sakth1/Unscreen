"""Lifecycle sweep: end-to-end collection wiring and headless UI boot.

Phase 4 of the QA overhaul. Exercises the real wiring — real ``TickBus``,
real ``Scheduler``, real ``Storage``, real event bridge — behind a fake
platform runtime, plus a headless ``App()`` boot across every
``ScreenFormFactor`` and its navigation paths. No hardcoded module or
function names beyond the classes under test.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import patch

import flet as ft
from sweep_helpers import mock_page

from core.application.collection_manager import CollectionManager, _EventBridge
from core.config_manager import ConfigManager
from core.models import Tick, WatcherConfig
from UI.layout.models import (
    NavigationPattern,
    ScreenFormFactor,
    SecondaryNavigationPattern,
)
from utils.platform import OSType

_EVENT_TIMEOUT_S = 5.0


def _walk_controls(control):
    """Yield a control and its descendants (controls lists + container content)."""
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from _walk_controls(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk_controls(content)


class _FakeWatcher:
    """Real watcher protocol, fake payloads — every tick emits real data."""

    def __init__(self, name: str, interval_s: float = 0.01):
        self.config = WatcherConfig(name=name, interval_s=interval_s)
        self.ticks = 0
        self._n = 0

    async def tick(self) -> Tick:
        self.ticks += 1
        self._n += 1
        return Tick(watcher=self.config.name, data={"app": f"App{self._n % 3}.exe"})


class _FakeRuntime:
    def __init__(self, watchers):
        self._watchers = watchers
        self.shutdown_calls = 0

    def create_watchers(self):
        return list(self._watchers)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


async def _wait_for(predicate, timeout: float = _EVENT_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"condition not met within {timeout}s")


class TestEventBridge:
    def test_unknown_watcher_writes_nothing(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(Tick(watcher="mystery", data={"app": "x"}))
        assert in_memory_db.get_raw_events() == []

    def test_foreground_transition_deduped(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(Tick(watcher="foreground", data={"app": "Code.exe"}))
        bridge(Tick(watcher="foreground", data={"app": "Code.exe"}))
        assert len(in_memory_db.get_raw_events()) == 1

        bridge(Tick(watcher="foreground", data={"app": "Browser.exe"}))
        events = in_memory_db.get_raw_events()
        assert len(events) == 2
        assert events[0]["event_type"] == "foreground_transition"

    def test_app_usage_intervals_fan_out(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "android")
        bridge(
            Tick(
                watcher="android_app_usage",
                data={
                    "intervals": [{"app": "A", "start": 1}, {"app": "B", "start": 2}]
                },
            )
        )
        events = in_memory_db.get_raw_events()
        assert len(events) == 2
        assert all(e["event_type"] == "app_usage_interval" for e in events)
        assert events[0]["payload"]["app"] == "A"

    def test_other_watcher_types_mapped(self, in_memory_db):
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime.now(timezone.utc)
        bridge(Tick(watcher="afk", data={"status": "idle"}, timestamp=t0))
        bridge(
            Tick(
                watcher="android_afk",
                data={"present": False},
                timestamp=t0 + timedelta(milliseconds=1),
            )
        )
        bridge(
            Tick(
                watcher="power",
                data={"on_ac": True},
                timestamp=t0 + timedelta(milliseconds=2),
            )
        )
        bridge(
            Tick(
                watcher="android_power",
                data={"on_ac": True},
                timestamp=t0 + timedelta(milliseconds=3),
            )
        )
        types = {e["event_type"] for e in in_memory_db.get_raw_events()}
        assert types == {"idle_transition", "user_presence", "power_change"}

    @staticmethod
    def _url_visit(url: str, **overrides) -> dict:
        visit = {
            "url": url,
            "browser": "brave",
            "extraction_method": "uia",
            "confidence": "high",
            "scheme": "https",
            "host": "example.com",
            "domain": "example.com",
            "path": "/",
            "is_trackable": True,
        }
        visit.update(overrides)
        return visit

    def test_url_visit_written_with_fresh_event_id(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(
            Tick(
                watcher="foreground",
                data={
                    "app": "brave.exe",
                    "url_visit": self._url_visit("https://a.com"),
                },
            )
        )
        events = in_memory_db.get_raw_events()
        assert len(events) == 1
        assert "url_visit" not in events[0]["payload"]
        visits = in_memory_db.get_url_visits()
        assert len(visits) == 1
        assert visits[0]["event_id"] == events[0]["id"]
        assert visits[0]["url"] == "https://a.com"
        assert visits[0]["browser"] == "brave"

    def test_app_sessions_produced_on_windows_transitions(self, in_memory_db):
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(Tick(watcher="foreground", data={"app": "Code.exe"}, timestamp=t0))
        t1 = t0 + timedelta(seconds=90)
        bridge(
            Tick(
                watcher="foreground",
                data={"app": "brave.exe", "title": "x"},
                timestamp=t1,
            )
        )
        sessions = in_memory_db.get_app_sessions()
        assert len(sessions) == 2
        first, second = sessions
        assert first["app_key"] == "Code.exe"
        assert first["end_ts"] == int(t1.timestamp() * 1000)
        assert first["duration_s"] == 90.0
        assert second["app_key"] == "brave.exe"
        assert second["end_ts"] is None  # still open
        events = in_memory_db.get_raw_events()
        assert first["event_id"] == events[0]["id"]
        assert second["event_id"] == events[1]["id"]

    def test_app_sessions_deduped_ticks_do_not_produce_sessions(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(Tick(watcher="foreground", data={"app": "Code.exe"}))
        bridge(Tick(watcher="foreground", data={"app": "Code.exe"}))
        assert len(in_memory_db.get_app_sessions()) == 1

    def test_android_produces_no_app_sessions(self, in_memory_db):
        from datetime import datetime, timezone

        bridge = _EventBridge(in_memory_db, "android")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(
            Tick(
                watcher="android_foreground",
                data={"package": "com.android.chrome"},
                timestamp=t0,
            )
        )
        assert len(in_memory_db.get_raw_events()) == 1
        assert len(in_memory_db.get_app_sessions()) == 0

    def test_finalize_closes_open_app_session(self, in_memory_db):
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(Tick(watcher="foreground", data={"app": "Code.exe"}, timestamp=t0))
        stop = t0 + timedelta(minutes=5)
        bridge.finalize_open_sessions(int(stop.timestamp() * 1000))
        sessions = in_memory_db.get_app_sessions()
        assert len(sessions) == 1
        assert sessions[0]["end_ts"] == int(stop.timestamp() * 1000)
        assert sessions[0]["duration_s"] == 300.0

    def test_finalize_is_noop_when_nothing_open(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge.finalize_open_sessions(1234)
        assert len(in_memory_db.get_app_sessions()) == 0

    def test_finalize_skipped_on_android(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "android")
        bridge(Tick(watcher="android_foreground", data={"package": "a.b.c"}))
        bridge.finalize_open_sessions(1234)
        assert len(in_memory_db.get_app_sessions()) == 0

    def test_url_visits_backfilled_on_session_close(self, in_memory_db):
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(
            Tick(
                watcher="foreground",
                data={
                    "app": "brave.exe",
                    "url_visit": self._url_visit("https://a.com"),
                },
                timestamp=t0,
            )
        )
        assert all(v["session_id"] is None for v in in_memory_db.get_url_visits())
        bridge(
            Tick(
                watcher="foreground",
                data={"app": "Code.exe"},
                timestamp=t0 + timedelta(seconds=10),
            )
        )
        visits = in_memory_db.get_url_visits()
        assert len(visits) == 1
        sessions = in_memory_db.get_app_sessions()
        assert visits[0]["session_id"] == sessions[0]["id"]

    def test_status_sessions_produced_on_windows_entries(self, in_memory_db):
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(
            Tick(
                watcher="afk",
                data={"status": "active", "idle_seconds": 5.0},
                timestamp=t0,
            )
        )
        t1 = t0 + timedelta(seconds=90)
        bridge(
            Tick(
                watcher="afk",
                data={"status": "idle", "idle_seconds": 65.0},
                timestamp=t1,
            )
        )
        blocks = in_memory_db.get_status_sessions()
        assert len(blocks) == 2
        active_block, idle_block = blocks
        assert active_block["status"] == "active"
        assert active_block["end_ts"] == int(t1.timestamp() * 1000)
        assert active_block["duration_s"] == 90.0
        assert idle_block["status"] == "idle"
        assert idle_block["end_ts"] is None  # still open
        events = in_memory_db.get_raw_events()
        assert active_block["event_id"] == events[0]["id"]
        assert idle_block["event_id"] == events[1]["id"]

    def test_duplicate_status_entries_each_get_a_block(self, in_memory_db):
        """Bridge contract: one status block per received idle event.

        Status-change dedup (F1) lives in AfkWatcher, not the bridge — a
        raw feed of N entries yields N blocks with half-open intervals.
        """
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(
            Tick(
                watcher="afk",
                data={"status": "idle", "idle_seconds": 61.0},
                timestamp=t0,
            )
        )
        bridge(
            Tick(
                watcher="afk",
                data={"status": "idle", "idle_seconds": 62.0},
                timestamp=t0 + timedelta(seconds=5),
            )
        )
        blocks = in_memory_db.get_status_sessions()
        assert len(blocks) == 2
        assert blocks[0]["end_ts"] == int(
            (t0 + timedelta(seconds=5)).timestamp() * 1000
        )
        assert blocks[1]["end_ts"] is None

    def test_android_produces_no_status_sessions(self, in_memory_db):
        from datetime import datetime, timezone

        bridge = _EventBridge(in_memory_db, "android")
        bridge(
            Tick(
                watcher="android_afk",
                data={"present": True, "screen_on": True},
                timestamp=datetime(2026, 7, 19, tzinfo=timezone.utc),
            )
        )
        assert len(in_memory_db.get_status_sessions()) == 0

    def test_finalize_closes_open_status_block(self, in_memory_db):
        from datetime import datetime, timedelta, timezone

        bridge = _EventBridge(in_memory_db, "windows")
        t0 = datetime(2026, 7, 19, tzinfo=timezone.utc)
        bridge(
            Tick(
                watcher="afk",
                data={"status": "away", "idle_seconds": 400.0},
                timestamp=t0,
            )
        )
        stop = t0 + timedelta(minutes=2)
        bridge.finalize_open_sessions(int(stop.timestamp() * 1000))
        blocks = in_memory_db.get_status_sessions()
        assert len(blocks) == 1
        assert blocks[0]["end_ts"] == int(stop.timestamp() * 1000)
        assert blocks[0]["duration_s"] == 120.0

    def test_url_visit_reuses_cached_event_id_on_tab_change(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(
            Tick(
                watcher="foreground",
                data={
                    "app": "brave.exe",
                    "url_visit": self._url_visit("https://a.com"),
                },
            )
        )
        bridge(
            Tick(
                watcher="foreground",
                data={
                    "app": "brave.exe",
                    "url_visit": self._url_visit("https://b.com"),
                },
            )
        )
        events = in_memory_db.get_raw_events()
        assert len(events) == 1, "tab change must not emit a new raw event"
        visits = in_memory_db.get_url_visits()
        assert len(visits) == 2
        assert visits[0]["event_id"] == visits[1]["event_id"] == events[0]["id"]

    def test_url_visit_on_app_change_gets_fresh_event(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(
            Tick(
                watcher="foreground",
                data={
                    "app": "brave.exe",
                    "url_visit": self._url_visit("https://a.com"),
                },
            )
        )
        bridge(
            Tick(
                watcher="foreground",
                data={"app": "Code.exe", "url_visit": self._url_visit("https://c.com")},
            )
        )
        events = in_memory_db.get_raw_events()
        assert len(events) == 2
        visits = in_memory_db.get_url_visits()
        assert len(visits) == 2
        assert visits[0]["event_id"] != visits[1]["event_id"]

    def test_duplicate_url_in_same_session_does_not_crash(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        for _ in range(2):
            bridge(
                Tick(
                    watcher="foreground",
                    data={
                        "app": "brave.exe",
                        "url_visit": self._url_visit("https://a.com"),
                    },
                )
            )
        visits = in_memory_db.get_url_visits()
        assert len(visits) == 1

    def test_low_confidence_fallback_url_visit(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(
            Tick(
                watcher="foreground",
                data={
                    "app": "brave.exe",
                    "url_visit": self._url_visit(
                        "https://example.com", extraction_method=None, confidence="low"
                    ),
                },
            )
        )
        visits = in_memory_db.get_url_visits()
        assert visits[0]["confidence"] == "low"
        assert visits[0]["extraction_method"] is None

    def test_timestamps_are_milliseconds(self, in_memory_db):
        bridge = _EventBridge(in_memory_db, "windows")
        bridge(Tick(watcher="afk", data={"status": "idle"}))
        ts = in_memory_db.get_raw_events()[0]["timestamp"]
        assert isinstance(ts, int)
        assert ts > 1_700_000_000_000


class TestCollectionEndToEnd:
    async def test_start_flows_ticks_into_storage(self, tmp_path):
        config = ConfigManager(path=str(tmp_path / "config.json"))
        cm = CollectionManager(config)
        runtime = _FakeRuntime([_FakeWatcher("foreground"), _FakeWatcher("afk")])
        with (
            patch(
                "core.application.collection_manager.detect_os",
                return_value=OSType.WINDOWS,
            ),
            patch.object(cm, "_create_runtime", return_value=runtime),
        ):
            await cm.start()

        try:
            assert cm.is_running
            assert cm.system_type is OSType.WINDOWS
            assert len(cm._scheduler._watchers) == 2

            await _wait_for(lambda: len(cm.storage.get_raw_events()) >= 2)

            events = cm.storage.get_raw_events()
            assert {e["event_type"] for e in events} >= {
                "foreground_transition",
                "idle_transition",
            }
        finally:
            await cm.stop()

        assert runtime.shutdown_calls == 1
        assert not cm.is_running
        assert cm._scheduler._tasks == []
        assert cm._health_monitor_task is None

    async def test_pause_halts_resume_restarts_flow(self, tmp_path):
        config = ConfigManager(path=str(tmp_path / "config.json"))
        cm = CollectionManager(config)
        runtime = _FakeRuntime([_FakeWatcher("afk")])
        with (
            patch(
                "core.application.collection_manager.detect_os",
                return_value=OSType.WINDOWS,
            ),
            patch.object(cm, "_create_runtime", return_value=runtime),
        ):
            await cm.start()

        try:
            await _wait_for(lambda: len(cm.storage.get_raw_events()) > 0)

            cm.pause()
            assert cm.is_paused
            assert not config.collection_enabled
            count_at_pause = len(cm.storage.get_raw_events())
            await asyncio.sleep(0.05)
            assert len(cm.storage.get_raw_events()) == count_at_pause

            cm.resume()
            assert not cm.is_paused
            assert config.collection_enabled
            await _wait_for(lambda: len(cm.storage.get_raw_events()) > count_at_pause)
        finally:
            await cm.stop()

    async def test_start_with_collection_disabled_auto_pauses(self, tmp_path):
        config = ConfigManager(path=str(tmp_path / "config.json"))
        config.collection_enabled = False
        config.save()
        cm = CollectionManager(config)
        runtime = _FakeRuntime([_FakeWatcher("afk")])
        with (
            patch(
                "core.application.collection_manager.detect_os",
                return_value=OSType.WINDOWS,
            ),
            patch.object(cm, "_create_runtime", return_value=runtime),
        ):
            await cm.start()

        try:
            assert cm.is_running
            assert cm.is_paused
            await asyncio.sleep(0.05)
            assert cm.storage.get_raw_events() == []
        finally:
            await cm.stop()

    async def test_stop_is_idempotent(self, tmp_path):
        cm = CollectionManager(ConfigManager(path=str(tmp_path / "config.json")))
        await cm.stop()
        await cm.stop()
        assert not cm.is_running
        assert cm._scheduler._tasks == []


class TestAppHeadlessBoot:
    @staticmethod
    def _page(width=None, height=None):
        page = mock_page()
        page.window.width = width
        page.window.height = height
        return page

    def test_desktop_boot(self):
        from app import App

        app = App(self._page(1280, 800))
        assert app.layout.screen_form_factor is ScreenFormFactor.DESKTOP
        assert app.navigation_rail is not None
        assert app.navigation_rail.extended is True
        assert len(app.shell.controls) == 2
        assert app.page.navigation_bar is None

    def test_tablet_portrait_boot(self):
        from app import App

        app = App(self._page(800, 1280))
        assert app.layout.screen_form_factor is ScreenFormFactor.TABLET_PORTRAIT
        assert app.page.navigation_bar is None
        assert app.navigation_rail is not None
        assert app.navigation_rail.extended is False  # mini rail
        assert app.route_manager.current_route == "/dashboard"

    def test_tablet_landscape_boot(self):
        from app import App

        app = App(self._page(960, 800))
        assert app.layout.screen_form_factor is ScreenFormFactor.TABLET_LANDSCAPE
        assert app.page.navigation_bar is None
        assert app.navigation_rail is not None
        assert app.navigation_rail.extended is True
        assert app.route_manager.current_route == "/dashboard"

    def test_mobile_boot(self):
        from app import App

        app = App(self._page(400, 800))
        assert app.layout.screen_form_factor is ScreenFormFactor.MOBILE
        assert app.page.navigation_bar is not None
        assert app.navigation_rail is None

    def test_android_boot_without_window_size(self):
        from app import App

        page = mock_page()
        page.platform.is_mobile.return_value = True
        app = App(page)

        assert app._is_mobile is True
        assert app.layout.screen_form_factor is ScreenFormFactor.MOBILE
        assert app.layout.navigation is NavigationPattern.BOTTOM_BAR
        assert app.layout.secondary_navigation is SecondaryNavigationPattern.INLINE
        assert app.page.navigation_bar is not None
        assert app.navigation_rail is None
        assert page.window.width is None  # precondition: mobile reports no size

    def test_mobile_settings_renders_inline_picker(self):
        from app import App
        from UI.screens.settings.settings_card import SettingsCard

        app = App(self._page(400, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()

        content = app.content_container.content
        assert isinstance(content, SettingsCard)
        tiles = content.content.content.controls[1:]
        assert [tile.title.value for tile in tiles] == ["General", "Data", "App Info"]

    def test_mobile_picker_tile_navigates_to_section(self):
        from app import App

        app = App(self._page(400, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()

        tiles = app.content_container.content.content.content.controls[1:]
        tiles[1].on_click(None)  # Data

        assert app.route_manager.current_route == "/settings/data"
        assert app.content_container.content is app.settings_page.data_section

    def test_mobile_section_stays_open_after_layout_refresh(self):
        from app import App
        from UI.screens.settings.settings_card import SettingsCard

        app = App(self._page(400, 800))
        app.route_manager.navigate("/settings/data")
        app._update_layout()

        assert app.content_container.content is app.settings_page.data_section
        assert not isinstance(app.content_container.content, SettingsCard)

    def test_mobile_picker_not_shown_for_plain_routes(self):
        from app import App
        from UI.screens.settings.settings_card import SettingsCard

        app = App(self._page(400, 800))
        app._update_layout()

        assert app.content_container.content is app.dashboard_page
        assert not isinstance(app.content_container.content, SettingsCard)

    def test_desktop_resize_restores_screen_from_inline_picker(self):
        from app import App

        app = App(self._page(400, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()

        page = app.page
        page.width = 1280
        page.height = 800
        page.media = None
        page.navigation_bar = None
        app._handle_page_resize(None)

        assert app.content_container.content is app.settings_page
        assert app.navigation_rail is not None
        assert page.navigation_bar is None

    def test_section_back_returns_to_parent(self):
        from app import App

        app = App(self._page(1280, 800))
        app.route_manager.navigate("/settings/data")
        app._go_back()

        assert app.route_manager.current_route == "/settings"
        assert app.content_container.content is app.settings_page

    def test_back_button_navigates_to_parent(self):
        from app import App

        app = App(self._page(1280, 800))
        app.route_manager.navigate("/settings/data")
        header = app.settings_page.data_section.content.controls[0]
        back = next(c for c in header.controls if c.icon == ft.Icons.ARROW_BACK)
        back.on_click(None)

        assert app.route_manager.current_route == "/settings"

    def test_section_back_on_mobile_restores_picker(self):
        from app import App
        from UI.screens.settings.settings_card import SettingsCard

        app = App(self._page(400, 800))
        app.route_manager.navigate("/settings/data")
        app._go_back()

        assert app.route_manager.current_route == "/settings"
        assert isinstance(app.content_container.content, SettingsCard)

    def test_go_back_is_noop_on_top_level_route(self):
        from app import App

        app = App(self._page(400, 800))
        app._go_back()

        assert app.route_manager.current_route == "/dashboard"

    def test_navigate_every_route(self):
        from app import App

        app = App(self._page(1280, 800))
        for route, screen in (
            ("/dashboard", app.dashboard_page),
            ("/timeline", app.timeline_page),
            ("/analytics", app.analytics_page),
            ("/settings", app.settings_page),
        ):
            app.route_manager.navigate(route)
            assert app.content_container.content is screen
            assert app.route_manager.current_route == route

    def test_route_change_event_before_any_navigate(self):
        from app import App

        app = App(self._page(400, 800))
        event = ft.Event(name="routeChange", control=None)
        event.route = "/timeline"
        app.route_manager.handle_route_change(event)
        assert app.route_manager.current_route == "/timeline"
        assert app.content_container.content is app.timeline_page

    def test_navigation_bar_select_switches_view(self):
        from app import App

        app = App(self._page(400, 800))
        app.page.navigation_bar.select_index(2)
        assert app.content_container.content is app.analytics_page
        assert app.route_manager.current_route == "/analytics"

    def test_navigation_bar_reused_across_layout_updates(self):
        from app import App

        app = App(self._page(400, 800))
        bar = app.page.navigation_bar

        app._update_layout()
        app._update_layout()

        assert app.page.navigation_bar is bar

    def test_navigation_bar_selection_survives_click_cycle(self):
        from app import App
        from UI.screens.settings.settings_card import SettingsCard

        app = App(self._page(400, 800))
        app.page.navigation_bar.select_index(2)
        assert app.page.navigation_bar.selected_index == 2

        app.page.navigation_bar.select_index(3)
        assert app.page.navigation_bar.selected_index == 3
        assert app.route_manager.current_route == "/settings"
        assert isinstance(app.content_container.content, SettingsCard)

    def test_navigation_bar_seeded_from_current_route(self):
        from app import App

        app = App(self._page(400, 800))
        app.route_manager.navigate("/analytics")
        app.page.navigation_bar = None
        app._update_layout()

        assert app.page.navigation_bar.selected_index == 2

    def test_route_lookup_resolves_screen(self):
        from app import App

        app = App(self._page(400, 800))
        assert app.route_manager.view_for("/dashboard") is app.dashboard_page
        assert app.route_manager.view_for("/settings") is app.settings_page
        assert app.route_manager.view_for("/nope") is None

    def test_navigation_rail_select_switches_view(self):
        from app import App

        app = App(self._page(1280, 800))
        app.navigation_rail.select_index(1)
        assert app.content_container.content is app.timeline_page

    def test_rail_settings_trailing_click(self):
        from app import App

        app = App(self._page(1280, 800))
        on_click = app.navigation_rail.trailing.on_click
        assert on_click is not None
        on_click(None)  # type: ignore[reportCallIssue]
        assert app.content_container.content is app.settings_page
        assert app.route_manager.current_route == "/settings"

    def test_secondary_panel_builds_with_sections(self):
        from app import App

        app = App(self._page(1280, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()
        panel = app.secondary_navigation_panel
        assert panel is not None
        assert [d.label for d in panel.final_destinations] == [
            "General",
            "Data",
            "App Info",
        ]
        assert panel.selected_index == 0
        assert app.settings_page.content is app.settings_page.general_section

    def test_secondary_panel_select_navigates_to_section(self):
        from app import App
        from UI.layout.models import SecondaryNavigationChangeData

        app = App(self._page(1280, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()
        event = ft.Event(
            name="SecondaryNavigationChange",
            control=app.secondary_navigation_panel,
            data=SecondaryNavigationChangeData(
                index=1, label="Data", route="/settings/data"
            ),
        )
        app._handle_secondary_change(event)
        assert app.route_manager.current_route == "/settings/data"
        assert app.content_container.content is app.settings_page.data_section

    def test_secondary_panel_index_fallback_navigates_to_section(self):
        from app import App

        app = App(self._page(1280, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()
        control = type("PanelStub", (), {"selected_index": 1})()
        event = ft.Event(
            name="SecondaryNavigationChange",
            control=control,
        )
        app._handle_secondary_change(event)
        assert app.route_manager.current_route == "/settings/data"

    def test_secondary_panel_change_out_of_range_ignored(self):
        from app import App

        app = App(self._page(1280, 800))
        app.route_manager.navigate("/settings")
        app._update_layout()
        control = type("PanelStub", (), {"selected_index": 9})()
        event = ft.Event(
            name="SecondaryNavigationChange",
            control=control,
        )
        app._handle_secondary_change(event)
        assert app.route_manager.current_route == "/settings"

    def test_unknown_route_falls_back_to_dashboard(self, caplog):
        from app import App

        app = App(self._page(1280, 800))
        with caplog.at_level(logging.WARNING, logger="UI.routing"):
            app.route_manager.navigate("/nope")
        assert "Unknown route" in caplog.text
        assert app.content_container.content is app.dashboard_page
        assert app.route_manager.current_route == "/dashboard"

    def test_resize_switches_form_factor(self):
        from app import App

        app = App(self._page(1280, 800))
        assert app.layout.screen_form_factor is ScreenFormFactor.DESKTOP

        page = app.page
        page.width = 400
        page.height = 800
        page.media = None
        page.navigation_bar = None
        app._handle_page_resize(None)
        assert app.layout.screen_form_factor is ScreenFormFactor.MOBILE
        assert page.navigation_bar is not None
        assert len(app.shell.controls) == 1

        page.width = 1280
        page.height = 800
        app._handle_page_resize(None)
        assert app.layout.screen_form_factor is ScreenFormFactor.DESKTOP
        assert page.navigation_bar is None
        assert len(app.shell.controls) == 2

    def test_alert_dialog_close_runs_callback(self):
        from UI.dialogs import show_alert_dialog

        page = mock_page()
        closed = []
        show_alert_dialog(page, "Title", "Message", on_close=lambda: closed.append(1))
        dialog = page.show_dialog.call_args.args[0]
        dialog.actions[0].on_click(None)
        assert closed == [1]
        page.pop_dialog.assert_called()

    def test_permission_dialog_shows(self):
        from UI.dialogs import show_permission_dialog

        page = mock_page()
        show_permission_dialog(page)
        page.show_dialog.assert_called_once()

    def test_packaged_second_instance_refuses_to_start(self):
        import pytest

        from app import App

        with (
            patch("app.detect_os", return_value=OSType.WINDOWS),
            patch("app.is_packaged", return_value=True),
            patch("app.acquire_instance_mutex", return_value=None),
            pytest.raises(RuntimeError, match="already running"),
        ):
            App(self._page(1280, 800))

    def test_unpackaged_instance_ignores_mutex_contention(self):
        from app import App

        with (
            patch("app.detect_os", return_value=OSType.WINDOWS),
            patch("app.is_packaged", return_value=False),
            patch("app.acquire_instance_mutex", return_value=None),
        ):
            app = App(self._page(1280, 800))
        assert app.page.title == "Unscreen"

    def test_startup_error_renders_inline_instead_of_blank_window(self):
        from app import _render_startup_error

        page = mock_page()
        _render_startup_error(page, RuntimeError("another instance"))

        page.clean.assert_called_once()
        page.update.assert_called_once()
        root = page.add.call_args.args[0]
        texts = [c.value for c in _walk_controls(root) if isinstance(c, ft.Text)]
        assert any("could not start" in (t or "") for t in texts)
        assert any("another instance" in (t or "") for t in texts)

        buttons = [c for c in _walk_controls(root) if isinstance(c, ft.FilledButton)]
        assert len(buttons) == 1
        buttons[0].on_click(None)
        page.run_task.assert_called_once_with(page.window.destroy)

    def test_entrypoint_converts_startup_crash_into_error_screen(self):
        import asyncio

        from app import entrypoint

        page = mock_page()
        with patch("app.App", side_effect=RuntimeError("boom")):
            asyncio.run(entrypoint(page))
        page.clean.assert_called_once()
        page.update.assert_called_once()
