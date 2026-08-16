import json
import logging
import os
import uuid
from functools import lru_cache

from utils.paths import get_data_dir
from utils.win32 import get_winreg

logger = logging.getLogger(__name__)


def _machine_guid() -> str | None:
    winreg = get_winreg()
    if winreg is None:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        )
        guid = winreg.QueryValueEx(key, "MachineGuid")[0]
        winreg.CloseKey(key)
        return guid.strip()
    except Exception:
        logger.debug("MachineGuid unavailable, falling back to device file")
        return None


def _file_device_id() -> str:
    config_dir = get_data_dir()
    device_file = os.path.join(config_dir, "device.json")
    os.makedirs(config_dir, exist_ok=True)

    if os.path.isfile(device_file):
        try:
            with open(device_file) as f:
                data = json.load(f)
            return data["device_id"]
        except Exception:
            logger.warning("device.json unreadable, generating a new device id")

    new_id = str(uuid.uuid4())
    try:
        with open(device_file, "w") as f:
            json.dump(
                {
                    "device_id": new_id,
                    "hostname": os.environ.get("COMPUTERNAME", ""),
                },
                f,
            )
    except Exception:
        logger.warning("Failed to persist device.json; device id is volatile")


@lru_cache(maxsize=1)
def get_device_id() -> str:
    guid = _machine_guid()
    if guid:
        return guid
    return _file_device_id()
