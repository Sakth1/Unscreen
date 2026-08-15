"""Core event-domain contracts shared across the collection pipeline.

These are the runtime models of the event pipeline (watchers → bus → storage
→ UI). Pure dataclasses with no dependencies on any layer.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Tick:
    watcher: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatcherConfig:
    name: str = ""
    interval_s: float = 1.0
    enabled: bool = True


@dataclass
class RawEvent:
    id: int = 0
    device_id: str = ""
    platform: str = ""
    event_type: str = ""
    timestamp: int = 0
    collected_at: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
