"""In-process event bus fanning watcher ticks out to subscribers."""

import logging
from collections.abc import Callable

from core.models import Tick

logger = logging.getLogger(__name__)


class TickBus:
    def __init__(self):
        self._subscribers: list[Callable[[Tick], None]] = []

    def subscribe(self, callback: Callable[[Tick], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Tick], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def send(self, tick: Tick) -> None:
        for cb in self._subscribers:
            try:
                cb(tick)
            except Exception:
                logger.exception("Subscriber callback failed")
