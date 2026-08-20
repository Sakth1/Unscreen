from unittest.mock import patch

_MS_PER_S = 1000
_60S = 60 * _MS_PER_S


class TestInitialization:
    BASE_TIME = 1_700_000_000_000

    async def test_first_tick_initializes_and_returns_none(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
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

    async def test_first_tick_initializes_from_events(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME - 5000,
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
        assert w._current_app == "com.test.app"
        assert w._initialized is True

    async def test_first_tick_falls_back_to_stats(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
            patch("core.collectors.android.foreground.query_usage_stats") as mock_stats,
        ):
            mock_stats.return_value = {
                "com.top.app": {
                    "package_name": "com.top.app",
                    "total_time_foreground_ms": 500000,
                    "last_time_used_ms": 100000,
                    "first_time_used_ms": 50000,
                    "app_name": "Top App",
                },
                "com.other.app": {
                    "package_name": "com.other.app",
                    "total_time_foreground_ms": 100000,
                    "last_time_used_ms": 50000,
                    "first_time_used_ms": 10000,
                    "app_name": "Other App",
                },
            }
            w = AndroidForegroundWatcher()
            tick = await w.tick()

        assert tick is not None
        assert tick.data["package"] == "com.top.app"
        assert w._current_app == "com.top.app"
        assert w._initialized is True


class TestTransition:
    BASE_TIME = 1_700_000_000_000

    async def test_same_app_no_transition(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME - 5000,
                    },
                ],
            ),
        ):
            w = AndroidForegroundWatcher()
            await w.tick()

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME + 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is None

    async def test_app_change_emits_transition(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.app1",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME - 5000,
                    },
                ],
            ),
        ):
            w = AndroidForegroundWatcher()
            await w.tick()

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.app2",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME + 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.watcher == "android_foreground"
        assert tick.data["package"] == "com.app2"
        assert "app_name" in tick.data
        assert w._current_app == "com.app2"

    async def test_transition_data_has_correct_keys(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = "com.old.app"
        w._last_tick_ms = self.BASE_TIME
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.new.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME + 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.data.keys() == {"package", "app_name"}
        assert "durations" not in tick.data
        assert "source" not in tick.data


class TestF7aResolution:
    """Harden foreground resolution (F7a): PAUSED events name the active
    app, a wide fallback window resolves apps after missed ticks, RESUMED
    beats PAUSED, and a screen-off startup never fabricates an app."""

    BASE_TIME = 1_700_000_000_000

    async def test_paused_event_names_active_app(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = "com.other.app"
        w._last_tick_ms = self.BASE_TIME - 1000
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.leaving.app",
                        "event_type": 2,  # PAUSED
                        "time_stamp_ms": self.BASE_TIME - 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.data["package"] == "com.leaving.app"
        assert w._current_app == "com.leaving.app"

    async def test_resumed_preferred_over_paused(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = None
        w._last_tick_ms = self.BASE_TIME - 1000
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.paused.app",
                        "event_type": 2,
                        "time_stamp_ms": self.BASE_TIME - 1000,
                    },
                    {
                        "package_name": "com.resumed.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME - 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.data["package"] == "com.resumed.app"

    async def test_wide_fallback_resolves_event_after_missed_ticks(self):
        """No events in the 2-minute overlap window, but a RESUMED exists
        5 minutes ago — the wide fallback resolves it instead of the
        inaccurate day-level top app."""
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = None
        w._last_tick_ms = self.BASE_TIME - 1000
        w._initialized = True

        def windowed(begin_ms, end_ms):
            # Overlap window (last ~2 min) is empty; the wide window
            # (last 10 min) contains a RESUMED from 5 minutes ago.
            if begin_ms <= self.BASE_TIME - 600_000:
                return [
                    {
                        "package_name": "com.old.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME - 300_000,
                    },
                ]
            return []

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                side_effect=windowed,
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.data["package"] == "com.old.app"
        assert w._current_app == "com.old.app"

    async def test_screen_off_startup_stays_pending(self):
        """F7a: initializing while the screen is off must not fabricate a
        foreground app — stay pending and retry on the next tick."""
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.stale.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME - 5000,
                    },
                ],
            ) as mock_events,
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch(
                "core.collectors.android.foreground.is_screen_on", return_value=False
            ),
        ):
            w = AndroidForegroundWatcher()
            tick = await w.tick()

        assert tick is None
        assert w._initialized is False
        assert not mock_events.called

    async def test_pending_initialization_retries_on_next_tick(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            w = AndroidForegroundWatcher()
            assert await w.tick() is None
            assert w._initialized is False

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.now.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME + 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.data["package"] == "com.now.app"
        assert w._initialized is True


class TestScreenAndIdle:
    BASE_TIME = 1_700_000_000_000

    async def test_stale_app_when_screen_on_and_no_events(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = "com.last.app"
        w._last_tick_ms = self.BASE_TIME
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is None
        assert w._current_app == "com.last.app"

    async def test_idle_when_screen_off_and_no_events(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = "com.last.app"
        w._last_tick_ms = self.BASE_TIME
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch(
                "core.collectors.android.foreground.is_screen_on", return_value=False
            ),
        ):
            tick = await w.tick()

        assert tick is None
        assert w._current_app is None

    async def test_resume_after_idle_emits_transition(self):
        """F5: idle clears the current app so the resumed app opens a new
        session instead of silently extending the old one."""
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = None
        w._last_tick_ms = self.BASE_TIME
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events",
                return_value=[
                    {
                        "package_name": "com.test.app",
                        "event_type": 1,
                        "time_stamp_ms": self.BASE_TIME + 5000,
                    },
                ],
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is not None
        assert tick.data["package"] == "com.test.app"
        assert w._current_app == "com.test.app"

    async def test_idle_when_no_previous_app_and_no_events(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = None
        w._last_tick_ms = self.BASE_TIME
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=True,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
            patch(
                "core.collectors.android.foreground.query_usage_events", return_value=[]
            ),
            patch(
                "core.collectors.android.foreground.query_usage_stats", return_value={}
            ),
            patch("core.collectors.android.foreground.is_screen_on", return_value=True),
        ):
            tick = await w.tick()

        assert tick is None


class TestPermission:
    BASE_TIME = 1_700_000_000_000

    async def test_pauses_on_permission_loss(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=False,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
            ),
        ):
            w = AndroidForegroundWatcher()
            tick1 = await w.tick()
            assert tick1 is None
            assert w._permission_lost

            tick2 = await w.tick()
            assert tick2 is None

    async def test_resumes_on_permission_restored(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=False,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME,
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
                return_value=self.BASE_TIME + 100_000,
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

    async def test_clears_state_on_permission_loss(self):
        from core.collectors.android.foreground import AndroidForegroundWatcher

        w = AndroidForegroundWatcher()
        w._current_app = "com.test.app"
        w._last_tick_ms = self.BASE_TIME
        w._initialized = True

        with (
            patch(
                "core.collectors.android.foreground.check_usage_stats_permission",
                return_value=False,
            ),
            patch(
                "core.collectors.android.foreground.get_current_time_ms",
                return_value=self.BASE_TIME + 10_000,
            ),
        ):
            tick = await w.tick()

        assert tick is None
        assert w._current_app is None
        assert w._last_tick_ms is None
        assert w._initialized is False
