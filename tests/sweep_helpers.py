"""Shared machinery for the self-discovering sweep tests.

Nothing in this module names a specific function, class or module of the app.
Everything is derived from the code itself: module discovery via the filesystem,
arguments via introspection of annotations and defaults, flet objects via
``unittest.mock``.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import importlib.util
import inspect
import types
import typing
import unittest.mock
from pathlib import Path
from typing import Any, get_args, get_origin

import flet as ft

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

# Non-app code that must never be imported or executed.
EXCLUDED_NAMES = {"__pycache__", "trial and error", "UIold"}

# Built-in exception families a synthetic/garbage argument may legitimately
# trigger (input validation, missing files, missing keys). Everything else
# raised during a sweep call is treated as a genuine defect.
ACCEPTED_CALL_EXCEPTIONS = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    FileNotFoundError,
    OSError,
)

# Generic rules for the import sweep: a failed import is only an expected
# platform gap when its traceback passes through one of these markers.
PLATFORM_MARKERS = ("ctypes.windll", "pywinauto", "jnius")


def discover_module_names() -> list[str]:
    """Every importable module under ``src/``, excluding non-app code."""
    names: list[str] = []
    for py in sorted(SRC_DIR.rglob("*.py")):
        parts = py.relative_to(SRC_DIR).parts
        if any(p in EXCLUDED_NAMES for p in parts):
            continue
        if parts[-1] == "__init__.py":
            name = ".".join(parts[:-1])
            if name:
                names.append(name)
        else:
            names.append(".".join(parts[:-1] + (py.stem,)))
    return names


def mock_page() -> unittest.mock.MagicMock:
    """A headless ``ft.Page`` stand-in that satisfies every read/write the app makes."""
    page = unittest.mock.MagicMock(spec=ft.Page)
    page.platform = unittest.mock.MagicMock()
    page.platform.is_desktop.return_value = False
    page.platform.is_mobile.return_value = False
    page.window = unittest.mock.MagicMock()
    page.width = None
    page.height = None
    page.window.width = None
    page.window.height = None
    page.navigation_bar = None
    page.media = None
    page.views = []
    page.controls = []
    page.overlay = []
    page.title = ""
    page.theme_mode = None
    page.on_resize = None
    page.on_route_change = None
    page.route = "/dashboard"
    page.update = unittest.mock.MagicMock()
    page.add = unittest.mock.MagicMock()
    page.run_task = unittest.mock.MagicMock()
    page.show_dialog = unittest.mock.MagicMock()
    return page


def _flet_mock_for(annotation: type) -> Any:
    if annotation is ft.Page:
        return mock_page()
    if issubclass(annotation, ft.Event):
        return unittest.mock.MagicMock(spec=ft.Event)
    if issubclass(annotation, ft.Control):
        return unittest.mock.MagicMock(spec=ft.Control)
    return None


_str_counter = 0


def reset_strings() -> None:
    """Give every generated string a unique value per sweep run."""
    global _str_counter
    _str_counter = 0


def _unique_string() -> str:
    global _str_counter
    _str_counter += 1
    return f"sample_{_str_counter}"


def _sample_from_string(text: str) -> Any:
    """Parse a stringified annotation (PEP 563) into a best-effort sample."""
    text = text.strip()
    containers = {
        "list[": [],
        "List[": [],
        "dict[": {},
        "Dict[": {},
        "set[": set(),
        "Set[": set(),
        "tuple[": (),
        "Tuple[": (),
    }
    for prefix, sample in containers.items():
        if text.startswith(prefix):
            return sample
    if text.startswith("Optional[") and text.endswith("]"):
        return sample_value(text[len("Optional[") : -1])
    if " | " in text:
        parts = [p.strip() for p in text.split("|")]
        if "None" in parts:
            others = [p for p in parts if p != "None"]
            return sample_value(others[0]) if others else None
    if text.startswith("Callable") or text == "callable":
        return unittest.mock.MagicMock()
    if text.startswith(("ft.", "flet.")):
        return unittest.mock.MagicMock()
    if text == "bool":
        return True
    if text == "int":
        return 1
    if text == "float":
        return 1.0
    if text in ("bytes", "bytearray"):
        return b"sample"
    if text in ("str", "string"):
        return _unique_string()
    if text == "Path":
        return Path("sample_file")
    if text == "Any":
        return None
    if text == "None":
        return None
    return unittest.mock.MagicMock()


def _construct_class(cls: type) -> Any:
    """Best-effort real instance: no-arg first, then signature-derived kwargs."""
    try:
        return cls()
    except TypeError:
        try:
            kwargs = {
                p.name: sample_value(p.annotation)
                for p in inspect.signature(cls).parameters.values()
                if p.kind
                not in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
                and p.name not in ("self", "cls")
            }
            return cls(**kwargs)
        except (TypeError, ValueError):
            return None
    except Exception:
        return None


def sample_value(annotation: Any) -> Any:
    """One deterministic sample value for an annotation, recursively derived."""
    if annotation is None or annotation is inspect.Parameter.empty:
        return unittest.mock.MagicMock()
    if isinstance(annotation, str):
        return _sample_from_string(annotation)

    origin = get_origin(annotation)
    if origin is not None:
        if origin is typing.Union or origin is types.UnionType:
            args = [a for a in get_args(annotation) if a is not type(None)]
            return sample_value(args[0]) if args else None
        if origin is dict:
            return {}
        if origin is list:
            return []
        if origin is set:
            return set()
        if origin is tuple:
            return ()
        if isinstance(origin, type):
            return sample_value(origin)
        return None

    if isinstance(annotation, type):
        flet_mock = _flet_mock_for(annotation)
        if flet_mock is not None:
            return flet_mock
        if issubclass(annotation, enum.Enum):
            members = list(annotation)
            return members[0] if members else None
        if dataclasses.is_dataclass(annotation):
            return construct_dataclass(annotation)
        if issubclass(annotation, Path):
            return Path("sample_file")
        if issubclass(annotation, bool):
            return True
        if issubclass(annotation, int):
            return 1
        if issubclass(annotation, float):
            return 1.0
        if issubclass(annotation, str):
            return _unique_string()
        if issubclass(annotation, bytes):
            return b"sample"
        if issubclass(annotation, dict):
            return {}
        if issubclass(annotation, list):
            return []
        if annotation is Any:
            return None
        return _construct_class(annotation)
    origin_attr = getattr(annotation, "__origin__", None)
    if isinstance(origin_attr, type):
        return sample_value(origin_attr)
    return None


def construct_dataclass(cls: type) -> Any:
    """Construct a dataclass instance from recursive sample values."""
    try:
        kwargs = {f.name: sample_value(f.type) for f in dataclasses.fields(cls)}
        return cls(**kwargs)
    except TypeError:
        try:
            return cls()
        except Exception:
            return None


def build_call_kwargs(func) -> dict[str, Any]:
    """Map every parameter of *func* to a sample value (required ones included)."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return {}
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}
    kwargs: dict[str, Any] = {}
    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name == "self" or param.name == "cls":
            continue
        annotation = hints.get(param.name, param.annotation)
        kwargs[param.name] = sample_value(annotation)
    return kwargs


def expected_exception(func, exc: BaseException) -> bool:
    """True when *exc* is an acceptable outcome of sweeping *func* with samples."""
    if isinstance(exc, ACCEPTED_CALL_EXCEPTIONS):
        return True
    module = inspect.getmodule(func)
    if module is not None:
        for _name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseException)
                and isinstance(exc, obj)
            ):
                return True
    return False


def public_callables(module) -> list[tuple[str, Any]]:
    """Functions and classes defined in *module* itself (public names only)."""
    found: list[tuple[str, Any]] = []
    module_name = getattr(module, "__name__", "")
    for name, obj in vars(module).items():
        if name.startswith("_"):
            continue
        if name == "main":
            continue  # entry-point convention: launches the app / side effects
        is_local_callable = (inspect.isfunction(obj) or inspect.isclass(obj)) and (
            obj.__module__ == module_name
        )
        if is_local_callable:
            found.append((name, obj))
    return found


def public_methods(cls: type) -> list[tuple[str, Any]]:
    """Public methods declared on *cls* (inherited ones stay in their own module)."""
    found: list[tuple[str, Any]] = []
    for name, member in vars(cls).items():
        if name.startswith("_"):
            continue
        if callable(member):
            found.append((name, member))
    return found


def import_fresh(module_name: str) -> types.ModuleType:
    """Import *module_name* as a brand-new module object (real code, no cache)."""
    module = importlib.import_module(module_name)
    spec = importlib.util.spec_from_file_location(
        f"_fresh_{module_name}", module.__file__
    )
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh
