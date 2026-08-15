"""Theme glue that needs both flet and the core theme catalog."""

import flet as ft


def apply_accent_theme(page: ft.Page, theme_name: str) -> None:
    """Apply the named accent theme (seed color) to light and dark schemes."""
    from core.theme import theme_seed

    seed = theme_seed(theme_name)
    page.theme = ft.Theme(color_scheme_seed=seed)
    page.dark_theme = ft.Theme(color_scheme_seed=seed)
