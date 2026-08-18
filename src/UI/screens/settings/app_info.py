import asyncio
import logging
from typing import Callable, Optional

import flet as ft

from core.config_manager import ConfigManager
from core.state.app_state import (
    KEY_UPDATE_STATUS,
    UpdateStatus,
    get_app_state,
)
from core.update_checker import UpdateChecker, UpdateCheckError
from UI.components.card_section import CardSection
from UI.components.dialogs import show_alert_dialog
from UI.custom.update_dialog import show_update_dialog
from UI.screens.settings.builders import section_scaffold
from utils.flet_helpers import show_snack_bar
from utils.paths import get_data_dir
from utils.platform import detect_os

logger = logging.getLogger(__name__)


def _info_row(label: str, value: str) -> ft.Row:
    return ft.Row(
        controls=[
            ft.Text(label, width=160),
            ft.Text(value, selectable=True, expand=True),
        ],
    )


class AppInfo(ft.Container):
    """App information section rendered under ``/settings/app-info``."""

    def __init__(
        self,
        config: ConfigManager,
        page: ft.Page | None = None,
        on_back: Optional[Callable[[], None]] = None,
        on_install_launched: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._config = config or ConfigManager()
        self._page = page
        self._on_install_launched = on_install_launched
        self._update_checker = UpdateChecker()
        self._checking = False

        from core import device_identity
        from utils.versions import get_current_version

        self._version = get_current_version()
        self._platform = detect_os().name.lower()
        self._device_id = device_identity.get_device_id()
        self._data_dir = get_data_dir()

        self._auto_update_switch = ft.Switch(
            value=self._config.auto_update_enabled,
            label="Check for updates on startup",
            on_change=self._on_auto_update_changed,
        )
        self._prerelease_switch = ft.Switch(
            value=self._config.check_prereleases,
            label="Check for prerelease builds",
            on_change=self._on_prerelease_changed,
        )
        self._check_btn = ft.FilledTonalButton(
            "Check for updates",
            icon=ft.Icons.UPDATE,
            on_click=self._check_for_updates,
        )
        self._open_releases_btn = ft.OutlinedButton(
            "Open releases page",
            icon=ft.Icons.OPEN_IN_NEW,
            on_click=self._open_latest_release,
        )
        self._chip_text = ft.Text(size=11, weight=ft.FontWeight.W_500)
        self._update_chip = ft.Container(
            content=self._chip_text,
            padding=ft.padding.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=12,
            visible=False,
        )

        cards = [
            CardSection(
                "Updates",
                [
                    ft.Row(
                        wrap=True,
                        run_spacing=4,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text(f"Installed version: {self._version}"),
                            self._update_chip,
                        ],
                    ),
                    self._auto_update_switch,
                    self._prerelease_switch,
                    ft.Row(
                        controls=[self._check_btn, self._open_releases_btn],
                        wrap=True,
                        run_spacing=8,
                    ),
                ],
            ),
            CardSection(
                "About",
                [
                    _info_row("Version", self._version),
                    _info_row("Platform", self._platform),
                    _info_row("Device ID", self._device_id),
                    _info_row("Data directory", self._data_dir),
                ],
            ),
            CardSection(
                "Privacy",
                [
                    ft.Text(
                        "Unscreen is privacy-first: all collected data stays "
                        "on this device. Nothing is uploaded, no account is "
                        "required, and no analytics are collected.",
                        size=12,
                    ),
                ],
            ),
        ]

        self.content = section_scaffold("App info", cards, on_back=on_back)

        get_app_state().on_change(KEY_UPDATE_STATUS, self._refresh_update_chip)
        self._refresh_update_chip()

    # ── Update status chip ─────────────────────────────────────────────────

    def _refresh_update_chip(self, _key: str | None = None) -> None:
        state = get_app_state()
        if state.update_status is UpdateStatus.AVAILABLE and state.update_info:
            self._chip_text.value = f"Update v{state.update_info.version} available"
            self._chip_text.color = ft.Colors.ON_PRIMARY_CONTAINER
            self._update_chip.bgcolor = ft.Colors.PRIMARY_CONTAINER
            self._update_chip.visible = True
        elif state.update_status is UpdateStatus.CHECKING:
            self._chip_text.value = "Checking…"
            self._chip_text.color = ft.Colors.ON_SECONDARY_CONTAINER
            self._update_chip.bgcolor = ft.Colors.SECONDARY_CONTAINER
            self._update_chip.visible = True
        elif state.update_status is UpdateStatus.FAILED:
            self._chip_text.value = "Check failed"
            self._chip_text.color = ft.Colors.ON_ERROR_CONTAINER
            self._update_chip.bgcolor = ft.Colors.ERROR_CONTAINER
            self._update_chip.visible = True
        else:
            self._update_chip.visible = False
        if self.parent is not None:
            self.update()

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_auto_update_changed(self, event: ft.ControlEvent) -> None:
        self._config.auto_update_enabled = bool(getattr(event.control, "value", False))
        self._config.save()
        self._toast(
            "Auto-update check "
            + ("enabled" if self._config.auto_update_enabled else "disabled")
        )

    def _on_prerelease_changed(self, event: ft.ControlEvent) -> None:
        self._config.check_prereleases = bool(getattr(event.control, "value", False))
        self._config.save()
        self._toast(
            "Prerelease check "
            + ("enabled" if self._config.check_prereleases else "disabled")
        )

    def _check_for_updates(self, _event) -> None:
        if self._page is None or self._checking:
            return
        self._checking = True
        self._check_btn.disabled = True
        self._check_btn.text = "Checking…"
        self._toast("Checking for updates…")
        self._page.run_task(self._run_update_check)
        self._page.update()

    async def _run_update_check(self) -> None:
        page = self._page
        state = get_app_state()
        state.set_update_status(UpdateStatus.CHECKING)
        state.set_update_error(None)
        try:
            info = await asyncio.to_thread(
                self._update_checker.check_for_update,
                include_prereleases=self._config.check_prereleases,
            )
        except UpdateCheckError as exc:
            state.set_update_status(UpdateStatus.FAILED)
            state.set_update_error(str(exc))
            self._toast("Update check failed")
            if page is not None:
                show_alert_dialog(page, "Update check failed", str(exc))
            return
        finally:
            self._checking = False
            if self._page is not None:
                self._check_btn.disabled = False
                self._check_btn.text = "Check for updates"
                self._page.update()

        if info is None:
            state.set_update_status(UpdateStatus.IDLE)
            state.set_update_info(None)
            self._toast("You're up to date")
            if page is not None:
                show_alert_dialog(
                    page,
                    "Up to date",
                    f"You're running the latest version ({self._version}).",
                )
            return

        state.set_update_info(info)
        state.set_update_status(UpdateStatus.AVAILABLE)
        if page is not None:
            show_update_dialog(
                page,
                info,
                self._version,
                on_install_launched=self._on_install_launched,
            )

    def _open_latest_release(self, _event) -> None:
        if self._page is not None:
            self._page.run_task(self._resolve_release_url)

    async def _resolve_release_url(self) -> None:
        page = self._page
        if page is None:
            return
        url = await asyncio.to_thread(
            self._update_checker.latest_release_url,
            self._config.check_prereleases,
        )
        await page.launch_url(url)

    def _toast(self, message: str) -> None:
        if self._page is not None:
            show_snack_bar(self._page, message)

    def on_sub_route(self, route: str) -> None:
        """Refresh control values when the section becomes visible."""
        self._auto_update_switch.value = self._config.auto_update_enabled
        self._prerelease_switch.value = self._config.check_prereleases
        self._refresh_update_chip()
        if self.parent is not None:
            self.update()
