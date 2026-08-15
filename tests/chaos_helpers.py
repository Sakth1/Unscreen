"""Shared machinery for the mindless-user chaos suite.

Nothing in this module names a specific function, class or module of the app.
Everything is derived from the code itself at runtime: the live control tree,
the app source (AST scan), adversarial values, and the seeded action log the
driver produces — so a control added in any future release is discovered and
attacked without touching this suite.

Two kinds of findings are distinguished:

- **hard** — the event had the shape a real flet dispatch always produces
  (a real control attached), so an exception is a user-visible defect;
- **garbage-input** — the event was synthetic nonsense no real client can
  send; exceptions here are robustness findings, logged but not fatal.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import logging
import os
import random
import re
import tempfile
import time
import unittest.mock
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import flet as ft
import pytest

logger = logging.getLogger(__name__)

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

EXCLUDED_DIRS = {"__pycache__", "trial and error", "UIold"}

_ERROR_MARKERS = re.compile(r"\b(ERROR|CRITICAL|Traceback)\b")

#: Findings policy: the committed allowlist of known defects (see baseline.json).
_BASELINE_PATH = Path(__file__).resolve().parent / "chaos" / "baseline.json"
_POLICY_ENV = "UNSCREEN_CHAOS_POLICY"


def resolve_policy() -> str:
    """'baseline' (default: fail only on findings not pinned in baseline.json)
    or 'strict' (fail on anything unexpected)."""
    policy = os.environ.get(_POLICY_ENV, "baseline").strip().lower()
    if policy not in ("baseline", "strict"):
        raise ValueError(
            f"UNSCREEN_CHAOS_POLICY must be 'baseline' or 'strict', got {policy!r}"
        )
    return policy


def finding_key(label: str, exc: BaseException | type[BaseException]) -> str:
    """Stable, payload-independent key: ``module.qualname:ExceptionType``.

    Random fuzz draws collapse onto one key per (callable, exception) pair, so
    nondeterministic examples don't churn the baseline.
    """
    exc_type = exc if isinstance(exc, type) else type(exc)
    return f"{label}:{exc_type.__name__}"


class Baseline:
    """Committed allowlist of known findings.

    Unknown findings still fail the run; a pinned key that stops reproducing
    is reported as "no longer reproducible" (informational — a fix landed).
    """

    def __init__(self, path: Path = _BASELINE_PATH) -> None:
        self.path = path
        self.keys = self._load()

    def _load(self) -> set[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        return set(data.get("known", []))

    def known(self, key: str) -> bool:
        return key in self.keys

    def missing(self, observed: set[str]) -> list[str]:
        """Pinned keys never observed this run — the bug may have been fixed."""
        return sorted(self.keys - observed)


def report_dir() -> Path:
    return Path(
        os.environ.get(
            "UNSCREEN_CHAOS_REPORT", Path(tempfile.gettempdir()) / "unscreen_chaos"
        )
    )


def write_chaos_report(
    fragments: list[dict], baseline_keys: set[str]
) -> tuple[Path, Path]:
    """Aggregate per-run fragments into a JSON + human-readable report.

    Written even when the suite passes, so baseline/log-only modes still
    surface what was found. Returns ``(json_path, txt_path)``.
    """
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    txt_path = directory / "report.txt"

    all_findings = [f for run in fragments for f in run.get("findings", [])]
    observed = {f["key"] for f in all_findings if f["key"]}
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "policy": resolve_policy(),
        "baseline_file": str(_BASELINE_PATH),
        "known_keys": len(baseline_keys),
        "runs": fragments,
        "summary": {
            "findings": len(all_findings),
            "known": sum(1 for f in all_findings if f.get("known")),
            "new": sum(1 for f in all_findings if not f.get("known")),
            "baseline_no_longer_reproducible": sorted(baseline_keys - observed),
        },
    }
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"unscreen chaos report — {report['timestamp']}",
        f"policy: {report['policy']}",
        f"known findings pinned in baseline: {len(baseline_keys)}",
        "",
    ]
    for run in fragments:
        findings = run.get("findings", [])
        if not findings:
            continue
        lines.append(
            f"== {run['test']} (seed={run.get('seed')}, steps={run.get('steps')})"
        )
        for f in findings:
            tag = "KNOWN " if f.get("known") else "NEW   "
            lines.append(f"{tag}{f['key']}  {f.get('text', '')}")
        lines.append("")
    stale = report["summary"]["baseline_no_longer_reproducible"]
    if stale:
        lines.append("baseline entries no longer reproduced (bug fixed?):")
        lines += [f"  - {k}" for k in stale]
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Chaos report written: %s", json_path)
    return json_path, txt_path


#: Control attributes that may hold event-handler callables.
HANDLER_ATTRS = (
    "on_click",
    "on_change",
    "on_select",
    "on_submit",
    "on_blur",
    "on_focus",
    "on_hover",
    "on_long_press",
    "on_tap",
    "on_tap_down",
    "on_tap_up",
    "on_keyboard_event",
    "on_secondary_tap",
    "on_dismiss",
    "on_open",
    "on_close",
    "on_action",
)


def default_step_budget() -> int:
    """Chaos action budget, overridable via ``UNSCREEN_CHAOS_STEPS``."""
    return int(os.environ.get("UNSCREEN_CHAOS_STEPS", "200"))


def resolve_seed(seed: int | None = None) -> int:
    """A deterministic seed (env ``UNSCREEN_CHAOS_SEED`` wins over *seed*)."""
    if seed is not None:
        return seed
    env = os.environ.get("UNSCREEN_CHAOS_SEED")
    if env:
        return int(env)
    return random.SystemRandom().randint(0, 2**32 - 1)


def hostile_dimensions(rng: random.Random) -> list[tuple[float, float]]:
    """Window sizes a mindless user could resize to, including hostile ones."""
    fixed: list[tuple[float, float]] = [
        (0, 0),
        (-1, -1),
        (1, 1),
        (399, 799),
        (400, 800),
        (800, 1280),
        (960, 800),
        (1280, 800),
        (1281, 801),
        (10000, 10000),
        (10, 10000),
        (10000, 10),
        (0, 800),
        (1280, 0),
    ]
    return fixed + [(rng.randint(-50, 3000), rng.randint(-50, 3000)) for _ in range(4)]


def hostile_strings(rng: random.Random) -> list[str]:
    """Text a mindless user could type into any field."""
    pool = [
        "",
        " ",
        "\n",
        "\t",
        "\x00",
        "\ud800",
        "\uffff",
        "\u200b",
        "a" * 10000,
        "\u00e9" * 500,
        "\U0001f4a5" * 50,
        "nan",
        "inf",
        "-1",
        "-0.0001",
        "1e999",
        "0x1F",
        "None",
        "null",
        "true",
        "[]",
        "{}",
        "SELECT * FROM raw_events;",
        "../..",
        "%00%0d%0a",
        "<script>alert(1)</script>",
        "\\\\",
        '"',
        "'",
        "a" * 0,
        "3.5.2.1",
        "0",
        "99999999999999999999",
        "\u202eRTL override",
    ]
    return pool + [rng.choice(pool) + str(rng.randint(0, 10**9)) for _ in range(4)]


def adversarial_events() -> list[tuple[str, Any, bool]]:
    """``(description, event, is_garbage)`` — synthetic events a real client
    can never produce. Real dispatches always carry a control."""
    return [
        ("None event", None, True),
        ("object() event", object(), True),
        ("MagicMock event", unittest.mock.MagicMock(), True),
        ("bare Event", ft.Event(name="garbage", control=None, data=None), True),
        ("Event data=[]", ft.Event(name="garbage", control=None, data=[]), True),
        ("Namespace control=None", SimpleNamespace(control=None, data="1"), True),
        ("Namespace data=[]", SimpleNamespace(control=object(), data=[]), True),
        ("Namespace garbage data", SimpleNamespace(control=object(), data=42), True),
    ]


def is_reasonable_event(event: Any) -> bool:
    """A real user action always carries a real control (flet dispatches it)."""
    return event is not None and isinstance(getattr(event, "control", None), ft.Control)


def walk_controls(*roots: Any) -> list[ft.Control]:
    """Every ``ft.Control`` reachable from *roots*.

    Follows all public attributes, iterable slots and dataclass fields, so a
    future control that stores children in a new attribute is still found.
    Cycles are cut by identity.
    """
    seen: set[int] = set()
    found: list[ft.Control] = []

    def visit(obj: Any) -> None:
        if isinstance(obj, ft.Control):
            if id(obj) in seen:
                return
            seen.add(id(obj))
            found.append(obj)
            for name in dir(obj):
                if name.startswith("_"):
                    continue
                try:
                    child = getattr(obj, name)
                except Exception:
                    continue
                visit(child)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for item in obj:
                visit(item)
        elif isinstance(obj, dict):
            for value in obj.values():
                visit(value)
        elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            for f in dataclasses.fields(obj):
                visit(getattr(obj, f.name))

    for root in roots:
        visit(root)
    return found


def interactive_handlers(control: ft.Control) -> dict[str, Callable]:
    """Handler-name → bound callable for every event a control exposes."""
    handlers: dict[str, Callable] = {}
    for name in HANDLER_ATTRS:
        try:
            handler = getattr(control, name)
        except Exception:
            continue
        if callable(handler):
            handlers[name] = handler
    return handlers


@dataclass
class ChaosFailure:
    action: str
    error: str
    garbage: bool = False
    key: str = ""


@dataclass
class ChaosRun:
    """The seeded driver state: action history + collected findings."""

    seed: int = field(default_factory=resolve_seed)
    steps: int = field(default_factory=default_step_budget)
    actions: list[str] = field(default_factory=list)
    failures: list[ChaosFailure] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def log(self, action: str) -> None:
        self.actions.append(action)

    def record(
        self,
        action: str,
        exc: BaseException | None = None,
        message: str | None = None,
        garbage: bool = False,
    ) -> None:
        error = message or (
            f"{type(exc).__name__}: {exc}" if exc is not None else "unknown"
        )
        key = finding_key(action, exc) if exc is not None else ""
        self.failures.append(
            ChaosFailure(action=action, error=error, garbage=garbage, key=key)
        )
        logger.error("CHAOS [seed=%s] %s -> %s", self.seed, action, error)

    def _describe(self) -> list[str]:
        lines = [f"seed={self.seed} steps={self.steps}"]
        lines += [
            f"HARD: {f.action} -> {f.error}" for f in self.failures if not f.garbage
        ]
        lines += [
            f"garbage-input: {f.action} -> {f.error}"
            for f in self.failures
            if f.garbage
        ]
        return lines

    def _report_fragment(self, test_name: str) -> dict:
        return {
            "test": test_name,
            "seed": self.seed,
            "steps": self.steps,
            "findings": [
                {"key": f.key, "known": False, "text": f"{f.action} -> {f.error}"}
                for f in self.failures
            ],
        }

    def fail_if_any(
        self,
        report: list | None = None,
        policy: str | None = None,
        test_name: str = "",
    ) -> None:
        """Collect-all policy: report everything found, fail at the end.

        ``policy`` defaults to :func:`resolve_policy`: under ``baseline`` only
        findings not pinned in ``baseline.json`` fail the run; under
        ``strict`` any hard finding fails. Pass ``report`` (a session list) to
        accumulate a fragment for the aggregated chaos report.
        """
        policy = policy or resolve_policy()
        if not self.failures:
            return
        if report is not None:
            report.append(self._report_fragment(test_name))
        lines = self._describe()
        hard = [f for f in self.failures if not f.garbage]
        if not hard:
            logger.warning("Chaos garbage-input findings only:\n%s", "\n".join(lines))
            return
        if policy == "baseline":
            baseline = Baseline()
            new = [f for f in hard if f.key and not baseline.known(f.key)]
            missing = baseline.missing({f.key for f in hard if f.key})
            if missing:
                logger.warning(
                    "Chaos baseline entries no longer reproduced (bug fixed?): %s",
                    missing,
                )
            if not new:
                logger.warning(
                    "Chaos findings match the pinned baseline:\n%s", "\n".join(lines)
                )
                return
            lines = [f"seed={self.seed} steps={self.steps}"]
            lines += [f"NEW: {f.action} -> {f.error}" for f in new]
            pytest.fail("Mindless user found NEW problems:\n" + "\n".join(lines))
        pytest.fail("Mindless user found problems:\n" + "\n".join(lines))


class TaskExceptionCollector:
    """Captures every ``Task exception was never retrieved`` style event."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self, loop, context: dict) -> None:
        exc = context.get("exception")
        if exc is not None:
            self.events.append(f"{type(exc).__name__}: {exc}")
        else:
            self.events.append(str(context))


class LogTripwire:
    """Tails every ``*.log`` under a directory and reports new error lines.

    Used by the e2e driver: the built app runs out of process, so its own log
    file (``{data_dir}/logs/app.log``) is the only in-app exception signal.
    """

    def __init__(self, root_dir: Path | str) -> None:
        self._offsets: dict[Path, int] = {}
        for path in Path(root_dir).rglob("*.log"):
            try:
                self._offsets[path] = path.stat().st_size
            except OSError:
                continue

    def new_errors(self) -> list[str]:
        hits: list[str] = []
        for path in list(self._offsets):
            if not path.exists():
                continue
            try:
                size = path.stat().st_size
                if size < self._offsets[path]:  # rotated: re-read everything
                    self._offsets[path] = 0
                with open(path, encoding="utf-8", errors="replace") as fh:
                    fh.seek(self._offsets[path])
                    chunk = fh.read()
                    self._offsets[path] = fh.tell()
            except OSError:
                continue
            for line in chunk.splitlines():
                if _ERROR_MARKERS.search(line) and len(line) < 500:
                    hits.append(f"{path.name}: {line}")
        return hits


@dataclass
class ControlInventory:
    """Source-derived attack corpus: every control the app can render.

    Built with an AST scan so a control added in any future release is picked
    up automatically. ``text_fields`` holds the keys of ``ft.TextField``
    controls (targets for text-entry chaos).
    """

    keys: set[str] = field(default_factory=set)
    texts: set[str] = field(default_factory=set)
    icons: set[str] = field(default_factory=set)
    tooltips: set[str] = field(default_factory=set)
    text_fields: set[str] = field(default_factory=set)

    @classmethod
    def from_source(cls, src_dir: Path = SRC_DIR) -> "ControlInventory":
        inv = cls()
        for py in sorted(src_dir.rglob("*.py")):
            if any(part in EXCLUDED_DIRS for part in py.relative_to(src_dir).parts):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "ft"
                ):
                    continue
                control_type = func.attr
                key: str | None = None
                for kw in node.keywords:
                    if not isinstance(kw.value, ast.Constant) or not isinstance(
                        kw.value.value, str
                    ):
                        continue
                    value = kw.value.value
                    if kw.arg == "key":
                        key = value
                        inv.keys.add(value)
                    elif kw.arg == "tooltip":
                        inv.tooltips.add(value)
                    elif kw.arg in ("text", "label", "title", "hint_text") and value:
                        inv.texts.add(value)
                if control_type == "TextField" and key is not None:
                    inv.text_fields.add(key)
            for node in ast.walk(tree):
                # ft.Icons.X / Icons.X references, anywhere (args, kwargs, ...)
                if isinstance(node, ast.Attribute) and (
                    (
                        isinstance(node.value, ast.Attribute)
                        and isinstance(node.value.value, ast.Name)
                        and node.value.value.id == "ft"
                        and node.value.attr == "Icons"
                    )
                    or (isinstance(node.value, ast.Name) and node.value.id == "Icons")
                ):
                    inv.icons.add(node.attr)
        return inv
