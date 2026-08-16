"""First-run onboarding flow (Material 3).

Three swipeable pages — Welcome, How It Works, and a final setup page
(Permission Setup on Android, "You're all set" + auto-start elsewhere) —
with an animated dot indicator and a bottom action bar. Compact layouts
(<600dp) fill the screen; medium+ layouts center a split-panel card: a
brand panel on the left (Discord/Spotify-style) and the step content on
the right. The flow replaces the normal shell chrome until the user
finishes or skips it.
"""

import logging

import flet as ft

from UI.screens.base_screen import BaseScreen
from utils.constants import DEFAULT_PAGE_WIDTH
from utils.platform import OSType, detect_os

logger = logging.getLogger(__name__)

_MEDIUM_WIDTH = 600.0
_CARD_MAX_WIDTH = 680.0
_CARD_HEIGHT = 500.0
_BRAND_PANEL_WIDTH = 260.0
_TOUCH_MIN_HEIGHT = 48.0
_PAGE_TRANSITION_MS = 200
_DOT_SIZE = 8.0
_DOT_WIDTH_IDLE = _DOT_SIZE
_DOT_WIDTH_ACTIVE = 24.0


class OnboardingScreen(BaseScreen):
    def __init__(self, page: ft.Page, on_done, config=None):
        super().__init__()
        self.title = "Onboarding"
        self._page_ref = page
        self._on_done = on_done
        self._config = config
        self._current_index = 0

        width = (
            getattr(page, "width", None)
            or getattr(page.window, "width", None)
            or DEFAULT_PAGE_WIDTH
        )
        self._compact = float(width) < _MEDIUM_WIDTH

        self._action_icon = ft.AnimatedSwitcher(
            content=ft.Icon(ft.Icons.ARROW_FORWARD, size=20),
            transition=ft.AnimatedSwitcherTransition.SCALE,
            duration=_PAGE_TRANSITION_MS,
        )
        self.action_button = ft.FilledButton(
            content=ft.Row(
                spacing=8,
                controls=[ft.Text("Next"), self._action_icon],
            ),
            height=_TOUCH_MIN_HEIGHT,
            on_click=self._go_next,
        )
        self.skip_button = ft.TextButton(
            "Skip",
            height=_TOUCH_MIN_HEIGHT,
            on_click=lambda _e: self._skip(),
        )

        self.page_view = ft.PageView(
            expand=True,
            pad_ends=False,
            controls=[
                self._welcome_page(),
                self._how_it_works_page(),
                self._setup_page(),
            ],
            on_change=self._on_page_changed,
        )

        self._dots = [
            ft.Container(
                width=_DOT_WIDTH_ACTIVE if i == 0 else _DOT_WIDTH_IDLE,
                height=_DOT_SIZE,
                border_radius=_DOT_SIZE / 2,
                bgcolor=(ft.Colors.PRIMARY if i == 0 else ft.Colors.OUTLINE_VARIANT),
                animate=ft.Animation(_PAGE_TRANSITION_MS, ft.AnimationCurve.EASE_OUT),
            )
            for i in range(3)
        ]
        dots_row = ft.Row(
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            controls=self._dots,
        )
        self._dots_row = dots_row

        header = ft.Row(
            alignment=ft.MainAxisAlignment.END,
            controls=[self.skip_button],
        )
        footer = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[dots_row, self.action_button],
        )

        inner = ft.Column(
            expand=True,
            spacing=0,
            controls=[header, self.page_view, footer],
        )
        if self._compact:
            inner = ft.Container(
                content=inner,
                padding=ft.Padding.all(24),
            )
        else:
            inner = self._desktop_card(header, footer, inner)

        self.content = ft.SafeArea(content=inner)

    def _desktop_card(
        self, header: ft.Control, footer: ft.Control, inner: ft.Column
    ) -> ft.Row:
        """Split-panel card: brand panel left, step content right.

        The card has a fixed height so every ``expand`` inside it resolves
        against bounded constraints (unbounded expands render empty on
        Windows desktop — see flet-dev/flet#6646).
        """
        card = ft.Container(
            width=_CARD_MAX_WIDTH,
            height=_CARD_HEIGHT,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            bgcolor=ft.Colors.SURFACE,
            border_radius=28,
            content=ft.Row(
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    self._brand_panel(),
                    ft.Container(
                        expand=True,
                        padding=ft.Padding.all(28),
                        content=inner,
                    ),
                ],
            ),
        )
        return ft.Row(
            expand=True,
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[card],
        )

    def _brand_panel(self) -> ft.Container:
        return ft.Container(
            width=_BRAND_PANEL_WIDTH,
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            border_radius=ft.BorderRadius.only(top_left=28, bottom_left=28),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        width=88,
                        height=88,
                        border_radius=44,
                        bgcolor=ft.Colors.PRIMARY,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Icon(
                            ft.Icons.INSIGHTS,
                            size=44,
                            color=ft.Colors.ON_PRIMARY,
                        ),
                    ),
                    ft.Text(
                        "Unscreen",
                        style=ft.TextThemeStyle.HEADLINE_SMALL,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Know where your time goes",
                        style=ft.TextThemeStyle.BODY_MEDIUM,
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

    def _welcome_page(self) -> ft.Container:
        return self._page_for(
            icon=ft.Icons.INSIGHTS,
            title="Welcome to Unscreen",
            body=(
                "Unscreen quietly records which apps and websites you use, "
                "and for how long — then shows you where your time really goes."
            ),
            compact_title_style=ft.TextThemeStyle.DISPLAY_SMALL,
        )

    def _how_it_works_page(self) -> ft.Container:
        return self._page_for(
            icon=ft.Icons.VISIBILITY,
            title="How it works",
            body=(
                "A background watcher notes the app in the foreground and "
                "your activity level. Every switch becomes a session on the "
                "Timeline, and the Analytics tab turns them into totals and "
                "trends."
            ),
        )

    def _setup_page(self) -> ft.Container:
        if detect_os() == OSType.ANDROID:
            return self._page_for(
                icon=ft.Icons.SHIELD,
                title="Permission setup",
                body=(
                    "On Android, Unscreen needs the \u201cUsage access\u201d "
                    "permission to read which app is in the foreground. Enable it "
                    "in Settings \u2192 Apps \u2192 Special access \u2192 Usage "
                    "access."
                ),
                extra=[self._permission_status()],
            )
        return self._page_for(
            icon=ft.Icons.SHIELD,
            title="You're all set",
            body=(
                "Everything stays on this device — no account, no cloud "
                "upload. Start Unscreen with Windows if you like."
            ),
            extra=[self._auto_start_row()],
        )

    def _page_for(
        self,
        icon: str,
        title: str,
        body: str,
        extra: list[ft.Control] | None = None,
        compact_title_style: str = ft.TextThemeStyle.HEADLINE_SMALL,
    ) -> ft.Container:
        if self._compact:
            return self._build_page(
                icon=icon,
                title=title,
                title_style=compact_title_style,
                body=body,
                extra=extra,
            )
        return self._build_desktop_page(icon=icon, title=title, body=body, extra=extra)

    def _permission_status(self) -> ft.Row:
        from core.collectors.android.usage_stats import check_usage_stats_permission

        granted = check_usage_stats_permission()
        return ft.Row(
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Icon(
                    ft.Icons.CHECK_CIRCLE if granted else ft.Icons.ERROR_OUTLINE,
                    color=ft.Colors.PRIMARY if granted else ft.Colors.ERROR,
                ),
                ft.Text(
                    "Usage access granted" if granted else "Usage access needed",
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    style=ft.TextThemeStyle.BODY_MEDIUM,
                ),
            ],
        )

    def _auto_start_row(self) -> ft.Row:
        switch = ft.Switch(
            value=(
                bool(self._config.auto_start_enabled)
                if self._config is not None
                else False
            ),
            on_change=self._on_auto_start_changed,
        )
        self._auto_start_switch = switch
        return ft.Row(
            spacing=12,
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.POWER_SETTINGS_NEW, color=ft.Colors.PRIMARY),
                ft.Column(
                    tight=True,
                    expand=True,
                    spacing=2,
                    controls=[
                        ft.Text(
                            "Start with Windows",
                            style=ft.TextThemeStyle.BODY_MEDIUM,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            "Launch Unscreen automatically when you sign in",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                ),
                switch,
            ],
        )

    def _on_auto_start_changed(self, event) -> None:
        if self._config is None:
            return
        from core.auto_start import disable as disable_auto_start
        from core.auto_start import enable as enable_auto_start

        enabled = bool(getattr(event.control, "value", False))
        try:
            ok = enable_auto_start() if enabled else disable_auto_start()
        except Exception:
            logger.exception("Failed to apply auto-start from onboarding")
            ok = False
        if not ok:
            event.control.value = self._config.auto_start_enabled
            return
        self._config.auto_start_enabled = enabled
        self._config.save()

    def _build_page(
        self,
        icon: str,
        title: str,
        title_style: str,
        body: str,
        extra: list[ft.Control] | None = None,
    ) -> ft.Container:
        hero = ft.Container(
            width=128,
            height=128,
            border_radius=64,
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(icon, size=72, color=ft.Colors.ON_PRIMARY_CONTAINER),
        )
        controls = [
            hero,
            ft.Text(
                title,
                style=title_style,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                body,
                style=ft.TextThemeStyle.BODY_LARGE,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
            ),
        ]
        if extra:
            controls.extend(extra)
        return ft.Container(
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=24,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=controls,
            ),
        )

    def _build_desktop_page(
        self,
        icon: str,
        title: str,
        body: str,
        extra: list[ft.Control] | None = None,
    ) -> ft.Container:
        tile = ft.Container(
            width=56,
            height=56,
            border_radius=14,
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(icon, size=32, color=ft.Colors.ON_PRIMARY_CONTAINER),
        )
        controls = [
            tile,
            ft.Text(
                title,
                style=ft.TextThemeStyle.HEADLINE_SMALL,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Text(
                body,
                style=ft.TextThemeStyle.BODY_LARGE,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if extra:
            controls.extend(extra)
        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border_radius=16,
            padding=ft.Padding.all(20),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=16,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.START,
                controls=controls,
            ),
        )

    def _on_page_changed(self, event) -> None:
        """Swipe (or animated jump) landed on a new page: refresh dots + action."""
        self._current_index = int(event.data)
        self._refresh_dots()
        self._refresh_action()

    def _refresh_dots(self) -> None:
        for i, dot in enumerate(self._dots):
            dot.width = (
                _DOT_WIDTH_ACTIVE if i == self._current_index else _DOT_WIDTH_IDLE
            )
            dot.bgcolor = (
                ft.Colors.PRIMARY
                if i == self._current_index
                else ft.Colors.OUTLINE_VARIANT
            )
        if self.parent is not None:
            self._dots_row.update()

    def _refresh_action(self) -> None:
        last = self._current_index == len(self.page_view.controls) - 1
        text = self.action_button.content.controls[0]
        text.value = "Get Started" if last else "Next"
        self.action_button.on_click = self._finish if last else self._go_next
        if self.parent is not None:
            self.action_button.update()

    def _go_next(self, _event) -> None:
        self._page_ref.run_task(self.page_view.go_to_page, self._current_index + 1)

    def _finish(self, _event) -> None:
        """Get Started: swap the arrow for a checkmark, then leave the flow."""
        self._action_icon.content = ft.Icon(ft.Icons.CHECK, size=20)
        if self.parent is not None:
            self._action_icon.update()
        self._complete()

    def _skip(self) -> None:
        self._complete()

    def _complete(self) -> None:
        if self._on_done is not None:
            self._on_done()
