import io
import urllib.request
import warnings
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Turn DeprecationWarning into errors so any deprecation (esp. from flet)
# in any test is immediately caught, not just in the compat gate.
warnings.filterwarnings("error", category=DeprecationWarning)

from utils.bus import TickBus  # noqa: E402
from utils.models import Tick, WatcherConfig  # noqa: E402


class _FakeHTTPResponse(io.BytesIO):
    """Stand-in for ``http.client.HTTPResponse`` with the attributes the app reads."""

    def __init__(
        self,
        data: bytes = b"{}",
        status: int = 200,
        headers: dict | None = None,
        url: str = "https://example.invalid/",
    ):
        super().__init__(data)
        self.status = status
        self.code = status
        self.headers = headers or {}
        self.url = url


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Never hit the real network: urllib is the app's only HTTP surface."""

    def fake_urlopen(request, timeout=None):
        return _FakeHTTPResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect all app disk I/O (data dir, logs, exports) into a per-test tmp dir."""
    monkeypatch.setenv("UNSCREEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("utils.paths.get_export_dir", lambda: str(tmp_path / "exports"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_winreg():
    """Never write the real Windows registry (auto-start enable/disable)."""
    with patch("core.auto_start.winreg") as mock:
        mock.HKEY_CURRENT_USER = "HKCU"
        mock.KEY_SET_VALUE = 0x0002
        mock.KEY_QUERY_VALUE = 0x0001
        mock.REG_SZ = 1
        yield mock


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """Run inside an empty tmp dir so relative writes never touch the repo."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(scope="session", autouse=True)
def patch_device_id():
    with patch("core.device_identity.get_device_id") as mock:
        mock.return_value = "00000000-0000-0000-0000-000000000001"
        yield


@pytest.fixture(autouse=True)
def reset_app_state():
    """Isolate the app state singleton between tests."""
    from core.state.app_state import reset_app_state

    reset_app_state()
    yield
    reset_app_state()


@pytest.fixture
def in_memory_db():
    from core.storage import Storage

    storage = Storage(db_path=":memory:")
    yield storage
    storage.close()


@pytest.fixture
def mock_tick_bus():
    return MagicMock(spec=TickBus)


@pytest.fixture
def make_tick():
    _counter = 0

    def _make_tick(
        watcher: str = "foreground",
        data: dict | None = None,
        timestamp: datetime | None = None,
    ) -> Tick:
        nonlocal _counter
        _counter += 1
        return Tick(
            watcher=watcher,
            timestamp=timestamp or datetime(2026, 7, 19, tzinfo=timezone.utc),
            data=data or {},
        )

    return _make_tick


class _MockWatcher:
    def __init__(self, config: WatcherConfig, tick_result: Tick | None):
        self.config = config
        self._tick_result = tick_result

    async def tick(self) -> Tick | None:
        return self._tick_result


@pytest.fixture
def mock_watcher():
    def _make(tick_result: Tick | None = None, **config_kwargs) -> _MockWatcher:
        return _MockWatcher(
            config=WatcherConfig(**config_kwargs),
            tick_result=tick_result,
        )

    return _make
