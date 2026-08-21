"""Update-offer overlay: version info, rendered release notes, download/install.

A full custom control (``custom/``), not a small component: it mounts a
scrim + centered surface into ``page.overlay``, owns a download/install
state machine, and records every step into
:class:`core.state.app_state.AppState` (``UpdateStatus`` / progress /
error) so any screen can observe it. It also owns the release-notes
pipeline — sanitizing GitHub bodies and rendering them with a themed
``ft.Markdown`` — so the whole feature lives in one module.

Sizing follows the layout-driven philosophy of the navigation controls:
window-derived numbers come from :func:`resolve_dialog_metrics` via
``AppLayout.dialog_metrics`` (never from ``page`` geometry inline), the
content-dependent notes estimate is composed on top, and the dialog
subscribes to ``KEY_LAYOUT`` so an open surface re-sizes when the window
changes (the status-bar / base-screen observer pattern).

Historical note: the old Material ``AlertDialog``'s release notes blew up
into a tall blank grey rectangle — bisected down to a flex child inside
the wrapping version row (``Text(expand=True)`` inside ``Row(wrap=True)``
breaks layout on the flet 0.86.5 client, painting everything below the
dialog header grey). The row's text must stay non-flex. The overlay
approach was kept anyway: it avoids the ``AlertDialog`` content wrapper
entirely, so the surface can size and scroll predictably on every target.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from core.state.app_state import (
    KEY_LAYOUT,
    UpdateStatus,
    get_app_state,
)
from core.update_checker import (
    ApplyResult,
    DownloadError,
    UpdateChecker,
    UpdateInfo,
)
from core.update_flow import UpdateApplyError, Updater, installer_extra_args
from UI.components.motion import entrance
from UI.layout.layout_resolver import resolve_dialog_metrics
from UI.layout.models import DialogMetrics, ScreenFormFactor
from utils.files import remove_file
from utils.flet_helpers import safe_update, show_snack_bar
from utils.platform import is_android, is_packaged

logger = logging.getLogger(__name__)

_UPDATE_POLL_INTERVAL = 0.15

#: Average glyph width for 12px notes text; used for line-wrap estimation.
_NOTES_CHAR_WIDTH = 7.5

#: Rendered line height for 12px notes text (1.4x line height).
_NOTES_LINE_HEIGHT = 17.0

#: Heading line height for 12-15px bold headings.
_HEADING_LINE_HEIGHT = 19.0

#: Vertical gap between markdown blocks (mirrors the stylesheet spacing).
_BLOCK_SPACING = 4.0

_MAX_NOTES_CHARS = 6000

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_FENCED_BLOCK_PATTERN = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_TILDE_BLOCK_PATTERN = re.compile(r"~~~[^\n]*\n.*?~~~", re.DOTALL)
_STRAY_FENCE_PATTERN = re.compile(r"^```[^\n]*\n?", re.MULTILINE)
_STRAY_TILDE_PATTERN = re.compile(r"^~~~[^\n]*\n?", re.MULTILINE)


def sanitize_release_notes(markdown: str, max_chars: int = _MAX_NOTES_CHARS) -> str:
    """Make a GitHub release body safe and compact for in-app rendering.

    - Image syntax (``![alt](url)``) is dropped, keeping the alt text.
    - Raw HTML tags are removed (GitHub bodies may carry ``<details>``,
      ``<img>``, ``<br>`` and friends that flet cannot render).
    - Fenced code blocks (backtick or tilde) are removed entirely: flet's
      markdown renders them with a ``SizedBox(width: 10000)`` that pushes
      the dialog content off-screen horizontally.
    - Runaway blank lines are collapsed.
    - The result is capped at ``max_chars`` with a trailing ellipsis.
    """
    text = _IMAGE_PATTERN.sub(r"\1", markdown or "")
    text = _HTML_TAG_PATTERN.sub("", text)
    text = _FENCED_BLOCK_PATTERN.sub("", text)
    text = _TILDE_BLOCK_PATTERN.sub("", text)
    text = _STRAY_FENCE_PATTERN.sub("", text)
    text = _STRAY_TILDE_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _estimate_notes_height(notes: str, width: float) -> float:
    """Approximate rendered height (px) of sanitized markdown in *width* px.

    Counts wrapped lines (``len / glyph-width``) per source line and adds
    the per-block spacing. Used to auto-size the surface so short notes get
    a compact dialog; the scroll mode absorbs any underestimation.
    """
    if not notes:
        return 0.0
    chars_per_line = max(24.0, width / _NOTES_CHAR_WIDTH)
    height = 0.0
    for line in notes.split("\n"):
        text = line.strip()
        if not text:
            height += _NOTES_LINE_HEIGHT
            continue
        wraps = max(1, math.ceil(len(text) / chars_per_line))
        line_height = (
            _HEADING_LINE_HEIGHT if text.startswith("#") else _NOTES_LINE_HEIGHT
        )
        height += wraps * line_height + _BLOCK_SPACING
    return height


def _surface_dimensions(metrics: DialogMetrics, notes: str) -> tuple[float, float]:
    """Surface width/height from resolved metrics plus the content estimate.

    The surface follows the estimated notes height (plus fixed chrome) so
    short notes produce a compact dialog, but never exceeds the window-
    relative cap from :func:`resolve_dialog_metrics` — longer content
    scrolls inside the surface.
    """
    width = metrics.width
    estimate = metrics.chrome_height + _estimate_notes_height(notes, width)
    height = min(metrics.max_height, max(metrics.min_height, estimate))
    return width, height


def _format_release_date(published_at: str) -> str:
    """``2026-08-01T16:51:33Z`` -> ``Aug 1, 2026``; empty when unparseable."""
    if not published_at:
        return ""
    try:
        stamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return stamp.astimezone().strftime("%b %d, %Y").replace(" 0", " ")


def _link_handler(page: ft.Page):
    async def _open(event) -> None:
        url = getattr(event, "data", None)
        if url:
            await page.launch_url(url)

    return _open


def build_notes_markdown(page: ft.Page, notes: str) -> ft.Markdown:
    """Build the themed, scrollable release-notes control.

    Uses the GitHub Web extension set so bare URLs (e.g. the "Full
    Changelog" line) are auto-linked. Links open via ``page.launch_url``;
    code blocks pick a highlight theme matching the current theme mode.
    """
    dark = getattr(page, "theme_mode", None) == ft.ThemeMode.DARK
    code_theme = ft.MarkdownCodeTheme.A11Y_DARK if dark else ft.MarkdownCodeTheme.GITHUB
    return ft.Markdown(
        value=notes,
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=code_theme,
        auto_follow_links=False,
        md_style_sheet=ft.MarkdownStyleSheet(
            p_text_style=ft.TextStyle(size=12, color=ft.Colors.ON_SURFACE, height=1.4),
            h1_text_style=ft.TextStyle(
                size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            h2_text_style=ft.TextStyle(
                size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            h3_text_style=ft.TextStyle(
                size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            h4_text_style=ft.TextStyle(
                size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            a_text_style=ft.TextStyle(
                color=ft.Colors.PRIMARY, weight=ft.FontWeight.W_500
            ),
            block_spacing=4,
        ),
        on_tap_link=_link_handler(page),
    )


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
    logger.info("_finish_activity_after_install: scheduling activity cleanup in 2.0s")

    async def _close() -> None:
        logger.info(
            "_finish_activity_after_install: waiting 2.0s before destroying activity"
        )
        await asyncio.sleep(2.0)
        logger.info("_finish_activity_after_install: calling page.window.destroy()")
        try:
            await page.window.destroy()
            logger.info(
                "_finish_activity_after_install: page.window.destroy() completed"
            )
        except Exception as exc:
            logger.error(
                "_finish_activity_after_install: page.window.destroy() failed: %s", exc
            )

    asyncio.create_task(_close())


def _chip(text: str, bgcolor: str, fgcolor: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_500, color=fgcolor),
        bgcolor=bgcolor,
        padding=ft.padding.Padding.symmetric(horizontal=8, vertical=3),
        border_radius=12,
    )


class _DownloadCanceled(Exception):
    pass


class UpdateDialog(ft.Stack):
    """Modal update-offer overlay: notes, size, download-with-progress, install.

    Constructed headless-safe: nothing touches ``page`` until :meth:`show`
    mounts the overlay (the ``CollectionStatusBar`` pattern). ``show``
    records ``AVAILABLE`` and plays the shared fade+scale entrance; the
    surface scrolls when the notes are long and Escape dismisses on desktop.
    Windows hands the verified installer to the elevated setup flow
    (:class:`core.update_flow.Updater`) and requests an app restart via
    ``on_install_launched``; Android opens the APK through the system
    installer. Manual-only releases (no auto-install asset) only get the
    releases-page button.

    Auto screen sizing: the surface dimensions come from
    :class:`DialogMetrics` resolved by :func:`resolve_dialog_metrics` and
    carried on ``AppLayout.dialog_metrics`` — the same layout-driven
    philosophy as the navigation controls. The dialog reads them from
    ``app_state.layout`` at ``show`` time and subscribes to ``KEY_LAYOUT``
    so an open surface re-sizes when the window changes.

    Every transition is recorded on :class:`core.state.app_state.AppState`
    (``AVAILABLE`` → ``DOWNLOADING`` → ``READY`` → ``APPLYING``, or
    ``FAILED`` / back to ``IDLE`` on close).
    """

    def __init__(self) -> None:
        super().__init__(expand=True, alignment=ft.Alignment.CENTER)
        self._page: ft.Page | None = None
        self._update: UpdateInfo | None = None
        self._installed_version = ""
        self._on_install_launched: Optional[Callable[[], None]] = None
        self._prior_keyboard_handler: Optional[Callable] = None
        self._progress = _UpdateProgress()
        self._surface: ft.Container | None = None
        self._actions_buttons: list[ft.Control] = []
        self._cancel_btn: ft.TextButton | None = None
        self._canceled = {"flag": False}
        self._metrics: DialogMetrics | None = None
        self._notes = ""
        get_app_state().on_change(KEY_LAYOUT, self._on_layout_changed)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def show(
        self,
        page: ft.Page,
        update: UpdateInfo,
        installed_version: str,
        on_install_launched: Optional[Callable[[], None]] = None,
    ) -> None:
        """Mount the overlay on *page* and record the update as available."""
        self._page = page
        self._update = update
        self._installed_version = installed_version
        self._on_install_launched = on_install_launched

        state = get_app_state()
        state.set_update_info(update)
        state.set_update_status(UpdateStatus.AVAILABLE)
        state.set_update_error(None)

        layout = state.layout
        if layout is not None:
            metrics = layout.dialog_metrics
        else:
            # Safety net for headless runs and early page loads; the app
            # shell always sets a layout before showing a dialog.
            metrics = resolve_dialog_metrics(
                ScreenFormFactor.DESKTOP, page.width or 1280, page.height or 800
            )
        self._metrics = metrics

        self.controls = [
            ft.Container(expand=True, bgcolor=ft.Colors.BLACK_54),
            self._build_surface(page, update, installed_version, metrics),
        ]
        page.overlay.append(self)
        safe_update(page)
        self._install_keyboard_handler(page)
        if self._surface is not None:
            entrance(self._surface)

    def close(self) -> None:
        """Dismiss the overlay, restore the page, reset the update state."""
        page = self._page
        self._page = None
        if page is None:
            return
        if self in page.overlay:
            page.overlay.remove(self)
        if self._prior_keyboard_handler is not None:
            page.on_keyboard_event = self._prior_keyboard_handler
            self._prior_keyboard_handler = None
        state = get_app_state()
        state.unsubscribe(KEY_LAYOUT, self._on_layout_changed)
        state.set_update_status(UpdateStatus.IDLE)
        state.set_update_progress(None)
        safe_update(page)

    # ── Layout ────────────────────────────────────────────────────────────

    def _on_layout_changed(self, _key: str) -> None:
        """Re-size the open surface when the window changes (auto sizing)."""
        if self._page is None or self._surface is None:
            return
        layout = get_app_state().layout
        if layout is not None:
            self._apply_metrics(layout.dialog_metrics)

    def _apply_metrics(self, metrics: DialogMetrics) -> None:
        """Re-derive the surface width/height from resolved dialog metrics."""
        self._metrics = metrics
        if self._surface is None:
            return
        width, height = _surface_dimensions(metrics, self._notes)
        self._surface.width = width
        self._surface.height = height
        safe_update(self._surface)

    # ── Surface ───────────────────────────────────────────────────────────

    def _build_surface(
        self,
        page: ft.Page,
        update: UpdateInfo,
        installed_version: str,
        metrics: DialogMetrics,
    ) -> ft.Container:
        notes = sanitize_release_notes(update.release_notes)
        self._notes = notes
        size_mb = (
            f"{(update.asset_size or 0) / 1_000_000:.1f} MB"
            if update.asset_size
            else "—"
        )

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
                "No release notes provided.",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
        notes_box = ft.Container(
            content=ft.Column(
                controls=[notes_control],
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

        installable = is_packaged() and not update.is_manual_only
        install_btn = ft.FilledButton(
            "Download & install",
            icon=ft.Icons.DOWNLOAD,
            on_click=self._begin_install,
        )
        cancel_btn = ft.TextButton("Cancel", on_click=self._later)
        releases_btn = ft.TextButton("Open releases page", on_click=self._open_releases)
        later_btn = ft.TextButton("Later", on_click=self._later)
        self._cancel_btn = cancel_btn
        self._actions_buttons = (
            [releases_btn, later_btn, install_btn]
            if installable
            else [later_btn, releases_btn]
        )
        actions = ft.Row(
            wrap=True,
            alignment=ft.MainAxisAlignment.END,
            spacing=8,
            controls=self._actions_buttons,
        )

        content = ft.Column(
            controls=[header, details, self._progress, actions],
            tight=True,
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        )
        width, height = _surface_dimensions(metrics, notes)
        self._surface = ft.Container(
            content=content,
            width=width,
            height=height,
            bgcolor=ft.Colors.SURFACE,
            border_radius=28,
            padding=ft.padding.Padding.symmetric(horizontal=24, vertical=20),
            shadow=ft.BoxShadow(
                blur_radius=32, spread_radius=2, color=ft.Colors.BLACK_26
            ),
        )
        return self._surface

    # ── Keyboard ─────────────────────────────────────────────────────────

    def _install_keyboard_handler(self, page: ft.Page) -> None:
        """Let Escape dismiss the dialog; the previous handler still fires."""
        prior = page.on_keyboard_event
        self._prior_keyboard_handler = prior if callable(prior) else None

        def _on_key(event) -> None:
            if getattr(event, "key", None) == "Escape":
                self._later(None)
            elif self._prior_keyboard_handler is not None:
                self._prior_keyboard_handler(event)

        page.on_keyboard_event = _on_key

    # ── Handlers ──────────────────────────────────────────────────────────

    def _set_busy(self, message: str, allow_cancel: bool = False) -> None:
        self._progress.set_busy(message)
        for control in self._actions_buttons:
            control.disabled = not (allow_cancel and control is self._cancel_btn)
        safe_update(self)

    def _set_progress(self, downloaded: int, total: int | None) -> None:
        get_app_state().set_update_progress((downloaded, total))
        self._progress.set_progress(downloaded, total)

    def _begin_install(self, _event) -> None:
        page = self._page
        update = self._update
        if page is None or update is None:
            return
        logger.info(
            "Update install requested: from=%s to=%s asset=%s size=%s url=%s "
            "android=%s packaged=%s tempdir=%s",
            self._installed_version,
            update.version,
            update.asset_name,
            update.asset_size,
            update.asset_url,
            is_android(),
            is_packaged(),
            tempfile.gettempdir(),
        )
        get_app_state().set_update_status(UpdateStatus.DOWNLOADING)
        self._set_busy("Preparing…", allow_cancel=True)
        page.run_task(self._run_download_and_install)

    async def _run_download_and_install(self) -> None:
        update = self._update
        if update is None:
            return
        checker = UpdateChecker()
        progress: dict[str, object] = {"downloaded": 0, "total": None, "changed": False}

        def on_progress(done: int, total: int | None) -> None:
            progress["downloaded"] = done
            progress["total"] = total
            progress["changed"] = True
            if self._canceled["flag"]:
                raise _DownloadCanceled

        download_task = asyncio.create_task(
            asyncio.to_thread(checker.download, update, None, on_progress)
        )
        while not download_task.done():
            await asyncio.sleep(_UPDATE_POLL_INTERVAL)
            if progress["changed"]:
                progress["changed"] = False
                self._set_progress(progress["downloaded"], progress["total"])

        try:
            installer_path = await download_task
        except _DownloadCanceled:
            logger.info("Update download canceled by user")
            remove_file(Path(tempfile.gettempdir()) / (update.asset_name or "update"))
            page = self._page
            self.close()
            if page is not None:
                show_snack_bar(page, "Download canceled")
            return
        except DownloadError as exc:
            logger.warning("Update download failed: %s", exc)
            get_app_state().set_update_status(UpdateStatus.FAILED)
            get_app_state().set_update_error(str(exc))
            page = self._page
            self.close()
            if page is not None:
                show_snack_bar(page, "Download failed — check your connection")
            return
        except Exception:
            logger.exception("Update download failed unexpectedly")
            get_app_state().set_update_status(UpdateStatus.FAILED)
            get_app_state().set_update_error("Unexpected download error")
            page = self._page
            self.close()
            if page is not None:
                show_snack_bar(page, "Download failed")
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
        get_app_state().set_update_status(UpdateStatus.READY)
        self._set_busy("Verifying…")
        await asyncio.sleep(0.05)
        if is_android():
            try:
                self._install_android(apk_path, update)
            except Exception:
                logger.exception(
                    "Unexpected error during Android update install: to=%s apk=%s",
                    update.version,
                    apk_path,
                )
                get_app_state().set_update_status(UpdateStatus.FAILED)
                get_app_state().set_update_error("Android install failed")
                page = self._page
                self.close()
                if page is not None:
                    show_snack_bar(page, "Update install failed — see app log")
            return
        self._install_windows(installer_path, update)

    def _install_windows(self, installer_path, update: UpdateInfo) -> None:
        page = self._page
        updater = Updater()
        get_app_state().set_update_status(UpdateStatus.APPLYING)
        self._set_busy("Starting installer…")
        try:
            outcome = updater.apply_update(
                update, installer_path, relaunch=True, extra_args=installer_extra_args()
            )
        except UpdateApplyError as exc:
            logger.error("Failed to start installer: %s", exc)
            get_app_state().set_update_status(UpdateStatus.FAILED)
            get_app_state().set_update_error(str(exc))
            self.close()
            if page is not None:
                show_snack_bar(page, "Could not start the installer")
            return
        if outcome.result == ApplyResult.CANCELED:
            self.close()
            if page is not None:
                show_snack_bar(page, "Update canceled")
            return
        if outcome.result is not ApplyResult.APPLIED:
            get_app_state().set_update_status(UpdateStatus.FAILED)
            get_app_state().set_update_error("Installer could not start")
            self.close()
            if page is not None:
                show_snack_bar(page, "The installer could not be started")
            return
        self.close()
        if self._on_install_launched is not None:
            self._on_install_launched()

    def _install_android(self, installer_path, update: UpdateInfo) -> None:
        page = self._page
        apk = Path(installer_path)
        logger.info(
            "Android install start: to=%s apk=%s exists=%s size=%s",
            update.version,
            apk,
            apk.is_file(),
            apk.stat().st_size if apk.is_file() else -1,
        )
        updater = Updater()
        get_app_state().set_update_status(UpdateStatus.APPLYING)
        try:
            outcome = updater.apply_update(update, apk, relaunch=False)
        except UpdateApplyError as exc:
            logger.error(
                "Failed to start APK install: to=%s apk=%s error=%s",
                update.version,
                apk,
                exc,
            )
            get_app_state().set_update_status(UpdateStatus.FAILED)
            get_app_state().set_update_error(str(exc))
            self.close()
            if page is not None:
                show_snack_bar(page, "Could not start the installer")
            return
        self.close()
        if page is None:
            return
        if outcome.result == ApplyResult.MANUAL_REQUIRED:
            show_snack_bar(
                page, "Allow app installs from Android settings, then try again"
            )
        elif outcome.result == ApplyResult.APPLIED:
            show_snack_bar(page, "Installer opened — follow the on-screen instructions")
            _finish_activity_after_install(page)
        else:
            show_snack_bar(page, "The installer could not be started")

    def _open_releases(self, _event) -> None:
        page = self._page
        url = getattr(self._update, "html_url", None)
        self.close()
        if page is not None and url:
            asyncio.create_task(page.launch_url(url))

    def _later(self, _event) -> None:
        self._canceled["flag"] = True
        self.close()


def show_update_dialog(
    page: ft.Page,
    update: UpdateInfo,
    installed_version: str,
    on_install_launched: Optional[Callable[[], None]] = None,
) -> None:
    """Show the update offer on *page* (compat entry point)."""
    UpdateDialog().show(page, update, installed_version, on_install_launched)
