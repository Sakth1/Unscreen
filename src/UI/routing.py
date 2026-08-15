from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING

import flet as ft

from core.state.app_state import get_app_state
from UI.layout.models import NavigationChangeData, NavigationDestination

if TYPE_CHECKING:
    from UI.custom.navigation_bar import CustomNavigationBar

logger = logging.getLogger(__name__)


class RouteManager:
    def __init__(
        self,
        page: ft.Page,
        container: ft.Container,
        destinations: list[NavigationDestination],
        section_routes: dict[str, list[str]] | None = None,
        section_views: dict[str, object] | None = None,
    ):
        self._page: ft.Page = page
        self._container: ft.Container = container
        self._destinations: list[NavigationDestination] = list(destinations)
        self._route_views: dict[str, object] = {
            d.route: d.view for d in self._destinations
        }
        #: Sub-routes (e.g. ``/settings/data``) → the section view to render.
        self._section_views: dict[str, object] = dict(section_views or {})
        self._route_to_index: dict[str, int] = {
            d.route: i for i, d in enumerate(self._destinations)
        }
        self._label_to_route: dict[str, str] = {
            d.label: d.route for d in self._destinations
        }
        self._route_to_label: dict[str, str] = {
            d.route: d.label for d in self._destinations
        }
        self._section_routes: dict[str, list[str]] = section_routes or {}
        self.current_route: str = "/dashboard"

    def view_for(self, route: str) -> object:
        """Return the screen registered for a route.

        Sub-routes resolve to their parent section's screen. Example:
        ``view_for("/settings")`` returns the settings screen.
        """
        view = self._route_views.get(route)
        if view is None:
            parent = self._parent_for(route)
            if parent is not None:
                view = self._route_views.get(parent)
        return view

    def navigate(self, route: str) -> None:
        try:
            parent = self._parent_for(route)
            new_view = self._route_views.get(route)
            if new_view is None:
                new_view = self._section_views.get(route)
            if new_view is None and parent is not None:
                new_view = self._route_views.get(parent)

            if new_view is None:
                logger.warning("Unknown route=%s, falling back to /dashboard", route)
                route = "/dashboard"
                parent = None
                new_view = self._route_views.get("/dashboard")

            self._container.content = new_view
            self.current_route = route
            get_app_state().set_route(route)

            on_sub_route = getattr(new_view, "on_sub_route", None)
            if parent is not None and callable(on_sub_route):
                on_sub_route(route)

            with suppress(RuntimeError):
                # Container not attached to a page yet (headless construction).
                self._container.update()

            idx = self._index_for_route(route)
            nav: CustomNavigationBar | None = getattr(
                self._page, "navigation_bar", None
            )
            if nav is not None:
                # Programmatic sync must not re-enter the change handler:
                # the app would re-navigate and clobber the view we just set.
                nav.select_index(idx, app_init=True)

            navigate = getattr(self._page, "navigate", None)
            if callable(navigate):
                navigate(route)
                return
            self._page.run_task(self._push_route, route)

        except Exception:
            logger.exception("Route transition failed route=%s", route)

    async def _push_route(self, route: str) -> None:
        """Navigate from synchronous callbacks without leaking a coroutine."""
        await self._page.push_route(route)

    def _parent_for(self, route: str) -> str | None:
        """Return the section (top-level) route owning *route*, if any."""
        best: str | None = None
        for parent in self._section_routes:
            if route.startswith(f"{parent}/") and (
                best is None or len(parent) > len(best)
            ):
                best = parent
        return best

    def _index_for_route(self, route: str) -> int:
        """Main-nav index for a route, falling back to its parent section."""
        idx = self._route_to_index.get(route)
        if idx is not None:
            return idx
        parent = self._parent_for(route)
        if parent is not None:
            return self._route_to_index.get(parent, 0)
        return 0

    def handle_route_change(self, event) -> None:
        """Handle ``page.on_route_change`` and navigate accordingly.

        If the route is already current (e.g. already set by
        :meth:`swap_view` in the async flow), the event is
        silently ignored to prevent duplicate lifecycle calls.
        """
        route = getattr(event, "route", None) or "/dashboard"
        if route == self.current_route:
            return
        self.navigate(route)

    def handle_navigation_change(self, event: ft.Event[CustomNavigationBar]) -> None:
        """Handle ``page.navigation_bar.on_change`` and navigate accordingly."""
        data = getattr(event, "data", None)
        if isinstance(data, NavigationChangeData):
            route = self._label_to_route.get(data.label)
            if route is not None:
                self.navigate(route)
                return
            idx = data.index
        else:
            idx = getattr(event.control, "selected_index", None)
        route_by_index = {v: k for k, v in self._route_to_index.items()}
        route = route_by_index.get(idx or 0, "/dashboard")
        self.navigate(route)
