import asyncio
import logging
import platform
import tempfile
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from core.config_manager import ConfigManager
from core.update_checker import (
    ApplyResult,
    DownloadError,
    UpdateChecker,
    UpdateCheckError,
    UpdateInfo,
)
from core.update_flow import UpdateApplyError, Updater, installer_extra_args
from UI.dialogs import show_alert_dialog
from UI.screens.settings.builders import section_scaffold
from UI.screens.settings.settings_card import SettingsCard
from utils.constants import RELEASES_PAGE_URL
from utils.files import remove_file
from utils.flet_helpers import safe_pop_dialog, safe_update, show_snack_bar
from utils.paths import get_data_dir
from utils.platform import detect_os, is_packaged

logger = logging.getLogger(__name__)

_UPDATE_POLL_INTERVAL = 0.15


class _DownloadCanceled(Exception):
    pass


def _info_row(label: str, value: str) -> ft.Row:
    return ft.Row(
        controls=[
            ft.Text(label, width=160),
            ft.Text(value, selectable=True, expand=True),
        ],
    )


def show_update_dialog(
    page: ft.Page,
    update: UpdateInfo,
    installed_version: str,
    on_install_launched: Optional[Callable[[], None]] = None,
) -> None:
    """Modal update offer: notes, size, download-with-progress, install.

    Windows hands the verified installer to the elevated setup flow
    (:class:`core.update_flow.Updater`) and requests an app restart via
    ``on_install_launched``. Android opens the APK through the system
    installer. Manual-only releases (no auto-install asset) only get the
    releases-page button.
    """
    installable = is_packaged() and not update.is_manual_only
    notes = (update.release_notes or "No release notes provided.").strip()
    size_mb = (
        f"{(update.asset_size or 0) / 1_000_000:.1f} MB" if update.asset_size else "—"
    )

    title = ft.Text("Update available", weight=ft.FontWeight.BOLD)
    details = ft.Column(
        controls=[
            ft.Text(f"Version {update.version} is available.", selectable=True),
            ft.Text(f"Installed: {installed_version}", size=12),
            ft.Container(
                content=ft.Column(
                    controls=[ft.Text(notes, size=12)],
                    height=140,
                    scroll=ft.ScrollMode.AUTO,
                    spacing=0,
                ),
                padding=8,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8,
            ),
            ft.Text(f"Download size: {size_mb}", size=12),
        ],
        tight=True,
        spacing=8,
    )
    progress_bar = ft.ProgressBar(value=0)
    status_text = ft.Text("", size=12)
    progress_col = ft.Column(controls=[progress_bar, status_text], tight=True)

    dialog = ft.AlertDialog(
        modal=True,
        title=title,
        content=ft.Column(controls=[details, progress_col], tight=True),
    )

    def close() -> None:
        safe_pop_dialog(page)

    def toast(message: str) -> None:
        show_snack_bar(page, message)

    def set_busy(message: str, allow_cancel: bool = False) -> None:
        progress_bar.value = None
        status_text.value = message
        for control in dialog.actions:
            control.disabled = not (allow_cancel and control is cancel_btn)
        dialog.update()

    def set_progress(downloaded: int, total: int | None) -> None:
        if total:
            progress_bar.value = min(1.0, downloaded / total)
            status_text.value = f"Downloading… {downloaded / 1_000_000:.1f} / {total / 1_000_000:.1f} MB"
        else:
            progress_bar.value = None
            status_text.value = f"Downloading… {downloaded / 1_000_000:.1f} MB"

    canceled = {"flag": False}

    def _begin_install(_event) -> None:
        set_busy("Preparing…", allow_cancel=True)
        page.run_task(_run_download_and_install)

    async def _run_download_and_install() -> None:
        checker = UpdateChecker()
        progress: dict[str, object] = {"downloaded": 0, "total": None, "changed": False}

        def on_progress(done: int, total: int | None) -> None:
            progress["downloaded"] = done
            progress["total"] = total
            progress["changed"] = True
            if canceled["flag"]:
                raise _DownloadCanceled

        download_task = asyncio.create_task(
            asyncio.to_thread(checker.download, update, None, on_progress)
        )
        while not download_task.done():
            await asyncio.sleep(_UPDATE_POLL_INTERVAL)
            if progress["changed"]:
                progress["changed"] = False
                set_progress(progress["downloaded"], progress["total"])

        try:
            installer_path = await download_task
        except _DownloadCanceled:
            logger.info("Update download canceled by user")
            remove_file(Path(tempfile.gettempdir()) / (update.asset_name or "update"))
            close()
            toast("Download canceled")
            return
        except DownloadError as exc:
            logger.warning("Update download failed: %s", exc)
            close()
            toast("Download failed — check your connection")
            return
        except Exception:
            logger.exception("Update download failed unexpectedly")
            close()
            toast("Download failed")
            return

        set_busy("Verifying…")
        await asyncio.sleep(0.05)
        if platform.system() == "Android":
            _install_android(installer_path, update)
            return
        _install_windows(installer_path, update)

    def _install_windows(installer_path, update: UpdateInfo) -> None:
        updater = Updater()
        set_busy("Starting installer…")
        try:
            outcome = updater.apply_update(
                update, installer_path, relaunch=True, extra_args=installer_extra_args()
            )
        except UpdateApplyError as exc:
            logger.error("Failed to start installer: %s", exc)
            close()
            toast("Could not start the installer")
            return
        if outcome.result == ApplyResult.CANCELED:
            close()
            toast("Update canceled")
            return
        if outcome.result is not ApplyResult.APPLIED:
            close()
            toast("The installer could not be started")
            return
        close()
        if on_install_launched is not None:
            on_install_launched()

    def _install_android(installer_path, update: UpdateInfo) -> None:
        updater = Updater()
        try:
            outcome = updater.apply_update(update, installer_path, relaunch=False)
        except UpdateApplyError as exc:
            logger.error("Failed to start APK install: %s", exc)
            close()
            toast("Could not start the installer")
            return
        close()
        if outcome.result == ApplyResult.MANUAL_REQUIRED:
            toast("Allow app installs from Android settings, then try again")
        elif outcome.result == ApplyResult.APPLIED:
            toast("Installer opened — follow the on-screen instructions")
        else:
            toast("The installer could not be started")

    def _open_releases(_event) -> None:
        close()
        asyncio.create_task(page.launch_url(RELEASES_PAGE_URL))

    def _later(_event) -> None:
        canceled["flag"] = True
        close()

    install_btn = ft.FilledTonalButton(
        "Download & install",
        icon=ft.Icons.DOWNLOAD,
        on_click=_begin_install,
    )
    cancel_btn = ft.TextButton("Cancel", on_click=_later)
    releases_btn = ft.TextButton("Open releases page", on_click=_open_releases)
    later_btn = ft.TextButton("Later", on_click=_later)

    dialog.actions = (
        [install_btn, cancel_btn] if installable else [releases_btn, later_btn]
    )
    dialog.actions_alignment = ft.MainAxisAlignment.END
    page.show_dialog(dialog)
    safe_update(page)


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
            url=RELEASES_PAGE_URL,
        )

        cards = [
            SettingsCard(
                "Updates",
                [
                    ft.Text(f"Installed version: {self._version}"),
                    self._auto_update_switch,
                    self._prerelease_switch,
                    ft.Row(
                        controls=[self._check_btn, self._open_releases_btn],
                        wrap=True,
                        run_spacing=8,
                    ),
                ],
            ),
            SettingsCard(
                "About",
                [
                    _info_row("Version", self._version),
                    _info_row("Platform", self._platform),
                    _info_row("Device ID", self._device_id),
                    _info_row("Data directory", self._data_dir),
                ],
            ),
            SettingsCard(
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
        try:
            info = await asyncio.to_thread(
                self._update_checker.check_for_update,
                include_prereleases=self._config.check_prereleases,
            )
        except UpdateCheckError as exc:
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
            self._toast("You're up to date")
            if page is not None:
                show_alert_dialog(
                    page,
                    "Up to date",
                    f"You're running the latest version ({self._version}).",
                )
            return

        if page is not None:
            show_update_dialog(
                page,
                info,
                self._version,
                on_install_launched=self._on_install_launched,
            )

    def _toast(self, message: str) -> None:
        if self._page is not None:
            show_snack_bar(self._page, message)

    def on_sub_route(self, route: str) -> None:
        """Refresh control values when the section becomes visible."""
        self._auto_update_switch.value = self._config.auto_update_enabled
        self._prerelease_switch.value = self._config.check_prereleases
        if self.parent is not None:
            self.update()
