import contextlib
import logging

import flet as ft

logger = logging.getLogger(__name__)


def safe_pop_dialog(page: ft.Page) -> None:
    """Close the topmost dialog while tolerating detached-control errors."""
    with contextlib.suppress(IndexError, RuntimeError):
        page.pop_dialog()


def safe_update(control: ft.Control) -> None:
    """Update a Flet control while tolerating detached-control errors."""
    try:
        control.update()
    except RuntimeError as exc:
        logger.debug("safe_update suppressed RuntimeError: %s", exc, exc_info=True)
    except Exception as exc:
        logger.warning(
            "safe_update suppressed unexpected error: %s", exc, exc_info=True
        )


def show_snack_bar(page: ft.Page, message: str) -> None:
    """Show a transient snack bar via the page overlay (flet 0.86 pattern)."""
    snack = ft.SnackBar(
        content=ft.Text(message),
        open=True,
    )
    page.overlay.append(snack)
    safe_update(page)


def apply_accent_theme(page: ft.Page, theme_name: str) -> None:
    """Apply the named accent theme (seed color) to light and dark schemes."""
    from core.theme import theme_seed

    seed = theme_seed(theme_name)
    page.theme = ft.Theme(color_scheme_seed=seed)
    page.dark_theme = ft.Theme(color_scheme_seed=seed)
