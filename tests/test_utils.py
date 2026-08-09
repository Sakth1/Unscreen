import re

from utils.files import remove_file, timestamped_filename
from utils.net import extract_domain, is_trackable_url, normalize_url
from utils.time_utils import day_start_ms, fmt_timestamp, get_current_time_ms
from utils.versions import compare_versions, normalize_version


class TestVersions:
    def test_normalize_version_strips_v_prefix(self):
        assert normalize_version("v0.4.2") == "0.4.2"
        assert normalize_version("0.4.2") == "0.4.2"

    def test_compare_versions_orders_core(self):
        assert compare_versions("0.4.3", "0.4.2") > 0
        assert compare_versions("0.4.2", "0.4.3") < 0
        assert compare_versions("0.4.2", "v0.4.2") == 0

    def test_compare_versions_orders_prereleases(self):
        assert compare_versions("1.0.0-rc.1", "1.0.0") < 0
        assert compare_versions("1.0.0-rc.1", "1.0.0-rc.2") < 0
        assert compare_versions("1.0.0-beta", "1.0.0-alpha") > 0

    def test_compare_versions_treats_garbage_as_equal(self):
        assert compare_versions("not-a-version", "0.4.2") == 0


class TestNet:
    def test_normalize_url_adds_http(self):
        assert normalize_url("example.com") == "http://example.com"

    def test_normalize_url_keeps_scheme(self):
        assert normalize_url("https://example.com") == "https://example.com"
        assert normalize_url("about:blank") == "about:blank"

    def test_is_trackable_url_rejects_internal_pages(self):
        assert is_trackable_url("about:blank") is False
        assert is_trackable_url("chrome://newtab/") is False
        assert is_trackable_url("chrome-extension://abc/popup.html") is False

    def test_is_trackable_url_accepts_http(self):
        assert is_trackable_url("https://github.com") is True
        assert is_trackable_url(None) is False
        assert is_trackable_url("   ") is False

    def test_extract_domain_falls_back_to_last_two_labels(self):
        assert extract_domain("www.example.com") == "example.com"
        assert extract_domain("localhost") == "localhost"
        assert extract_domain("") is None

    def test_extract_domain_uses_psl(self):
        class FakePsl:
            def privatesuffix(self, host):
                return "example.co.uk"

        assert extract_domain("a.b.example.co.uk", FakePsl()) == "example.co.uk"


class TestTimeUtils:
    def test_get_current_time_ms_is_positive(self):
        assert get_current_time_ms() > 0

    def test_day_start_ms_returns_local_midnight(self):
        now_ms = get_current_time_ms()
        start = day_start_ms(now_ms)
        assert start <= now_ms
        assert (now_ms - start) < 24 * 60 * 60 * 1000

    def test_fmt_timestamp_formats_utc(self):
        assert fmt_timestamp(0.0) == "1970-01-01 00:00:00.000000"


class TestFiles:
    def test_remove_file_missing_is_silent(self, tmp_path):
        remove_file(tmp_path / "nope.txt")

    def test_remove_file_existing(self, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("x")
        remove_file(target)
        assert not target.exists()

    def test_timestamped_filename(self):
        name = timestamped_filename("raw_events", "csv")
        assert re.fullmatch(r"raw_events_\d{8}_\d{6}\.csv", name)


class TestInstanceMutex:
    def test_not_available_off_windows(self):
        from unittest.mock import patch

        from utils.platform import acquire_instance_mutex

        with patch("utils.platform.sys.platform", "linux"):
            assert acquire_instance_mutex("AnyName") is None

    def test_acquire_returns_handle_on_windows(self):
        import ctypes
        import sys

        import pytest

        if sys.platform != "win32":
            pytest.skip("Windows named mutex is not available here")

        from utils.platform import acquire_instance_mutex

        first = acquire_instance_mutex("UnscreenTestMutex_1")
        second = acquire_instance_mutex("UnscreenTestMutex_1")
        try:
            assert first
            assert second
        finally:
            if first:
                ctypes.windll.kernel32.CloseHandle(first)
            if second:
                ctypes.windll.kernel32.CloseHandle(second)
