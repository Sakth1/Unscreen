"""Tests for core/analytics/analytics_store.py (issue #26).

Seeds app_sessions directly (the store is a read-only aggregation layer)
to keep timestamps and device scope precise; sessions follow the real
schema, including open rows with NULL end_ts/duration_s.
"""

import datetime
import itertools
import json

import pytest

from core.analytics import ALL_DEVICES, AnalyticsStore
from utils.time_utils import day_start_ms, get_current_time_ms, week_start_ms

_ids = itertools.count(1)

_TODAY = datetime.date.today()
_DAY_MS = 24 * 60 * 60 * 1000


def _ms(dt: datetime.datetime) -> int:
    return int(dt.timestamp() * 1000)


def _local(dt: datetime.datetime) -> int:
    return int(
        datetime.datetime(
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second
        ).timestamp()
        * 1000
    )


def _seed_session(
    storage,
    app_key: str,
    payload: dict,
    start_ts: int,
    end_ts: int | None = None,
    duration_s: float | None = None,
    device_id: str | None = None,
) -> None:
    fk = (
        storage._resolve_device_fk(device_id) if device_id else storage._device_fk
    )  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        "INSERT INTO app_sessions (device_fk, event_id, start_ts, end_ts, duration_s,"
        " app_key, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fk, next(_ids), start_ts, end_ts, duration_s, app_key, json.dumps(payload)),
    )


def _seeded(storage):
    """AnalyticsStore over a db pre-seeded with a couple of today's apps."""
    today_start = day_start_ms(get_current_time_ms())
    _seed_session(
        storage,
        "com.foo",
        {"package": "com.foo", "app_name": "Foo"},
        today_start,
        today_start + 90_000,
        90.0,
    )
    _seed_session(
        storage,
        "bar.exe",
        {"app": "bar.exe"},
        today_start + 5_000,
        today_start + 35_000,
        30.0,
    )
    return AnalyticsStore(storage)


class TestDailyTotals:
    def test_default_today_sorted_with_share(self, in_memory_db):
        store = _seeded(in_memory_db)
        totals = store.daily_totals()
        assert [(t.app_key, t.app_name) for t in totals] == [
            ("com.foo", "Foo"),
            ("bar.exe", "bar"),
        ]
        assert totals[0].total_s == 90.0
        assert totals[0].share_pct == 75.0
        assert totals[1].total_s == 30.0
        assert totals[1].share_pct == 25.0

    def test_specific_day_boundaries(self, in_memory_db):
        day = datetime.date(2026, 8, 20)
        midnight = _ms(datetime.datetime(2026, 8, 20))
        next_midnight = midnight + _DAY_MS
        _seed_session(in_memory_db, "a", {}, midnight, next_midnight, 100.0)
        _seed_session(
            in_memory_db, "b", {}, next_midnight - 1000, next_midnight + 9000, 10.0
        )
        _seed_session(in_memory_db, "c", {}, next_midnight, next_midnight + 5000, 5.0)
        store = AnalyticsStore(in_memory_db)
        totals = store.daily_totals(day)
        assert {t.app_key: t.total_s for t in totals} == {"a": 100.0, "b": 10.0}
        assert totals[0].app_key == "a"

    def test_midnight_crossing_session_counts_on_start_day(self, in_memory_db):
        day = datetime.date(2026, 8, 20)
        midnight = _ms(datetime.datetime(2026, 8, 20))
        _seed_session(
            in_memory_db,
            "late",
            {},
            midnight + _DAY_MS - 60_000,
            midnight + _DAY_MS + 60_000,
            120.0,
        )
        totals = AnalyticsStore(in_memory_db).daily_totals(day)
        assert totals[0].app_key == "late"
        assert totals[0].total_s == 120.0


class TestWeeklyTotals:
    def test_monday_to_sunday_week(self, in_memory_db):
        thursday = datetime.date(2026, 8, 20)
        monday = thursday - datetime.timedelta(days=thursday.weekday())
        monday_ms = _ms(datetime.datetime(monday.year, monday.month, monday.day))
        sunday_ms = monday_ms + 6 * _DAY_MS
        _seed_session(
            in_memory_db, "in-week", {}, monday_ms + 60_000, monday_ms + 360_000, 300.0
        )
        _seed_session(
            in_memory_db, "prev-week", {}, monday_ms - 1, monday_ms + 1000, 1.0
        )
        _seed_session(
            in_memory_db,
            "next-week",
            {},
            sunday_ms + _DAY_MS,
            sunday_ms + _DAY_MS + 1000,
            1.0,
        )
        totals = AnalyticsStore(in_memory_db).weekly_totals(thursday)
        assert [t.app_key for t in totals] == ["in-week"]

    def test_default_is_current_week(self, in_memory_db):
        start = week_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "a", {}, start, start + 60_000, 60.0)
        _seed_session(in_memory_db, "before", {}, start - 1, start + 1, 0.001)
        totals = AnalyticsStore(in_memory_db).weekly_totals()
        assert [t.app_key for t in totals] == ["a"]


class TestTotals:
    def test_share_uses_full_range_not_limit(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        for i, (key, dur) in enumerate([("a", 60.0), ("b", 30.0), ("c", 10.0)]):
            _seed_session(
                in_memory_db, key, {}, now + i * 1000, now + i * 1000 + dur * 1000, dur
            )
        totals = AnalyticsStore(in_memory_db).totals(now, now + _DAY_MS, limit=2)
        assert [t.app_key for t in totals] == ["a", "b"]
        assert totals[0].share_pct == 60.0
        assert totals[1].share_pct == 30.0

    def test_open_sessions_excluded(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "closed", {}, now, now + 60_000, 60.0)
        _seed_session(in_memory_db, "open", {}, now, None, None)
        totals = AnalyticsStore(in_memory_db).daily_totals()
        assert [t.app_key for t in totals] == ["closed"]
        assert totals[0].share_pct == 100.0

    def test_empty_range(self, in_memory_db):
        assert (
            AnalyticsStore(in_memory_db).daily_totals(datetime.date(2020, 1, 1)) == []
        )

    def test_all_open_sessions_empty(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "open", {}, now, None, None)
        assert AnalyticsStore(in_memory_db).daily_totals() == []

    def test_totals_requires_start_before_until(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        assert AnalyticsStore(in_memory_db).totals(now, now) == []


class TestDeviceScope:
    def test_defaults_to_current_device(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "mine", {}, now, now + 60_000, 60.0)
        _seed_session(
            in_memory_db, "theirs", {}, now, now + 60_000, 60.0, device_id="foreign-1"
        )
        totals = AnalyticsStore(in_memory_db).daily_totals()
        assert [t.app_key for t in totals] == ["mine"]

    def test_all_devices(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "mine", {}, now, now + 60_000, 60.0)
        _seed_session(
            in_memory_db, "theirs", {}, now, now + 120_000, 120.0, device_id="foreign-1"
        )
        totals = AnalyticsStore(in_memory_db).daily_totals(device_id=ALL_DEVICES)
        assert [t.app_key for t in totals] == ["theirs", "mine"]
        assert totals[0].share_pct == pytest.approx(66.7, abs=0.1)

    def test_explicit_foreign_device(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "mine", {}, now, now + 60_000, 60.0)
        _seed_session(
            in_memory_db, "theirs", {}, now, now + 60_000, 60.0, device_id="foreign-1"
        )
        totals = AnalyticsStore(in_memory_db).daily_totals(device_id="foreign-1")
        assert [t.app_key for t in totals] == ["theirs"]


class TestAppNameResolution:
    def test_android_app_name(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(
            in_memory_db,
            "com.android.chrome",
            {"package": "com.android.chrome", "app_name": "Chrome"},
            now,
            now + 1000,
            1.0,
        )
        totals = AnalyticsStore(in_memory_db).daily_totals()
        assert totals[0].app_name == "Chrome"

    def test_windows_browser_map(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(
            in_memory_db, "chrome.exe", {"app": "chrome.exe"}, now, now + 1000, 1.0
        )
        assert AnalyticsStore(in_memory_db).daily_totals()[0].app_name == "Chrome"

    def test_windows_generic_exe_stripped(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(
            in_memory_db, "editor.exe", {"app": "editor.exe"}, now, now + 1000, 1.0
        )
        assert AnalyticsStore(in_memory_db).daily_totals()[0].app_name == "editor"

    def test_fallback_to_app_key(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(in_memory_db, "weird-key", {}, now, now + 1000, 1.0)
        assert AnalyticsStore(in_memory_db).daily_totals()[0].app_name == "weird-key"

    def test_mixed_platforms_bucketed_by_key(self, in_memory_db):
        now = day_start_ms(get_current_time_ms())
        _seed_session(
            in_memory_db,
            "com.a",
            {"package": "com.a", "app_name": "A"},
            now,
            now + 1000,
            1.0,
        )
        _seed_session(
            in_memory_db,
            "com.a",
            {"package": "com.a", "app_name": "A"},
            now + 5000,
            now + 6000,
            1.0,
            device_id="foreign-1",
        )
        totals = AnalyticsStore(in_memory_db).daily_totals(device_id=ALL_DEVICES)
        assert [t.app_key for t in totals] == ["com.a"]
        assert totals[0].total_s == 2.0
