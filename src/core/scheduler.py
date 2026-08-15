import asyncio
import logging

from core.collectors.base import Watcher
from core.event_bus import TickBus

logger = logging.getLogger(__name__)

_CIRCUIT_BREAKER_THRESHOLD = 10
_SELF_HEAL_INTERVAL = 30


class Scheduler:
    def __init__(self, bus: TickBus):
        self._bus = bus
        self._watchers: list[Watcher] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._paused = False
        self._failures: dict[str, int] = {}
        self._paused_watchers: set[str] = set()
        self._skip_counts: dict[str, int] = {}

    def register(self, watcher: Watcher) -> None:
        self._watchers.append(watcher)

    @property
    def watcher_names(self) -> list[str]:
        return [w.config.name for w in self._watchers]

    def update_interval(self, name: str, interval_s: float) -> bool:
        """Mutate a running watcher's poll interval in place.

        ``_run_loop`` re-reads ``cfg.interval_s`` on every iteration, so
        the change takes effect on the next sleep. Returns ``True`` when a
        watcher with that name was found.
        """
        for w in self._watchers:
            cfg = getattr(w, "config", None)
            if cfg is None or cfg.name != name:
                continue
            cfg.interval_s = max(0.1, float(interval_s))
            logger.info("Watcher %s interval updated to %ss", name, cfg.interval_s)
            return True
        logger.warning("No watcher named %s to update", name)
        return False

    async def start(self) -> None:
        self._running = True
        for w in self._watchers:
            task = asyncio.create_task(self._run_loop(w))
            self._tasks.append(task)

    def pause(self) -> None:
        self._paused = True
        logger.info("Scheduler paused")

    def resume(self) -> None:
        self._paused = False
        logger.info("Scheduler resumed")

    def resume_watcher(self, name: str) -> None:
        self._paused_watchers.discard(name)
        self._failures.pop(name, None)
        self._skip_counts.pop(name, None)
        logger.info("Watcher %s manually resumed", name)

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def paused_watchers(self) -> frozenset[str]:
        return frozenset(self._paused_watchers)

    @property
    def failure_counts(self) -> dict[str, int]:
        return dict(self._failures)

    def _escalate(self, name: str, count: int) -> None:
        if count >= _CIRCUIT_BREAKER_THRESHOLD:
            self._paused_watchers.add(name)
            logger.critical(
                "Watcher %s paused after %d consecutive failures", name, count
            )
        elif count >= 7:
            logger.error(
                "Watcher %s failed (%d/%d)", name, count, _CIRCUIT_BREAKER_THRESHOLD
            )
        elif count >= 4:
            logger.warning(
                "Watcher %s failed (%d/%d)", name, count, _CIRCUIT_BREAKER_THRESHOLD
            )
        else:
            logger.debug(
                "Watcher %s failed (%d/%d)", name, count, _CIRCUIT_BREAKER_THRESHOLD
            )

    async def _run_loop(self, watcher: Watcher) -> None:
        cfg = watcher.config
        name = cfg.name
        logger.info("Starting watcher %s (every %ss)", name, cfg.interval_s)
        while self._running:
            should_tick = not self._paused
            if should_tick and name in self._paused_watchers:
                skips = self._skip_counts.get(name, 0) + 1
                self._skip_counts[name] = skips
                should_tick = skips >= _SELF_HEAL_INTERVAL

            if should_tick:
                try:
                    tick = await watcher.tick()
                    if tick is not None:
                        await self._bus.send(tick)
                    if name in self._paused_watchers:
                        logger.info("Watcher %s recovered, resuming", name)
                        self._paused_watchers.discard(name)
                        self._skip_counts.pop(name, None)
                    self._failures[name] = 0
                except Exception:
                    self._skip_counts.pop(name, None)
                    count = self._failures.get(name, 0) + 1
                    self._failures[name] = count
                    self._escalate(name, count)

            await asyncio.sleep(cfg.interval_s)

    async def stop(self) -> None:
        self._running = False
        self._paused = False
        self._paused_watchers.clear()
        self._failures.clear()
        self._skip_counts.clear()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._watchers.clear()
