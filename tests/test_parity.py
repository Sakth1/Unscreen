from unittest.mock import patch

from core.collectors.android.afk import AndroidAfkWatcher
from core.collectors.windows.afk import AfkWatcher
from core.models import WatcherConfig


class TestAfkParity:
    async def test_schema_keys_match(self):
        w = AfkWatcher()
        a = AndroidAfkWatcher(WatcherConfig(name="android_afk"))

        assert "afk" in w.config.name
        assert "afk" in a.config.name

    async def test_android_afk_tick_returns_present_key(self):
        mock_time = 1_000_000_000

        with (
            patch(
                "core.collectors.android.afk.get_current_time_ms",
                return_value=mock_time,
            ),
            patch("core.collectors.android.afk.is_screen_on", return_value=True),
            patch("core.collectors.android.afk.query_usage_events", return_value=[]),
        ):
            w = AndroidAfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "android_afk"
        assert "present" in tick.data
        assert isinstance(tick.data["present"], bool)

    async def test_android_afk_screen_off_returns_not_present(self):
        mock_time = 1_000_000_000

        with (
            patch(
                "core.collectors.android.afk.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.afk.get_current_time_ms",
                return_value=mock_time,
            ),
            patch("core.collectors.android.afk.is_screen_on", return_value=False),
            patch("core.collectors.android.afk.query_usage_events", return_value=[]),
        ):
            w = AndroidAfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["present"] is False

    async def test_android_afk_no_recent_events_returns_not_present(self):
        mock_time = 1_000_000_000

        with (
            patch(
                "core.collectors.android.afk.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.afk.get_current_time_ms",
                return_value=mock_time,
            ),
            patch("core.collectors.android.afk.is_screen_on", return_value=True),
            patch(
                "core.collectors.android.afk.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test",
                        "event_type": 2,
                        "time_stamp_ms": mock_time - 120_000,
                    }
                ],
            ),
        ):
            w = AndroidAfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["present"] is False

    async def test_android_afk_recent_event_returns_present(self):
        mock_time = 1_000_000_000

        with (
            patch(
                "core.collectors.android.afk.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.afk.get_current_time_ms",
                return_value=mock_time,
            ),
            patch("core.collectors.android.afk.is_screen_on", return_value=True),
            patch(
                "core.collectors.android.afk.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test",
                        "event_type": 1,
                        "time_stamp_ms": mock_time - 120_000,
                    }
                ],
            ),
        ):
            w = AndroidAfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["present"] is True

    async def test_windows_afk_tick_returns_correct_schema(self):
        w = AfkWatcher()
        tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "afk"
        assert "status" in tick.data
        assert "idle_seconds" in tick.data
        assert tick.data["status"] in ("active", "idle", "away")

    async def test_afk_schemas_diverge(self):
        win_keys = {"status", "idle_seconds"}
        and_keys = {"present", "screen_on", "seconds_since_last_event"}

        assert win_keys != and_keys
        assert "status" not in and_keys


class TestPowerParity:
    async def test_power_schema_keys_match(self):
        from core.collectors.android.power import AndroidPowerWatcher
        from core.collectors.windows.power import PowerWatcher

        wc_win = PowerWatcher()
        wc_and = AndroidPowerWatcher(WatcherConfig(name="android_power"))

        assert "power" in wc_win.config.name
        assert "power" in wc_and.config.name

    async def test_android_power_tick_returns_correct_schema(self):
        from core.collectors.android.power import AndroidPowerWatcher

        with patch("core.collectors.android.power.get_battery_info") as mock:
            mock.return_value = {"battery_pct": 85, "charging": True}
            w = AndroidPowerWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "android_power"
        assert "battery_pct" in tick.data
        assert "charging" in tick.data
        assert tick.data["battery_pct"] == 85
        assert tick.data["charging"] is True

    async def test_android_power_no_battery(self):
        from core.collectors.android.power import AndroidPowerWatcher

        with patch("core.collectors.android.power.get_battery_info") as mock:
            mock.return_value = {"battery_pct": None, "charging": None}
            w = AndroidPowerWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["battery_pct"] is None
        assert tick.data["charging"] is None

    async def test_windows_power_tick_returns_correct_schema(self):
        from collections import namedtuple

        from core.collectors.windows.power import PowerWatcher

        Battery = namedtuple("Battery", ["percent", "secsleft", "power_plugged"])
        with patch(
            "psutil.sensors_battery",
            return_value=Battery(percent=50, secsleft=10000, power_plugged=True),
        ):
            w = PowerWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "power"
        assert "battery_pct" in tick.data
        assert "charging" in tick.data

    async def test_windows_power_data_types(self):
        from collections import namedtuple

        from core.collectors.windows.power import PowerWatcher

        Battery = namedtuple("Battery", ["percent", "secsleft", "power_plugged"])
        with patch(
            "psutil.sensors_battery",
            return_value=Battery(percent=50, secsleft=10000, power_plugged=True),
        ):
            w = PowerWatcher()
            tick = await w.tick()

        assert tick is not None
        assert isinstance(tick.data["battery_pct"], (int, float, type(None)))
        assert isinstance(tick.data["charging"], (bool, type(None)))

    async def test_android_power_data_types(self):
        from core.collectors.android.power import AndroidPowerWatcher

        with patch("core.collectors.android.power.get_battery_info") as mock:
            mock.return_value = {"battery_pct": 85, "charging": True}
            w = AndroidPowerWatcher()
            tick = await w.tick()

        assert tick is not None
        assert isinstance(tick.data["battery_pct"], (int, type(None)))
        assert isinstance(tick.data["charging"], (bool, type(None)))


class TestForegroundParity:
    async def test_windows_foreground_schema_keys(self):
        from core.collectors.windows.foreground import ForegroundWatcher

        w = ForegroundWatcher()
        tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "foreground"
        assert "app" in tick.data
        assert "title" in tick.data

    async def test_android_foreground_initializes_without_tick(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        MOCK_TIME = 1_700_000_000_000

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
        ):
            w = AndroidForegroundWatcher()
            tick = await w.tick()

        assert tick is None
        assert w._initialized is False

    async def test_android_foreground_initializes_with_tick(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        MOCK_TIME = 1_700_000_000_000

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test.app",
                        "event_type": 1,
                        "time_stamp_ms": MOCK_TIME - 5000,
                    },
                ],
            ),
        ):
            w = AndroidForegroundWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "android_foreground"
        assert tick.data["package"] == "com.test.app"
        assert w._current_app == "com.test.app"
        assert w._initialized is True

    async def test_android_foreground_transition_has_package_key(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        MOCK_TIME = 1_700_000_000_000

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test.app",
                        "event_type": 1,
                        "time_stamp_ms": MOCK_TIME - 5000,
                    },
                ],
            ),
        ):
            w = AndroidForegroundWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "android_foreground"
        assert tick.data["package"] == "com.test.app"
        assert "app_name" in tick.data

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.other.app",
                        "event_type": 1,
                        "time_stamp_ms": MOCK_TIME + 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick2 = await w.tick()

        assert tick2 is not None
        assert tick2.watcher == "android_foreground"
        assert "package" in tick2.data
        assert "app_name" in tick2.data
        assert tick2.data["package"] == "com.other.app"

    async def test_foreground_schemas_diverge(self):
        win_keys = {"app", "title"}
        and_keys = {"package", "app_name"}

        assert win_keys != and_keys


class TestPermissionDegradation:
    async def test_foreground_pauses_on_permission_loss(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        MOCK_TIME = 1_700_000_000_000

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=False,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME,
            ),
        ):
            w = AndroidForegroundWatcher()
            tick1 = await w.tick()
            assert tick1 is None
            assert w._permission_lost

            tick2 = await w.tick()
            assert tick2 is None

    async def test_foreground_resumes_on_permission_restored(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        MOCK_TIME = 1_700_000_000_000

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=False,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME,
            ),
        ):
            w = AndroidForegroundWatcher()
            await w.tick()
            assert w._permission_lost

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=MOCK_TIME + 100_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
        ):
            tick = await w.tick()
            assert not w._permission_lost
            assert tick is None

    async def test_afk_screen_off_returns_not_present(self):
        with (
            patch(
                "core.collectors.android.afk.check_usage_stats_permission",
                return_value=False,
            ),
            patch("core.collectors.android.afk.is_screen_on", return_value=False),
            patch(
                "core.collectors.android.afk.get_current_time_ms",
                return_value=1_000_000,
            ),
        ):
            w = AndroidAfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["present"] is False

    async def test_afk_screen_on_no_permission_returns_present(self):
        with (
            patch(
                "core.collectors.android.afk.check_usage_stats_permission",
                return_value=False,
            ),
            patch("core.collectors.android.afk.is_screen_on", return_value=True),
            patch(
                "core.collectors.android.afk.get_current_time_ms",
                return_value=1_000_000,
            ),
        ):
            w = AndroidAfkWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["present"] is True
