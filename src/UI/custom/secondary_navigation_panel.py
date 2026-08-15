from __future__ import annotations

from dataclasses import field
from typing import Callable, Optional, Union

import flet as ft

from UI.layout.models import (
    AppLayout,
    SecondaryDrawerMetrics,
    SecondaryNavigationChangeData,
    SecondaryNavigationPattern,
)


@ft.control
class SecondaryNavigationDestination(ft.Container):
    """A single secondary-navigation pill (icon + label).

    Parameters
    ----------
    icon:
        Material icon name shown next to the label.
    label:
        Destination title.
    route:
        Sub-route this destination owns (e.g. ``/settings/data``).
    selected:
        Whether this pill is currently selected.
    on_select:
        Callback invoked when this pill is clicked.
    """

    icon: str = ft.Icons.HELP
    label: str = ""
    route: str = ""
    selected: bool = False
    on_select: Optional[Callable[["SecondaryNavigationDestination"], None]] = None

    def init(self):
        self._icon = ft.Icon(icon=self.icon, color=self._color())
        self._text = ft.Text(self.label, color=self._color(), size=12)
        self.padding = ft.padding.Padding.only(top=4, bottom=4, left=8, right=8)
        self._display_label = True
        self.content_controls: list[ft.Icon] | list[Union[ft.Icon, ft.Text]] = (
            [self._icon, self._text] if self._display_label else [self._icon]
        )
        self.content: Optional[ft.Control] = ft.Row(controls=self.content_controls)
        self.border_radius = 10
        self.ink = True
        self.animate = 200
        self.on_click = self._handle_click
        self._render()

    def _render(self) -> None:
        self.content.controls = (
            [self._icon, self._text] if self._display_label else [self._icon]
        )
        self.bgcolor = ft.Colors.WHITE_10 if self.selected else None
        self._icon.color = self._color()
        self._text.color = self._color()
        if self.parent is not None:
            self.update()

    def _color(self) -> str:
        return ft.Colors.WHITE if self.selected else ft.Colors.WHITE_54

    def _handle_click(self, e):
        if self.on_select:
            self.on_select(self)

    def toggle_label(self):
        self._display_label = not self._display_label
        self._render()

    def set_selected(self, value: bool) -> bool:
        if value == self.selected:
            return False
        self.selected = value
        self._render()
        return True

    def apply_metrics(self, metrics: SecondaryDrawerMetrics) -> None:
        self.padding = ft.padding.Padding.only(
            top=4,
            bottom=4,
            left=metrics.destination_padding,
            right=metrics.destination_padding,
        )
        if self.content is not None:
            if self._display_label:
                self.content.alignment = ft.MainAxisAlignment.START
                self.content.spacing = metrics.item_spacing
            else:
                self.content.alignment = ft.MainAxisAlignment.CENTER
                self.content.spacing = 0
        if self.parent is not None:
            self.update()


@ft.control
class SecondaryNavigationPanel(ft.Container):
    """In-screen sub-navigation for a route's secondary sections.

    Renders the destinations of a parent route (e.g. ``/settings``) as
    selectable pills and reports the chosen index via ``on_change``.

    Parameters
    ----------
    destinations:
        The secondary destinations rendered by this panel.
    selected_index:
        Index of the initially selected destination.
    on_change:
        Callback fired with a ``ft.Event`` (control = this panel) whenever the
        selection changes.
    layout:
        Resolved :class:`AppLayout`; drives the panel's geometry.
    """

    destinations: list[SecondaryNavigationDestination] = field(
        default_factory=list, metadata={"skip": True}
    )
    extended: bool = True
    selected_index: int = 0
    on_change: Optional[Callable[[ft.Event], None]] = None
    layout: Optional[AppLayout] = field(default=None, metadata={"skip": True})

    def init(self):
        self._layout: Optional[AppLayout] = None
        self.bgcolor = ft.Colors.SURFACE_CONTAINER

        self.final_destinations: list[SecondaryNavigationDestination] = [
            i for i in self.destinations if i is not None
        ]

        for i, dest in enumerate(self.final_destinations):
            dest.on_select = lambda d, i=i: self._select(i)

        self.content = ft.Column(
            controls=self.final_destinations,
            alignment=ft.MainAxisAlignment.SPACE_EVENLY,
            tight=True,
            expand=True,
            run_spacing=0,
        )

        self.alignment = ft.alignment.Alignment.TOP_LEFT

        # Apply the initial selection without firing ``on_change`` (avoids a
        # spurious navigation while the shell is still being constructed).
        self._sync_selection()

    def select_index(self, index: int) -> None:
        if index == self.selected_index:
            return
        self.selected_index = index
        changed = self._sync_selection()
        for dest in changed:
            if dest.parent is not None:
                dest.update()
        if self.on_change:
            dest = self._destination_at(index)
            self.on_change(
                ft.Event(
                    name="SecondaryNavigationChange",
                    control=self,
                    data=SecondaryNavigationChangeData(
                        index=index,
                        label=dest.label if dest is not None else "",
                        route=dest.route if dest is not None else "",
                    ),
                )
            )

    def _select(self, index: int) -> None:
        self.select_index(index)

    def _destination_at(self, index: int) -> Optional[SecondaryNavigationDestination]:
        if 0 <= index < len(self.final_destinations):
            return self.final_destinations[index]
        return None

    def _sync_selection(self) -> list[SecondaryNavigationDestination]:
        changed: list[SecondaryNavigationDestination] = []
        for i, dest in enumerate(self.final_destinations):
            if dest.set_selected(i == self.selected_index):
                changed.append(dest)
        return changed

    def apply_layout(self, layout: AppLayout) -> None:
        """Re-derive width, padding, spacing, and label mode from a layout.

        Side-panel layouts (tablet landscape, desktop) render the panel
        extended with the resolved metrics; inline layouts (phones, tablet
        portrait) collapse it to zero width so the shell can drop it.
        """
        self._layout = layout
        side_panel = (
            layout.secondary_navigation is SecondaryNavigationPattern.SIDE_PANEL
        )
        self._apply_extended(side_panel)
        self._apply_metrics()

    def _apply_extended(self, extended: bool) -> None:
        if extended == self.extended:
            return
        self.extended = extended
        for dest in self.final_destinations:
            dest.toggle_label()

    def _apply_metrics(self) -> None:
        metrics = self._layout.secondary_navigation_metrics
        self.width = metrics.width
        self.content.run_spacing = metrics.item_spacing
        for dest in self.final_destinations:
            dest.apply_metrics(metrics)
