import logging
from typing import Any, Callable, Optional

import flet as ft

import core.auto_start as auto_start
from core.config_manager import ConfigManager
from core.theme import theme_is_valid, theme_label, theme_names
from UI.components.card_section import CardSection
from UI.screens.settings.builders import section_scaffold
from UI.theme import apply_accent_theme
from utils.flet_helpers import show_snack_bar
from utils.platform import OSType, detect_os

logger = logging.getLogger(__name__)

#: (watcher name, human label) — platform-specific, resolved at construction.
_WINDOWS_WATCHERS = [
    ("foreground", "Foreground app"),
    ("afk", "Activity / AFK"),
    ("power", "Power state"),
]

_ANDROID_WATCHERS = [
    ("android_foreground", "Foreground app"),
    ("android_app_usage", "App usage"),
    ("android_afk", "Presence"),
    ("android_power", "Power"),
]

#: (config value, human label) for the theme segmented button.
_THEME_OPTIONS = [
    ("system", "System"),
    ("light", "Light"),
    ("dark", "Dark"),
]

_THEME_TO_FT = {
    "system": ft.ThemeMode.SYSTEM,
    "light": ft.ThemeMode.LIGHT,
    "dark": ft.ThemeMode.DARK,
}

_DEFAULT_INTERVALS: dict[str, float] = {
    "foreground": 2.0,
    "afk": 5.0,
    "power": 60.0,
    "android_foreground": 10.0,
    "android_app_usage": 60.0,
    "android_afk": 5.0,
    "android_power": 60.0,
}


class General(ft.Container):
    """General settings section rendered under ``/settings/general``.

    Dependencies are injected by the app shell so the section stays
    headless-testable: ``config`` is required, ``page`` may be ``None``
    until the section is mounted.
    """

    def __init__(
        self,
        config: ConfigManager,
        collection_manager: Any = None,
        page: ft.Page | None = None,
        on_back: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._config = config or ConfigManager()
        self._collection_manager = collection_manager
        self._page = page
        self._watcher_fields: dict[str, ft.TextField] = {}

        is_android = detect_os() == OSType.ANDROID
        watchers = _ANDROID_WATCHERS if is_android else _WINDOWS_WATCHERS

        self._collection_switch = ft.Switch(
            value=self._config.collection_enabled,
            label="Track my usage",
            on_change=self._on_collection_changed,
        )
        self._url_switch = ft.Switch(
            value=self._config.url_extraction_enabled,
            label="Track browser page details",
            on_change=self._on_url_changed,
        )
        self._hide_system_switch = ft.Switch(
            value=self._config.hide_system_apps,
            label="Hide system apps in usage",
            on_change=self._on_hide_system_changed,
        )
        self._hidden_keys_field = ft.TextField(
            value=", ".join(self._config.hidden_app_keys),
            label="Also hide (comma-separated app keys)",
            dense=True,
            hint_text="com.example.app, myapp.exe",
            on_change=self._on_hidden_keys_changed,
        )
        self._watcher_toggles: dict[str, ft.Switch] = {}
        watcher_rows = self._build_watcher_rows(watchers)

        self._theme_btn = ft.SegmentedButton(
            segments=[
                ft.Segment(value=value, label=ft.Text(label))
                for value, label in _THEME_OPTIONS
            ],
            selected=[
                value for value, _ in _THEME_OPTIONS if value == self._config.theme_mode
            ],
            on_change=self._on_theme_changed,
        )
        self._theme_picker = ft.Dropdown(
            label="Accent color",
            options=[
                ft.dropdown.Option(key=name, text=theme_label(name))
                for name in theme_names()
            ],
            value=self._config.theme,
            on_select=self._on_accent_theme_changed,
        )
        self._maximized_switch = ft.Switch(
            value=self._config.start_maximized,
            label="Start the window maximized",
            on_change=self._on_maximized_changed,
        )
        self._autostart_switch = ft.Switch(
            value=self._config.auto_start_enabled,
            label="Start Unscreen when I log in",
            on_change=self._on_autostart_changed,
        )
        self._idle_field = ft.TextField(
            value=f"{self._config.afk_idle_threshold_s:g}",
            label="Idle after (seconds)",
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._on_idle_threshold_changed,
        )
        self._away_field = ft.TextField(
            value=f"{self._config.afk_away_threshold_s:g}",
            label="Away after (seconds)",
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._on_away_threshold_changed,
        )

        cards = [
            CardSection(
                "Tracking",
                [
                    self._collection_switch,
                    self._url_switch,
                    *watcher_rows,
                ],
            ),
            CardSection(
                "Usage",
                [
                    self._hide_system_switch,
                    self._hidden_keys_field,
                ],
            ),
            CardSection(
                "Appearance",
                [
                    ft.Row(
                        controls=[ft.Text("Theme mode"), self._theme_btn],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        wrap=True,
                        run_spacing=8,
                    ),
                    ft.Row(
                        controls=[ft.Text("Accent color"), self._theme_picker],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        wrap=True,
                        run_spacing=8,
                    ),
                    self._maximized_switch,
                ],
            ),
            CardSection("Startup", [self._autostart_switch]),
        ]
        if not is_android:
            cards.append(
                CardSection(
                    "Activity thresholds",
                    [
                        ft.Text("When is the machine considered idle or away?"),
                        self._idle_field,
                        self._away_field,
                    ],
                )
            )

        self.content = section_scaffold("General settings", cards, on_back=on_back)

    # ── Control builders ──────────────────────────────────────────────────

    def _build_watcher_rows(self, watchers: list[tuple[str, str]]) -> list[ft.Row]:
        enabled = set(self._config.watchers_enabled)
        rows: list[ft.Row] = []
        for name, label in watchers:
            toggle = ft.Switch(
                value=name in enabled,
                label=label,
                on_change=lambda e, n=name: self._on_watcher_toggled(e, n),
            )
            interval = self._config.get_interval(
                name, _DEFAULT_INTERVALS.get(name, 60.0)
            )
            field = ft.TextField(
                value=f"{interval:g}",
                label="interval (s)",
                dense=True,
                width=120,
                disabled=not toggle.value,
                keyboard_type=ft.KeyboardType.NUMBER,
                on_submit=lambda e, n=name: self._on_interval_submitted(e, n),
            )
            self._watcher_fields[name] = field
            self._watcher_toggles[name] = toggle
            # Wrapped rows cannot contain expand children (Flutter wraps
            # reject flex children); SPACE_BETWEEN does the right-edge push.
            rows.append(
                ft.Row(
                    controls=[toggle, field],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                    run_spacing=8,
                )
            )
        return rows

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_collection_changed(self, event: ft.ControlEvent) -> None:
        enabled = bool(getattr(event.control, "value", False))
        cm = self._collection_manager
        if cm is not None:
            # pause()/resume() persist the config and talk to the scheduler.
            if enabled and cm.is_paused:
                cm.resume()
            elif not enabled and not cm.is_paused:
                cm.pause()
        else:
            self._config.collection_enabled = enabled
            self._config.save()
        self._toast(f"Tracking {'enabled' if enabled else 'paused'}")

    def _on_url_changed(self, event: ft.ControlEvent) -> None:
        self._config.url_extraction_enabled = bool(
            getattr(event.control, "value", False)
        )
        self._config.save()
        # The foreground watcher reads this at construction time.
        self._restart_if_running()
        self._toast("Browser tracking will apply on the next collection cycle")

    def _on_hide_system_changed(self, event: ft.ControlEvent) -> None:
        self._config.hide_system_apps = bool(getattr(event.control, "value", False))
        self._config.save()
        self._toast("Usage totals update immediately")

    def _on_hidden_keys_changed(self, event: ft.ControlEvent) -> None:
        raw = getattr(event.control, "value", "") or ""
        keys = [token.strip() for token in raw.split(",") if token.strip()]
        if keys == self._config.hidden_app_keys:
            return
        self._config.hidden_app_keys = keys
        self._config.save()

    def _on_watcher_toggled(self, event: ft.ControlEvent, name: str) -> None:
        current = set(self._config.watchers_enabled)
        if getattr(event.control, "value", False):
            current.add(name)
        else:
            current.discard(name)
        self._config.watchers_enabled = sorted(current)
        self._config.save()
        self._watcher_fields[name].disabled = not bool(event.control.value)
        self._restart_if_running()

    def _on_interval_submitted(self, event: ft.ControlEvent, name: str) -> None:
        seconds = _parse_positive_float(event.control.value)
        if seconds is None:
            self._toast("Enter a valid number of seconds")
            return
        self._config.set_interval(name, seconds)
        self._config.save()
        self._restart_if_running()

    def _on_theme_changed(self, event: ft.ControlEvent) -> None:
        selected = getattr(event.control, "selected", None)
        if not selected:
            return
        mode = next(iter(selected))
        self._config.theme_mode = mode
        self._config.save()
        ft_mode = _THEME_TO_FT.get(mode, ft.ThemeMode.SYSTEM)
        if self._page is not None:
            try:
                self._page.theme_mode = ft_mode
                self._page.update()
            except Exception:
                logger.exception("Failed to apply theme mode %s", mode)

    def _on_accent_theme_changed(self, event: ft.ControlEvent) -> None:
        name = getattr(event.control, "value", None) or getattr(event, "data", None)
        if not name or not theme_is_valid(name):
            return
        self._config.theme = name
        self._config.save()
        if self._page is not None:
            try:
                apply_accent_theme(self._page, name)
                self._page.update()
            except Exception:
                logger.exception("Failed to apply accent theme %s", name)

    def _on_maximized_changed(self, event: ft.ControlEvent) -> None:
        self._config.start_maximized = bool(getattr(event.control, "value", False))
        self._config.save()
        self._toast("Applies the next time Unscreen starts")

    def _on_autostart_changed(self, event: ft.ControlEvent) -> None:
        enabled = bool(getattr(event.control, "value", False))
        ok = auto_start.enable() if enabled else auto_start.disable()
        if not ok:
            event.control.value = self._config.auto_start_enabled
            self._toast("Auto-start could not be changed on this device")
            return
        self._config.auto_start_enabled = enabled
        self._config.save()

    def _on_idle_threshold_changed(self, event: ft.ControlEvent) -> None:
        value = _parse_positive_float(event.control.value)
        if value is None:
            return
        self._config.afk_idle_threshold_s = value
        self._config.save()

    def _on_away_threshold_changed(self, event: ft.ControlEvent) -> None:
        value = _parse_positive_float(event.control.value)
        if value is None:
            return
        self._config.afk_away_threshold_s = value
        self._config.save()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _restart_if_running(self) -> None:
        cm = self._collection_manager
        if cm is None or not getattr(cm, "is_running", False):
            return
        if self._page is None:
            logger.info("Collection running; restart deferred until a page is attached")
            return
        self._page.run_task(cm.restart)

    def _toast(self, message: str) -> None:
        if self._page is not None:
            show_snack_bar(self._page, message)

    def on_sub_route(self, route: str) -> None:
        """Refresh control states when the section becomes visible."""
        self._collection_switch.value = self._config.collection_enabled
        self._url_switch.value = self._config.url_extraction_enabled
        self._hide_system_switch.value = self._config.hide_system_apps
        self._hidden_keys_field.value = ", ".join(self._config.hidden_app_keys)
        self._autostart_switch.value = self._config.auto_start_enabled
        self._maximized_switch.value = self._config.start_maximized
        self._theme_btn.selected = [self._config.theme_mode]
        self._theme_picker.value = self._config.theme
        for name, toggle in self._watcher_toggles.items():
            toggle.value = name in self._config.watchers_enabled
            field = self._watcher_fields.get(name)
            if field is not None:
                field.disabled = not toggle.value
        if self.parent is not None:
            self.update()


def _parse_positive_float(value: str | None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed
