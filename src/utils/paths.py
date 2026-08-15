import os
import platform
from pathlib import Path

from utils.platform import is_android

_DATA_DIR_NAME = "Unscreen"
_ANDROID_PACKAGE = "com.mycompany.unscreen"


def get_data_dir() -> str:
    # App-specific override for development/testing.
    override = os.environ.get("UNSCREEN_DATA_DIR")
    if override:
        return override

    # Under `flet run` (CLI dev mode) the flet CLI points FLET_APP_STORAGE_DATA
    # at a project-local, git-ignored `.flet/storage/data` dir; honor it there
    # so dev runs never touch %APPDATA%. Packaged builds set the same variable
    # to the OS app-support dir; that path is deliberately NOT honored so
    # installed builds always store user data in the canonical location
    # (%APPDATA%\Unscreen on Windows, app internal storage on Android) —
    # see docs/architecture/db-query-guide.md §1 and docs/ci-cd.md.
    flet_data = os.environ.get("FLET_APP_STORAGE_DATA")
    if flet_data and Path(flet_data).parts[-3:] == (".flet", "storage", "data"):
        return flet_data

    if is_android():
        home = os.environ.get("HOME")
        if home:
            return os.path.join(home, _DATA_DIR_NAME)
        return os.path.join(f"/data/data/{_ANDROID_PACKAGE}/files", _DATA_DIR_NAME)
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA")
        if base:
            return os.path.join(base, _DATA_DIR_NAME)
    base = os.path.expanduser("~")
    return os.path.join(base, _DATA_DIR_NAME)


def get_export_dir() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    path = os.path.join(docs, _DATA_DIR_NAME, "exports")
    os.makedirs(path, exist_ok=True)
    return path
