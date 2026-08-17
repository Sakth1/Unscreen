"""M3 empty-state component: large muted icon, headline, optional body/action."""

from __future__ import annotations

import flet as ft

from UI.components.motion import (
    EMPTY_FADE_MS,
    EMPTY_SCALE_MS,
    entrance_fade,
    is_reduced_motion,
)

#: Icon opacity per M3 empty-state guidance.
_ICON_OPACITY = 0.38

#: Icon size in logical pixels.
_ICON_SIZE = 96

#: Content width cap on non-compact layouts.
_EXPANDED_CONTENT_WIDTH = 400

#: Minimum height of the empty-state area.
_MIN_HEIGHT = 200


class EmptyState(ft.Container):
    """Centered empty-state placeholder.

    Shows a large muted icon, a HeadlineSmall headline, an optional
    BodyMedium body and an optional action control (usually a
    ``FilledButton``). Full-width on compact layouts, capped and centered
    on expanded ones. Enters with a 200ms fade plus scale-in unless reduced
    motion is active.
    """

    def __init__(
        self,
        icon: str,
        headline: str,
        body: str | None = None,
        action: ft.Control | None = None,
        compact: bool = False,
        height: float | None = _MIN_HEIGHT,
    ):
        controls: list[ft.Control] = [
            ft.Icon(
                icon,
                size=_ICON_SIZE,
                color=ft.Colors.with_opacity(_ICON_OPACITY, ft.Colors.ON_SURFACE),
            ),
            ft.Text(
                headline,
                style=ft.TextThemeStyle.HEADLINE_SMALL,
                text_align=ft.TextAlign.CENTER,
            ),
        ]
        if body:
            controls.append(
                ft.Text(
                    body,
                    style=ft.TextThemeStyle.BODY_MEDIUM,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                )
            )
        if action is not None:
            controls.append(action)
        super().__init__(
            content=ft.Container(
                width=None if compact else _EXPANDED_CONTENT_WIDTH,
                content=ft.Column(
                    spacing=16,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=controls,
                ),
            ),
            alignment=ft.Alignment.CENTER,
            padding=16,
            height=height,
            animate_opacity=entrance_fade(EMPTY_FADE_MS),
            animate_scale=(
                None
                if is_reduced_motion()
                else ft.Animation(EMPTY_SCALE_MS, ft.AnimationCurve.EASE_OUT)
            ),
        )
