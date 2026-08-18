"""Tests for Android session derivation (core/application/session_reconstructor.py).

The reconstructor derives ``app_sessions`` / ``status_sessions`` from the
raw event stream: transitions bound app sessions, screen-off splits them,
and status blocks follow screen state only. All derivation is
deterministic and the storage replace is idempotent.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.application.session_reconstructor import (
    derive_app_sessions,
    derive_status_sessions,
    rebuild_sessions,
)

_T0 = datetime(2026, 8, 16, tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _evts(*specs: tuple | dict) -> list[dict]:
    """Build an ordered event stream from ``(id, type, ts, payload)`` tuples
    or pre-built event dicts."""
    events = []
    for i, spec in enumerate(specs):
        if isinstance(spec, dict):
            events.append(spec)
        else:
            etype, ts, payload = spec
            events.append(
                {"id": i, "event_type": etype, "timestamp": _ms(ts), "payload": payload}
            )
    return events


def _fg(pkg: str, at: datetime) -> tuple:
    return ("foreground_transition", at, {"package": pkg, "app_name": pkg})


def _on(at: datetime) -> tuple:
    return ("screen_state_change", at, {"screen_on": True})


def _off(at: datetime) -> tuple:
    return ("screen_state_change", at, {"screen_on": False})


class TestDeriveAppSessions:
    def test_transitions_close_previous_session(self):
        a = _T0
        b = a + timedelta(seconds=90)
        events = _evts(_fg("com.a", a), _fg("com.b", b))
        sessions = derive_app_sessions(events)
        assert len(sessions) == 2
        first, second = sessions
        assert first["app_key"] == "com.a"
        assert first["start_ts"] == _ms(a)
        assert first["end_ts"] == _ms(b)
        assert first["duration_s"] == 90.0
        assert first["event_id"] == 0
        assert second["app_key"] == "com.b"
        assert second["end_ts"] is None
        assert second["duration_s"] is None
        assert second["event_id"] == 1

    def test_screen_off_closes_session_and_screen_on_resumes_last_app(self):
        a = _T0
        off = a + timedelta(minutes=5)
        on = a + timedelta(minutes=7)
        events = _evts(_fg("com.a", a), _off(off), _on(on))
        sessions = derive_app_sessions(events)
        assert len(sessions) == 2
        first, resumed = sessions
        assert first["app_key"] == "com.a"
        assert first["end_ts"] == _ms(off)
        assert first["duration_s"] == 300.0
        assert resumed["app_key"] == "com.a"
        assert resumed["start_ts"] == _ms(on)
        assert resumed["event_id"] == 2
        assert resumed["end_ts"] is None

    def test_resume_closed_by_next_transition(self):
        a = _T0
        off = a + timedelta(minutes=1)
        on = a + timedelta(minutes=2)
        c = a + timedelta(minutes=3)
        events = _evts(_fg("com.a", a), _off(off), _on(on), _fg("com.c", c))
        sessions = derive_app_sessions(events)
        assert len(sessions) == 3
        assert sessions[1]["app_key"] == "com.a"
        assert sessions[1]["end_ts"] == _ms(c)
        assert sessions[2]["app_key"] == "com.c"

    def test_screen_off_without_resume_leaves_no_open_session(self):
        a = _T0
        off = a + timedelta(minutes=1)
        events = _evts(_fg("com.a", a), _off(off))
        sessions = derive_app_sessions(events)
        assert len(sessions) == 1
        assert sessions[0]["end_ts"] == _ms(off)

    def test_consecutive_screen_events_do_not_duplicate_sessions(self):
        a = _T0
        events = _evts(
            _fg("com.a", a),
            _on(a + timedelta(seconds=1)),
            _on(a + timedelta(seconds=2)),
        )
        assert len(derive_app_sessions(events)) == 1
        off = a + timedelta(seconds=5)
        events = _evts(_fg("com.a", a), _off(off), _off(a + timedelta(seconds=6)))
        assert len(derive_app_sessions(events)) == 1
        assert derive_app_sessions(events)[0]["end_ts"] == _ms(off)

    def test_no_screen_events_single_open_session(self):
        events = _evts(_fg("com.a", _T0))
        sessions = derive_app_sessions(events)
        assert len(sessions) == 1
        assert sessions[0]["end_ts"] is None

    def test_screen_on_without_known_app_opens_nothing(self):
        events = _evts(_on(_T0))
        assert derive_app_sessions(events) == []

    def test_payload_carried_from_owning_transition(self):
        a = _T0
        events = _evts(_fg("com.a", a))
        assert derive_app_sessions(events)[0]["payload"] == {
            "package": "com.a",
            "app_name": "com.a",
        }

    def test_non_relevant_events_ignored(self):
        a = _T0
        events = [
            {
                "id": 0,
                "event_type": "user_presence",
                "timestamp": _ms(a),
                "payload": {},
            },
            {
                "id": 1,
                "event_type": "foreground_transition",
                "timestamp": _ms(a + timedelta(seconds=1)),
                "payload": {"package": "com.a"},
            },
        ]
        assert len(derive_app_sessions(events)) == 1


class TestDeriveStatusSessions:
    def test_alternating_screen_blocks(self):
        a = _T0
        off = a + timedelta(minutes=1)
        on = a + timedelta(minutes=2)
        events = _evts(_on(a), _off(off), _on(on))
        blocks = derive_status_sessions(events)
        assert len(blocks) == 3
        active, away, active2 = blocks
        assert active["status"] == "active"
        assert active["end_ts"] == _ms(off)
        assert active["duration_s"] == 60.0
        assert away["status"] == "away"
        assert away["end_ts"] == _ms(on)
        assert away["duration_s"] == 60.0
        assert active2["status"] == "active"
        assert active2["end_ts"] is None

    def test_head_active_block_from_first_event(self):
        """Data before the first screen event implies an awake device."""
        a = _T0
        off = a + timedelta(minutes=5)
        events = _evts(
            {
                "id": 0,
                "event_type": "user_presence",
                "timestamp": _ms(a),
                "payload": {},
            },
            {
                "id": 1,
                "event_type": "foreground_transition",
                "timestamp": _ms(a + timedelta(seconds=1)),
                "payload": {"package": "com.a"},
            },
            _off(off),
        )
        blocks = derive_status_sessions(events)
        assert len(blocks) == 2
        assert blocks[0]["status"] == "active"
        assert blocks[0]["event_id"] == 0
        assert blocks[0]["end_ts"] == _ms(off)
        assert blocks[1]["status"] == "away"
        assert blocks[1]["end_ts"] is None

    def test_consecutive_same_state_merged(self):
        a = _T0
        events = _evts(
            _on(a), _on(a + timedelta(seconds=1)), _on(a + timedelta(seconds=2))
        )
        assert len(derive_status_sessions(events)) == 1

    def test_no_screen_events_single_active_block(self):
        a = _T0
        events = [
            {
                "id": 0,
                "event_type": "user_presence",
                "timestamp": _ms(a),
                "payload": {},
            },
            {
                "id": 1,
                "event_type": "power_change",
                "timestamp": _ms(a + timedelta(seconds=1)),
                "payload": {},
            },
        ]
        blocks = derive_status_sessions(events)
        assert len(blocks) == 1
        assert blocks[0]["status"] == "active"
        assert blocks[0]["end_ts"] is None

    def test_away_block_opens_on_first_screen_event_off(self):
        a = _T0
        events = _evts(_off(a))
        blocks = derive_status_sessions(events)
        assert len(blocks) == 1
        assert blocks[0]["status"] == "away"
        assert blocks[0]["event_id"] == 0


class TestRebuildSessions:
    def _android_events(self, storage, fg_apps, screen_pairs):
        """Write android_foreground + screen_state_change events via storage."""
        from core.application.collection_manager import _EventBridge
        from core.models import Tick

        bridge = _EventBridge(storage, "android")
        for app, at in fg_apps:
            bridge(
                Tick(watcher="android_foreground", data={"package": app}, timestamp=at)
            )
        for screen_on, at in screen_pairs:
            storage.write_event(
                "screen_state_change",
                _ms(at),
                {"screen_on": screen_on},
                "screen_monitor",
            )

    def test_rebuild_produces_android_sessions(self, in_memory_db):
        self._android_events(
            in_memory_db,
            fg_apps=[("com.a", _T0), ("com.b", _T0 + timedelta(minutes=2))],
            screen_pairs=[(False, _T0 + timedelta(minutes=1))],
        )
        result = rebuild_sessions(in_memory_db, in_memory_db.device_id)
        assert result["app_sessions"] == 2
        assert result["status_sessions"] == 2

        app = in_memory_db.get_app_sessions()
        status = in_memory_db.get_status_sessions()
        assert app[0]["app_key"] == "com.a"
        assert app[0]["end_ts"] == _ms(_T0 + timedelta(minutes=1))
        assert app[1]["app_key"] == "com.b"
        assert app[1]["end_ts"] is None
        statuses = [s["status"] for s in status]
        assert statuses == ["active", "away"]

    def test_rebuild_is_idempotent(self, in_memory_db):
        self._android_events(
            in_memory_db,
            fg_apps=[("com.a", _T0)],
            screen_pairs=[(False, _T0 + timedelta(minutes=1))],
        )
        first = rebuild_sessions(in_memory_db, in_memory_db.device_id)
        rows_a = self._logical(in_memory_db.get_app_sessions())
        rows_s = self._logical(in_memory_db.get_status_sessions())
        second = rebuild_sessions(in_memory_db, in_memory_db.device_id)
        assert first == second
        assert rows_a == self._logical(in_memory_db.get_app_sessions())
        assert rows_s == self._logical(in_memory_db.get_status_sessions())

    @staticmethod
    def _logical(rows: list[dict]) -> list[tuple]:
        """Session identity without rowids (re-inserts assign fresh ids)."""
        return [tuple((k, v) for k, v in row.items() if k != "id") for row in rows]

    def test_rebuild_replaces_stale_rows(self, in_memory_db):
        from core.application.collection_manager import _EventBridge
        from core.models import Tick

        bridge = _EventBridge(in_memory_db, "android")
        t0 = _T0
        bridge(
            Tick(watcher="android_foreground", data={"package": "com.a"}, timestamp=t0)
        )
        event_id = in_memory_db.get_raw_events()[0]["id"]
        in_memory_db.open_app_session(
            event_id, _ms(t0), "com.stale", {"package": "com.stale"}
        )
        assert len(in_memory_db.get_app_sessions()) == 1

        rebuild_sessions(in_memory_db, in_memory_db.device_id)
        sessions = in_memory_db.get_app_sessions()
        assert len(sessions) == 1
        assert sessions[0]["app_key"] == "com.a"

    def test_rebuild_empty_device_is_noop(self, in_memory_db):
        result = rebuild_sessions(in_memory_db, in_memory_db.device_id)
        assert result["app_sessions"] == 0
        assert result["status_sessions"] == 0
        assert in_memory_db.get_app_sessions() == []
        assert in_memory_db.get_status_sessions() == []

    def test_rebuild_foreign_device(self, in_memory_db):
        """Sessions rebuild for imported devices, not just the current one."""
        import json

        foreign_fk = in_memory_db._resolve_device_fk("foreign-imported-device")
        assert foreign_fk is not None
        fg_type = in_memory_db._resolve_name_fk(
            "event_types", "foreground_transition", {}
        )
        scr_type = in_memory_db._resolve_name_fk(
            "event_types", "screen_state_change", {}
        )
        source = in_memory_db._resolve_name_fk("sources", "android_foreground", {})
        now = _ms(_T0)
        conn = in_memory_db._conn
        for row in (
            (foreign_fk, fg_type, source, now, json.dumps({"package": "com.imported"})),
            (
                foreign_fk,
                scr_type,
                source,
                now + 60_000,
                json.dumps({"screen_on": False}),
            ),
        ):
            conn.execute(
                "INSERT INTO raw_events"
                " (device_fk, event_type_fk, source_fk, timestamp, collected_at,"
                " payload, payload_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, 0)",
                (row[0], row[1], row[2], row[3], now, row[4]),
            )

        result = rebuild_sessions(in_memory_db, "foreign-imported-device")
        assert result["app_sessions"] == 1
        assert result["status_sessions"] == 2
        sessions = in_memory_db.get_app_sessions(device_id="foreign-imported-device")
        assert len(sessions) == 1
        assert sessions[0]["app_key"] == "com.imported"
        assert sessions[0]["end_ts"] == now + 60_000
        blocks = in_memory_db.get_status_sessions(device_id="foreign-imported-device")
        assert [b["status"] for b in blocks] == ["active", "away"]

    def test_rebuild_summary_counts_match_rows(self, in_memory_db):
        self._android_events(
            in_memory_db,
            fg_apps=[("com.a", _T0)],
            screen_pairs=[
                (False, _T0 + timedelta(minutes=1)),
                (True, _T0 + timedelta(minutes=2)),
            ],
        )
        result = rebuild_sessions(in_memory_db, in_memory_db.device_id)
        assert result["app_sessions"] == len(in_memory_db.get_app_sessions())
        assert result["status_sessions"] == len(in_memory_db.get_status_sessions())


class TestStorageReplaceDeviceSessions:
    def test_replace_rolls_back_on_bad_row(self, in_memory_db):
        from core.application.collection_manager import _EventBridge
        from core.models import Tick

        bridge = _EventBridge(in_memory_db, "android")
        bridge(
            Tick(watcher="android_foreground", data={"package": "com.a"}, timestamp=_T0)
        )
        rebuild_sessions(in_memory_db, in_memory_db.device_id)
        before = in_memory_db.get_app_sessions()

        with pytest.raises(KeyError):
            in_memory_db.replace_device_sessions(
                in_memory_db.device_id,
                [{"event_id": 9999}],  # missing required keys
                [],
            )
        assert in_memory_db.get_app_sessions() == before
