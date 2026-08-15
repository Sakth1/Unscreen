import argparse
import subprocess
import sys


def _step(name: str, *args: str) -> None:
    print(f"\n=== {name} ===")
    result = subprocess.run(args)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(prog="check")
    parser.add_argument("--fix", action="store_true", help="run ruff with --fix")
    parser.add_argument(
        "--unsafe-fixes", action="store_true", help="run ruff with --unsafe-fixes"
    )
    args = parser.parse_args()

    ruff_fix_args: list[str] = []
    if args.fix:
        ruff_fix_args.append("--fix")
    if args.unsafe_fixes:
        ruff_fix_args.append("--unsafe-fixes")

    _step("1. uv sync (frozen)", "uv", "sync", "--frozen")
    _step(
        "2. black formating (check only)",
        "uv",
        "run",
        "black",
        "src/",
        "tests/",
        "--target-version",
        "py312",
        "--check",
    )
    ruff_args = ["uv", "run", "ruff", "check", "src/", "tests/"]
    if ruff_fix_args:
        ruff_args.extend(ruff_fix_args)
    _step("3. ruff check", *ruff_args)
    _step("4. pyright", "uv", "run", "pyright", "src/")
    _step("5. pytest", "uv", "run", "pytest", "tests/", "-v", "--tb=short", "-q")
    print("\n=== All CI checks passed ===")


if __name__ == "__main__":
    main()
