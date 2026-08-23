"""Icon system chaos: adversarial testing of icon resolution, caching, and config.

Fires garbage inputs at ``package_icon_png``, ``exe_icon_png``, ``IconCache``,
``_resolve_icon``, and the ``fetch_favicons`` config toggle. Any unhandled
exception, any corrupt cache state, any config mutation that breaks the toggle
is a defect.

Run: ``uv run pytest tests/chaos -m chaos``
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import time
import zlib

import pytest
from chaos_helpers import ChaosRun, finding_key

from core.config_manager import ConfigManager
from core.icons.icon_cache import IconCache
from core.icons.icon_resolver import (
    _encode_bgra_png,
    _png_from_rgba,
    _valid_png,
    exe_icon_png,
    ico_to_png,
    is_site_bucket,
    package_icon_png,
    site_key_to_domain,
)

pytestmark = pytest.mark.chaos


# ---------------------------------------------------------------------------
# Garbage inputs for icon extraction functions
# ---------------------------------------------------------------------------


def _hostile_strings(rng) -> list[str]:
    """Paths, packages, and keys a mindless user could produce."""
    pool = [
        "",
        " ",
        "\n",
        "\t",
        "\x00",
        "\ud800",
        "\uffff",
        "\u200b",
        "a" * 10000,
        "\u00e9" * 500,
        "\U0001f4a5" * 50,
        "nan",
        "inf",
        "-1",
        "-0.0001",
        "1e999",
        "0x1F",
        "None",
        "null",
        "true",
        "[]",
        "{}",
        "SELECT * FROM raw_events;",
        "../..",
        "%00%0d%0a",
        "<script>alert(1)</script>",
        "\\\\",
        '"',
        "'",
        "3.5.2.1",
        "0",
        "99999999999999999999",
        "\u202eRTL override",
        "com.android.chrome",
        "chrome.exe",
        "browser:youtube",
        "browser:notasite",
        "C:\\Windows\\System32\\notepad.exe",
        "/usr/bin/ls",
        "com.google.android.youtube",
        "com.whatsapp",
    ]
    return pool + [rng.choice(pool) + str(rng.randint(0, 10**9)) for _ in range(4)]


def _hostile_png_bytes(rng) -> list[bytes]:
    """Random bytes that look like PNG or not."""
    pool = [
        b"",
        b"\x89PNG\r\n\x1a\n",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
        b"not png",
        b"\x00\x00\x01\x00",
        b"\x00\x00\x00\x00" * 100,
        b"\xff" * 1000,
        b"\x89PNG" + b"\x00" * 5000,
        b"\x89PNG\r\n\x1a\n" + b"IHDR" + b"\x00" * 100,
        b"\x89PNG\r\n\x1a\n" + b"IEND" + b"\x00" * 8,
    ]
    return pool + [bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 500))) for _ in range(8)]


def _hostile_ints(rng) -> list[int]:
    """Sizes, timestamps, and other integer values."""
    return [
        0, -1, -100, 1, 2, 3, 4, 8, 16, 32, 48, 64, 96, 128, 256, 512, 1024,
        2**31 - 1, 2**31, 2**32 - 1, 2**32, 2**63 - 1, 2**63,
        -2**31, -2**63,
        rng.randint(-1000, 1000),
        rng.randint(0, 2**32),
    ]


# ---------------------------------------------------------------------------
# Tests: icon resolver functions with hostile inputs
# ---------------------------------------------------------------------------


class TestPackageIconPngChaos:
    """Fire garbage at package_icon_png — must never raise unhandled."""

    def test_hostile_strings_do_not_crash(self):
        run = ChaosRun()
        for s in _hostile_strings(run.rng):
            try:
                result = package_icon_png(s)
                assert result is None or isinstance(result, bytes)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"package_icon_png({s!r})", exc)
        run.fail_if_any(test_name="test_package_icon_pngx_hostile_strings")


class TestExeIconPngChaos:
    """Fire garbage at exe_icon_png — must never raise unhandled."""

    def test_hostile_strings_do_not_crash(self):
        run = ChaosRun()
        for s in _hostile_strings(run.rng):
            try:
                result = exe_icon_png(s)
                assert result is None or isinstance(result, bytes)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"exe_icon_png({s!r})", exc)
        run.fail_if_any(test_name="test_exe_icon_pngx_hostile_strings")

    def test_hostile_sizes_do_not_crash(self):
        run = ChaosRun()
        for size in _hostile_ints(run.rng):
            try:
                result = exe_icon_png("C:\\fake.exe", size=size)
                assert result is None or isinstance(result, bytes)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"exe_icon_png(size={size})", exc)
        run.fail_if_any(test_name="test_exe_icon_pngx_hostile_sizes")


class TestIcoToPngChaos:
    """Fire garbage at ico_to_png — must never raise unhandled."""

    def test_hostile_bytes_do_not_crash(self):
        run = ChaosRun()
        for data in _hostile_png_bytes(run.rng):
            try:
                result = ico_to_png(data)
                assert result is None or isinstance(result, bytes)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"ico_to_png(len={len(data)})", exc)
        run.fail_if_any(test_name="test_ico_to_pngx_hostile_bytes")


class TestPngFromRgbaChaos:
    """Fire garbage at _png_from_rgba — must never raise unhandled."""

    def test_hostile_inputs_do_not_crash(self):
        run = ChaosRun()
        for width in _hostile_ints(run.rng):
            for height in _hostile_ints(run.rng)[:5]:  # limit combinations
                try:
                    if abs(width) > 4096 or abs(height) > 4096 or width <= 0 or height <= 0:
                        result = _png_from_rgba(b"", width, height)
                        assert isinstance(result, bytes)
                    else:
                        rgba = b"\x00" * (width * height * 4)
                        result = _png_from_rgba(rgba, width, height)
                        assert isinstance(result, bytes)
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    run.record(f"_png_from_rgba({width}x{height})", exc)
        run.fail_if_any(test_name="test_png_from_rgbax_hostile_inputs")


class TestValidPngChaos:
    """Fire garbage at _valid_png — must never raise unhandled."""

    def test_hostile_bytes_do_not_crash(self):
        run = ChaosRun()
        for data in _hostile_png_bytes(run.rng):
            try:
                result = _valid_png(data)
                assert isinstance(result, bool)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"_valid_png(len={len(data)})", exc)
        run.fail_if_any(test_name="test_valid_pngx_hostile_bytes")


class TestEncodeBgraPngChaos:
    """Fire garbage at _encode_bgra_png — must never raise unhandled."""

    def test_hostile_inputs_do_not_crash(self):
        run = ChaosRun()
        for size in _hostile_ints(run.rng)[:10]:
            try:
                if abs(size) > 4096 or size <= 0:
                    result = _encode_bgra_png(b"", size)
                    assert isinstance(result, bytes)
                else:
                    bgra = b"\x00" * (size * size * 4)
                    result = _encode_bgra_png(bgra, size)
                    assert isinstance(result, bytes)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"_encode_bgra_png(size={size})", exc)
        run.fail_if_any(test_name="test_encode_bgra_pngx_hostile_inputs")


# ---------------------------------------------------------------------------
# Tests: IconCache with hostile inputs
# ---------------------------------------------------------------------------


class TestIconCacheChaos:
    """Fire garbage at IconCache — must never raise unhandled."""

    def _make_cache(self, tmp_path) -> IconCache:
        db = sqlite3.connect(str(tmp_path / "chaos.db"))
        db.execute(
            """CREATE TABLE IF NOT EXISTS app_icons (
                app_key     TEXT PRIMARY KEY,
                source      TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                png         BLOB NOT NULL,
                width       INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            )"""
        )
        return IconCache(db)

    def test_hostile_keys_get_put_invalidate(self, tmp_path):
        run = ChaosRun()
        cache = self._make_cache(tmp_path)
        for key in _hostile_strings(run.rng):
            try:
                png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
                cache.put(key, "site_fingerprint", "fp", png, 48)
                result = cache.get(key)
                assert result is None or isinstance(result, bytes)
                cache.invalidate(key)
                assert cache.get(key) is None
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"IconCache ops({key!r})", exc)
        run.fail_if_any(test_name="test_icon_cachex_hostile_keys")

    def test_hostile_fingerprints(self, tmp_path):
        run = ChaosRun()
        cache = self._make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        for fp in _hostile_strings(run.rng):
            try:
                cache.put("test.app", "android_package", fp, png, 96)
                changed = cache.fingerprint_changed("test.app", fp)
                assert isinstance(changed, bool)
                different = cache.fingerprint_changed("test.app", "other")
                assert isinstance(different, bool)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"IconCache fingerprint({fp!r})", exc)
        run.fail_if_any(test_name="test_icon_cachex_hostile_fingerprints")

    def test_hostile_max_age_eviction(self, tmp_path):
        run = ChaosRun()
        cache = self._make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        for age in _hostile_ints(run.rng)[:10]:
            try:
                now_ms = int(time.time() * 1000)
                db = cache._conn
                db.execute(
                    "INSERT OR REPLACE INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
                    (f"test.{age}", "site_fingerprint", "fp", png, 48, now_ms - age * 1000),
                )
                evicted = cache.evict_expired(max_age_days=max(0, age))
                assert isinstance(evicted, int)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"IconCache evict(max_age={age})", exc)
        run.fail_if_any(test_name="test_icon_cachex_hostile_eviction")

    def test_concurrent_cache_ops(self, tmp_path):
        """Two threads hammering one cache — must not corrupt."""
        import threading

        run = ChaosRun()
        db_path = str(tmp_path / "concurrent.db")
        errors: list[BaseException] = []

        def hammer(base: int) -> None:
            db = sqlite3.connect(db_path)
            db.execute(
                """CREATE TABLE IF NOT EXISTS app_icons (
                    app_key     TEXT PRIMARY KEY,
                    source      TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    png         BLOB NOT NULL,
                    width       INTEGER NOT NULL,
                    updated_at  INTEGER NOT NULL
                )"""
            )
            cache = IconCache(db)
            for i in range(100):
                try:
                    key = f"app.{base + i}"
                    png = b"\x89PNG\r\n\x1a\n" + bytes([i % 256]) * 20
                    cache.put(key, "site_fingerprint", f"fp.{i}", png, 48)
                    cache.get(key)
                    cache.fingerprint_changed(key, f"fp.{i + 1}")
                    if i % 10 == 0:
                        cache.evict_expired()
                except BaseException as exc:
                    errors.append(exc)
            db.close()

        threads = [threading.Thread(target=hammer, args=(i * 1000,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for exc in errors:
            run.record("concurrent cache", exc)
        run.fail_if_any()


# ---------------------------------------------------------------------------
# Tests: fetch_favicons config toggle with hostile values
# ---------------------------------------------------------------------------


class TestFetchFaviconsConfigChaos:
    """Corrupt the config and verify the toggle degrades gracefully."""

    def test_hostile_values_for_fetch_favicons(self, tmp_path):
        run = ChaosRun()
        hostile_values = [
            None, 0, 1, -1, 42, 3.14, "", "true", "false", "yes", "no",
            [], {}, set(), b"true", object(), object(), float("nan"),
            float("inf"), float("-inf"),
        ]
        for val in hostile_values:
            try:
                cm = ConfigManager(path=str(tmp_path / f"config_{id(val)}.json"))
                # Directly set internal data to bypass the setter
                cm._data["fetch_favicons"] = val
                # The property should still return a bool
                result = cm.fetch_favicons
                assert isinstance(result, bool), f"fetch_favicons returned {type(result)} for {val!r}"
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"fetch_favicons({val!r})", exc)
        run.fail_if_any(test_name="test_fetch_faviconsx_hostile_values")

    def test_config_save_load_roundtrip_with_toggling(self, tmp_path):
        """Rapidly toggle fetch_favicons and verify persistence."""
        run = ChaosRun()
        for i in range(50):
            try:
                p = tmp_path / f"toggle_{i}.json"
                cm = ConfigManager(path=str(p))
                cm.fetch_favicons = bool(i % 2)
                cm.save()
                cm2 = ConfigManager(path=str(p))
                cm2.load()
                assert cm2.fetch_favicons == bool(i % 2)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"config toggle {i}", exc)
        run.fail_if_any(test_name="test_fetch_faviconsx_toggle_roundtrip")


# ---------------------------------------------------------------------------
# Tests: site_key_to_domain / is_site_bucket with hostile inputs
# ---------------------------------------------------------------------------


class TestSiteBucketChaos:
    """Fire garbage at site_key_to_domain and is_site_bucket."""

    def test_hostile_strings_do_not_crash(self):
        run = ChaosRun()
        for key in _hostile_strings(run.rng):
            try:
                domain = site_key_to_domain(key)
                assert domain is None or isinstance(domain, str)
                result = is_site_bucket(key)
                assert isinstance(result, bool)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                run.record(f"site_key_to_domain({key!r})", exc)
        run.fail_if_any(test_name="test_site_bucketx_hostile_strings")
