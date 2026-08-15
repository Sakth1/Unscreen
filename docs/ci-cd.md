# CI/CD Pipeline

## Architecture

Single unified workflow (`.github/workflows/ci.yml`) with a shared-environment
job chain:

```
push (master/dev) or PR
        │
  ┌─────▼───────┐
  │  prepare    │  checkout + setup-uv + uv sync --frozen
  │             │  saves .venv to actions/cache (once per lock change)
  └──────┬──────┘
         ├────────────────────┐
  ┌──────▼──────┐      ┌──────▼──────┐
  │    lint     │      │    test     │   both restore the cached
  │             │      │             │   .venv and run in parallel
  │ ruff+pyright │     │   pytest    │
  └──────┬──────┘      └──────┬──────┘
         │                     │
  ┌─────▼──────────┐
  │ detect-version │  only on master push
  │    -bump       │  compares pyproject.toml version
  └─────┬──────────┘
        │ (if version bumped)
  ┌─────▼──────┐
  │   build    │  Windows EXE + Android APK (parallel)
  └─────┬──────┘
        │
  ┌─────▼──────┐
  │  publish   │  GitHub Release + asset upload
  └────────────┘
```

The environment is set up **once** by `prepare`: the resulting `.venv` is
saved to `actions/cache` keyed on `uv.lock` + `pyproject.toml` hashes, and
both `lint` and `test` restore it instead of installing dependencies again.
On a cache miss (first run, lock change) `prepare` still pays the full
install once; subsequent runs restore in seconds. Note the workflow needs
`actions: write` permission for cache saves (both the `.venv` cache and
`setup-uv`'s own dependency cache).

## Fail-Safe Guarantees

| Guarantee | Mechanism |
|-----------|-----------|
| No release if tests fail | `detect-version-bump` requires `test` |
| No binary build if tests fail | `build-*` requires `test` |
| No release without version bump | `detect-version-bump` checks pyproject.toml |
| No partially completed release | `publish-release` requires all builds |
| Tests work in shallow clones | Migration tests embed schema SQL, no `git show` |

## Release Process

1. Bump `version` in `pyproject.toml` on `master`.
2. Push triggers `ci.yml`.
3. `prepare` sets up the environment once; `lint` and `test` run in parallel.
4. `detect-version-bump` detects increase.
5. `build-windows` and `build-android` run in parallel.
6. `publish-release` creates GitHub release and attaches binaries.

## Troubleshooting

### Tests fail locally but pass in CI
- Check `SCHEMA_VERSION` against `_run_migrations()` — stale migration code is the most common cause.
- Run `pytest -v --tb=long tests/test_storage.py -k "TestSchemaMigration"` to isolate.

### Tests fail in CI but pass locally
- Run `uv run python scripts/ci/local_ci.py` — replicates the CI environment exactly (fresh checkout copy + fresh `uv sync --frozen` venv), so environment-only failures (stale `.pyc` caches masking compile-time warnings) surface locally. The pre-push hook runs this automatically.
- CI uses shallow clone (`fetch-depth: 0` in prepare/lint/test jobs ensures full history).
- CI runs on Windows Server 2022, Python 3.12.x (patch may differ).
- Check for environment-dependent behavior (file paths, permissions, timezone).

## Auto-Update Support

The build pipeline must generate installers compatible with v0.5.0's silent auto-update system:

| Platform | Update Mechanism | CI Requirements |
|----------|-----------------|-----------------|
| Windows | Inno Setup silent install (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`) | Inno Setup script must set `DisableDirPage=auto`, `DisableProgramGroupPage=auto`, verify `UsePreviousAppDir=yes` |
| Android | APK download + `ACTION_VIEW` install intent | Standard APK build is sufficient (install intent handled client-side) |

After auto-update, user data in `%APPDATA%\Unscreen\` (Windows) or app internal storage (Android) is preserved — only program files are replaced.

### Release not created
- Verify `version` in `pyproject.toml` was increased.
- Check `detect-version-bump` step output in CI logs.
- Ensure push was to `master` branch (not `dev`).
