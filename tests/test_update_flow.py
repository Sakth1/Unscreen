"""Tests for the update flow orchestration (watchdog, updater)."""

from pathlib import Path
from unittest.mock import patch

from core.update_checker import ApplyOutcome, ApplyResult, UpdateInfo
from core.update_flow import (
    Updater,
    installer_extra_args,
    spawn_watchdog,
    write_relaunch_watchdog,
)


def _update() -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        tag_name="v9.9.9",
        release_notes="",
        published_at="",
        prerelease=False,
        html_url="https://github.com/Sakth1/Unscreen/releases/tag/v9.9.9",
        asset_name="Unscreen-9.9.9-setup.exe",
        asset_url="https://example.com/Unscreen-9.9.9-setup.exe",
        asset_size=10,
        asset_digest=None,
    )


def test_write_watchdog_contains_pid_and_app(tmp_path):
    path = write_relaunch_watchdog(
        4242, r"C:\Program Files\Unscreen\unscreen.exe", tmp_path
    )
    assert path.exists()
    raw = path.read_bytes()
    text = raw.decode("ascii")
    assert "4242" in text
    assert r"unscreen.exe" in text
    assert 'start "" "%APP%"' in text
    assert 'del "%~f0"' in text
    assert b"\r\n" in raw
    assert not raw.startswith(b"\xff\xfe")


def test_write_watchdog_escapes_embedded_quotes(tmp_path):
    path = write_relaunch_watchdog(9, r'C:\x\we"ird.exe', tmp_path)
    text = path.read_bytes().decode("ascii")
    assert 'we^"ird.exe' in text


def test_spawn_watchdog_hides_window(tmp_path):
    watch = write_relaunch_watchdog(5, r"C:\app\unscreen.exe", tmp_path)
    with patch("core.update_flow.subprocess.Popen") as popen:
        spawn_watchdog(watch)
    assert popen.call_args.args[0] == [str(watch)]


def test_installer_extra_args_builds_scope_and_dir():
    with (
        patch("core.update_flow.detect_install_mode", return_value="per-user"),
        patch(
            "core.update_flow.install_dir",
            return_value=Path(r"C:\Program Files\Unscreen"),
        ),
    ):
        args = installer_extra_args()
    assert "/CURRENTUSER" in args
    assert '"/DIR=' in args[0] or any(a.startswith('/DIR="') for a in args)


def test_updater_applies_and_arms_relaunch_on_windows():
    updater = Updater()
    installer = Path(r"C:\temp\setup.exe")
    outcome = ApplyOutcome(ApplyResult.APPLIED, process_id=77)
    with (
        patch.object(updater.checker, "apply", return_value=outcome) as apply_mock,
        patch("core.update_flow._is_windows", return_value=True),
        patch(
            "core.update_flow.install_dir",
            return_value=Path(r"C:\Program Files\Unscreen"),
        ),
        patch(
            "core.update_flow.write_relaunch_watchdog", return_value=Path("w.cmd")
        ) as write,
        patch("core.update_flow.spawn_watchdog") as spawn,
    ):
        result = updater.apply_update(_update(), installer)
    assert result == outcome
    apply_mock.assert_called_once_with(_update(), installer, None)
    write.assert_called_once_with(77, Path(r"C:\Program Files\Unscreen"))
    spawn.assert_called_once()


def test_updater_no_watchdog_when_canceled():
    fake = Updater()
    outcome = ApplyOutcome(ApplyResult.CANCELED)
    with (
        patch.object(fake.checker, "apply", return_value=outcome),
        patch("core.update_flow._is_windows", return_value=True),
        patch("core.update_flow.write_relaunch_watchdog") as write,
        patch("core.update_flow.spawn_watchdog") as spawn,
    ):
        result = fake.apply_update(_update(), "setup.exe")
    assert result.result == ApplyResult.CANCELED
    write.assert_not_called()
    spawn.assert_not_called()


def test_updater_no_watchdog_when_disabled():
    fake = Updater()
    outcome = ApplyOutcome(ApplyResult.APPLIED, process_id=3)
    with (
        patch.object(fake.checker, "apply", return_value=outcome),
        patch("core.update_flow._is_windows", return_value=True),
        patch("core.update_flow.write_relaunch_watchdog") as write,
        patch("core.update_flow.spawn_watchdog") as spawn,
    ):
        fake.apply_update(_update(), Path("setup.exe"), relaunch=False)
    write.assert_not_called()
    spawn.assert_not_called()


def test_updater_no_watchdog_on_non_windows():
    fake = Updater()
    outcome = ApplyOutcome(ApplyResult.APPLIED, process_id=3)
    with (
        patch.object(fake.checker, "apply", return_value=outcome),
        patch("core.update_flow._is_windows", return_value=False),
        patch("core.update_flow.write_relaunch_watchdog") as write,
    ):
        fake.apply_update(_update(), Path("setup.exe"))
    write.assert_not_called()
