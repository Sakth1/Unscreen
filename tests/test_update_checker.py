import hashlib
import importlib.metadata
import json
import sys
import types
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from core.update_checker import (
    ApplyError,
    ApplyResult,
    DigestMismatchError,
    DownloadError,
    UpdateChecker,
    UpdateCheckError,
    UpdateInfo,
    _api_request,
    _select_asset,
)
from utils.versions import (
    get_current_version,
)

RELEASE = {
    "tag_name": "v0.4.2",
    "body": "## What's Changed",
    "published_at": "2026-08-04T15:26:38Z",
    "prerelease": False,
    "html_url": "https://github.com/Sakth1/Unscreen/releases/tag/v0.4.2",
    "assets": [
        {
            "name": "0.4.2-setup.exe",
            "browser_download_url": "https://example.com/0.4.2-setup.exe",
            "size": 1000,
            "digest": "sha256:" + "a" * 64,
        },
        {
            "name": "0.4.2-portable.zip",
            "browser_download_url": "https://example.com/0.4.2-portable.zip",
            "size": 2000,
        },
        {
            "name": "0.4.2.apk",
            "browser_download_url": "https://example.com/0.4.2.apk",
            "size": 3000,
        },
    ],
}


class _FakeResponse:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self._chunks.pop(0) if self._chunks else b""


def _update(
    asset_name="0.4.2-setup.exe",
    digest="a" * 64,
    asset_size=6,
) -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        tag_name="v9.9.9",
        release_notes="",
        published_at="",
        prerelease=False,
        html_url="https://github.com/Sakth1/Unscreen/releases/tag/v9.9.9",
        asset_name=asset_name,
        asset_url=f"https://example.com/{asset_name}",
        asset_size=asset_size,
        asset_digest=digest,
    )


# ── version helpers ────────────────────────────────────────────────────────
# Version ordering contracts live in tests/test_utils.py; only the
# update-checker-specific get_current_version behavior belongs here.


def test_get_current_version_reads_metadata():
    with patch("importlib.metadata.version", return_value="9.9.9"):
        assert get_current_version() == "9.9.9"


def test_get_current_version_falls_back_without_metadata():
    from utils.constants import FALLBACK_APP_VERSION

    with patch(
        "importlib.metadata.version",
        side_effect=importlib.metadata.PackageNotFoundError("unscreen"),
    ):
        assert get_current_version() == FALLBACK_APP_VERSION


# ── asset selection ────────────────────────────────────────────────────────


def test_select_asset_windows_prefers_installer():
    with patch("core.update_checker.platform.system", return_value="Windows"):
        selected = _select_asset(RELEASE)
        assert selected is not None
        assert selected["name"] == "0.4.2-setup.exe"


def test_select_asset_windows_falls_back_to_portable():
    with patch("core.update_checker.platform.system", return_value="Windows"):
        portable_only = {**RELEASE, "assets": RELEASE["assets"][1:]}
        selected = _select_asset(portable_only)
        assert selected is not None
        assert selected["name"] == "0.4.2-portable.zip"


def test_select_asset_android_picks_apk():
    with patch("core.update_checker.platform.system", return_value="Android"):
        selected = _select_asset(RELEASE)
        assert selected is not None
        assert selected["name"] == "0.4.2.apk"


def test_select_asset_returns_none_when_no_match():
    with patch("core.update_checker.platform.system", return_value="Linux"):
        assert _select_asset(RELEASE) is None


# ── check_for_update ───────────────────────────────────────────────────────


def test_check_reports_newer_release():
    checker = UpdateChecker(current_version="0.4.2")
    with patch(
        "core.update_checker._api_request",
        return_value={**RELEASE, "tag_name": "v9.9.9"},
    ):
        update = checker.check_for_update()
    assert update is not None
    assert update.version == "9.9.9"
    assert update.release_notes == "## What's Changed"
    assert update.asset_name == "0.4.2-setup.exe"
    assert update.asset_digest == "a" * 64
    assert update.is_manual_only is False


def test_check_returns_none_when_current_is_newer_or_equal():
    checker = UpdateChecker(current_version="0.4.2")
    with patch("core.update_checker._api_request", return_value=RELEASE):
        assert checker.check_for_update() is None
    with patch(
        "core.update_checker._api_request",
        return_value={**RELEASE, "tag_name": "v0.4.1"},
    ):
        assert checker.check_for_update() is None


def test_check_returns_manual_only_when_no_matching_asset():
    checker = UpdateChecker(current_version="0.4.2")
    release = {
        **RELEASE,
        "tag_name": "v9.9.9",
        "assets": [{"name": "0.4.2.dmg", "browser_download_url": "https://x"}],
    }
    with patch("core.update_checker._api_request", return_value=release):
        update = checker.check_for_update()
    assert update is not None
    assert update.is_manual_only is True


def test_check_returns_none_on_404():
    checker = UpdateChecker(current_version="0.4.2")
    error = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
    with patch("core.update_checker._api_request", side_effect=error):
        assert checker.check_for_update() is None


def test_check_raises_on_http_error():
    checker = UpdateChecker(current_version="0.4.2")
    error = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)
    with patch("core.update_checker._api_request", side_effect=error):
        with pytest.raises(UpdateCheckError, match="HTTP 403"):
            checker.check_for_update()


def test_check_raises_on_network_error():
    checker = UpdateChecker(current_version="0.4.2")
    with patch(
        "core.update_checker._api_request",
        side_effect=urllib.error.URLError("offline"),
    ):
        with pytest.raises(UpdateCheckError, match="offline"):
            checker.check_for_update()


def test_check_raises_on_invalid_json():
    checker = UpdateChecker(current_version="0.4.2")
    with patch(
        "core.update_checker._api_request",
        side_effect=json.JSONDecodeError("bad", "bad", 0),
    ):
        with pytest.raises(UpdateCheckError, match="invalid JSON"):
            checker.check_for_update()


def test_api_request_parses_json_with_headers():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse([json.dumps(RELEASE).encode()])

    with patch("core.update_checker.urllib.request.urlopen", side_effect=fake_urlopen):
        data = _api_request("https://api.github.com/x", 5)

    assert data == RELEASE
    assert captured["timeout"] == 5
    assert captured["request"].get_header("Accept") == "application/vnd.github+json"
    assert captured["request"].get_header("User-agent").startswith("Unscreen-updater/")


# ── download ───────────────────────────────────────────────────────────────


def test_download_streams_with_progress(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    calls = []
    with patch(
        "core.update_checker.urllib.request.urlopen",
        return_value=_FakeResponse([b"abc", b"def"]),
    ):
        path = checker.download(
            _update(digest=None),
            destination_dir=tmp_path,
            progress=lambda done, total: calls.append((done, total)),
        )
    assert path == tmp_path / "0.4.2-setup.exe"
    assert path.read_bytes() == b"abcdef"
    assert calls == [(3, 6), (6, 6)]


def test_download_verifies_sha256(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    payload = b"abc"
    digest = hashlib.sha256(payload).hexdigest()
    with patch(
        "core.update_checker.urllib.request.urlopen",
        return_value=_FakeResponse([payload]),
    ):
        path = checker.download(
            _update(digest=digest, asset_size=3), destination_dir=tmp_path
        )
    assert path.read_bytes() == payload


def test_download_removes_file_on_digest_mismatch(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    with patch(
        "core.update_checker.urllib.request.urlopen",
        return_value=_FakeResponse([b"abcdef"]),
    ):
        with pytest.raises(DigestMismatchError):
            checker.download(_update(), destination_dir=tmp_path)
    assert not (tmp_path / "0.4.2-setup.exe").exists()


def test_download_removes_file_on_incomplete_transfer(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    with patch(
        "core.update_checker.urllib.request.urlopen",
        return_value=_FakeResponse([b"abc"]),
    ):
        with pytest.raises(DownloadError, match="Incomplete"):
            checker.download(_update(digest=None), destination_dir=tmp_path)
    assert not (tmp_path / "0.4.2-setup.exe").exists()


def test_download_removes_file_on_network_error(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    with patch(
        "core.update_checker.urllib.request.urlopen",
        side_effect=urllib.error.URLError("boom"),
    ):
        with pytest.raises(DownloadError, match="boom"):
            checker.download(_update(), destination_dir=tmp_path)
    assert not (tmp_path / "0.4.2-setup.exe").exists()


def test_download_rejects_release_without_asset(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    manual = UpdateInfo(
        version="9.9.9",
        tag_name="v9.9.9",
        release_notes="",
        published_at="",
        prerelease=False,
        html_url="https://github.com/Sakth1/Unscreen/releases/tag/v9.9.9",
    )
    with pytest.raises(DownloadError, match="no downloadable asset"):
        checker.download(manual, destination_dir=tmp_path)


# ── apply ──────────────────────────────────────────────────────────────────


def test_apply_refuses_in_dev_mode(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"x")
    with patch("core.update_checker.is_packaged", return_value=False):
        with pytest.raises(ApplyError, match="running from source"):
            checker.apply(_update(), installer)


def test_apply_windows_launches_silent_installer_elevated(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Windows"),
        patch("core.update_checker._launch_elevated", return_value=4242) as launch,
    ):
        outcome = checker.apply(_update(), installer)
    assert outcome.result == ApplyResult.APPLIED
    assert outcome.process_id == 4242
    command, args = launch.call_args.args
    cwd = launch.call_args.kwargs["cwd"]
    assert command == str(installer)
    assert cwd == str(tmp_path)
    assert "/VERYSILENT" in args
    assert "/SUPPRESSMSGBOXES" in args
    assert "/NORESTART" in args


def test_apply_windows_forwards_extra_args(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Windows"),
        patch("core.update_checker._launch_elevated", return_value=7) as launch,
    ):
        outcome = checker.apply(
            _update(),
            installer,
            extra_args=["/ALLUSERS", '/DIR="C:\\Program Files\\Unscreen"'],
        )
    assert outcome.result == ApplyResult.APPLIED
    _, args = launch.call_args.args
    assert "/ALLUSERS" in args
    assert '/DIR="C:\\Program Files\\Unscreen"' in args


def test_apply_windows_canceled_returns_canceled(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Windows"),
        patch("core.update_checker._launch_elevated", return_value=None),
    ):
        outcome = checker.apply(_update(), installer)
    assert outcome.result == ApplyResult.CANCELED
    assert outcome.process_id is None


def test_apply_windows_bubbles_launch_failure(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Windows"),
        patch(
            "core.update_checker._launch_elevated",
            side_effect=ApplyError("Failed to launch setup.exe (error 5)"),
        ),
    ):
        with pytest.raises(ApplyError, match="error 5"):
            checker.apply(_update(), installer)


def test_apply_windows_missing_installer_raises(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Windows"),
    ):
        with pytest.raises(ApplyError, match="Installer not found"):
            checker.apply(_update(), tmp_path / "missing.exe")


def test_apply_android_manual_without_jnius(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    apk = tmp_path / "0.4.2.apk"
    apk.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Android"),
        patch.dict(sys.modules, {"jnius": None}),
    ):
        outcome = checker.apply(_update(asset_name="0.4.2.apk"), apk)
    assert outcome.result == ApplyResult.MANUAL_REQUIRED


def test_apply_android_triggers_install_intent(tmp_path):
    jnius = types.ModuleType("jnius")
    activity = MagicMock()
    activity.mActivity = MagicMock()

    def fake_autoclass(name):
        if name == "org.kivy.android.PythonActivity":
            return activity
        return MagicMock()

    jnius.autoclass = fake_autoclass
    checker = UpdateChecker(current_version="0.4.2")
    apk = tmp_path / "0.4.2.apk"
    apk.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Android"),
        patch.dict(sys.modules, {"jnius": jnius}),
    ):
        outcome = checker.apply(_update(asset_name="0.4.2.apk"), apk)
    assert outcome.result == ApplyResult.APPLIED
    assert activity.mActivity.startActivity.called


def test_apply_unsupported_platform_is_not_applicable(tmp_path):
    checker = UpdateChecker(current_version="0.4.2")
    installer = tmp_path / "setup"
    installer.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Linux"),
    ):
        outcome = checker.apply(_update(), installer)
    assert outcome.result == ApplyResult.NOT_APPLICABLE
