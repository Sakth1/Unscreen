import hashlib
import json
import logging
import os
import platform
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from functools import cmp_to_key
from pathlib import Path
from typing import Callable, Sequence

from utils.constants import RELEASES_PAGE_URL, RELEASES_REPO_URL
from utils.files import remove_file
from utils.platform import is_android, is_packaged
from utils.versions import (
    compare_versions,
    get_current_version,
    normalize_version,
    parse_version,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024

_API_VERSION_HEADER = "2022-11-28"


class UpdateCheckError(Exception):
    """Raised when the latest release cannot be fetched or parsed."""


class DownloadError(Exception):
    """Raised when a release asset cannot be downloaded."""


class DigestMismatchError(DownloadError):
    """Raised when a downloaded asset fails sha256 verification."""


class ApplyError(Exception):
    """Raised when a downloaded update cannot be applied."""


class ApplyResult(Enum):
    APPLIED = "applied"
    CANCELED = "canceled"
    MANUAL_REQUIRED = "manual_required"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ApplyOutcome:
    """Result of :meth:`UpdateChecker.apply`.

    ``process_id`` is set when an installer process actually started; the
    caller can monitor it (e.g. from a relaunch watchdog) without holding
    extra privileges.
    """

    result: ApplyResult
    process_id: int | None = None


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    release_notes: str
    published_at: str
    prerelease: bool
    html_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int | None = None
    asset_digest: str | None = None

    @property
    def is_manual_only(self) -> bool:
        """True when the release has no asset this platform can auto-install."""
        return self.asset_url is None


def _parse_digest(digest: str | None) -> str | None:
    """Extract the hex sha256 from a GitHub asset digest (``sha256:...``)."""
    if not digest:
        return None
    if ":" in digest:
        digest = digest.rsplit(":", 1)[1]
    try:
        bytes.fromhex(digest)
    except ValueError:
        logger.warning("Unparseable asset digest: %r", digest)
        return None
    return digest


def _select_asset(release: dict) -> dict | None:
    """Pick the asset this platform can auto-install, if any."""
    assets = release.get("assets") or []
    system = platform.system()
    for asset in assets:
        name = asset.get("name", "")
        if system == "Windows" and name.endswith("-setup.exe"):
            return asset
        if is_android() and name.endswith(".apk"):
            return asset
    if system == "Windows":
        for asset in assets:
            if asset.get("name", "").endswith("-portable.zip"):
                return asset
    return None


def _api_request(url: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION_HEADER,
            "User-Agent": f"Unscreen-updater/{get_current_version()}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# Error 1223 from ShellExecuteEx: the user declined the UAC consent prompt.
_ERROR_CANCELLED = 1223

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_SHOWNORMAL = 1


def _launch_elevated(
    executable: str, parameters: str = "", cwd: str = ""
) -> int | None:
    """Launch an admin-requiring program via ShellExecuteEx with ``runas``.

    ``subprocess.Popen`` (CreateProcess) is not usable here: Inno's setup.exe
    declares ``requireAdministrator`` in its manifest, and CreateProcess from
    a non-elevated process fails with ERROR_ELEVATION_REQUIRED (740) instead
    of showing the consent prompt. Returns the child PID, or ``None`` when the
    user canceled the UAC prompt.
    """
    if platform.system() != "Windows":
        raise ApplyError("Elevated launch is only implemented on Windows")
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = parameters or None
    info.lpDirectory = cwd or None
    info.nShow = _SW_SHOWNORMAL

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32 = ctypes.windll.kernel32
    kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessId.restype = wintypes.DWORD

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == _ERROR_CANCELLED:
            logger.info("UAC consent canceled for %s", executable)
            return None
        raise ApplyError(f"Failed to launch {executable} (error {error})")

    try:
        if info.hProcess:
            pid = int(kernel32.GetProcessId(info.hProcess))
            return pid
        return None
    finally:
        if info.hProcess:
            kernel32.CloseHandle(info.hProcess)


class UpdateChecker:
    def __init__(
        self,
        current_version: str | None = None,
        api_url: str = RELEASES_REPO_URL,
        include_prereleases: bool = False,
        timeout: float = 10,
    ):
        self._current_version = normalize_version(
            current_version or get_current_version()
        )
        self._api_url = api_url
        self._include_prereleases = include_prereleases
        self._timeout = timeout

    @property
    def current_version(self) -> str:
        return self._current_version

    def check_for_update(
        self, include_prereleases: bool | None = None
    ) -> UpdateInfo | None:
        """Query released versions and return the newest one that beats local.

        Returns ``None`` when the app is up to date or the repository has no
        releases yet. ``include_prereleases=True`` (or the constructor flag)
        also considers dev/alpha/beta releases; otherwise only stable ones
        are candidates. Raises :class:`UpdateCheckError` on network/API
        failure.
        """
        include_pre = (
            self._include_prereleases
            if include_prereleases is None
            else include_prereleases
        )
        release = self._newest_release(include_pre)
        if release is None:
            logger.info(
                "No release matches the update channel (current %s)",
                self._current_version,
            )
            return None

        update = self._build_update_info(release)
        if compare_versions(update.version, self._current_version) <= 0:
            logger.info("No update available (current %s)", self._current_version)
            return None
        logger.info("Update available: %s -> %s", self._current_version, update.version)
        return update

    def _newest_release(self, include_prereleases: bool) -> dict | None:
        """Return the newest non-draft release matching the channel."""
        releases = self._fetch_releases()
        if not releases:
            return None
        candidates = [
            release
            for release in releases
            if release.get("tag_name")
            and not release.get("draft")
            and parse_version(release["tag_name"]) is not None
        ]
        if not include_prereleases:
            candidates = [
                release for release in candidates if not release.get("prerelease")
            ]
        if not candidates:
            return None
        return max(
            candidates,
            key=cmp_to_key(
                lambda left, right: compare_versions(
                    left.get("tag_name", ""), right.get("tag_name", "")
                )
            ),
        )

    def latest_release_url(self, include_prereleases: bool = False) -> str:
        """Resolve the newest release page matching the channel.

        GitHub only shortcuts the latest stable release (``/releases/latest``);
        the newest prerelease has to be looked up via the API. Falls back to
        :data:`RELEASES_PAGE_URL` when nothing matches or the API is
        unreachable.
        """
        try:
            release = self._newest_release(include_prereleases)
        except UpdateCheckError as exc:
            logger.warning("Could not resolve the latest release page: %s", exc)
            return RELEASES_PAGE_URL
        if release is None:
            return RELEASES_PAGE_URL
        return release.get("html_url") or RELEASES_PAGE_URL

    def _fetch_releases(self) -> list[dict] | None:
        """Fetch the release list; ``None`` when the repository has no releases."""
        try:
            payload = _api_request(self._api_url, self._timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                logger.warning("No releases found at %s", self._api_url)
                return None
            raise UpdateCheckError(f"GitHub API returned HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UpdateCheckError(f"Could not reach GitHub API: {error}") from error
        except json.JSONDecodeError as error:
            raise UpdateCheckError("GitHub API returned invalid JSON") from error
        if isinstance(payload, dict):
            return [payload]
        if not isinstance(payload, list):
            logger.warning("Unexpected releases payload: %r", type(payload).__name__)
            return []
        return payload

    def _build_update_info(self, release: dict) -> UpdateInfo:
        tag = release.get("tag_name", "")
        asset = _select_asset(release)
        return UpdateInfo(
            version=normalize_version(tag),
            tag_name=tag,
            release_notes=release.get("body") or "",
            published_at=release.get("published_at") or "",
            prerelease=bool(release.get("prerelease")),
            html_url=release.get("html_url") or RELEASES_PAGE_URL,
            asset_name=asset.get("name") if asset else None,
            asset_url=asset.get("browser_download_url") if asset else None,
            asset_size=asset.get("size") if asset else None,
            asset_digest=_parse_digest(asset.get("digest")) if asset else None,
        )

    def download(
        self,
        update: UpdateInfo,
        destination_dir: str | os.PathLike | None = None,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        """Stream the update asset to disk and verify its sha256 digest.

        ``progress`` receives ``(downloaded_bytes, total_bytes)``; ``total`` is
        ``None`` when the server did not report a length. The partial file is
        removed on failure or digest mismatch.
        """
        if not update.asset_url or not update.asset_name:
            raise DownloadError(
                f"Release {update.version} has no downloadable asset for this platform"
            )
        destination_dir = destination_dir or tempfile.gettempdir()
        directory = Path(destination_dir)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / update.asset_name

        request = urllib.request.Request(
            update.asset_url,
            headers={"User-Agent": f"Unscreen-updater/{get_current_version()}"},
        )
        downloaded = 0
        total = update.asset_size
        try:
            with (
                urllib.request.urlopen(request, timeout=self._timeout) as response,
                destination.open("wb") as fp,
            ):
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    fp.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            remove_file(destination)
            raise DownloadError(
                f"Failed to download {update.asset_name}: {error}"
            ) from error

        if total is not None and downloaded != total:
            remove_file(destination)
            raise DownloadError(f"Incomplete download: {downloaded} of {total} bytes")
        self._verify_digest(destination, update.asset_digest)
        return destination

    def _verify_digest(self, path: Path, expected: str | None) -> None:
        if not expected:
            logger.warning("No sha256 digest for %s; skipping verification", path.name)
            return
        hasher = hashlib.sha256()
        with path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(CHUNK_SIZE), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != expected:
            logger.error("sha256 mismatch for %s", path)
            remove_file(path)
            raise DigestMismatchError(
                f"Downloaded {path.name} failed sha256 verification"
            )
        logger.info("sha256 verified for %s", path.name)

    def apply(
        self,
        update: UpdateInfo,
        installer_path: str | os.PathLike,
        extra_args: Sequence[str] | None = None,
    ) -> ApplyOutcome:
        """Apply the downloaded update for the current platform.

        Windows launches the Inno Setup installer via ``ShellExecuteExW`` with
        the ``runas`` verb, so the standard UAC consent flow runs even though
        the app itself is not elevated; ``/VERYSILENT`` plus ``extra_args``
        (install dir / scope) are passed on the command line. The caller is
        responsible for exiting the app afterwards (the installer waits on the
        app mutex before replacing files). Android attempts an ``ACTION_VIEW``
        install intent and falls back to manual install.
        """
        if not is_packaged():
            raise ApplyError(
                "Cannot apply an update when running from source; run the packaged app"
            )
        system = platform.system()
        if system == "Windows":
            return self._apply_windows(installer_path, extra_args)
        if is_android():
            return self._apply_android(installer_path)
        logger.warning("No update apply path for platform %s", system)
        return ApplyOutcome(ApplyResult.NOT_APPLICABLE)

    def _apply_windows(
        self, installer_path: str | os.PathLike, extra_args: Sequence[str] | None
    ) -> ApplyOutcome:
        installer = Path(installer_path)
        if not installer.is_file():
            raise ApplyError(f"Installer not found: {installer}")
        args = " ".join(
            ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", *(extra_args or [])]
        )
        process_id = _launch_elevated(str(installer), args, cwd=str(installer.parent))
        if process_id is None:
            logger.info("User canceled the installer consent prompt")
            return ApplyOutcome(ApplyResult.CANCELED)
        logger.info("Launched installer %s (pid %s)", installer, process_id)
        return ApplyOutcome(ApplyResult.APPLIED, process_id=process_id)

    def _apply_android(self, installer_path: str | os.PathLike) -> ApplyOutcome:
        apk = Path(installer_path)
        if not apk.is_file():
            raise ApplyError(f"APK not found: {apk}")
        try:
            from jnius import autoclass  # type: ignore
        except ImportError:
            logger.warning("pyjnius not available; manual APK install required")
            return ApplyOutcome(ApplyResult.MANUAL_REQUIRED)
        try:
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            File = autoclass("java.io.File")
            Settings = autoclass("android.provider.Settings")
            BuildVersion = autoclass("android.os.Build$VERSION")
            FileProvider = autoclass("androidx.core.content.FileProvider")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
        except Exception:
            logger.exception(
                "Android install bridge unavailable; manual install required"
            )
            return ApplyOutcome(ApplyResult.MANUAL_REQUIRED)
        if BuildVersion.SDK_INT >= 26:
            try:
                if not (
                    activity.getPackageManager().canRequestPackageInstalls(
                        activity.getPackageName()
                    )
                ):
                    logger.info(
                        "Unknown sources not allowed; opening install-source settings"
                    )
                    intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
                    intent.setData(Uri.parse(f"package:{activity.getPackageName()}"))
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    activity.startActivity(intent)
                    return ApplyOutcome(ApplyResult.MANUAL_REQUIRED)
            except Exception:
                logger.exception("Failed to check unknown-source permission")
        try:
            if BuildVersion.SDK_INT >= 24:
                uri = FileProvider.getUriForFile(
                    activity,
                    f"{activity.getPackageName()}.provider",
                    File(str(apk)),
                )
                flags = (
                    Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            else:
                uri = Uri.fromFile(File(str(apk)))
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(uri, "application/vnd.android.package-archive")
            intent.addFlags(flags)
            activity.startActivity(intent)
        except Exception:
            logger.exception("APK install intent failed; manual install required")
            return ApplyOutcome(ApplyResult.MANUAL_REQUIRED)
        remove_file(apk)
        logger.info("Triggered APK install for %s", apk)
        return ApplyOutcome(ApplyResult.APPLIED)


if __name__ == "__main__":
    uc = UpdateChecker("0.4.0")
    info: UpdateInfo | None = uc.check_for_update()
    if info is None:
        print("Up to date.")
    else:
        print(f"Update available: {info.version}")
        print(info.release_notes)

        installer_path = uc.download(
            info,
            destination_dir=r"E:\Files",
            progress=lambda d, t: print(f"Downloaded {d}/{t}"),
        )

        outcome = uc.apply(info, installer_path)
        print(f"Apply: {outcome.result.value} (pid {outcome.process_id})")
