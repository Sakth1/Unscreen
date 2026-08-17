"""Skeleton loading placeholders built on ``ft.Shimmer`` (M3 motion).

Shapes mirror the layout of the content they stand in for: status-card
shapes for dashboard cards, list rows for timeline sessions. When reduced
motion is active the shimmer wrapper is dropped and the shapes render
static so nothing pulses.
"""

from __future__ import annotations

import flet as ft

from UI.components.motion import SKELETON_PULSE_MS, is_reduced_motion

#: M3 surface tone used for placeholder shapes.
_SHAPE_COLOR = ft.Colors.SURFACE_CONTAINER_HIGH

#: Corner radius for card/row shapes.
_SHAPE_RADIUS = 12

#: Avatar circle diameter for row skeletons.
_AVATAR_SIZE = 40


def shimmer(content: ft.Control) -> ft.Control:
    """Wrap ``content`` in a shimmer sweep; returns it static when reduced."""
    if is_reduced_motion():
        return content
    return ft.Shimmer(
        content=content,
        base_color=ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE),
        highlight_color=ft.Colors.with_opacity(0.35, ft.Colors.ON_SURFACE),
        period=SKELETON_PULSE_MS,
        loop=0,
    )


def _box(
    width: float | None,
    height: float,
    radius: float = _SHAPE_RADIUS,
) -> ft.Container:
    return ft.Container(
        width=width, height=height, bgcolor=_SHAPE_COLOR, border_radius=radius
    )


def status_card_skeleton(height: float = 96) -> ft.Control:
    """Skeleton shaped like an M3 status card (filled-card proportions)."""
    return shimmer(_box(None, height))


def list_row_skeleton(height: float = 64) -> ft.Control:
    """Skeleton shaped like a list row: avatar circle plus two text lines."""
    return shimmer(
        ft.Container(
            height=height,
            content=ft.Row(
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    _box(_AVATAR_SIZE, _AVATAR_SIZE, radius=_AVATAR_SIZE / 2),
                    ft.Column(
                        spacing=8,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        controls=[
                            _box(None, 12, radius=6),
                            _box(180, 12, radius=6),
                        ],
                    ),
                ],
            ),
        )
    )
