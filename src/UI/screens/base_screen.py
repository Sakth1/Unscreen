import logging

import flet as ft

from core.state.app_state import KEY_LAYOUT, get_app_state
from UI.layout.models import AppLayout, NavigationDestination

logger = logging.getLogger(__name__)


class BaseScreen(ft.Container):
    def __init__(self, secondary_options: bool = False):
        super().__init__()
        self.title = "BaseScreen"
        self._layout: AppLayout | None = None
        self._secondary_options: bool = secondary_options
        get_app_state().on_change(KEY_LAYOUT, self._on_layout_changed)

    def _on_layout_changed(self, _key: str) -> None:
        layout = get_app_state().layout
        if layout is not None:
            self.apply_layout(layout)

    def apply_layout(self, layout: AppLayout) -> None:
        """Apply layout-derived spacing; the shell owns page-level padding.

        Screens sit inside the padded content container, so they keep zero
        padding of their own. On wide layouts the content is capped at
        ``content_max_width`` and centered by the parent ResponsiveRow.
        """
        self._layout = layout
        if self.parent is not None:
            self.update()

    def _page_update(self):
        self.page.update()

    def _get_secondary_options(self) -> list[NavigationDestination]: ...
