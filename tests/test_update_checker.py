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


def _release(tag: str, prerelease: bool = False, **extra) -> dict:
    return {**RELEASE, "tag_name": f"v{tag}", "prerelease": prerelease, **extra}


def test_check_reports_newer_release_from_list():
    checker = UpdateChecker(current_version="0.4.2")
    releases = [_release("0.4.1"), _release("9.9.9")]
    with patch("core.update_checker._api_request", return_value=releases):
        update = checker.check_for_update()
    assert update is not None
    assert update.version == "9.9.9"
    assert update.prerelease is False


def test_check_stable_only_skips_newer_prerelease():
    checker = UpdateChecker(current_version="0.4.2")
    releases = [_release("0.4.2"), _release("9.9.9-dev.1", prerelease=True)]
    with patch("core.update_checker._api_request", return_value=releases):
        assert checker.check_for_update() is None


def test_check_prereleases_on_picks_newest_overall():
    checker = UpdateChecker(current_version="0.4.2", include_prereleases=True)
    releases = [_release("9.9.9-dev.1", prerelease=True), _release("0.4.2")]
    with patch("core.update_checker._api_request", return_value=releases):
        update = checker.check_for_update()
    assert update is not None
    assert update.version == "9.9.9-dev.1"
    assert update.prerelease is True


def test_check_prereleases_on_still_chooses_newer_stable():
    checker = UpdateChecker(current_version="0.4.2", include_prereleases=True)
    releases = [_release("9.9.9-dev.1", prerelease=True), _release("9.9.9")]
    with patch("core.update_checker._api_request", return_value=releases):
        update = checker.check_for_update()
    assert update is not None
    assert update.version == "9.9.9"


def test_check_prereleases_off_with_only_prereleases_returns_none():
    checker = UpdateChecker(current_version="0.4.2")
    releases = [_release("9.9.9-dev.1", prerelease=True)]
    with patch("core.update_checker._api_request", return_value=releases):
        assert checker.check_for_update() is None


def test_check_skips_drafts_and_unparsable_tags():
    checker = UpdateChecker(current_version="0.4.2", include_prereleases=True)
    releases = [
        _release("draft-junk", draft=True),
        {"tag_name": "not-a-version", "prerelease": False},
        _release("9.9.9"),
    ]
    with patch("core.update_checker._api_request", return_value=releases):
        update = checker.check_for_update()
    assert update is not None
    assert update.version == "9.9.9"


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
    assert apk.exists()


def _android_bridge(sdk_int: int, can_install: bool = True):
    """Build a fake ``jnius`` module bridging Java classes for tests."""
    jnius = types.ModuleType("jnius")
    activity = MagicMock()
    activity.mActivity.getPackageName.return_value = "com.mycompany.unscreen"
    package_manager = MagicMock()
    package_manager.canRequestPackageInstalls.return_value = can_install
    activity.mActivity.getPackageManager.return_value = package_manager
    real_files: list[str] = []
    classes: dict[str, MagicMock] = {}

    def fake_autoclass(name: str):
        if name == "org.kivy.android.PythonActivity":
            return activity
        if name == "android.os.Build$VERSION":
            return types.SimpleNamespace(SDK_INT=sdk_int)
        if name == "java.io.File":
            return lambda path: real_files.append(str(path)) or path
        if name == "android.content.Intent":
            intent_class = MagicMock()
            intent_class.FLAG_ACTIVITY_NEW_TASK = 0x10000000
            intent_class.FLAG_GRANT_READ_URI_PERMISSION = 0x00000001
            intent_class.ACTION_VIEW = 0x00010000
            classes[name] = intent_class
            return intent_class
        mock = MagicMock()
        classes[name] = mock
        return mock

    jnius.autoclass = fake_autoclass
    return jnius, activity, real_files, classes


def test_apply_android_uses_file_provider_on_modern_api(tmp_path):
    jnius, activity, real_files, classes = _android_bridge(sdk_int=34)
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
    file_provider = classes["androidx.core.content.FileProvider"]
    file_provider.getUriForFile.assert_called_once_with(
        activity.mActivity,
        "com.mycompany.unscreen.provider",
        str(apk),
    )
    assert real_files == [str(apk)]
    intent = classes["android.content.Intent"]
    uri, mime = intent.return_value.setDataAndType.call_args.args
    assert mime == "application/vnd.android.package-archive"
    assert uri is file_provider.getUriForFile.return_value
    flags = {call.args[0] for call in intent.return_value.addFlags.call_args_list}
    assert flags == {
        classes["android.content.Intent"].FLAG_ACTIVITY_NEW_TASK
        | classes["android.content.Intent"].FLAG_GRANT_READ_URI_PERMISSION
    }
    classes["android.net.Uri"].fromFile.assert_not_called()
    assert activity.mActivity.startActivity.called
    assert not apk.exists()


def test_apply_android_falls_back_to_file_uri_before_api_24(tmp_path):
    jnius, activity, real_files, classes = _android_bridge(sdk_int=21)
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
    classes["androidx.core.content.FileProvider"].getUriForFile.assert_not_called()
    uri, mime = classes["android.content.Intent"].return_value.setDataAndType.call_args.args
    assert mime == "application/vnd.android.package-archive"
    assert uri == classes["android.net.Uri"].fromFile.return_value
    flags = {
        call.args[0]
        for call in classes["android.content.Intent"].return_value.addFlags.call_args_list
    }
    assert flags == {classes["android.content.Intent"].FLAG_ACTIVITY_NEW_TASK}
    assert (
        classes["android.content.Intent"].FLAG_GRANT_READ_URI_PERMISSION
        not in flags
    )
    assert activity.mActivity.startActivity.called
    assert not apk.exists()


def test_apply_android_opens_unknown_sources_settings_when_not_allowed(tmp_path):
    jnius, activity, real_files, classes = _android_bridge(sdk_int=34, can_install=False)
    checker = UpdateChecker(current_version="0.4.2")
    apk = tmp_path / "0.4.2.apk"
    apk.write_bytes(b"x")
    with (
        patch("core.update_checker.is_packaged", return_value=True),
        patch("core.update_checker.platform.system", return_value="Android"),
        patch.dict(sys.modules, {"jnius": jnius}),
    ):
        outcome = checker.apply(_update(asset_name="0.4.2.apk"), apk)

    assert outcome.result == ApplyResult.MANUAL_REQUIRED
    intent = classes["android.content.Intent"].return_value
    intent.setData.assert_called_once_with(
        classes["android.net.Uri"].parse.return_value
    )
    classes["android.net.Uri"].parse.assert_called_once_with(
        "package:com.mycompany.unscreen"
    )
    assert (
        classes["android.content.Intent"].return_value.setDataAndType.call_count == 0
    )
    assert activity.mActivity.startActivity.called
    assert apk.exists()


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
