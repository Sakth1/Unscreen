import asyncio

import pytest

from core.event_bus import TickBus
from core.models import Tick, WatcherConfig
from core.scheduler import Scheduler


class _CountingWatcher:
    def __init__(self, config: WatcherConfig, result: Tick | None = None):
        self.config = config
        self.result = result or Tick(watcher=config.name)
        self.tick_count = 0

    async def tick(self) -> Tick | None:
        self.tick_count += 1
        return self.result


class _CrashingWatcher:
    def __init__(self, config: WatcherConfig):
        self.config = config

    async def tick(self) -> Tick | None:
        raise RuntimeError("simulated watcher crash")


class _VariableReturnWatcher:
    def __init__(self, config: WatcherConfig, returns: list[Tick | None]):
        self.config = config
        self._returns = returns
        self._idx = 0

    async def tick(self) -> Tick | None:
        if self._idx < len(self._returns):
            val = self._returns[self._idx]
            self._idx += 1
            return val
        return None


class TestUpdateInterval:
    def test_updates_running_watcher_config_in_place(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=2.0))
        scheduler.register(w)

        assert scheduler.update_interval("fg", 5.0) is True
        assert w.config.interval_s == 5.0

    def test_unknown_watcher_returns_false(self, scheduler):
        assert scheduler.update_interval("nope", 1.0) is False

    def test_interval_floor(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=2.0))
        scheduler.register(w)

        scheduler.update_interval("fg", 0.0)
        assert w.config.interval_s == 0.1

    def test_watcher_names_lists_registered(self, scheduler):
        scheduler.register(_CountingWatcher(WatcherConfig(name="fg", interval_s=1.0)))
        scheduler.register(_CountingWatcher(WatcherConfig(name="afk", interval_s=1.0)))
        assert scheduler.watcher_names == ["fg", "afk"]


@pytest.fixture
def bus():
    return TickBus()


@pytest.fixture
def scheduler(bus):
    return Scheduler(bus)


class TestSchedulerLifecycle:
    async def test_start_creates_task_per_watcher(self, scheduler):
        w1 = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        w2 = _CountingWatcher(WatcherConfig(name="afk", interval_s=0.01))
        scheduler.register(w1)
        scheduler.register(w2)

        await scheduler.start()
        assert scheduler._running
        assert len(scheduler._tasks) == 2

        await scheduler.stop()

    async def test_stop_cancels_tasks_and_clears_state(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)

        await scheduler.start()
        assert scheduler._running
        assert len(scheduler._tasks) == 1

        await scheduler.stop()
        assert not scheduler._running
        assert len(scheduler._tasks) == 0
        assert len(scheduler._watchers) == 0

    async def test_tasks_are_truly_done_after_stop(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.5))
        scheduler.register(w)

        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

        assert all(t.done() for t in scheduler._tasks)

    async def test_restart_after_stop(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)

        await scheduler.start()
        await asyncio.sleep(0.05)
        before = w.tick_count
        await scheduler.stop()

        scheduler.register(w)
        await scheduler.start()
        await asyncio.sleep(0.05)
        assert w.tick_count > before

        await scheduler.stop()

    async def test_watcher_crash_does_not_kill_loop(self, scheduler):
        crashing = _CrashingWatcher(WatcherConfig(name="crash", interval_s=0.01))
        working = _CountingWatcher(WatcherConfig(name="working", interval_s=0.01))
        scheduler.register(crashing)
        scheduler.register(working)

        await scheduler.start()
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if working.tick_count > 0:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()

        assert working.tick_count > 0

    async def test_watcher_crash_does_not_stop_own_loop(self, scheduler):
        crash_count = 0

        class _CrashThenRecover:
            def __init__(self):
                self.config = WatcherConfig(name="crash_recover", interval_s=0.01)

            async def tick(self) -> Tick | None:
                nonlocal crash_count
                crash_count += 1
                if crash_count <= 3:
                    raise RuntimeError("transient failure")
                return Tick(watcher="crash_recover")

        w = _CrashThenRecover()
        scheduler.register(w)

        await scheduler.start()
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if crash_count > 3:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()

        assert crash_count > 3

    async def test_tick_delivered_to_bus(self, scheduler, bus):
        received = []
        bus.subscribe(lambda t: received.append(t.watcher))

        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

        assert "fg" in received
        assert len(received) > 0

    async def test_multiple_watchers_independent_intervals(self, scheduler):
        fast = _CountingWatcher(WatcherConfig(name="fast", interval_s=0.01))
        slow = _CountingWatcher(WatcherConfig(name="slow", interval_s=0.05))
        scheduler.register(fast)
        scheduler.register(slow)

        await scheduler.start()
        await asyncio.sleep(0.15)
        await scheduler.stop()

        assert fast.tick_count >= 5
        assert slow.tick_count >= 1
        assert fast.tick_count > slow.tick_count

    async def test_watcher_returns_none_does_not_send_to_bus(self, scheduler, bus):
        received = []
        bus.subscribe(lambda t: received.append(t.watcher))

        w = _VariableReturnWatcher(
            WatcherConfig(name="fg", interval_s=0.01),
            returns=[None, Tick(watcher="fg"), None, Tick(watcher="fg")],
        )
        scheduler.register(w)
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

        assert len(received) < w._idx

    async def test_stop_without_start(self, scheduler):
        await scheduler.stop()
        assert not scheduler._running
        assert len(scheduler._tasks) == 0


class TestSchedulerPauseResume:
    async def test_pause_stops_tick_delivery(self, scheduler, bus):
        received = []
        bus.subscribe(lambda t: received.append(t.watcher))

        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)

        await scheduler.start()
        await asyncio.sleep(0.05)
        before = len(received)

        scheduler.pause()
        assert scheduler.is_paused
        await asyncio.sleep(0.05)
        after_pause = len(received)

        assert after_pause == before

        await scheduler.stop()

    async def test_resume_resumes_tick_delivery(self, scheduler, bus):
        received = []
        bus.subscribe(lambda t: received.append(t.watcher))

        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)

        await scheduler.start()
        await asyncio.sleep(0.05)
        scheduler.pause()
        await asyncio.sleep(0.05)

        scheduler.resume()
        assert not scheduler.is_paused
        await asyncio.sleep(0.05)
        after_resume = len(received)

        assert after_resume > 0

        await scheduler.stop()

    async def test_pause_does_not_cancel_tasks(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)

        await scheduler.start()
        tasks_before = scheduler._tasks.copy()

        scheduler.pause()
        await asyncio.sleep(0.05)

        assert len(scheduler._tasks) == len(tasks_before)
        assert not any(t.done() for t in scheduler._tasks)

        await scheduler.stop()

    async def test_pause_twice_is_noop(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)
        await scheduler.start()

        scheduler.pause()
        tick_count_before = w.tick_count
        scheduler.pause()

        assert scheduler.is_paused
        assert w.tick_count == tick_count_before

        await scheduler.stop()

    async def test_resume_without_pause_is_noop(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)
        await scheduler.start()

        scheduler.resume()
        assert not scheduler.is_paused

        await scheduler.stop()

    async def test_stop_while_paused_clears_state(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)
        await scheduler.start()
        scheduler.pause()

        await scheduler.stop()
        assert not scheduler._running
        assert not scheduler.is_paused
        assert len(scheduler._tasks) == 0

    async def test_watcher_count_increments_while_running_not_paused(self, scheduler):
        w = _CountingWatcher(WatcherConfig(name="fg", interval_s=0.01))
        scheduler.register(w)

        await scheduler.start()
        await asyncio.sleep(0.05)
        count_while_running = w.tick_count

        scheduler.pause()
        await asyncio.sleep(0.05)
        count_while_paused = w.tick_count

        assert count_while_paused == count_while_running
        assert count_while_running > 0

        await scheduler.stop()


class TestCircuitBreaker:
    async def _wait_for_paused(self, scheduler, name, timeout=5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if name in scheduler.paused_watchers:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"watcher {name!r} was not paused within {timeout}s")

    async def test_pauses_after_threshold_failures(self, scheduler):
        class _AlwaysCrash:
            def __init__(self):
                self.config = WatcherConfig(name="crashy", interval_s=0.01)

            async def tick(self):
                raise RuntimeError("boom")

        w = _AlwaysCrash()
        scheduler.register(w)
        await scheduler.start()
        await self._wait_for_paused(scheduler, "crashy")

        assert "crashy" in scheduler.paused_watchers

        await scheduler.stop()

    async def test_does_not_pause_before_threshold(self, scheduler):
        class _CrashFew:
            def __init__(self):
                self.count = 0
                self.config = WatcherConfig(name="few", interval_s=0.01)

            async def tick(self):
                self.count += 1
                if self.count <= 3:
                    raise RuntimeError("transient")
                return Tick(watcher="few")

        w = _CrashFew()
        scheduler.register(w)
        await scheduler.start()
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if w.count > 3:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()

        assert "few" not in scheduler.paused_watchers
        assert scheduler.failure_counts.get("few", 0) == 0

    async def test_other_watchers_unaffected(self, scheduler):
        class _AlwaysCrash:
            def __init__(self):
                self.config = WatcherConfig(name="crashy", interval_s=0.01)

            async def tick(self):
                raise RuntimeError("boom")

        crashing = _AlwaysCrash()
        working = _CountingWatcher(WatcherConfig(name="working", interval_s=0.01))
        scheduler.register(crashing)
        scheduler.register(working)
        await scheduler.start()
        await self._wait_for_paused(scheduler, "crashy")

        assert "crashy" in scheduler.paused_watchers
        assert "working" not in scheduler.paused_watchers
        assert working.tick_count > 0

        await scheduler.stop()

    async def test_resume_watcher_manually(self, scheduler):
        class _AlwaysCrash:
            def __init__(self):
                self.config = WatcherConfig(name="crashy", interval_s=0.01)

            async def tick(self):
                raise RuntimeError("boom")

        w = _AlwaysCrash()
        scheduler.register(w)
        await scheduler.start()
        await self._wait_for_paused(scheduler, "crashy")
        assert "crashy" in scheduler.paused_watchers

        scheduler.resume_watcher("crashy")
        assert "crashy" not in scheduler.paused_watchers
        assert "crashy" not in scheduler.failure_counts

        await scheduler.stop()

    async def test_failure_counter_resets_on_success(self, scheduler):
        class _CrashThenSucceed:
            def __init__(self):
                self.count = 0
                self.config = WatcherConfig(name="recover", interval_s=0.01)

            async def tick(self):
                self.count += 1
                if self.count <= 5:
                    raise RuntimeError("transient")
                return Tick(watcher="recover")

        w = _CrashThenSucceed()
        scheduler.register(w)
        await scheduler.start()
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if w.count > 5:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()

        assert scheduler.failure_counts.get("recover", 0) == 0
        assert "recover" not in scheduler.paused_watchers
        assert w.count > 5

    async def test_log_escalation(self, scheduler, caplog):
        caplog.set_level(10)

        class _AlwaysCrash:
            def __init__(self):
                self.config = WatcherConfig(name="noisy", interval_s=0.01)

            async def tick(self):
                raise RuntimeError("boom")

        w = _AlwaysCrash()
        scheduler.register(w)
        await scheduler.start()
        await self._wait_for_paused(scheduler, "noisy")
        await scheduler.stop()

        records = [r for r in caplog.records if r.name.startswith("core.scheduler")]
        noisy_records = [r for r in records if "noisy" in r.getMessage()]

        levels = {r.levelno for r in noisy_records}
        assert 10 in levels  # DEBUG
        assert 30 in levels  # WARNING
        assert 40 in levels  # ERROR
        assert 50 in levels  # CRITICAL
