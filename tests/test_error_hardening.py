from unittest.mock import patch


class TestWindowsAfkHardening:
    async def test_idle_seconds_returns_zero_on_ctypes_failure(self):
        from core.collectors.windows.afk import _idle_seconds

        with patch(
            "core.collectors.windows.afk._user32.GetLastInputInfo",
            side_effect=OSError("mock"),
        ):
            result = _idle_seconds()
            assert result == 0.0

        with patch(
            "core.collectors.windows.afk._kernel32.GetTickCount64",
            side_effect=OSError("mock"),
        ):
            result = _idle_seconds()
            assert result == 0.0

    async def test_tick_returns_defaults_on_unexpected_failure(self):
        from core.collectors.windows.afk import AfkWatcher

        with patch(
            "core.collectors.windows.afk._idle_seconds",
            side_effect=RuntimeError("unexpected"),
        ):
            w = AfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "afk"
        assert tick.data["status"] == "active"
        assert tick.data["idle_seconds"] == 0.0

    async def test_tick_returns_normal_data_when_ok(self):
        from core.collectors.windows.afk import AfkWatcher

        with patch("core.collectors.windows.afk._idle_seconds", return_value=10.0):
            w = AfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "afk"
        assert tick.data["status"] == "active"
        assert tick.data["idle_seconds"] == 10.0

    async def test_tick_returns_idle_status(self):
        from core.collectors.windows.afk import AfkWatcher

        with patch("core.collectors.windows.afk._idle_seconds", return_value=120.0):
            w = AfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["status"] == "idle"

    async def test_tick_returns_away_status(self):
        from core.collectors.windows.afk import AfkWatcher

        with patch("core.collectors.windows.afk._idle_seconds", return_value=600.0):
            w = AfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["status"] == "away"

    async def test_unchanged_status_skips_tick(self):
        from core.collectors.windows.afk import AfkWatcher

        with patch("core.collectors.windows.afk._idle_seconds", return_value=10.0):
            w = AfkWatcher()
            first = await w.tick()
            assert first is not None

            for _ in range(3):
                assert await w.tick() is None, "unchanged status must be skipped"

    async def test_status_change_emits_tick(self):
        from core.collectors.windows.afk import AfkWatcher

        with patch("core.collectors.windows.afk._idle_seconds", return_value=10.0):
            w = AfkWatcher()
            assert await w.tick() is not None  # active
            assert await w.tick() is None  # still active

        with patch("core.collectors.windows.afk._idle_seconds", return_value=120.0):
            idle_tick = await w.tick()
            assert idle_tick is not None
            assert idle_tick.data["status"] == "idle"
            assert await w.tick() is None  # still idle

        with patch("core.collectors.windows.afk._idle_seconds", return_value=10.0):
            active_tick = await w.tick()
            assert active_tick is not None
            assert active_tick.data["status"] == "active"


class TestWindowsPowerHardening:
    async def test_tick_returns_defaults_on_psutil_failure(self):
        from core.collectors.windows.power import PowerWatcher

        with patch("psutil.sensors_battery", side_effect=PermissionError("mock")):
            w = PowerWatcher()
            tick = await w.tick()

        assert tick is None

    async def test_tick_returns_none_when_no_battery(self):
        from core.collectors.windows.power import PowerWatcher

        with patch("psutil.sensors_battery", return_value=None):
            w = PowerWatcher()
            tick = await w.tick()

        assert tick is None

    async def test_tick_returns_battery_data(self):
        from collections import namedtuple

        from core.collectors.windows.power import PowerWatcher

        Battery = namedtuple("Battery", ["percent", "secsleft", "power_plugged"])
        mock_battery = Battery(percent=75, secsleft=10000, power_plugged=True)
        with patch("psutil.sensors_battery", return_value=mock_battery):
            w = PowerWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["battery_pct"] == 75
        assert tick.data["charging"] is True


class TestAndroidForegroundHardening:
    async def test_day_start_ms_handles_bad_timestamp(self):
        from utils.time_utils import day_start_ms

        result = day_start_ms(-1)
        assert result == -1

        result = day_start_ms(0)
        assert isinstance(result, int)
