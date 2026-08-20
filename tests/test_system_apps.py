"""Tests for core/application/system_apps.py (F6 curated system-app lists)."""

from core.application.system_apps import (
    ANDROID_SYSTEM_KEYS,
    PLATFORM_ANDROID,
    PLATFORM_WINDOWS,
    SYSTEM_KEYS_BY_PLATFORM,
    WINDOWS_SYSTEM_KEYS,
    effective_system_keys,
)


class TestCuratedLists:
    def test_bug_offenders_are_hidden(self):
        assert "com.sec.android.app.launcher" in ANDROID_SYSTEM_KEYS
        assert "com.google.android.packageinstaller" in ANDROID_SYSTEM_KEYS

    def test_real_android_apps_stay_visible(self):
        assert "com.android.chrome" not in ANDROID_SYSTEM_KEYS
        assert "com.google.android.youtube" not in ANDROID_SYSTEM_KEYS
        assert "com.google.android.apps.maps" not in ANDROID_SYSTEM_KEYS
        assert "org.telegram.messenger" not in ANDROID_SYSTEM_KEYS

    def test_no_blanket_google_prefix(self):
        # Only curated google packages are hidden — never the whole prefix.
        google = {
            key for key in ANDROID_SYSTEM_KEYS if key.startswith("com.google.android.")
        }
        assert google == {
            "com.google.android.packageinstaller",
            "com.google.android.gms",
            "com.google.android.inputmethod.latin",
            "com.google.android.apps.nexuslauncher",
        }

    def test_windows_desktop_shell_hidden(self):
        assert "dwm.exe" in WINDOWS_SYSTEM_KEYS
        assert "searchhost.exe" in WINDOWS_SYSTEM_KEYS
        assert "textinputhost.exe" in WINDOWS_SYSTEM_KEYS

    def test_file_explorer_stays_visible(self):
        assert "explorer.exe" not in WINDOWS_SYSTEM_KEYS
        assert "chrome.exe" not in WINDOWS_SYSTEM_KEYS

    def test_keys_are_lowercase_with_extension(self):
        assert all(key.islower() for key in WINDOWS_SYSTEM_KEYS)
        assert all(key.endswith(".exe") for key in WINDOWS_SYSTEM_KEYS)
        assert all(key.islower() for key in ANDROID_SYSTEM_KEYS)

    def test_platform_map_covers_both(self):
        assert SYSTEM_KEYS_BY_PLATFORM[PLATFORM_ANDROID] is ANDROID_SYSTEM_KEYS
        assert SYSTEM_KEYS_BY_PLATFORM[PLATFORM_WINDOWS] is WINDOWS_SYSTEM_KEYS


class TestEffectiveSystemKeys:
    def test_single_platform(self):
        assert effective_system_keys((PLATFORM_ANDROID,)) == ANDROID_SYSTEM_KEYS

    def test_windows_platform(self):
        assert effective_system_keys((PLATFORM_WINDOWS,)) == WINDOWS_SYSTEM_KEYS

    def test_union_of_both(self):
        keys = effective_system_keys((PLATFORM_ANDROID, PLATFORM_WINDOWS))
        assert keys == ANDROID_SYSTEM_KEYS | WINDOWS_SYSTEM_KEYS

    def test_unknown_platform_contributes_nothing(self):
        assert effective_system_keys(("atari",)) == set()

    def test_user_extras_normalized(self):
        keys = effective_system_keys(
            (PLATFORM_ANDROID,), ("  Com.Foo.Bar ", "Chrome.EXE")
        )
        assert "com.foo.bar" in keys
        assert "chrome.exe" in keys

    def test_blank_extras_ignored(self):
        keys = effective_system_keys((PLATFORM_ANDROID,), ("   ", ""))
        assert keys == ANDROID_SYSTEM_KEYS
