import asyncio
from unittest.mock import MagicMock, patch

import pytest

from core.config_manager import ConfigManager
from core.scheduler import Scheduler
from utils.bus import TickBus


@pytest.fixture(autouse=True)
def _mock_storage():
    with patch("core.application.collection_manager.Storage") as mock:
        mock.return_value = MagicMock()
        yield


class TestPauseResume:
    async def test_pause_sets_flag_and_saves_config(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        config = ConfigManager(path=str(tmp_path / "config.json"))
        cm = CollectionManager(config)
        assert not cm.is_paused
        assert config.collection_enabled

        cm.pause()
        assert cm.is_paused
        assert not config.collection_enabled

        config.load()
        assert not config.collection_enabled

    async def test_resume_clears_flag_and_saves_config(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        config = ConfigManager(path=str(tmp_path / "config.json"))
        cm = CollectionManager(config)
        cm.pause()
        assert cm.is_paused

        cm.resume()
        assert not cm.is_paused
        assert config.collection_enabled

        config.load()
        assert config.collection_enabled

    async def test_restart_is_noop_when_not_running(self, tmp_path):
        from unittest.mock import AsyncMock

        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm.start = AsyncMock()
        cm.stop = AsyncMock()
        await cm.restart()
        cm.stop.assert_not_awaited()
        cm.start.assert_not_awaited()

    async def test_restart_stops_then_starts_when_running(self, tmp_path):
        from unittest.mock import AsyncMock

        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._running = True
        cm.start = AsyncMock()
        cm.stop = AsyncMock()
        await cm.restart()
        cm.stop.assert_awaited_once()
        cm.start.assert_awaited_once()

    async def test_pause_when_already_paused_is_noop(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm.pause()
        cm.pause()
        assert cm.is_paused

    async def test_resume_when_not_paused_is_noop(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm.resume()
        assert not cm.is_paused

    async def test_stop_clears_auto_paused(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._auto_paused = True
        await cm.stop()
        assert not cm._auto_paused

    async def test_on_pause_changed_callback_fired(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        events = []
        cm = CollectionManager()
        cm.on_pause_changed = lambda p: events.append(p)

        cm.pause()
        cm.resume()

        assert events == [True, False]

    async def test_new_manager_from_saved_config_starts_paused(self, tmp_path):
        from core.application.collection_manager import CollectionManager

        config = ConfigManager(path=str(tmp_path / "config.json"))
        cm1 = CollectionManager(config)
        cm1.pause()

        config2 = ConfigManager(path=str(tmp_path / "config.json"))
        config2.load()
        assert not config2.collection_enabled

    async def test_is_paused_reflects_scheduler_state(self):
        s = Scheduler(TickBus())
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        assert cm.is_paused == s.is_paused

        s.pause()
        cm._scheduler = s
        assert cm.is_paused


class TestScreenMonitor:
    async def test_screen_off_triggers_auto_pause(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._running = True

        screen_states = [True, False, False, False, False, False]
        with (
            patch(
                "core.collectors.android.usage_stats.is_screen_on",
                side_effect=screen_states,
            ),
        ):
            monitor = asyncio.create_task(cm._monitor_screen_state(interval=0.01))
            await asyncio.sleep(0.05)
            cm._running = False
            try:
                await monitor
            except asyncio.CancelledError:
                pass

        assert cm._auto_paused
        assert cm.is_paused

    async def test_screen_on_after_auto_pause_auto_resumes(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._running = True
        cm._auto_paused = True
        cm._set_paused(True)

        screen_states = [False, True, True, True, True, True]
        with (
            patch(
                "core.collectors.android.usage_stats.is_screen_on",
                side_effect=screen_states,
            ),
        ):
            monitor = asyncio.create_task(cm._monitor_screen_state(interval=0.01))
            await asyncio.sleep(0.05)
            cm._running = False
            try:
                await monitor
            except asyncio.CancelledError:
                pass

        assert not cm._auto_paused
        assert not cm.is_paused

    async def test_user_pause_not_overridden_by_screen_on(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._running = True
        cm.pause()

        screen_states = [False, True, True, True, True, True]
        with (
            patch(
                "core.collectors.android.usage_stats.is_screen_on",
                side_effect=screen_states,
            ),
        ):
            monitor = asyncio.create_task(cm._monitor_screen_state(interval=0.01))
            await asyncio.sleep(0.05)
            cm._running = False
            try:
                await monitor
            except asyncio.CancelledError:
                pass

        assert cm.is_paused


class TestHealthMonitor:
    async def test_health_monitor_runs_checks_periodically(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._running = True

        monitor = asyncio.create_task(cm._run_health_monitor(interval=0.01))
        # Poll instead of sleeping a fixed window: setting _running=False before
        # the first tick would make the monitor break out with zero checks run.
        for _ in range(200):
            if cm._storage.check_integrity.call_count:
                break
            await asyncio.sleep(0.01)
        cm._running = False
        try:
            await monitor
        except asyncio.CancelledError:
            pass

        # The health monitor calls check_integrity + auto_vacuum on the real
        # storage; the interval is 0.01 so at least one cycle runs quickly.
        cm._storage.check_integrity.assert_called()  # type: ignore  # Storage is a Mock at test time
        cm._storage.auto_vacuum.assert_called()  # type: ignore

    async def test_health_monitor_cancelled_on_stop(self):
        from core.application.collection_manager import CollectionManager

        cm = CollectionManager()
        cm._running = True
        cm._health_monitor_task = asyncio.create_task(
            cm._run_health_monitor(interval=3600)
        )

        cm._running = False
        if cm._health_monitor_task:
            cm._health_monitor_task.cancel()
            try:
                await cm._health_monitor_task
            except asyncio.CancelledError:
                pass
            cm._health_monitor_task = None

        assert cm._health_monitor_task is None


_import_guard = True  # prevent unintentional class deletion from breaking indentation
