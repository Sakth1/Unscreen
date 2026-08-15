"""Smoke tests verifying the test infrastructure works."""


class TestFixturesAvailable:
    def test_in_memory_db_creates_tables(self, in_memory_db):
        tables = in_memory_db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r[0] for r in tables]
        assert "devices" in names
        assert "raw_events" in names
        assert "sessions" in names

    def test_device_registered(self, in_memory_db):
        row = in_memory_db._conn.execute(
            "SELECT device_id, is_current FROM devices LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[1] == 1

    def test_make_tick_creates_ticks(self, make_tick):
        t1 = make_tick(watcher="afk", data={"status": "active"})
        assert t1.watcher == "afk"
        assert t1.data == {"status": "active"}

    def test_mock_tick_bus(self, mock_tick_bus):
        import asyncio

        from core.models import Tick

        tick = Tick()
        asyncio.run(mock_tick_bus.send(tick))
        mock_tick_bus.send.assert_called_once_with(tick)

    def test_mock_watcher(self, mock_watcher, make_tick):
        import asyncio

        tick = make_tick()
        w = mock_watcher(tick_result=tick, name="test", interval_s=1.0)
        assert w.config.name == "test"
        assert w.config.interval_s == 1.0
        result = asyncio.run(w.tick())
        assert result is tick

    def test_mock_watcher_returns_none(self, mock_watcher):
        import asyncio

        w = mock_watcher(tick_result=None, name="silent")
        result = asyncio.run(w.tick())
        assert result is None

    def test_in_memory_db_stores_events(self, in_memory_db, make_tick):
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1000,
            payload={"app": "Code.exe"},
            source="foreground",
        )
        in_memory_db.write_event(
            event_type="idle_transition",
            timestamp=1100,
            payload={"status": "active"},
            source="afk",
        )
        in_memory_db.write_event(
            event_type="foreground_transition",
            timestamp=1200,
            payload={"app": "Code.exe"},
            source="foreground",
        )
        events = in_memory_db.get_raw_events()
        assert len(events) == 3
        assert events[0]["event_type"] == "foreground_transition"
        assert events[1]["event_type"] == "idle_transition"
