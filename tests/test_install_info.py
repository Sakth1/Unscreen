"""Tests for installer location / scope detection and arg building."""

from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from utils.install_info import (
    build_installer_args,
    detect_install_mode,
    install_dir,
    installer_scope_args,
)

UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\D2E3F4A5-B6C7-48D9-A0B1-C2D3E4F5A6B7_is1"
)


def _fake_winreg(hives: list[str]) -> ModuleType:
    """Stand-in winreg module; ``hives`` lists which hives hold the key."""

    class _Handle:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    module = ModuleType("fake_winreg")
    module.HKEY_CURRENT_USER = "HKCU"
    module.HKEY_LOCAL_MACHINE = "HKLM"
    module.KEY_READ = 0x20019

    def open_key(hive, key, *_args, **_kwargs):
        if (hive, key) in {(h, UNINSTALL_KEY) for h in hives}:
            return _Handle()
        raise FileNotFoundError

    module.OpenKey = open_key
    return module


def _patch_winreg(monkeypatch, hives: list[str]) -> None:
    monkeypatch.setattr("utils.install_info.get_winreg", lambda: _fake_winreg(hives))


def test_detect_install_mode_none_without_registry(monkeypatch):
    monkeypatch.setattr("utils.install_info.get_winreg", lambda: None)
    assert detect_install_mode() is None


def test_detect_install_mode_none_when_uninstalled(monkeypatch):
    _patch_winreg(monkeypatch, [])
    assert detect_install_mode() is None


def test_detect_install_mode_per_user(monkeypatch):
    _patch_winreg(monkeypatch, ["HKCU"])
    assert detect_install_mode() == "per-user"


def test_detect_install_mode_per_machine(monkeypatch):
    _patch_winreg(monkeypatch, ["HKLM"])
    assert detect_install_mode() == "per-machine"


def test_detect_install_mode_prefers_per_user_over_machine(monkeypatch):
    _patch_winreg(monkeypatch, ["HKCU", "HKLM"])
    assert detect_install_mode() == "per-user"


def test_install_scope_args():
    assert installer_scope_args("per-machine") == ["/ALLUSERS"]
    assert installer_scope_args("per-user") == ["/CURRENTUSER"]
    assert installer_scope_args(None) == []


def test_build_installer_args_combines_scope_and_dir(tmp_path):
    args = build_installer_args(directory=tmp_path, mode="per-user")
    assert args == ["/CURRENTUSER", f'/DIR="{tmp_path}"']


def test_build_installer_args_empty():
    assert build_installer_args() == []


def test_install_dir_none_from_source():
    with patch("utils.install_info.sys.frozen", False, create=True):
        with patch(
            "utils.install_info.sys.executable",
            r"C:\Python312\python.exe",
            create=True,
        ):
            assert install_dir() is None


def test_install_dir_packaged():
    with patch("utils.install_info.sys.frozen", True, create=True):
        with patch(
            "utils.install_info.sys.executable",
            r"C:\Program Files\Unscreen\unscreen.exe",
            create=True,
        ):
            assert install_dir() == Path(r"C:\Program Files\Unscreen")
