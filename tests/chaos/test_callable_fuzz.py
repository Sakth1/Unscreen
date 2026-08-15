"""Callable fuzz: every public callable under hostile, generated inputs.

Extends the callable sweep (one sample per signature) with Hypothesis-driven
garbage: unicode bombs, NaN/Inf, huge integers, wrong-shaped containers,
``None`` in the wrong place. Async callables run under a deadline. Findings
are collected per module and reported at the end — the sweep philosophy
("fail on anything unexpected") applied to thousands of inputs.

Run: ``uv run pytest tests/chaos -m chaos``
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import sqlite3
import types
import typing
import unittest.mock
import warnings

import flet as ft
import pytest
from chaos_helpers import Baseline, finding_key, resolve_policy
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.database import InMemoryExampleDatabase
from sweep_helpers import (
    discover_module_names,
    expected_exception,
    mock_page,
    public_callables,
    public_methods,
    sample_value,
)

logger = logging.getLogger(__name__)

_TIMEOUT_S = 2.0
_MAX_EXAMPLES = int(os.environ.get("UNSCREEN_CHAOS_EXAMPLES", "15"))

pytestmark = pytest.mark.chaos

_GARBAGE = [
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**63), max_value=2**63),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(min_size=0, max_size=2000),
    st.binary(max_size=512),
    st.lists(st.integers(), max_size=8),
    st.dictionaries(st.text(max_size=32), st.integers(), max_size=8),
    st.just(object()),
    st.just([]),
    st.just({}),
    st.just(()),
    st.just(set()),
    st.just(b""),
    st.just(""),
    st.just("a" * 10000),
    st.just("SELECT * FROM raw_events; --"),
    st.just("\ud800\uffff\u200b"),
]

#: Structured findings: (stable key, display text) — see chaos_helpers.finding_key.
_log: list[tuple[str, str]] = []


def _fuzz_expected(target, exc: BaseException) -> bool:
    """Accepted outcome: sweep's input-validation families + sqlite errors."""
    if isinstance(exc, sqlite3.Error):
        return True
    return expected_exception(target, exc)


_PRIMITIVE_TYPES = (str, int, float, bool, bytes, type(None), complex, object)


def _is_typed_domain(annotation) -> bool:
    """A concrete non-primitive class (app types, enums, Path, dataclasses…):
    only valid instances can reach it from real call sites, so garbage is
    pointless there (mirrors how ``ft.Page`` is treated)."""
    return isinstance(annotation, type) and annotation not in _PRIMITIVE_TYPES


def _sample_container(annotation) -> st.SearchStrategy | None:
    """Typed containers of domain objects (e.g. ``list[NavigationDestination]``,
    ``dict[str, Destination]``): build a sample-filled container instead of
    garbage, matching the typed-domain policy."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (list, set, tuple) and args and _is_typed_domain(args[0]):
        try:
            return st.just(origin([sample_value(args[0])]))
        except Exception:
            return None
    if origin is dict and len(args) == 2 and _is_typed_domain(args[1]):
        try:
            return st.just({sample_value(args[0]): sample_value(args[1])})
        except Exception:
            return None
    return None


def _unwrap_optional(annotation):
    """Collapse ``Optional[T]``/``T | None`` to ``T`` when T is a domain type."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        members = [m for m in typing.get_args(annotation) if m is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _param_strategy(annotation) -> st.SearchStrategy:
    unwrapped = _unwrap_optional(annotation)
    if isinstance(unwrapped, type):
        if issubclass(unwrapped, ft.Page):
            return st.just(mock_page())
        if issubclass(unwrapped, ft.Event):
            return st.just(unittest.mock.MagicMock(spec=ft.Event))
    if _is_typed_domain(unwrapped):
        try:
            return st.just(sample_value(unwrapped))
        except Exception:
            return st.one_of(*_GARBAGE)
    container = _sample_container(annotation)
    if container is not None:
        return container
    strategies = list(_GARBAGE)
    try:
        sample = sample_value(annotation)
        strategies.append(st.just(sample))
    except Exception:
        pass
    return st.one_of(*strategies)


def _kwargs_strategy(func) -> st.SearchStrategy:
    hints_target = func.__init__ if inspect.isclass(func) else func
    try:
        signature = inspect.signature(func)
        hints = typing.get_type_hints(hints_target)
    except Exception:
        return st.fixed_dictionaries({})
    mapping: dict[str, st.SearchStrategy] = {}
    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name in ("self", "cls"):
            continue
        mapping[param.name] = _param_strategy(hints.get(param.name, param.annotation))
    return st.fixed_dictionaries(mapping)


@settings(
    max_examples=_MAX_EXAMPLES,
    deadline=_TIMEOUT_S * 1000,
    database=InMemoryExampleDatabase(),
)
def _fuzz_call(target, kwargs: dict) -> None:
    label = f"{target.__module__}.{getattr(target, '__qualname__', target.__name__)}"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = target(**kwargs)
            if inspect.iscoroutine(result):
                asyncio.run(asyncio.wait_for(result, timeout=_TIMEOUT_S))
        except asyncio.TimeoutError:
            _log.append(
                (
                    finding_key(label, TimeoutError),
                    f"{label}({kwargs}) HUNG longer than {_TIMEOUT_S}s",
                )
            )
        except BaseException as exc:
            if not _fuzz_expected(target, exc):
                _log.append(
                    (
                        finding_key(label, exc),
                        f"{label}({kwargs}) raised {type(exc).__name__}: {exc}",
                    )
                )
    for w in caught:
        _log.append(
            (
                f"{label}:warning:{w.category.__name__}",
                f"{label}({kwargs}) emitted warning {w.category.__name__}: {w.message}",
            )
        )


def _fuzz_function(func) -> None:
    given(kwargs=_kwargs_strategy(func))(_fuzz_call)(func)


def _fuzz_class(cls) -> None:
    label = f"{cls.__module__}.{cls.__name__}"

    @settings(
        max_examples=min(_MAX_EXAMPLES, 5),
        deadline=_TIMEOUT_S * 1000,
        suppress_health_check=[HealthCheck.nested_given],
        database=InMemoryExampleDatabase(),
    )
    def _construct(target, kwargs: dict) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                instance = target(**kwargs)
            except BaseException as exc:
                if not _fuzz_expected(target, exc):
                    _log.append(
                        (
                            finding_key(label, exc),
                            f"{label}({kwargs}) raised {type(exc).__name__}: {exc}",
                        )
                    )
                return
        for w in caught:
            _log.append(
                (
                    f"{label}:warning:{w.category.__name__}",
                    f"{label}({kwargs}) emitted warning {w.category.__name__}: {w.message}",
                )
            )
        for name, _member in public_methods(target):
            member = getattr(target, name)
            member_target = (
                member.__func__
                if isinstance(member, (classmethod, staticmethod))
                else getattr(instance, name)
            )
            _fuzz_function(member_target)

    given(kwargs=_kwargs_strategy(cls))(_construct)(cls)


def _sweep_module(module_name: str) -> None:
    module = importlib.import_module(module_name)
    for _name, obj in public_callables(module):
        if inspect.isclass(obj):
            _fuzz_class(obj)
        else:
            _fuzz_function(obj)


@pytest.mark.parametrize("module_name", discover_module_names())
def test_fuzz_every_public_callable(module_name: str, chdir_tmp, chaos_report) -> None:
    _log.clear()
    _sweep_module(module_name)
    findings = [{"key": key, "known": False, "text": text} for key, text in _log]
    chaos_report.append(
        {
            "test": f"test_fuzz_every_public_callable[{module_name}]",
            "steps": _MAX_EXAMPLES,
            "findings": findings,
        }
    )
    if not _log:
        return
    if resolve_policy() == "strict":
        pytest.fail(
            f"Callable fuzz found problems in {module_name}:\n\n"
            + "\n".join(text for _key, text in _log)
        )
    baseline = Baseline()
    for finding in findings:
        finding["known"] = baseline.known(finding["key"])
    new = [f for f in findings if not f["known"]]
    missing = baseline.missing({f["key"] for f in findings})
    if missing:
        logger.warning(
            "Chaos baseline entries no longer reproduced (bug fixed?): %s", missing
        )
    if not new:
        logger.warning(
            "Callable fuzz findings in %s match the pinned baseline:\n%s",
            module_name,
            "\n".join(f["text"] for f in findings),
        )
        return
    pytest.fail(
        f"Callable fuzz found NEW problems in {module_name}:\n\n"
        + "\n".join(f["text"] for f in new)
    )
