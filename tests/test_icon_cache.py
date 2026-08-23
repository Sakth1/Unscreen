"""Tests for the SQLite-backed icon cache."""

import sqlite3
import time

from core.icons.icon_cache import IconCache


def _make_cache(tmp_path) -> tuple[IconCache, sqlite3.Connection]:
    """Create an in-memory-ish cache backed by a temp file DB."""
    db = sqlite3.connect(str(tmp_path / "test.db"))
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
    return IconCache(db), db


class TestIconCacheGet:
    def test_returns_none_for_missing_key(self, tmp_path):
        cache, _ = _make_cache(tmp_path)
        assert cache.get("nonexistent") is None

    def test_returns_png_for_fresh_entry(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        now_ms = int(time.time() * 1000)
        db.execute(
            "INSERT INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
            ("com.test.app", "android_package", "pkg", png, 96, now_ms),
        )
        assert cache.get("com.test.app") == png

    def test_returns_none_for_stale_entry(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        # Entry from 31 days ago
        old_ms = int((time.time() - 31 * 86400) * 1000)
        db.execute(
            "INSERT INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
            ("com.test.app", "android_package", "pkg", png, 96, old_ms),
        )
        assert cache.get("com.test.app") is None

    def test_returns_none_on_exception(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        # Simulate a broken connection
        db.close()
        assert cache.get("com.test.app") is None


class TestIconCachePut:
    def test_inserts_new_entry(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        cache.put("com.test.app", "android_package", "pkg", png, 96)
        row = db.execute("SELECT * FROM app_icons WHERE app_key = ?", ("com.test.app",)).fetchone()
        assert row is not None
        assert row[1] == "android_package"
        assert row[2] == "pkg"
        assert row[3] == png
        assert row[4] == 96

    def test_updates_existing_entry(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        png2 = b"\x89PNG\r\n\x1a\n" + b"\x01" * 20
        cache.put("com.test.app", "android_package", "v1", png1, 96)
        cache.put("com.test.app", "android_package", "v2", png2, 128)
        rows = db.execute("SELECT * FROM app_icons WHERE app_key = ?", ("com.test.app",)).fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "v2"
        assert rows[0][3] == png2
        assert rows[0][4] == 128


class TestIconCacheInvalidate:
    def test_removes_entry(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        cache.put("com.test.app", "android_package", "pkg", png, 96)
        cache.invalidate("com.test.app")
        assert db.execute("SELECT COUNT(*) FROM app_icons").fetchone()[0] == 0

    def test_noop_on_missing_key(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        cache.invalidate("nonexistent")
        assert db.execute("SELECT COUNT(*) FROM app_icons").fetchone()[0] == 0


class TestIconCacheEvictExpired:
    def test_evicts_old_entries(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        now_ms = int(time.time() * 1000)
        old_ms = int((time.time() - 31 * 86400) * 1000)
        # Fresh entry
        db.execute(
            "INSERT INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
            ("com.fresh", "android_package", "pkg", png, 96, now_ms),
        )
        # Stale entry
        db.execute(
            "INSERT INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
            ("com.stale", "android_package", "pkg", png, 96, old_ms),
        )
        evicted = cache.evict_expired()
        assert evicted == 1
        assert db.execute("SELECT COUNT(*) FROM app_icons").fetchone()[0] == 1
        assert cache.get("com.fresh") == png
        assert cache.get("com.stale") is None

    def test_returns_zero_on_exception(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        db.close()
        assert cache.evict_expired() == 0


class TestIconCacheFingerprintChanged:
    def test_returns_true_for_missing_key(self, tmp_path):
        cache, _ = _make_cache(tmp_path)
        assert cache.fingerprint_changed("com.test.app", "new_fp") is True

    def test_returns_true_when_fingerprint_differs(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        now_ms = int(time.time() * 1000)
        db.execute(
            "INSERT INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
            ("com.test.app", "android_package", "old_fp", png, 96, now_ms),
        )
        assert cache.fingerprint_changed("com.test.app", "new_fp") is True

    def test_returns_false_when_fingerprint_matches(self, tmp_path):
        cache, db = _make_cache(tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        now_ms = int(time.time() * 1000)
        db.execute(
            "INSERT INTO app_icons VALUES (?, ?, ?, ?, ?, ?)",
            ("com.test.app", "android_package", "same_fp", png, 96, now_ms),
        )
        assert cache.fingerprint_changed("com.test.app", "same_fp") is False
