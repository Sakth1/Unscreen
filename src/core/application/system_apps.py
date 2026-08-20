"""Curated lists of system-level apps hidden from usage dashboards.

Some foreground entries are OS chrome, not user activity: the Android
launcher (One UI Home), package installers, permission prompts, IME
keyboards, and on Windows the desktop shell (`dwm.exe` when clicking the
desktop), search overlays and IME hosts. ``AnalyticsStore`` filters these
out of the dashboard totals so shares reflect real usage (F6).

The lists are deliberately *curated*: blanket prefixes like
``com.google.android.*`` would hide real apps (YouTube, Maps, Gmail), so
each entry names one specific package / process. ``explorer.exe`` stays
visible — File Explorer is genuine user activity. Users can extend the
list with their own keys via the ``hidden_app_keys`` config setting.
"""

PLATFORM_ANDROID = "android"
PLATFORM_WINDOWS = "windows"
ALL_PLATFORMS = "*"

ANDROID_SYSTEM_KEYS = frozenset(
    {
        # Stock launchers (the home screen is not an activity)
        "com.sec.android.app.launcher",
        "com.android.launcher",
        "com.android.launcher3",
        "com.google.android.apps.nexuslauncher",
        "com.miui.home",
        "com.oplus.launcher",
        "com.coloros.launcher",
        "com.huawei.android.launcher",
        "com.huawei.android.launcher3",
        # System shell and settings
        "com.android.systemui",
        "com.android.settings",
        "com.android.providers.settings",
        # Package installers / permission prompts
        "com.google.android.packageinstaller",
        "com.android.packageinstaller",
        "com.android.permissioncontroller",
        # Stock keyboards (IME overlays)
        "com.google.android.inputmethod.latin",
        "com.android.inputmethod.latin",
        "com.sec.android.inputmethod",
        "com.samsung.android.honeyboard",
        # Telephony services
        "com.android.phone",
        "com.android.server.telecom",
        "com.android.incallui",
        # Background Google services that occasionally surface
        "com.google.android.gms",
    }
)

WINDOWS_SYSTEM_KEYS = frozenset(
    {
        "dwm.exe",  # Desktop Window Manager (clicking the desktop)
        "searchhost.exe",  # Windows Search overlay
        "shellexperiencehost.exe",  # Taskbar / action center
        "startmenuexperiencehost.exe",
        "runtimebroker.exe",
        "textinputhost.exe",  # Touch keyboard / IME overlay
        "ctfmon.exe",  # Text services framework
        "sihost.exe",  # Shell infrastructure host
        "lockapp.exe",  # Lock screen
    }
)

SYSTEM_KEYS_BY_PLATFORM = {
    PLATFORM_ANDROID: ANDROID_SYSTEM_KEYS,
    PLATFORM_WINDOWS: WINDOWS_SYSTEM_KEYS,
}


def effective_system_keys(
    platforms: tuple[str, ...], extra_keys: tuple[str, ...] = ()
) -> set[str]:
    """The system-app keys to exclude for ``platforms`` plus user extras.

    ``platforms`` accepts :data:`PLATFORM_ANDROID`, :data:`PLATFORM_WINDOWS`
    and :data:`ALL_PLATFORMS`. ``extra_keys`` come from the
    ``hidden_app_keys`` config setting and are normalized to lowercase so
    ``Chrome.EXE`` and ``chrome.exe`` match the same stored key.
    """
    keys: set[str] = set()
    for platform in platforms:
        keys |= SYSTEM_KEYS_BY_PLATFORM.get(platform, set())
    for key in extra_keys:
        normalized = key.strip().lower()
        if normalized:
            keys.add(normalized)
    return keys
