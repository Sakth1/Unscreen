import os
import platform

from utils.platform import is_android

_DATA_DIR_NAME = "Unscreen"
_ANDROID_PACKAGE = "com.mycompany.unscreen"


def get_data_dir() -> str:
    # App-specific override for development/testing. flet's own
    # FLET_APP_STORAGE_DATA is deliberately NOT honored here so installed
    # builds always store user data in the canonical location
    # (%APPDATA%\Unscreen on Windows, app internal storage on Android) —
    # see docs/architecture/db-query-guide.md §1 and docs/ci-cd.md.
    override = os.environ.get("UNSCREEN_DATA_DIR")
    if override:
        return override

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
