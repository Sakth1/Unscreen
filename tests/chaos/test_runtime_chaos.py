"""Runtime chaos: the real collection pipeline under hostile conditions.

Real ``CollectionManager`` + ``Scheduler`` + ``TickBus`` + ``Storage`` behind
watchers that raise, return garbage, hang or go silent; configs corrupted
mid-run; random start/stop/pause/resume/restart churn; fuzzed payload rows
hammered through every query; concurrent writers on one database. Any task
that dies silently, any query that raises on stored garbage, any unclean
shutdown is collected and reported.

Run: ``uv run pytest tests/chaos -m chaos``
"""

from __future__ import annotations

import asyncio
import math
import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from chaos_helpers import ChaosRun, TaskExceptionCollector

from core.application.collection_manager import CollectionManager
from core.config_manager import ConfigManager
from core.models import Tick, WatcherConfig
from utils.platform import OSType, detect_os

pytestmark = pytest.mark.chaos


class _PassiveWatcher:
    """Emits a real tick every poll — the happy path baseline."""

    def __init__(self, name: str, interval_s: float = 0.01):
        self.config = WatcherConfig(name=name, interval_s=interval_s)
        self.ticks = 0

    async def tick(self) -> Tick:
        self.ticks += 1
        return Tick(watcher=self.config.name, data={"app": "Chaos.exe"})


class _BoomWatcher:
    """Raises on every poll — must trip the circuit breaker, not die."""

    def __init__(self, name: str, interval_s: float = 0.01):
        self.config = WatcherConfig(name=name, interval_s=interval_s)

    async def tick(self) -> Tick:
        raise RuntimeError("boom")


class _GarbageWatcher:
    """Returns malformed ticks — must be contained by the bus, not crash it."""

    def __init__(self, name: str, interval_s: float = 0.01):
        self.config = WatcherConfig(name=name, interval_s=interval_s)
        self._n = 0

    async def tick(self) -> Tick:
        self._n += 1
        payloads = [
            None,
            42,
            "garbage",
            {"intervals": "not-a-list"},
            {"status": "idle"},
            {"app": "Code.exe"},
        ]
        return Tick(
            watcher=self.config.name,
            data=payloads[self._n % len(payloads)],
            timestamp=datetime.now(timezone.utc),
        )


class _NoneWatcher:
    """Goes silent — the scheduler must just wait for the next poll."""

    def __init__(self, name: str, interval_s: float = 0.01):
        self.config = WatcherConfig(name=name, interval_s=interval_s)

    async def tick(self) -> None:
        return None


class _HangWatcher:
    """Never returns — only a stop() may interrupt it."""

    def __init__(self, name: str, interval_s: float = 0.01):
        self.config = WatcherConfig(name=name, interval_s=interval_s)

    async def tick(self) -> Tick:
        await asyncio.sleep(60)
        return Tick(watcher=self.config.name, data={})


class _ConfigRuntime:
    """Builds watchers from the config exactly like the real runtimes do."""

    def __init__(self, config: ConfigManager, watchers: list | None = None):
        self._config = config
        self._watchers = watchers
        self.shutdown_calls = 0

    def create_watchers(self) -> list:
        if self._watchers is not None:
            return list(self._watchers)
        names = list(self._config.watchers_enabled) or ["foreground"]
        return [
            _PassiveWatcher(name, self._config.get_interval(name, 0.05))
            for name in names
        ]

    def shutdown(self) -> None:
        self.shutdown_calls += 1


async def _manager(tmp_path, config=None) -> CollectionManager:
    config = config or ConfigManager(path=str(tmp_path / "config.json"))
    return CollectionManager(config)


async def test_hostile_watchers_do_not_kill_the_pipeline(tmp_path) -> None:
    loop = asyncio.get_running_loop()
    collector = TaskExceptionCollector()
    loop.set_exception_handler(collector)

    cm = await _manager(tmp_path)
    runtime = _ConfigRuntime(
        cm.config,
        watchers=[
            _BoomWatcher("foreground"),
            _GarbageWatcher("afk"),
            _NoneWatcher("power"),
            _HangWatcher("hang"),
        ],
    )
    with (
        patch(
            "core.application.collection_manager.detect_os",
            return_value=OSType.WINDOWS,
        ),
        patch.object(cm, "_create_runtime", return_value=runtime),
    ):
        await cm.start()

    try:
        await asyncio.sleep(0.4)
        assert cm.is_running, "pipeline died under hostile watchers"
        assert len(cm._scheduler._tasks) >= 4, "watchers were dropped"
        assert len(cm.storage.get_raw_events()) >= 1, "garbage ticks never landed"
        assert collector.events == [], f"tasks died: {collector.events}"
    finally:
        await cm.stop()

    assert runtime.shutdown_calls == 1
    assert cm._scheduler._tasks == []
    assert collector.events == [], f"tasks died: {collector.events}"


async def test_management_state_churn(tmp_path) -> None:
    """Random start/stop/pause/resume/restart with mid-run config corruption."""
    loop = asyncio.get_running_loop()
    collector = TaskExceptionCollector()
    loop.set_exception_handler(collector)

    config = ConfigManager(path=str(tmp_path / "config.json"))
    cm = await _manager(tmp_path, config)
    runtime = _ConfigRuntime(config)
    run = ChaosRun(steps=40)

    mutations = [
        lambda: setattr(config, "tick_interval_overrides", {"foreground": 0}),
        lambda: setattr(config, "tick_interval_overrides", {"foreground": -5}),
        lambda: setattr(config, "tick_interval_overrides", {"foreground": "abc"}),
        lambda: setattr(config, "tick_interval_overrides", {"foreground": math.nan}),
        lambda: setattr(config, "tick_interval_overrides", {"foreground": 1e9}),
        lambda: setattr(config, "watchers_enabled", "foreground"),
        lambda: setattr(config, "afk_idle_threshold_s", -1),
    ]
    actions = ["start", "stop", "pause", "resume", "restart", "mutate"]

    with (
        patch(
            "core.application.collection_manager.detect_os",
            return_value=OSType.WINDOWS,
        ),
        patch.object(cm, "_create_runtime", return_value=runtime),
    ):
        for _ in range(run.steps):
            action = run.rng.choice(actions)
            try:
                if action == "start":
                    await cm.start()
                elif action == "stop":
                    await cm.stop()
                elif action == "pause":
                    cm.pause()
                elif action == "resume":
                    cm.resume()
                elif action == "restart":
                    await cm.restart()
                else:
                    run.rng.choice(mutations)()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(action, exc)
            await asyncio.sleep(0.01)

        await cm.stop()

    run.fail_if_any()
    assert collector.events == [], f"tasks died during churn: {collector.events}"


async def test_real_windows_watchers_soak(tmp_path) -> None:
    """The real Foreground/Afk/Power watchers against the real OS, ~6s."""
    if detect_os() is not OSType.WINDOWS:
        pytest.skip("real watchers need Windows APIs")

    loop = asyncio.get_running_loop()
    collector = TaskExceptionCollector()
    loop.set_exception_handler(collector)

    config = ConfigManager(path=str(tmp_path / "config.json"))
    config.watchers_enabled = ["foreground", "afk", "power"]
    cm = await _manager(tmp_path, config)
    run = ChaosRun()

    with patch(
        "core.application.collection_manager.detect_os",
        return_value=OSType.WINDOWS,
    ):
        await cm.start()

    try:
        await asyncio.sleep(6.0)
        events = cm.storage.get_raw_events()
        run.log(f"real watchers produced {len(events)} events")
        run.fail_if_any()
    finally:
        await cm.stop()

    assert collector.events == [], f"tasks died during soak: {collector.events}"


def test_corrupt_payloads_survive_every_query(in_memory_db) -> None:
    """Stored garbage must never break a query: any raise is a defect.

    Corrupted rows are realistic (partial writes, disk damage), not synthetic
    garbage — this test stays strict and is never allowlisted in the baseline.
    """
    run = ChaosRun()
    payloads = [
        "not json",
        "null",
        "42",
        "[1, 2]",
        '{"a": 1',
        '"\\ud800"',
        "{",
        "",
        "{}",
        '"' * 10,
        "\x00\x01\x02",
    ]
    in_memory_db.write_event(
        event_type="power_change", timestamp=0, payload={}, source="afk"
    )
    device_fk = in_memory_db._conn.execute("SELECT id FROM devices LIMIT 1").fetchone()[
        0
    ]
    event_type_fk = in_memory_db._conn.execute(
        "SELECT id FROM event_types WHERE name='power_change'"
    ).fetchone()[0]
    source_fk = in_memory_db._conn.execute(
        "SELECT id FROM sources WHERE name='afk'"
    ).fetchone()[0]
    for i, payload in enumerate(payloads):
        in_memory_db._conn.execute(
            "INSERT INTO raw_events (device_fk, event_type_fk, source_fk, timestamp,"
            " collected_at, payload, payload_hash) VALUES (?, ?, ?, ?, 1, ?, 0)",
            (device_fk, event_type_fk, source_fk, i + 1, payload),
        )

    queries = [
        in_memory_db.get_raw_events,
        in_memory_db.get_latest_battery,
        in_memory_db.get_today_seconds,
        in_memory_db.get_today_top_apps,
        in_memory_db.count_events,
        in_memory_db.get_url_visits,
        in_memory_db.get_app_sessions,
        in_memory_db.get_status_sessions,
        in_memory_db.check_integrity,
    ]
    for query in queries:
        try:
            query()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            run.record(f"query {query.__name__} on corrupt payloads", exc)
    run.fail_if_any(
        policy="strict", test_name="test_corrupt_payloads_survive_every_query"
    )


def test_concurrent_storage_hammer(tmp_path) -> None:
    """Two instances hammering one database, one thread each (the app's
    documented multi-instance contract). Any surfaced exception is a defect."""
    from core.storage import Storage

    db = str(tmp_path / "data.db")
    storages = [Storage(db_path=db), Storage(db_path=db)]
    run = ChaosRun()
    errors: list[BaseException] = []

    def hammer(storage: Storage, base: int) -> None:
        for i in range(300):
            try:
                storage.write_event(
                    event_type="idle_transition",
                    timestamp=base + i,
                    payload={"n": i},
                    source="afk",
                )
                storage.get_raw_events(limit=50)
            except BaseException as exc:
                errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(storages[i], i * 100_000))
        for i in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for storage in storages:
        storage._conn.close()
    for exc in errors:
        run.record("concurrent storage", exc)
    run.fail_if_any()
