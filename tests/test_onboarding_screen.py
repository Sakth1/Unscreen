"""Onboarding flow: screen structure, page wiring, layout modes, callbacks."""

import types
from unittest.mock import patch

import flet as ft
from sweep_helpers import mock_page

_MEDIUM_WIDTH = 600.0
_CARD_MAX_WIDTH = 680.0
_CARD_HEIGHT = 500.0
_BRAND_PANEL_WIDTH = 260.0


def _walk_controls(control):
    """Yield a control and its descendants (controls lists + container content)."""
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from _walk_controls(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk_controls(content)


def _make_screen(width=400.0, config=None):
    page = mock_page()
    page.window.width = width
    page.window.height = 800.0
    return page


def _make_config():
    from core.config_manager import ConfigManager

    config = ConfigManager()
    config.load()
    return config


def _make_screen_with_config(width=400.0):
    page = _make_screen(width)
    return page, _make_config()


def _page_change(index: int):
    return types.SimpleNamespace(data=str(index))


class TestOnboardingScreenStructure:
    def test_renders_three_pages(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        pages = [c for c in _walk_controls(screen) if isinstance(c, ft.PageView)]
        assert len(pages) == 1
        assert len(pages[0].controls) == 3

    def test_each_page_has_hero_title_and_body(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        for page in page_view.controls:
            texts = [c.value for c in _walk_controls(page) if isinstance(c, ft.Text)]
            assert len(texts) >= 2, "each page needs a title and a body"
            assert all(texts), "no empty title/body text"

    def test_dot_indicator_has_three_dots_first_active(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        dots = [
            c
            for c in _walk_controls(screen)
            if isinstance(c, ft.Container) and c.height == 8.0
        ]
        assert len(dots) == 3
        assert len([d for d in dots if d.width == 24.0]) == 1
        assert len([d for d in dots if d.width == 8.0]) == 2

    def test_skip_and_action_buttons_present(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        buttons = [c for c in _walk_controls(screen) if isinstance(c, ft.FilledButton)]
        skips = [c for c in _walk_controls(screen) if isinstance(c, ft.TextButton)]
        assert len(buttons) == 1
        assert len(skips) == 1
        assert buttons[0].height == 48
        assert skips[0].height == 48

    def test_wrapped_in_safe_area(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        assert any(isinstance(c, ft.SafeArea) for c in _walk_controls(screen))


class TestOnboardingPaging:
    def test_page_change_moves_dot_and_action(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        page_view.on_change(_page_change(1))

        active = [d for d in screen._dots if d.width == 24.0]
        assert len(active) == 1
        assert screen.action_button.content.controls[0].value == "Next"

        page_view.on_change(_page_change(2))
        active = [d for d in screen._dots if d.width == 24.0]
        assert screen._dots.index(active[0]) == 2
        assert screen.action_button.content.controls[0].value == "Get Started"

    def test_next_animates_page_view_forward(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        screen.action_button.on_click(None)

        args = screen._page_ref.run_task.call_args.args
        assert args[0] == page_view.go_to_page
        assert args[1] == 1

    def test_get_started_completes_with_checkmark(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        done = []
        screen = OnboardingScreen(_make_screen(), on_done=lambda: done.append(1))
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        page_view.on_change(_page_change(2))
        screen.action_button.on_click(None)

        assert done == [1]
        icon = screen._action_icon.content
        assert icon.icon == ft.Icons.CHECK

    def test_skip_completes_without_navigating(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        done = []
        screen = OnboardingScreen(_make_screen(), on_done=lambda: done.append(1))
        screen.skip_button.on_click(None)

        assert done == [1]
        assert screen._page_ref.run_task.call_count == 0


class TestOnboardingLayoutModes:
    def test_compact_layout_is_full_bleed(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(400.0), on_done=lambda: None)
        card = screen.content.content
        assert isinstance(card, ft.Container)
        assert card.width is None
        assert card.padding == ft.Padding.all(24)

    def test_medium_layout_is_split_panel_card(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(900.0), on_done=lambda: None)
        row = screen.content.content
        assert isinstance(row, ft.Row)
        card = row.controls[0]
        assert card.width == _CARD_MAX_WIDTH
        assert card.height == _CARD_HEIGHT
        assert card.clip_behavior == ft.ClipBehavior.ANTI_ALIAS

    def test_medium_layout_has_brand_panel_with_tagline(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(900.0), on_done=lambda: None)
        row = screen.content.content
        card = row.controls[0]
        panels = [
            c
            for c in _walk_controls(card)
            if isinstance(c, ft.Container) and c.width == _BRAND_PANEL_WIDTH
        ]
        assert len(panels) == 1
        texts = [c.value for c in _walk_controls(panels[0]) if isinstance(c, ft.Text)]
        assert any("Know where your time goes" in t for t in texts)

    def test_medium_layout_pages_are_bounded_containers(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(900.0), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        for page in page_view.controls:
            assert isinstance(page, ft.Container)
            assert page.bgcolor is not None
            assert page.width is None

    def test_width_boundary_is_600(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        compact = OnboardingScreen(_make_screen(599.0), on_done=lambda: None)
        assert isinstance(compact.content.content, ft.Container)
        medium = OnboardingScreen(_make_screen(_MEDIUM_WIDTH), on_done=lambda: None)
        assert isinstance(medium.content.content, ft.Row)


class TestOnboardingAutoStart:
    def _desktop_screen(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        page, config = _make_screen_with_config(900.0)
        return OnboardingScreen(page, on_done=lambda: None, config=config), config

    def test_desktop_page3_has_auto_start_switch(self):
        screen, _config = self._desktop_screen()
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        switches = [
            c for c in _walk_controls(page_view.controls[2]) if isinstance(c, ft.Switch)
        ]
        assert len(switches) == 1
        assert switches[0].value is False
        texts = [
            c.value
            for c in _walk_controls(page_view.controls[2])
            if isinstance(c, ft.Text)
        ]
        assert any("Start with Windows" in t for t in texts)

    def test_desktop_page3_has_no_permission_chip(self):
        screen, _config = self._desktop_screen()
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        texts = [
            c.value
            for c in _walk_controls(page_view.controls[2])
            if isinstance(c, ft.Text)
        ]
        assert not any("Usage access" in t for t in texts)

    def test_switch_reflects_saved_config(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        page, config = _make_screen_with_config(900.0)
        config.auto_start_enabled = True
        screen = OnboardingScreen(page, on_done=lambda: None, config=config)
        assert screen._auto_start_switch.value is True

    def test_toggle_on_persists_config(self):
        screen, config = self._desktop_screen()
        event = types.SimpleNamespace(control=screen._auto_start_switch)
        with patch("core.auto_start.enable", return_value=True) as enable:
            screen._auto_start_switch.value = True
            screen._on_auto_start_changed(event)
        enable.assert_called_once()
        assert config.auto_start_enabled is True

    def test_toggle_off_disables_auto_start(self):
        screen, config = self._desktop_screen()
        config.auto_start_enabled = True
        event = types.SimpleNamespace(control=screen._auto_start_switch)
        with patch("core.auto_start.disable", return_value=True) as disable:
            screen._auto_start_switch.value = False
            screen._on_auto_start_changed(event)
        disable.assert_called_once()
        assert config.auto_start_enabled is False

    def test_toggle_failure_reverts_switch(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        page, config = _make_screen_with_config(900.0)
        screen = OnboardingScreen(page, on_done=lambda: None, config=config)
        event = types.SimpleNamespace(control=screen._auto_start_switch)
        with patch("core.auto_start.enable", return_value=False):
            screen._auto_start_switch.value = True
            screen._on_auto_start_changed(event)
        assert screen._auto_start_switch.value is False
        assert config.auto_start_enabled is False


class TestOnboardingPermissionStatus:
    def test_status_shown_on_android(self):
        from UI.screens.onboarding_screen import OnboardingScreen
        from utils.platform import OSType

        with (
            patch(
                "UI.screens.onboarding_screen.detect_os",
                return_value=OSType.ANDROID,
            ),
            patch(
                "core.collectors.android.usage_stats.check_usage_stats_permission",
                return_value=False,
            ),
        ):
            screen = OnboardingScreen(_make_screen(400.0), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        texts = [
            c.value
            for c in _walk_controls(page_view.controls[2])
            if isinstance(c, ft.Text)
        ]
        assert any("Usage access needed" in t for t in texts)

    def test_granted_status_on_android(self):
        from UI.screens.onboarding_screen import OnboardingScreen
        from utils.platform import OSType

        with (
            patch(
                "UI.screens.onboarding_screen.detect_os",
                return_value=OSType.ANDROID,
            ),
            patch(
                "core.collectors.android.usage_stats.check_usage_stats_permission",
                return_value=True,
            ),
        ):
            screen = OnboardingScreen(_make_screen(400.0), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        texts = [
            c.value
            for c in _walk_controls(page_view.controls[2])
            if isinstance(c, ft.Text)
        ]
        assert any("Usage access granted" in t for t in texts)

    def test_no_status_chip_off_android(self):
        from UI.screens.onboarding_screen import OnboardingScreen

        screen = OnboardingScreen(_make_screen(400.0), on_done=lambda: None)
        page_view = next(
            c for c in _walk_controls(screen) if isinstance(c, ft.PageView)
        )
        texts = [
            c.value
            for c in _walk_controls(page_view.controls[2])
            if isinstance(c, ft.Text)
        ]
        assert not any("Usage access" in t for t in texts)
