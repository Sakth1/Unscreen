import platform

import pytest

from utils import paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("UNSCREEN_DATA_DIR", raising=False)
    monkeypatch.delenv("FLET_APP_STORAGE_DATA", raising=False)
    monkeypatch.setenv("APPDATA", r"C:\Users\dev\AppData\Roaming")
    monkeypatch.setattr(platform, "system", lambda: "Windows")


def test_override_wins(monkeypatch):
    monkeypatch.setenv("UNSCREEN_DATA_DIR", r"C:\override\data")
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", r"C:\proj\.flet\storage\data")
    assert paths.get_data_dir() == r"C:\override\data"


@pytest.mark.parametrize(
    "flet_path",
    [
        r"C:\proj\.flet\storage\data",
        "/proj/.flet/storage/data",
        r"C:\some\deep\proj\.flet\storage\data",
    ],
)
def test_flet_dev_storage_honored(monkeypatch, flet_path):
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", flet_path)
    assert paths.get_data_dir() == flet_path


@pytest.mark.parametrize(
    "flet_path",
    [
        r"C:\Users\x\AppData\Roaming\unscreen\data",
        r"C:\proj\.flet\storage",
        "/proj/.flet/other/data",
        "",
    ],
)
def test_packaged_or_unrelated_storage_ignored(monkeypatch, flet_path):
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", flet_path)
    assert paths.get_data_dir() == r"C:\Users\dev\AppData\Roaming\Unscreen"


def test_default_windows_path():
    assert paths.get_data_dir() == r"C:\Users\dev\AppData\Roaming\Unscreen"
