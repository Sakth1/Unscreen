import asyncio
import logging
import os
from contextlib import suppress

import flet as ft

from core.application.collection_manager import CollectionManager
from core.auto_start import enable as enable_auto_start
from core.auto_start import is_enabled as is_auto_start_enabled
from core.config_manager import ConfigManager
from core.logging_setup import apply_root_level, get_log_path, setup_file_logging
from core.state.app_state import UpdateStatus, get_app_state
from core.update_checker import UpdateChecker
from UI.components.card_section import CardSection
from UI.components.dialogs import show_permission_dialog
from UI.custom.navigation_bar import (
    CustomNavigationBar,
    CustomNavigationBarDestination,
)
from UI.custom.navigation_drawer import (
    CustomNavigationDrawer,
    CustomNavigationDrawerDestination,
)
from UI.custom.secondary_navigation_panel import (
    SecondaryNavigationDestination,
    SecondaryNavigationPanel,
)
from UI.custom.status_bar import CollectionStatusBar
from UI.custom.update_dialog import show_update_dialog
from UI.layout.layout_resolver import app_layout_resolver
from UI.layout.models import (
    AppLayout,
    NavigationDestination,
    NavigationPattern,
    SecondaryNavigationChangeData,
    SecondaryNavigationPattern,
)
from UI.routing import RouteManager
from UI.screens.analytics_screen import Analytics
from UI.screens.base_screen import BaseScreen
from UI.screens.dashboard_screen import Dashboard
from UI.screens.settings_screen import Settings
from UI.screens.timeline_screen import Timeline
from UI.theme import apply_accent_theme
from utils.constants import (
    ASSET_DIR,
    DEFAULT_PAGE_HEIGHT,
    DEFAULT_PAGE_WIDTH,
    MIN_PAGE_HEIGHT,
    MIN_PAGE_WIDTH,
    MOBILE_DEFAULT_HEIGHT,
    MOBILE_DEFAULT_WIDTH,
)
from utils.paths import get_data_dir
from utils.platform import OSType, detect_os, is_packaged
from utils.versions import get_current_version
from utils.win32 import acquire_instance_mutex

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def _write_startup_log(message: str) -> None:
    """Write a timestamped line to the startup log file.

    This persists even if the window is empty, allowing post-mortem
    diagnosis of startup failures.
    """
    import time
    from pathlib import Path

    try:
        data_dir = Path(os.environ.get("UNSCREEN_DATA_DIR") or get_data_dir())
        log_file = data_dir / "startup.log"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} {message}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


_THEME_MODES = {
    "system": ft.ThemeMode.SYSTEM,
    "light": ft.ThemeMode.LIGHT,
    "dark": ft.ThemeMode.DARK,
}


def _theme_mode_from_config(mode: str) -> ft.ThemeMode:
    return _THEME_MODES.get(mode, ft.ThemeMode.SYSTEM)


class App:
    def __init__(self, page: ft.Page):
        _write_startup_log("App.__init__ started")
        logger.info("App.__init__ started: page=%s", page)
        # The installer (AppMutex=Unscreen_Mutex) uses this to detect and
        # close a running app before updating it. A second concurrent
        # instance (e.g. a relaunch racing the previous process) would hit
        # locked database files, so refuse to start instead of crashing.
        if detect_os() == OSType.WINDOWS and is_packaged():
            mutex_result = acquire_instance_mutex("Unscreen_Mutex")
            _write_startup_log(
                f"mutex acquired: {'yes' if mutex_result is not None else 'FAILED'}"
            )
            logger.info(
                "Mutex acquisition: os=%s packaged=%s result=%s",
                detect_os(),
                is_packaged(),
                (
                    "acquired"
                    if mutex_result is not None
                    else "FAILED (another instance running)"
                ),
            )
            if mutex_result is None:
                raise RuntimeError(
                    "Another instance of Unscreen is already running. "
                    "Close it and try again."
                )

        self._closing = False

        self.page = page
        self.page.title = "Unscreen"

        if detect_os() == OSType.WINDOWS:
            # Graceful shutdown: intercept the window close so open
            # sessions are finalized before the process dies (a killed
            # process would leave orphaned end_ts=NULL rows behind).
            self.page.window.prevent_close = True
            self.page.window.on_event = self._on_window_event

        platform_obj = getattr(page, "platform", None)
        if platform_obj is not None and callable(
            getattr(platform_obj, "is_mobile", None)
        ):
            self._is_mobile = bool(platform_obj.is_mobile())
        else:
            self._is_mobile = detect_os() == OSType.ANDROID

        _write_startup_log("loading config")
        logger.info("Loading config...")
        self.config = ConfigManager()
        self.config.load()
        _write_startup_log(f"config loaded: theme_mode={self.config.theme_mode}")
        logger.info(
            "Config loaded: theme_mode=%s theme=%s",
            self.config.theme_mode,
            self.config.theme,
        )
        self.page.theme_mode = _theme_mode_from_config(self.config.theme_mode)
        apply_accent_theme(self.page, self.config.theme)
        self._set_window_icon()

        if self.config.start_maximized:
            # REMOVE THIS BS OF A CODE WHEN flet #6101 IS FIXED
            self._schedule_maximize()

        setup_file_logging()
        apply_root_level(self.config.log_level)

        logger.info(
            "App startup: version=%s os=%s packaged=%s",
            get_current_version(),
            detect_os(),
            is_packaged(),
        )
        logger.info(
            "Data storage: dir=%s (UNSCREEN_DATA_DIR=%s) log=%s",
            get_data_dir(),
            os.environ.get("UNSCREEN_DATA_DIR") or "unset",
            get_log_path() or "none",
        )

        self.collection_manager = CollectionManager(config=self.config)

        self.dashboard_page = Dashboard()
        self.timeline_page = Timeline()
        self.analytics_page = Analytics()
        self.settings_page = Settings(
            config=self.config,
            collection_manager=self.collection_manager,
            page=self.page,
            on_back=self._go_back,
            on_install_launched=self._request_app_exit,
        )

        self.content_container = ft.Container(expand=True)

        self.navigation_rail = None
        self.secondary_navigation_panel = None
        self._panel_view = None
        self.current_view: BaseScreen = None
        self.populated_options_inline = False
        self._inline_picker_view: object = None
        self._inline_picker_content: object = None
        self.status_bar = CollectionStatusBar()
        self.shell = ft.Row(expand=True, controls=[self.content_container])

        self.section_routes: dict[str, list[str]] = {
            "/settings": ["/settings/general", "/settings/data", "/settings/app-info"],
        }

        self.destinations = [
            NavigationDestination(
                "/dashboard",
                "Dashboard",
                ft.Icons.DASHBOARD,
                self.dashboard_page,
            ),
            NavigationDestination(
                "/timeline",
                "Timeline",
                ft.Icons.TIMELINE,
                self.timeline_page,
            ),
            NavigationDestination(
                "/analytics",
                "Analytics",
                ft.Icons.ANALYTICS,
                self.analytics_page,
            ),
            NavigationDestination(
                "/settings",
                "Settings",
                ft.Icons.SETTINGS,
                self.settings_page,
            ),
        ]

        section_views = {
            dest.route: dest.view
            for dest in self.settings_page._get_secondary_options()
        }
        self.route_manager = RouteManager(
            page=self.page,
            container=self.content_container,
            destinations=self.destinations,
            section_routes=self.section_routes,
            section_views=section_views,
        )

        self.page.on_route_change = self.route_manager.handle_route_change
        self.page.on_resize = self._handle_page_resize
        self.page.on_media_change = self._handle_media_change
        _write_startup_log("calling page.add()")
        logger.info("Calling page.add() to mount UI...")
        self.page.add(
            ft.Column(expand=True, spacing=0, controls=[self.shell, self.status_bar])
        )
        _write_startup_log("page.add() completed")
        logger.info("page.add() completed successfully")

        self._initiate()
        self.status_bar.start_refresh(self.page)
        self.dashboard_page.start_refresh(self.page)
        self.route_manager.navigate(self.route_manager.current_route)
        _write_startup_log("App.__init__ completed")
        logger.info("App.__init__ completed successfully")

    def _set_window_icon(self) -> None:
        if self.page.platform is not None and self.page.platform.is_desktop():
            icon = ASSET_DIR / "icon_windows.ico"
            if icon.exists():
                self.page.window.icon = str(icon)

    def _schedule_maximize(self):
        if self.page.platform is not None and self.page.platform.is_desktop() is True:
            self.page.run_task(self._maximize_after_delay)

    async def _maximize_after_delay(self):
        await asyncio.sleep(
            0.1
        )  # flet#6101: client window-state init must settle first
        self.page.window.maximized = True
        self.page.update()

    def _initiate(self):
        if (
            self.collection_manager.config.auto_start_enabled
            and not is_auto_start_enabled()
        ):
            enable_auto_start()

        if self.config.auto_update_enabled:
            self.page.run_task(self._startup_update_check)

        # Boot the collection loop. start() registers the watchers and
        # auto-pauses when collection_enabled is off (saved config), so
        # a fresh launch always collects when tracking is enabled.
        self.page.run_task(self._start_collection)

        if detect_os() == OSType.ANDROID:
            from core.collectors.android.usage_stats import check_usage_stats_permission

            if not check_usage_stats_permission():
                show_permission_dialog(self.page)

        if self._is_mobile:
            width = MOBILE_DEFAULT_WIDTH
            height = MOBILE_DEFAULT_HEIGHT
        else:
            width = (
                self.page.window.width
                if self.page.window.width is not None
                else DEFAULT_PAGE_WIDTH
            )
            height = (
                self.page.window.height
                if self.page.window.height is not None
                else DEFAULT_PAGE_HEIGHT
            )

        self.layout: AppLayout = app_layout_resolver(
            width,
            height,
            media=getattr(self.page, "media", None),
            is_mobile=self._is_mobile,
        )
        self._apply_layout(self.layout)

    async def _start_collection(self) -> None:
        try:
            await self.collection_manager.start()
        except Exception:
            logger.exception("Collection failed to start at boot")

    async def _startup_update_check(self) -> None:
        """Silently look for a newer release; offer the update when found."""
        try:
            info = await asyncio.to_thread(
                UpdateChecker().check_for_update,
                include_prereleases=self.config.check_prereleases,
            )
        except Exception:
            logger.exception("Startup update check failed")
            return
        if info is None:
            return
        get_app_state().set_update_info(info)
        get_app_state().set_update_status(UpdateStatus.AVAILABLE)
        get_app_state().set_update_error(None)

        size_mb = (
            f"{(info.asset_size or 0) / 1_000_000:.1f} MB" if info.asset_size else ""
        )
        snack = ft.SnackBar(
            content=ft.Row(
                spacing=12,
                controls=[
                    ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, color=ft.Colors.PRIMARY),
                    ft.Column(
                        tight=True,
                        spacing=2,
                        controls=[
                            ft.Text(
                                f"Update v{info.version} is available",
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                size_mb or "A new version is ready to install",
                                size=11,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                    ),
                ],
            ),
            action="Update now",
            on_action=lambda _e: self._open_update_dialog(info),
            open=True,
        )
        with suppress(RuntimeError):
            # The overlay may not be attached yet (bridge races during boot).
            self.page.overlay.append(snack)
            self.page.update()

    def _open_update_dialog(self, info) -> None:
        """Offer download + install for a known update (settings or snackbar)."""
        show_update_dialog(
            self.page,
            info,
            get_current_version(),
            on_install_launched=self._request_app_exit,
        )

    def _request_app_exit(self) -> None:
        """Called once the new installer is running; leave the rest to it.

        The app must exit so the installer can replace the running executable
        (the relaunch watchdog reopens it once setup has finished). Open
        sessions are finalized first, exactly as on a normal window close.
        """

        if self._closing:
            return
        self._closing = True
        asyncio.create_task(self._finalize_and_close())

    def _on_window_event(self, event) -> None:
        # flet reports the close signal in `event.type` (a WindowEventType);
        # `event.data` is always None on modern flet, so the legacy string
        # check alone would never match and prevent_close would block the
        # window forever. Both are checked for forward/backward compatibility.
        event_type = getattr(event, "type", None)
        event_data = getattr(event, "data", None)
        if (
            event_type in (ft.WindowEventType.CLOSE, "close")
            or event_data in (ft.WindowEventType.CLOSE, "close")
        ) and not self._closing:
            self._closing = True
            self.page.run_task(self._finalize_and_close)

    async def _finalize_and_close(self) -> None:
        """Finalize open sessions and status blocks, then destroy the window.

        The window close is intercepted (``prevent_close``) so collection
        stops gracefully instead of the process dying mid-session, which
        would leave ``end_ts NULL`` orphans in the database.
        """
        try:
            # A hung stop must never freeze the close: cap the wait so the
            # window is destroyed even if a finalizer stalls.
            await asyncio.wait_for(self.collection_manager.stop(), timeout=5)
        except asyncio.TimeoutError:
            logger.error("Timed out finalizing sessions during shutdown")
        except Exception:
            logger.exception("Failed to finalize sessions during shutdown")
        try:
            await self.page.window.destroy()
        except Exception:
            logger.exception("Failed to close the window")

    def _handle_page_resize(self, _event):
        self._apply_responsive_layout()

    def _handle_media_change(self, _event):
        self._apply_responsive_layout()

    def _apply_responsive_layout(self):
        page_width, page_height = self._resolve_page_dimensions()
        layout = app_layout_resolver(
            page_width,
            page_height,
            media=getattr(self.page, "media", None),
            is_mobile=self._is_mobile,
        )
        if self.layout is not None and layout == self.layout:
            return
        self.layout = layout
        self._apply_layout(layout)

    def _apply_layout(self, layout: AppLayout):
        get_app_state().set_layout(layout)

        self._update_layout()

    def _update_layout(self):
        match self.layout.navigation:
            case NavigationPattern.BOTTOM_BAR:
                nav = self._ensure_navigation_bar()
                nav.apply_layout(self.layout)
                self._populate_page_with_options()
                self.shell.controls = [self.content_container]

            case NavigationPattern.MINI_RAIL:
                self.page.navigation_bar = None
                self._ensure_rail(extended=False).apply_layout(self.layout)
                self._build_secondary_options(self.layout)

                self.shell.controls = [self.navigation_rail]
                self._append_secondary_panel()

            case NavigationPattern.EXTENDED_RAIL:
                self.page.navigation_bar = None
                self._ensure_rail(extended=True).apply_layout(self.layout)
                self._build_secondary_options(self.layout)

                self.shell.controls = [self.navigation_rail]
                self._append_secondary_panel()

            case _:
                raise NotImplementedError

        self._apply_content_padding(self.layout)
        self.page.update()

    def _append_secondary_panel(self) -> None:
        """Place the secondary side panel in the shell when it applies.

        The panel is only relevant for side-panel form factors; inline
        layouts (phones, tablet portrait) render the secondary sections
        inside the content area, so a leftover panel must not take space.
        """
        if (
            self.secondary_navigation_panel is not None
            and self.layout.secondary_navigation
            is SecondaryNavigationPattern.SIDE_PANEL
        ):
            self.secondary_navigation_panel.apply_layout(self.layout)
            self.shell.controls.append(self.secondary_navigation_panel)
        self.shell.controls.append(self.content_container)

    def _build_secondary_options(self, layout: AppLayout):
        match layout.secondary_navigation:
            case SecondaryNavigationPattern.INLINE:
                self._populate_page_with_options()
                self.populated_options_inline = True
            case SecondaryNavigationPattern.SIDE_PANEL:
                self._ensure_secondary_panel()
                if (
                    self._inline_picker_view is not None
                    and self.content_container.content is self._inline_picker_content
                ):
                    self.content_container.content = self.current_view
                self.populated_options_inline = False
            case _:
                raise NotImplementedError

    def _populate_page_with_options(self):
        """Render the section picker inline for phone / tablet-portrait layouts.

        Replaces the content area with a settings card listing the current
        view's sections. The picker only appears on the parent route (e.g.
        ``/settings``); opening a sub-route keeps the section itself on
        screen, so tiles stay clickable.
        """
        self.current_view = self.route_manager.view_for(
            self.route_manager.current_route
        )
        has_options = self.current_view is not None and getattr(
            self.current_view, "_secondary_options", False
        )
        if not has_options:
            self.secondary_navigation_panel = None
            self._panel_view = None
            return

        parent_route = next(
            (d.route for d in self.destinations if d.view is self.current_view), None
        )
        if self.route_manager.current_route != parent_route:
            return

        if (
            self._inline_picker_view is self.current_view
            and self.content_container.content is self._inline_picker_content
        ):
            return

        self.secondary_destination: list[NavigationDestination] = (
            self.current_view._get_secondary_options()
        )
        self._inline_picker_content = CardSection(
            title="Settings",
            controls=[
                ft.ListTile(
                    leading=ft.Icon(dest.icon),
                    title=ft.Text(dest.label),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                    on_click=lambda e, d=dest: self.route_manager.navigate(d.route),
                )
                for dest in self.secondary_destination
            ],
        )
        self._inline_picker_view = self.current_view
        self.content_container.content = self._inline_picker_content

    def _ensure_secondary_panel(self):
        self.current_view = self.route_manager.view_for(
            self.route_manager.current_route
        )
        has_options = self.current_view is not None and getattr(
            self.current_view, "_secondary_options", False
        )
        if not has_options:
            self.secondary_navigation_panel = None
            self._panel_view = None
            return

        # Reuse the panel while the same view owns it so window resizes do
        # not wipe the current section selection.
        if (
            self.secondary_navigation_panel is not None
            and self._panel_view is self.current_view
        ):
            self.secondary_navigation_panel.apply_layout(self.layout)
            return

        self.secondary_destination: list[NavigationDestination] = (
            self.current_view._get_secondary_options()
        )

        self.secondary_navigation_panel = SecondaryNavigationPanel(
            destinations=[
                SecondaryNavigationDestination(
                    icon=dest.icon,
                    label=dest.label,
                    route=dest.route,
                    selected=i == 0,
                )
                for i, dest in enumerate(self.secondary_destination)
            ],
            selected_index=0,
            adaptive=True,
            on_change=self._handle_secondary_change,
        )
        self.secondary_navigation_panel.apply_layout(self.layout)
        self._panel_view = self.current_view

    def _go_back(self) -> None:
        """Leave the current section and return to its parent route."""
        parent = self.route_manager._parent_for(self.route_manager.current_route)
        if parent is not None:
            self.route_manager.navigate(parent)
            # Re-render the layout so inline pickers / section views reflect
            # the parent route (matches the flow in _handle_navigation_change).
            self._update_layout()

    def _handle_secondary_change(self, event: ft.ControlEvent):
        """Navigate to the section behind the pill the user selected.

        Mirrors :meth:`UI.routing.RouteManager.handle_navigation_change`:
        the panel ships a :class:`SecondaryNavigationChangeData` payload
        carrying the sub-route; index-based resolution stays as a fallback
        for callers that only know the selection index.
        """
        destinations = getattr(self, "secondary_destination", None)
        data = getattr(event, "data", None)
        if isinstance(data, SecondaryNavigationChangeData):
            if data.route:
                self.route_manager.navigate(data.route)
                return
            index = data.index
        else:
            index = getattr(getattr(event, "control", None), "selected_index", None)
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        if destinations is not None and 0 <= index < len(destinations):
            self.route_manager.navigate(destinations[index].route)

    def _resolve_page_dimensions(self) -> tuple[float, float]:
        if self._is_mobile:
            page_width, page_height = MOBILE_DEFAULT_WIDTH, MOBILE_DEFAULT_HEIGHT
        else:
            page_width = getattr(self.page, "width", 0) or DEFAULT_PAGE_WIDTH
            page_height = getattr(self.page, "height", 0) or DEFAULT_PAGE_HEIGHT
        media = getattr(self.page, "media", None)
        padding = getattr(media, "padding", None)
        if padding is not None:
            page_width = max(
                MIN_PAGE_WIDTH,
                page_width
                - (getattr(padding, "left", 0) or 0)
                - (getattr(padding, "right", 0) or 0),
            )
            page_height = max(
                MIN_PAGE_HEIGHT,
                page_height
                - (getattr(padding, "top", 0) or 0)
                - (getattr(padding, "bottom", 0) or 0),
            )
        if getattr(self.page, "navigation_bar", None) is not None:
            page_height = max(
                MIN_PAGE_HEIGHT,
                page_height - (getattr(self.page.navigation_bar, "height", 0) or 0),
            )
        return page_width, page_height

    def _apply_content_padding(self, layout: AppLayout) -> None:
        """Pad the content area with design spacing plus system safe insets.

        The floating bottom bar already clears the gesture area on its own,
        so with a bottom bar the content does not need the extra bottom inset.
        """
        base = layout.padding
        safe_left, safe_top, safe_right, safe_bottom = layout.safe_padding
        bottom_bar = layout.navigation is NavigationPattern.BOTTOM_BAR
        self.content_container.padding = ft.padding.Padding.only(
            left=base + safe_left,
            top=base + safe_top,
            right=base + safe_right,
            bottom=base + (0.0 if bottom_bar else safe_bottom),
        )

    def _ensure_navigation_bar(self):
        if self.page.navigation_bar is not None:
            return self.page.navigation_bar

        selected_index = self.route_manager._index_for_route(
            self.route_manager.current_route
        )
        self.page.navigation_bar = CustomNavigationBar(
            destinations=[
                CustomNavigationBarDestination(
                    icon=dest.icon,
                    label=dest.label,
                    selected=i == selected_index,
                )
                for i, dest in enumerate(self.destinations)
            ],
            selected_index=selected_index,
            adaptive=True,
            label_behavior=ft.NavigationBarLabelBehavior.ONLY_SHOW_SELECTED,
            on_change=self._handle_navigation_change,
        )
        return self.page.navigation_bar

    def _ensure_rail(self, extended: bool) -> CustomNavigationDrawer:
        if self.navigation_rail is not None:
            return self.navigation_rail

        settings = next(d for d in self.destinations if d.route == "/settings")
        main = [d for d in self.destinations if d.route != "/settings"]

        self.navigation_rail = CustomNavigationDrawer(
            trailing=CustomNavigationDrawerDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                label=settings.label,
                tooltip=settings.label,
            ),
            destinations=[
                CustomNavigationDrawerDestination(
                    icon=dest.icon,
                    label=dest.label,
                    tooltip=dest.label,
                )
                for dest in main
            ],
            selected_index=0,
            extended=extended,
            on_change=self._handle_navigation_change,
        )
        return self.navigation_rail

    def _handle_navigation_change(self, event: ft.ControlEvent):
        self.route_manager.handle_navigation_change(event)
        self._update_layout()


async def entrypoint(page: ft.Page):
    _write_startup_log("entrypoint called")
    logger.info("entrypoint called: page=%s", page)
    try:
        _write_startup_log("creating App instance")
        logger.info("Creating App instance...")
        App(page)
        _write_startup_log("App instance created successfully")
        logger.info("App instance created successfully")
    except Exception as exc:
        _write_startup_log(f"Fatal error during app startup: {exc}")
        logger.exception("Fatal error during app startup")
        _render_startup_error(page, exc)


def _render_startup_error(page: ft.Page, exc: Exception) -> None:
    """Surface a startup failure in the window instead of a silent blank one.

    Anything raised while booting (storage wipe, schema creation, layout
    resolution) previously left the user staring at an empty window with no
    traceback visible anywhere. The error text and the log location are
    rendered inline; ``logger.exception`` above already recorded the full
    traceback.
    """
    log_path = get_log_path()
    message = (
        f"{type(exc).__name__}: {exc}\n\n"
        f"Details were written to {log_path or 'the console'}"
    )
    try:
        page.clean()
        page.add(
            ft.Column(
                expand=True,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=56, color=ft.Colors.ERROR),
                    ft.Text(
                        "Unscreen could not start",
                        weight=ft.FontWeight.BOLD,
                        size=20,
                    ),
                    ft.Text(
                        message,
                        selectable=True,
                        width=560,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.FilledButton(
                        "Close",
                        on_click=lambda _e: page.run_task(page.window.destroy),
                    ),
                ],
            )
        )
        page.update()
    except Exception:
        logger.exception("Failed to render the startup error screen")
