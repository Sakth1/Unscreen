"""Tests for RouteManager sub-route (section) navigation.

Sub-routes like ``/settings/app-info`` must resolve to the parent screen's
view, announce the full route to the view via ``on_sub_route``, and keep the
main navigation selection pinned to the parent section.
"""

from __future__ import annotations

import flet as ft

from core.state.app_state import reset_app_state
from UI.layout.models import NavigationChangeData, NavigationDestination
from UI.routing import RouteManager

DEFAULT_ROUTES = ["/dashboard", "/timeline", "/analytics", "/settings"]


class _PageStub:
    def __init__(self):
        self.navigation_bar = None
        self.route = None

    def navigate(self, route: str) -> None:
        self.route = route


class _ContainerStub:
    def __init__(self):
        self.content = None
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


class _ViewStub:
    def __init__(self):
        self.sub_routes: list[str] = []

    def on_sub_route(self, route: str) -> None:
        self.sub_routes.append(route)


class _PlainView:
    pass


class TestSubRouteNavigation:
    @staticmethod
    def _destinations(views: dict | None = None) -> list[NavigationDestination]:
        views = views or {}
        return [
            NavigationDestination(
                route,
                f"Label {route}",
                "HOME",
                views.get(route, _PlainView()),
            )
            for route in DEFAULT_ROUTES
        ]

    @staticmethod
    def _manager(
        views: dict | None = None,
        sections=None,
        section_views: dict | None = None,
    ) -> RouteManager:
        reset_app_state()
        return RouteManager(
            page=_PageStub(),
            container=_ContainerStub(),
            destinations=TestSubRouteNavigation._destinations(views),
            section_routes=sections,
            section_views=section_views,
        )

    def test_sub_route_targets_parent_view_and_announces_route(self):
        view = _ViewStub()
        rm = self._manager(
            views={"/settings": view},
            sections={"/settings": ["/settings/general", "/settings/app-info"]},
        )
        rm.navigate("/settings/app-info")

        assert rm.current_route == "/settings/app-info"
        assert rm._container.content is view
        assert view.sub_routes == ["/settings/app-info"]
        assert rm._index_for_route("/settings/app-info") == 3

    def test_parent_route_does_not_announce_sub_route(self):
        view = _ViewStub()
        rm = self._manager(
            views={"/settings": view},
            sections={"/settings": ["/settings/general"]},
        )
        rm.navigate("/settings")
        assert view.sub_routes == []
        assert rm.current_route == "/settings"

    def test_sub_route_preserves_selection_on_parent_visit(self):
        view = _ViewStub()
        rm = self._manager(
            views={"/settings": view},
            sections={"/settings": ["/settings/general", "/settings/app-info"]},
        )
        rm.navigate("/settings/app-info")
        rm.navigate("/settings")
        assert view.sub_routes == ["/settings/app-info"]

    def test_views_without_handler_are_ignored(self):
        view = _PlainView()
        rm = self._manager(
            views={"/settings": view},
            sections={"/settings": ["/settings/general"]},
        )
        rm.navigate("/settings/general")
        assert rm.current_route == "/settings/general"
        assert rm._container.content is view

    def test_unknown_route_falls_back_to_dashboard(self):
        dashboard = _PlainView()
        rm = self._manager(views={"/dashboard": dashboard})
        rm.navigate("/nope")
        assert rm.current_route == "/dashboard"
        assert rm._container.content is dashboard

    def test_unknown_sub_route_announced_but_ignored_by_view(self):
        view = _ViewStub()
        rm = self._manager(
            views={"/settings": view},
            sections={"/settings": ["/settings/general", "/settings/app-info"]},
        )
        rm.navigate("/settings/does-not-exist")
        assert rm.current_route == "/settings/does-not-exist"
        assert rm._container.content is view
        assert view.sub_routes == ["/settings/does-not-exist"]
        assert rm._index_for_route("/settings/does-not-exist") == 3

    def test_registered_section_view_replaces_parent_on_navigation(self):
        parent = _PlainView()
        section = _ViewStub()
        rm = self._manager(
            views={"/settings": parent},
            sections={"/settings": ["/settings/data"]},
            section_views={"/settings/data": section},
        )
        rm.navigate("/settings/data")
        assert rm._container.content is section
        assert section.sub_routes == ["/settings/data"]
        assert rm.current_route == "/settings/data"

    def test_section_view_wins_over_parent_when_registered(self):
        parent = _ViewStub()
        section = _PlainView()
        rm = self._manager(
            views={"/settings": parent},
            sections={"/settings": ["/settings/general", "/settings/data"]},
            section_views={"/settings/data": section},
        )
        rm.navigate("/settings/data")
        assert rm._container.content is section
        rm.navigate("/settings/general")
        assert rm._container.content is parent
        assert parent.sub_routes == ["/settings/general"]

    def test_view_for_section_still_resolves_to_parent_screen(self):
        parent = _PlainView()
        section = _PlainView()
        rm = self._manager(
            views={"/settings": parent},
            sections={"/settings": ["/settings/data"]},
            section_views={"/settings/data": section},
        )
        assert rm.view_for("/settings/data") is parent

    def test_navigate_pushes_route_to_page(self):
        view = _PlainView()
        page = _PageStub()
        reset_app_state()
        rm = RouteManager(
            page=page,
            container=_ContainerStub(),
            destinations=TestSubRouteNavigation._destinations({"/settings": view}),
            section_routes={"/settings": ["/settings/app-info"]},
        )
        rm.navigate("/settings/app-info")
        assert page.route == "/settings/app-info"

    def test_section_routes_stay_disjoint_from_top_level(self):
        view = _PlainView()
        rm = self._manager(
            views={"/settings": view},
            sections={"/settings": ["/settings/general"]},
        )
        assert rm._parent_for("/settings") is None
        assert rm._parent_for("/settings/general") == "/settings"
        assert rm._parent_for("/timeline") is None


class TestViewLookup:
    @staticmethod
    def _manager(views: dict | None = None, sections=None) -> RouteManager:
        return TestSubRouteNavigation._manager(views, sections)

    def test_view_for_returns_screen_by_route(self):
        settings_view = _PlainView()
        rm = self._manager(views={"/settings": settings_view})
        assert rm.view_for("/settings") is settings_view

    def test_view_for_unknown_route_returns_none(self):
        rm = self._manager()
        assert rm.view_for("/nope") is None

    def test_view_for_sub_route_resolves_to_parent_screen(self):
        settings_view = _PlainView()
        rm = self._manager(
            views={"/settings": settings_view},
            sections={"/settings": ["/settings/general"]},
        )
        assert rm.view_for("/settings/general") is settings_view

    def test_handle_navigation_change_uses_event_label(self):
        timeline_view = _PlainView()
        rm = self._manager(views={"/timeline": timeline_view})
        event = ft.Event(
            name="FloatingNavigationChange",
            control=None,
            data=NavigationChangeData(index=1, label="Label /timeline"),
        )
        rm.handle_navigation_change(event)
        assert rm.current_route == "/timeline"

    def test_handle_navigation_change_uses_event_data_index(self):
        timeline_view = _PlainView()
        rm = self._manager(views={"/timeline": timeline_view})
        event = ft.Event(
            name="FloatingNavigationChange",
            control=None,
            data=NavigationChangeData(index=1, label="Unregistered label"),
        )
        rm.handle_navigation_change(event)
        assert rm.current_route == "/timeline"

    def test_handle_navigation_change_falls_back_to_control_index(self):
        analytics_view = _PlainView()
        rm = self._manager(views={"/analytics": analytics_view})
        control = type("Nav", (), {"selected_index": 2})()
        event = ft.Event(name="FloatingNavigationChange", control=control)
        rm.handle_navigation_change(event)
        assert rm.current_route == "/analytics"
