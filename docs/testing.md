# Testing

The suite is a self-maintaining tripwire: four generic, self-discovering
sweeps that derive everything they exercise from the code itself, plus a
small set of targeted tests that encode specific behavioral contracts the
sweeps cannot express (schema parity, version ordering, migrations,
flet-API compatibility), plus a chaos layer that attacks everything with
hostile, randomized input. The generic sweeps survive refactors — renames,
new modules, signature changes — without touching the test files.

## Configuration (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src", "tests"]
testpaths = ["tests"]
addopts = [
    "--ignore=tests/e2e",
    "--ignore=tests/chaos",
    "--strict-markers",
    "--cov=src",
    "--cov-report=term-missing:skip-covered",
    "--cov-fail-under=60",
]
markers = [
    "e2e: end-to-end UI tests driven by flet.testing (require a Flutter test host; run via the e2e dependency group)",
    "chaos: adversarial mindless-user suite (callable fuzz, headless UI chaos, runtime chaos, e2e chaos; run via the chaos marker)",
]

[tool.coverage.run]
omit = ["src/UIold/*"]
```

- `asyncio_mode = "auto"` — test coroutines are auto-detected as async
- `pythonpath = ["src", "tests"]` — imports work as `from core.xxx import Xxx`
- Coverage gates at 60%; the deprecated `src/UIold/` is excluded from
  coverage (it is also excluded from the sweeps).
- `tests/e2e/` and `tests/chaos/` are excluded from the default run (see
  the Chaos layer and the E2E layer below).

## Shared machinery (`tests/sweep_helpers.py`)

Nothing in this module names a specific function, class or module of the
app. It provides:

- `discover_module_names()` — every importable module under `src/`
  (excludes `__pycache__`, `trial and error`, `UIold` by name only).
- `sample_value(annotation)` — deterministic sample values derived from
  annotations: primitives, enums, dataclasses (recursive), `Path`,
  flet `Page`/`Event`/`Control` mocks, PEP 563 string annotations, and
  real instances of other classes where cheap.
- `build_call_kwargs(func)` — signature-derived kwargs using
  `typing.get_type_hints` with graceful fallback to raw annotations.
- `mock_page()` — `MagicMock(spec=ft.Page)` with `window.width/height`
  defaulting to `None` (exercises the app's default-size branch).
- `expected_exception()` — the built-in exception families a synthetic
  argument may legitimately trigger (validation, missing keys/files),
  plus exception classes defined by the app module itself.
- `public_callables()` / `public_methods()` — introspection helpers.
- `PLATFORM_MARKERS` — `("ctypes.windll", "pywinauto", "jnius")`; the only
  generic platform-gap classification used by the import sweep.

## The four sweeps

### 1. Import sweep (`test_import_sweep.py`)

Imports and reloads every discovered module in a fresh subprocess. Fails on
any exception or warning. A failed import is only tolerated when its
traceback passes through a `PLATFORM_MARKERS` guard (missing Windows/Android
API on the current host).

### 2. Callable sweep (`test_callable_sweep.py`)

Invokes every public function, class constructor and method with
signature-derived samples, including None/empty boundary variants where the
signature admits them. Async callables run under a 2s deadline. Fails on
any unexpected exception or warning, logging the exact call that failed.

### 3. Lifecycle sweep (`test_lifecycle_sweep.py`)

Real wiring end to end, headless:

- `_EventBridge` mapping/dedup/fan-out against a real in-memory `Storage`.
- `CollectionManager` start → ticks flow through a real `TickBus` +
  `Scheduler` into real `Storage` → pause → resume → stop, with a fake
  platform runtime; auto-pause when `collection_enabled` is False.
- Headless `App(mock_page())` boot across MOBILE/TABLET/DESKTOP, route
  navigation, navigation-bar/rail selection, settings trailing click,
  unknown-route fallback, resize-driven form-factor switches, dialogs.

### 4. Robustness sweep (`test_robustness_sweep.py`)

Hostile conditions must degrade, never crash: garbage/corrupt database
files (quarantined and rebuilt), WAL-journal leftovers, concurrent
`Storage` instances on one file, a failing `TickBus` subscriber, Android
boot with missing usage-access permission (dialog shown), and the
auto-start registry wiring in `App._initiate`.

## Targeted contract tests (kept deliberately)

- `test_storage.py` — migrations v1→v6, integrity checks, auto-vacuum.
- `test_scheduler.py` — circuit breaker escalation/self-heal (polling,
  not fixed sleeps, so they are not flaky under CI load).
- `test_collection_manager.py` — pause/resume config round-trips, screen
  monitor, health monitor.
- `test_parity.py` — Windows vs Android watcher schema contracts.
- `test_utils.py` — version ordering, URL normalization/PSL, time utils,
  file helpers.
- `test_update_checker.py` — release selection, download verification,
  apply flows (version-ordering tests live in `test_utils.py`).
- `test_config.py`, `test_foreground.py`, `test_app_usage.py`,
  `test_url_processor.py`, `test_export_service.py`, `test_auto_start.py`,
  `test_navigation_drawer.py`, `test_flet_api_compat.py` (+ helpers),
  `test_error_hardening.py`, `test_smoke.py` (fixture sanity).

## Fixtures (`tests/conftest.py`)

Autouse: `no_network` (fake `urlopen`), `tmp_data_dir`
(`FLET_APP_STORAGE_DATA` + `get_export_dir` patch), `no_winreg`,
`chdir_tmp`, session `patch_device_id` (`get_device_id` →
`00000000-...-0001`).

Function-scoped: `in_memory_db` (`Storage(db_path=":memory:")`),
`mock_tick_bus`, `make_tick` (deterministic `Tick`s), `mock_watcher`
(`Watcher` protocol double).

## Chaos layer (`tests/chaos/`, `-m chaos`)

A mindless user with no context: it attacks every public callable, every
interactive control, the whole runtime pipeline and the real database with
hostile, randomized input — discovering what to attack from the code itself,
so future features are covered without touching the tests. All failures are
collected and reported at the end, each run printing its seed
(`UNSCREEN_CHAOS_SEED`) for replay.

Shared machinery (`tests/chaos_helpers.py`): `ChaosRun` (seeded RNG, step
budget from `UNSCREEN_CHAOS_STEPS`, collect-all `fail_if_any()`),
`walk_controls` (generic `dir()`/dataclass traversal of the live control
tree), `interactive_handlers` (every `on_*`-style callable on a control),
`adversarial_events` (synthetic `ft.Event`s), `hostile_strings` /
`hostile_dimensions` (unicode bombs, NaN/Inf, 0×0 … 4000×3000 windows),
`LogTripwire` (surfaces ERROR/CRITICAL/Traceback lines written by the app),
`ControlInventory.from_source` (AST scan of `src/` for keys, texts, icons,
tooltips and text fields — used by the e2e layer, which has no in-process
access to the app).

### Findings policy (`tests/chaos/baseline.json`)

The chaos suite distinguishes three outcome classes:

- **accepted** — exception families a synthetic argument may legitimately
  trigger (`ValueError`/`TypeError`/`KeyError`/`IndexError`/…, plus
  `sqlite3.Error`); never reported.
- **known** — genuine defects pinned in `baseline.json`, keyed stably as
  `module.qualname:ExceptionType` (payload-independent, so random fuzz
  draws collapse onto one key per callable/exception pair). Known findings
  are logged and land in the report, but do not fail the run.
- **new** — anything else: fails the run so a regression can never hide
  behind the baseline.

`UNSCREEN_CHAOS_POLICY=strict` restores the old fail-on-anything behavior.
A pinned key that stops reproducing is reported as *no longer
reproducible* — the bug was fixed; remove it from the baseline.

Every run writes an aggregated report (`report.json` + `report.txt`) to
`UNSCREEN_CHAOS_REPORT` (default `%TEMP%\unscreen_chaos\`), even when the
suite passes. To allowlist a new finding, copy its key from the report into
`baseline.json`.

Exceptions: `test_corrupt_payloads_survive_every_query` is always strict —
corrupted rows are realistic (partial writes, disk damage), not synthetic
garbage, so any raise there stays a hard failure regardless of policy.

- `test_callable_fuzz.py` — every public callable under Hypothesis-driven
  garbage (one parameterized test per module). Async callables run under a
  deadline; `UNSCREEN_CHAOS_EXAMPLES` (default 15) bounds the budget.
- `test_headless_ui_chaos.py` — a live `App(mock_page())` is operated for
  hundreds of steps: handler fires with reasonable events (hard failures),
  hostile resizes and route churn between actions, stale controls from
  before a resize are re-fired, `select_index` gets out-of-range indices.
- `test_runtime_chaos.py` — real `CollectionManager` + `Scheduler` +
  `Storage` behind watchers that boom, return garbage, go silent or hang;
  random start/stop/pause/resume/restart churn with config corruption
  mid-run; corrupt payloads run through every storage query (any raise is a
  defect); concurrent threads hammering one database; a real-Windows
  watcher soak (skipped elsewhere).
- `test_mindless_user.py` (in `tests/e2e/`) — the same mindless user driven
  through a real client via `flet.testing`, in device mode, tapping keys,
  texts, icons and tooltips discovered from source, typing hostile text,
  fuzzing coordinates and resizing the window, with a log tripwire.

Run the chaos layer (bounded, ~1–2 min):

```bash
uv run pytest tests/chaos -m chaos --no-cov --tb=short
```

It is not part of the default suite, CI, or the coverage gate; it is a
local soak tool and a CI-only smoke job if the budget allows.

## E2E layer (`tests/e2e/`, optional)

`flet.testing.FletTestApp` drives the real app against a real Flutter
client. Requires the Flutter SDK (`FLET_TEST_FLUTTER_EXE`) and the flet
client shell (`FLET_TEST_FLUTTER_APP_DIR`); without them the layer skips
via a generic availability guard. Run it with:

```bash
uv add --group e2e .          # numpy/pillow/scikit-image
uv run pytest tests/e2e -m e2e
```

## Running Tests

```bash
uv run pytest tests/ -v --tb=short          # default suite (coverage gate applies)
uv run pytest tests/ -v --no-cov            # focused runs without the coverage gate
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/ tests/
```

## CI Integration

Tests run on `windows-latest` via `uv sync --frozen` on every push/PR to
`master` or `dev` (see [ci-cd.md](ci-cd.md)). The default suite must stay
well under the 10-minute CI budget; the E2E layer is not part of it.
