"""Replicate the cloud CI lint+test jobs in a pristine local copy.

Recreates the ``actions/checkout`` + ``uv sync --frozen`` + test scenario
from ``.github/workflows/ci.yml``:

1. Copies the working tree into a scratch dir (``.git/unscreen-ci/``) using
   git's own file list (tracked + untracked non-ignored) — no
   ``__pycache__``, no ``.pytest_cache``, no ``.venv``, exactly like a fresh
   checkout. The scratch dir persists between runs so only changed files are
   re-copied.
2. Runs ``uv sync --frozen`` there. The venv is reused across runs while
   ``uv.lock`` + ``pyproject.toml`` are unchanged (fingerprint-compared,
   mirroring the CI cache key ``hashFiles('uv.lock', 'pyproject.toml')``);
   a dependency change wipes the scratch dir and reinstalls from scratch,
   restoring the fresh-checkout scenario. Before every run, all project
   ``__pycache__`` dirs are purged so every module compiles from source on
   first import, reproducing environment-only failures (e.g. compile-time
   ``SyntaxWarning`` emitted by lazy imports) exactly as on the CI runner.
3. Runs the same commands as the CI ``lint`` and ``test`` jobs with the
   exact CI flags (``pytest tests/ -v --tb=short -q``, which inherits the
   coverage gate from ``pyproject.toml`` addopts).
4. Prunes ``unscreen-ci-*`` leftovers in the OS temp dir older than 24h
   (from runs killed before cleanup).

Usage (from the repo root):

    uv run python scripts/ci/local_ci.py [--fresh]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRATCH = _REPO_ROOT / ".git" / "unscreen-ci"
_FINGERPRINT_FILE = _SCRATCH / "env.fingerprint"
_FINGERPRINT_FILES = ("uv.lock", "pyproject.toml")
_STALE_TMP_AGE_S = 24 * 3600

_STEPS = (
    ("black --check", ["uv", "run", "black", "src/", "tests/", "--target-version", "py312", "--check"]),
    ("ruff check", ["uv", "run", "ruff", "check", "src/", "tests/"]),
    ("pyright", ["uv", "run", "pyright", "src/"]),
    (
        "pytest",
        ["uv", "run", "pytest", "tests/", "-v", "--tb=short", "-q"],
    ),
)


def _git_files() -> list[str]:
    """Every file a fresh checkout would contain (tracked + untracked)."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=_REPO_ROOT, text=True
    ).split("\0")
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=_REPO_ROOT,
        text=True,
    ).split("\0")
    return [f for f in [*tracked, *untracked] if f]


def _copy_tree(dest: Path) -> None:
    for rel in _git_files():
        src = _REPO_ROOT / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def _sync_tree(dest: Path) -> int:
    """Re-copy only files whose size or mtime changed. Returns files copied."""
    copied = 0
    for rel in _git_files():
        src = _REPO_ROOT / rel
        if not src.is_file():
            continue
        target = dest / rel
        st = src.stat()
        try:
            tst = target.stat()
            same = tst.st_size == st.st_size and tst.st_mtime_ns == st.st_mtime_ns
        except FileNotFoundError:
            same = False
        if same:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    return copied


def _purge_project_pycache(scratch: Path) -> None:
    """Delete __pycache__ outside .venv so every module recompiles from source."""
    purged = 0
    for child in scratch.iterdir():
        if child.name == ".venv":
            continue
        for pycache in child.rglob("__pycache__"):
            shutil.rmtree(pycache, ignore_errors=True)
            purged += 1
    return purged


def _env_fingerprint() -> str:
    h = hashlib.sha256()
    for name in _FINGERPRINT_FILES:
        h.update(name.encode())
        h.update((_REPO_ROOT / name).read_bytes())
    return h.hexdigest()


def _prune_stale_tmp() -> None:
    """Remove unscreen-ci-* dirs in the OS temp dir older than 24h."""
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob("unscreen-ci-*"):
        try:
            if now - d.stat().st_mtime > _STALE_TMP_AGE_S:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="wipe the scratch copy and reinstall the venv from scratch",
    )
    args = parser.parse_args()

    _prune_stale_tmp()

    fingerprint = _env_fingerprint()
    prev = ""
    if _FINGERPRINT_FILE.is_file():
        prev = _FINGERPRINT_FILE.read_text()
    reuse = not args.fresh and _SCRATCH.exists() and prev == fingerprint

    try:
        if reuse:
            copied = _sync_tree(_SCRATCH)
            print(f"=== Scratch copy reused ({copied} file(s) synced) ===")
            print("=== uv sync --frozen (verify) ===")
            _run(["uv", "sync", "--frozen"], cwd=_SCRATCH)
        else:
            shutil.rmtree(_SCRATCH, ignore_errors=True)
            _SCRATCH.mkdir(parents=True)
            print(f"=== Fresh checkout copy: {_SCRATCH} ===")
            _copy_tree(_SCRATCH)
            print("=== uv sync --frozen ===")
            _run(["uv", "sync", "--frozen"], cwd=_SCRATCH)
            _FINGERPRINT_FILE.write_text(fingerprint)
        purged = _purge_project_pycache(_SCRATCH)
        print(f"=== Purged {purged} project __pycache__ dir(s) ===")
        for name, cmd in _STEPS:
            print(f"=== {name} ===")
            _run(cmd, cwd=_SCRATCH)
        print("=== Cloud CI replication passed ===")
        return 0
    except subprocess.CalledProcessError as exc:
        print(
            f"=== Cloud CI replication FAILED (exit {exc.returncode}) ===",
            file=sys.stderr,
        )
        print(f"Scratch copy kept at: {_SCRATCH}", file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    sys.exit(main())