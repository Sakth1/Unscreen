from __future__ import annotations

from dataclasses import field
from typing import Callable, Optional

import flet as ft

from UI.layout.models import AppLayout, NavBarMetrics, NavigationChangeData


@ft.control
class CustomNavigationBarDestination(ft.Container):
    """One pill: icon when unselected, label when selected (toggleable)."""

    icon: str = ft.Icons.HELP
    label: str = ""
    selected: bool = False
    on_select: Optional[Callable[["CustomNavigationBarDestination"], None]] = None

    def init(self):
        self._icon = ft.Icon(icon=self.icon, color=self._color())
        self._text = ft.Text(self.label, color=self._color(), size=12)
        self.padding = ft.padding.Padding.only(top=4, bottom=4, left=8, right=8)
        self.border_radius = 12
        self.ink = True
        self.animate = 200
        self.on_click = self._handle_click
        self._render()

    def _color(self) -> str:
        return ft.Colors.WHITE if self.selected else ft.Colors.WHITE_54

    def _render(self) -> None:
        self.content = self._text if self.selected else self._icon
        self.bgcolor = ft.Colors.WHITE_10 if self.selected else None
        self._icon.color = self._color()
        self._text.color = self._color()

    def set_selected(self, value: bool) -> bool:
        if value == self.selected:
            return False
        self.selected = value
        self._render()
        return True

    def _handle_click(self, e) -> None:
        if self.on_select:
            self.on_select(self)

    def apply_metrics(self, metrics: NavBarMetrics) -> None:
        self.padding = ft.padding.Padding.only(
            top=8,
            bottom=8,
            left=metrics.destination_padding,
            right=metrics.destination_padding,
        )


@ft.control
class CustomNavigationBar(ft.Container):
    """Floating pill-style bottom navigation bar.

    Renders each destination as a :class:`CustomNavigationBarDestination`
    inside a centered ``ft.Row``. Margins and paddings are re-derived from
    the resolved :class:`AppLayout` via :meth:`apply_layout`, so the pill
    keeps clearing the system gesture area on every platform.
    """

    destinations: list[CustomNavigationBarDestination] = field(
        default_factory=list, metadata={"skip": True}
    )
    selected_index: int = 0
    label_behavior: Optional[ft.NavigationBarLabelBehavior] = None
    layout: Optional[AppLayout] = field(default=None, metadata={"skip": True})
    on_change: Optional[Callable[[ft.Event], None]] = None

    def init(self):
        self._layout: Optional[AppLayout] = None
        self.bgcolor = ft.Colors.SURFACE_CONTAINER
        self.border_radius = 12
        self.margin = ft.margin.Margin(left=16, right=16, bottom=24)
        self.final_destinations: list[CustomNavigationBarDestination] = [
            x for x in self.destinations if x is not None
        ]
        for i, dest in enumerate(self.final_destinations):
            if dest is not None:
                dest.on_select = lambda d, i=i: self._select(i)
        self.content = ft.Row(
            controls=self.final_destinations,
            alignment=ft.MainAxisAlignment.SPACE_EVENLY,
            tight=True,
            run_spacing=0,
        )
        self._select(self.selected_index, app_init=True)

    def before_update(self):
        self._sync_selection()

    def select_index(self, index: int, app_init: bool = False) -> None:
        if index == self.selected_index:
            return
        self.selected_index = index
        changed = self._sync_selection()
        for dest in changed:
            if dest.parent is not None:
                dest.update()
        if self.on_change and not app_init:
            dest = self._destination_at(index)
            self.on_change(
                ft.Event(
                    name="FloatingNavigationChange",
                    control=self,
                    data=NavigationChangeData(
                        index=index, label=dest.label if dest is not None else ""
                    ),
                )
            )

    def _destination_at(self, index: int) -> Optional[CustomNavigationBarDestination]:
        if 0 <= index < len(self.final_destinations):
            return self.final_destinations[index]
        return None

    def _select(self, index: int, app_init: bool = False) -> None:
        self.select_index(index, app_init)

    def _sync_selection(self) -> list[CustomNavigationBarDestination]:
        changed: list[CustomNavigationBarDestination] = []
        for i, dest in enumerate(self.final_destinations):
            if dest is not None and dest.set_selected(i == self.selected_index):
                changed.append(dest)
        return changed

    def apply_layout(self, layout: AppLayout) -> None:
        """Re-derive margin and destination padding from a resolved layout."""
        self._layout = layout
        metrics: NavBarMetrics = layout.nav_bar_metrics
        self.margin = ft.margin.Margin(
            left=metrics.margin_left,
            right=metrics.margin_right,
            bottom=metrics.margin_bottom,
        )
        for dest in self.final_destinations:
            if dest is not None:
                dest.apply_metrics(metrics)
