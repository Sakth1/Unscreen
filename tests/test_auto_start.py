from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_winreg():
    with patch("core.auto_start.winreg") as mock:
        mock.HKEY_CURRENT_USER = "HKCU"
        mock.HKEY_LOCAL_MACHINE = "HKLM"
        mock.KEY_SET_VALUE = 0x0002
        mock.KEY_QUERY_VALUE = 0x0001
        mock.REG_SZ = 1
        yield mock


def test_enable_writes_run_key(mock_winreg):
    from core.auto_start import enable

    with patch("core.auto_start._get_target_path", return_value="D:\\app.exe"):
        result = enable()

    assert result is True
    mock_winreg.OpenKey.assert_called_once_with(
        "HKCU",
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        0x0002,
    )
    mock_winreg.SetValueEx.assert_called_once_with(
        mock_winreg.OpenKey.return_value,
        "Unscreen",
        0,
        1,
        "D:\\app.exe",
    )
    mock_winreg.CloseKey.assert_called_once_with(mock_winreg.OpenKey.return_value)


def test_enable_returns_false_when_target_none(mock_winreg):
    from core.auto_start import enable

    with patch("core.auto_start._get_target_path", return_value=None):
        result = enable()

    assert result is False
    mock_winreg.OpenKey.assert_not_called()


def test_disable_deletes_value_from_both_hives(mock_winreg):
    from core.auto_start import disable

    result = disable()

    assert result is True
    assert mock_winreg.OpenKey.call_count == 2
    assert mock_winreg.DeleteValue.call_count == 2
    hives = [call.args[0] for call in mock_winreg.OpenKey.call_args_list]
    assert hives == ["HKCU", "HKLM"]


def test_disable_succeeds_when_keys_absent(mock_winreg):
    from core.auto_start import disable

    mock_winreg.OpenKey.side_effect = FileNotFoundError
    mock_winreg.DeleteValue.side_effect = FileNotFoundError

    result = disable()

    assert result is True


def test_disable_returns_false_on_other_error(mock_winreg):
    from core.auto_start import disable

    mock_winreg.OpenKey.side_effect = OSError("access denied")

    result = disable()

    assert result is False


def test_is_enabled_queries_hkcu_first(mock_winreg):
    from core.auto_start import is_enabled

    result = is_enabled()

    assert result is True
    assert mock_winreg.OpenKey.call_count == 1
    assert mock_winreg.OpenKey.call_args.args[0] == "HKCU"
    mock_winreg.QueryValueEx.assert_called_once_with(
        mock_winreg.OpenKey.return_value, "Unscreen"
    )


def test_is_enabled_falls_back_to_hklm(mock_winreg):
    from core.auto_start import is_enabled

    mock_winreg.OpenKey.side_effect = [
        FileNotFoundError,
        mock_winreg.OpenKey.return_value,
    ]

    result = is_enabled()

    assert result is True
    assert mock_winreg.OpenKey.call_count == 2
    assert mock_winreg.OpenKey.call_args.args[0] == "HKLM"


def test_is_enabled_returns_false_when_missing(mock_winreg):
    from core.auto_start import is_enabled

    mock_winreg.QueryValueEx.side_effect = FileNotFoundError

    result = is_enabled()

    assert result is False
    assert mock_winreg.OpenKey.call_count == 2


def test_is_enabled_returns_false_on_error(mock_winreg):
    from core.auto_start import is_enabled

    mock_winreg.QueryValueEx.side_effect = OSError

    result = is_enabled()

    assert result is False


def test_all_functions_noop_when_winreg_none():
    with patch("core.auto_start.winreg", None):
        from core.auto_start import disable, enable, is_enabled

        assert enable() is False
        assert disable() is False
        assert is_enabled() is False


def test_get_target_path_frozen():
    with (
        patch("core.auto_start.sys.frozen", True, create=True),
        patch("core.auto_start.sys.executable", "C:\\Programs\\app.exe"),
    ):
        from core.auto_start import _get_target_path

        result = _get_target_path()
        assert result == "C:\\Programs\\app.exe"


def test_get_target_path_dev_mode(tmp_path):
    main_py = tmp_path / "main.py"
    main_py.write_text("")
    auto_start_dir = tmp_path / "core"
    auto_start_dir.mkdir()
    auto_start_py = auto_start_dir / "auto_start.py"
    auto_start_py.write_text("")

    with (
        patch("core.auto_start.__file__", str(auto_start_py)),
        patch("core.auto_start.sys.executable", "C:\\Python\\python.exe"),
        patch("core.auto_start.Path.exists", return_value=True),
    ):
        from core.auto_start import _get_target_path

        result = _get_target_path()
        assert result is not None
        assert "pythonw.exe" in result
        assert "main.py" in result


def test_get_target_path_dev_mode_falls_back_to_python_exe(tmp_path):
    main_py = tmp_path / "main.py"
    main_py.write_text("")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    auto_start_py = core_dir / "auto_start.py"
    auto_start_py.write_text("")

    python_exe = tmp_path / "python.exe"
    python_exe.write_text("")

    with (
        patch("core.auto_start.__file__", str(auto_start_py)),
        patch("core.auto_start.sys.executable", str(python_exe)),
    ):
        from core.auto_start import _get_target_path

        result = _get_target_path()
        assert result is not None
        assert "python.exe" in result
        assert "main.py" in result
