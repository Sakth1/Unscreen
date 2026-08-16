"""Update-offer dialog: version info, rendered release notes, download/install.

Pulled out of the App Info settings section so the same polished dialog is
reused by the startup snackbar and the manual check flow. The dialog adapts
its notes height and width to the current :class:`ScreenFormFactor` and
records every step of the flow into :class:`core.state.app_state.AppState`
(``UpdateStatus`` / progress / error) so any screen can observe it.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from core.state.app_state import UpdateStatus, get_app_state
from core.update_checker import (
    ApplyResult,
    DownloadError,
    UpdateChecker,
    UpdateInfo,
)
from core.update_flow import UpdateApplyError, Updater, installer_extra_args
from UI.layout.models import ScreenFormFactor
from UI.markdown_notes import build_notes_markdown, sanitize_release_notes
from utils.files import remove_file
from utils.flet_helpers import safe_pop_dialog, safe_update, show_snack_bar
from utils.platform import is_android, is_packaged

logger = logging.getLogger(__name__)

_UPDATE_POLL_INTERVAL = 0.15


class _DownloadCanceled(Exception):
    pass


def _notes_height(page_height: float, form_factor: ScreenFormFactor) -> float:
    """Release-notes box height adapted to the viewport and form factor.

    Phones and tablets in portrait get a tall box relative to the screen;
    landscape and desktop get a bounded window so the dialog never
    dominates the viewport.
    """
    match form_factor:
        case ScreenFormFactor.MOBILE:
            return min(340.0, max(140.0, page_height * 0.35))
        case ScreenFormFactor.TABLET_PORTRAIT:
            return min(420.0, max(180.0, page_height * 0.40))
        case _:
            return min(320.0, max(200.0, page_height * 0.30))


def _dialog_width(form_factor: ScreenFormFactor) -> Optional[float]:
    """Content width; ``None`` lets the platform size the dialog (mobile)."""
    if form_factor in (ScreenFormFactor.TABLET_LANDSCAPE, ScreenFormFactor.DESKTOP):
        return 420.0
    return None


def _format_release_date(published_at: str) -> str:
    """``2026-08-01T16:51:33Z`` -> ``Aug 1, 2026``; empty when unparseable."""
    if not published_at:
        return ""
    try:
        stamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return stamp.astimezone().strftime("%b %d, %Y").replace(" 0", " ")


class _UpdateProgress(ft.Column):
    """Progress bar and status text for update downloads.

    Shows a determinate bar with downloaded/total MB and transfer speed
    while downloading, and an indeterminate bar while busy. Uses
    :func:`safe_update` so it can also be unit-tested detached from a page.
    Starts hidden and becomes visible on the first status/progress push.
    """

    def __init__(self) -> None:
        super().__init__(tight=True, visible=False)
        self._bar = ft.ProgressBar(value=0)
        self._status = ft.Text("", size=12)
        self.controls = [self._bar, self._status]
        self._last_downloaded = 0
        self._last_time = 0.0

    def set_busy(self, message: str) -> None:
        self.visible = True
        self._bar.value = None
        self._status.value = message
        safe_update(self)

    def set_progress(self, downloaded: int, total: int | None) -> None:
        now = time.monotonic()
        speed = 0.0
        if self._last_time and downloaded > self._last_downloaded:
            speed_mb = (downloaded - self._last_downloaded) / 1_000_000
            elapsed = now - self._last_time
            if elapsed > 0:
                speed = speed_mb / elapsed
        self._last_downloaded = downloaded
        self._last_time = now
        speed_text = f" ({speed:.1f} MB/s)" if speed else ""
        self.visible = True
        if total:
            self._bar.value = min(1.0, downloaded / total)
            self._status.value = (
                f"Downloading… {downloaded / 1_000_000:.1f} / "
                f"{total / 1_000_000:.1f} MB{speed_text}"
            )
        else:
            self._bar.value = None
            self._status.value = (
                f"Downloading… {downloaded / 1_000_000:.1f} MB{speed_text}"
            )
        safe_update(self)


def _finish_activity_after_install(page: ft.Page) -> None:
    """Close the app after handing off to the Android installer.

    The flet template manifest uses ``singleTop`` with an empty task
    affinity, so a later launch (installer "Open", launcher) can create a
    second task — showing as duplicate instances in recents. Destroying the
    window finishes the current activity, so the stale task leaves recents
    and the next open is a single fresh task.
    """

    async def _close() -> None:
        await asyncio.sleep(0.8)
        await page.window.destroy()

    asyncio.create_task(_close())


def _chip(text: str, bgcolor: str, fgcolor: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_500, color=fgcolor),
        bgcolor=bgcolor,
        padding=ft.padding.Padding.symmetric(horizontal=8, vertical=3),
        border_radius=12,
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

    Every transition is recorded on :class:`core.state.app_state.AppState`
    (``AVAILABLE`` → ``DOWNLOADING`` → ``READY`` → ``APPLYING``, or
    ``FAILED`` / back to ``IDLE`` on cancel).
    """
    installable = is_packaged() and not update.is_manual_only
    notes = sanitize_release_notes(update.release_notes)
    size_mb = (
        f"{(update.asset_size or 0) / 1_000_000:.1f} MB" if update.asset_size else "—"
    )

    state = get_app_state()
    state.set_update_info(update)
    state.set_update_status(UpdateStatus.AVAILABLE)
    state.set_update_error(None)

    layout = state.layout
    form_factor = (
        layout.screen_form_factor if layout is not None else ScreenFormFactor.DESKTOP
    )
    page_height = float(getattr(page, "height", 0) or 800)
    notes_height = _notes_height(page_height, form_factor)
    width = _dialog_width(form_factor)

    header = ft.Row(
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Container(
                content=ft.Icon(
                    ft.Icons.SYSTEM_UPDATE_ALT,
                    color=ft.Colors.ON_PRIMARY_CONTAINER,
                    size=22,
                ),
                width=40,
                height=40,
                border_radius=20,
                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                alignment=ft.alignment.Alignment.CENTER,
            ),
            ft.Column(
                tight=True,
                spacing=2,
                controls=[
                    ft.Text("Update available", weight=ft.FontWeight.BOLD, size=18),
                    ft.Text(
                        f"v{update.version} is ready to install",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
            ),
        ],
    )

    chips = [
        _chip(
            f"v{update.version}",
            ft.Colors.PRIMARY_CONTAINER,
            ft.Colors.ON_PRIMARY_CONTAINER,
        )
    ]
    if update.prerelease:
        chips.append(
            _chip(
                "Prerelease",
                ft.Colors.TERTIARY_CONTAINER,
                ft.Colors.ON_TERTIARY_CONTAINER,
            )
        )
    version_row = ft.Row(
        wrap=True,
        run_spacing=4,
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            *chips,
            ft.Text(
                f"Installed: v{installed_version}",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
                expand=True,
            ),
        ],
    )

    meta_lines = []
    release_date = _format_release_date(update.published_at)
    if release_date:
        meta_lines.append(f"Released {release_date}")
    meta_lines.append(f"Download size: {size_mb}")
    meta_row = ft.Text(
        " · ".join(meta_lines), size=12, color=ft.Colors.ON_SURFACE_VARIANT
    )

    if notes:
        notes_control = build_notes_markdown(page, notes)
    else:
        notes_control = ft.Text(
            "No release notes provided.", size=12, color=ft.Colors.ON_SURFACE_VARIANT
        )
    notes_box = ft.Container(
        content=ft.Column(
            controls=[notes_control],
            height=notes_height,
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
        ),
        padding=10,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=8,
    )

    details = ft.Column(
        controls=[version_row, meta_row, notes_box],
        tight=True,
        spacing=8,
    )
    progress_col = _UpdateProgress()

    content = ft.Column(
        controls=[header, details, progress_col], tight=True, spacing=12
    )
    if width is not None:
        content = ft.Container(content=content, width=width)

    dialog = ft.AlertDialog(
        modal=True,
        scrollable=True,
        content=content,
    )

    def close() -> None:
        state.set_update_status(UpdateStatus.IDLE)
        state.set_update_progress(None)
        safe_pop_dialog(page)

    def toast(message: str) -> None:
        show_snack_bar(page, message)

    def set_busy(message: str, allow_cancel: bool = False) -> None:
        progress_col.set_busy(message)
        for control in dialog.actions:
            control.disabled = not (allow_cancel and control is cancel_btn)
        safe_update(dialog)

    def set_progress(downloaded: int, total: int | None) -> None:
        state.set_update_progress((downloaded, total))
        progress_col.set_progress(downloaded, total)

    canceled = {"flag": False}

    def _begin_install(_event) -> None:
        logger.info(
            "Update install requested: from=%s to=%s asset=%s size=%s url=%s "
            "android=%s packaged=%s tempdir=%s",
            installed_version,
            update.version,
            update.asset_name,
            update.asset_size,
            update.asset_url,
            is_android(),
            is_packaged(),
            tempfile.gettempdir(),
        )
        state.set_update_status(UpdateStatus.DOWNLOADING)
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
            state.set_update_status(UpdateStatus.FAILED)
            state.set_update_error(str(exc))
            close()
            toast("Download failed — check your connection")
            return
        except Exception:
            logger.exception("Update download failed unexpectedly")
            state.set_update_status(UpdateStatus.FAILED)
            state.set_update_error("Unexpected download error")
            close()
            toast("Download failed")
            return

        apk_path = Path(installer_path)
        logger.info(
            "Update download completed: to=%s path=%s exists=%s disk_size=%s expected=%s",
            update.version,
            apk_path,
            apk_path.is_file(),
            apk_path.stat().st_size if apk_path.is_file() else -1,
            update.asset_size,
        )
        state.set_update_status(UpdateStatus.READY)
        set_busy("Verifying…")
        await asyncio.sleep(0.05)
        if is_android():
            try:
                _install_android(apk_path, update)
            except Exception:
                logger.exception(
                    "Unexpected error during Android update install: to=%s apk=%s",
                    update.version,
                    apk_path,
                )
                state.set_update_status(UpdateStatus.FAILED)
                state.set_update_error("Android install failed")
                close()
                toast("Update install failed — see app log")
            return
        _install_windows(installer_path, update)

    def _install_windows(installer_path, update: UpdateInfo) -> None:
        updater = Updater()
        state.set_update_status(UpdateStatus.APPLYING)
        set_busy("Starting installer…")
        try:
            outcome = updater.apply_update(
                update, installer_path, relaunch=True, extra_args=installer_extra_args()
            )
        except UpdateApplyError as exc:
            logger.error("Failed to start installer: %s", exc)
            state.set_update_status(UpdateStatus.FAILED)
            state.set_update_error(str(exc))
            close()
            toast("Could not start the installer")
            return
        if outcome.result == ApplyResult.CANCELED:
            close()
            toast("Update canceled")
            return
        if outcome.result is not ApplyResult.APPLIED:
            state.set_update_status(UpdateStatus.FAILED)
            state.set_update_error("Installer could not start")
            close()
            toast("The installer could not be started")
            return
        close()
        if on_install_launched is not None:
            on_install_launched()

    def _install_android(installer_path, update: UpdateInfo) -> None:
        apk = Path(installer_path)
        logger.info(
            "Android install start: to=%s apk=%s exists=%s size=%s",
            update.version,
            apk,
            apk.is_file(),
            apk.stat().st_size if apk.is_file() else -1,
        )
        updater = Updater()
        state.set_update_status(UpdateStatus.APPLYING)
        try:
            outcome = updater.apply_update(update, apk, relaunch=False)
        except UpdateApplyError as exc:
            logger.error(
                "Failed to start APK install: to=%s apk=%s error=%s",
                update.version,
                apk,
                exc,
            )
            state.set_update_status(UpdateStatus.FAILED)
            state.set_update_error(str(exc))
            close()
            toast("Could not start the installer")
            return
        close()
        if outcome.result == ApplyResult.MANUAL_REQUIRED:
            toast("Allow app installs from Android settings, then try again")
        elif outcome.result == ApplyResult.APPLIED:
            toast("Installer opened — follow the on-screen instructions")
            _finish_activity_after_install(page)
        else:
            toast("The installer could not be started")

    def _open_releases(_event) -> None:
        close()
        asyncio.create_task(page.launch_url(update.html_url))

    def _later(_event) -> None:
        canceled["flag"] = True
        close()

    install_btn = ft.FilledButton(
        "Download & install",
        icon=ft.Icons.DOWNLOAD,
        on_click=_begin_install,
    )
    cancel_btn = ft.TextButton("Cancel", on_click=_later)
    releases_btn = ft.TextButton("Open releases page", on_click=_open_releases)
    later_btn = ft.TextButton("Later", on_click=_later)

    dialog.actions = (
        [releases_btn, later_btn, install_btn]
        if installable
        else [later_btn, releases_btn]
    )
    dialog.actions_alignment = ft.MainAxisAlignment.END
    page.show_dialog(dialog)
    safe_update(page)
