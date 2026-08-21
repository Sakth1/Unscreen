import importlib.metadata
import logging

from utils.constants import FALLBACK_APP_VERSION

logger = logging.getLogger(__name__)


def normalize_version(version: str) -> str:
    """Strip a leading ``v`` from a version/tag string."""
    return version[1:] if version.startswith("v") else version


def parse_version(
    version: str,
) -> tuple[tuple[int, int, int], tuple[str, ...]] | None:
    """Return ``((major, minor, patch), prerelease)`` for a version string.

    Accepts both the tag form (``0.4.5-dev1``) and the PEP 440 form that
    package metadata reports (``0.4.5.dev1``), with an optional ``+local``
    suffix. Returns ``None`` when the string cannot be parsed.
    """
    core = normalize_version(version)
    core, _, _ = core.partition("+")
    prerelease: tuple[str, ...] = ()
    if "-" in core:
        core, _, prerelease_str = core.partition("-")
        prerelease = tuple(prerelease_str.split("."))
    elif core.count(".") > 2:
        parts = core.split(".")
        core = ".".join(parts[:3])
        prerelease = tuple(parts[3:])
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return (int(parts[0]), int(parts[1]), int(parts[2])), prerelease


def compare_versions(left: str, right: str) -> int:
    """Compare two semver strings; returns ``>0`` when ``left`` is newer.

    Unparseable versions compare as equal (never report a bogus update).
    """
    left_parsed = parse_version(left)
    right_parsed = parse_version(right)
    if left_parsed is None or right_parsed is None:
        logger.warning("Comparing unparseable versions: %r vs %r", left, right)
        return 0
    left_core, left_pre = left_parsed
    right_core, right_pre = right_parsed
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if not left_pre and not right_pre:
        return 0
    if not left_pre:
        return 1
    if not right_pre:
        return -1
    for left_ident, right_ident in zip(left_pre, right_pre, strict=False):
        if left_ident == right_ident:
            continue
        left_is_num = left_ident.isdigit()
        right_is_num = right_ident.isdigit()
        if left_is_num and right_is_num:
            return (int(left_ident) > int(right_ident)) - (
                int(left_ident) < int(right_ident)
            )
        if left_is_num:
            return -1
        if right_is_num:
            return 1
        # Handle prerelease ids like "dev9" vs "dev10": split trailing numeric
        # suffix so numeric part is compared numerically, not lexicographically.
        import re as _re

        _m_left = _re.match(r"^([A-Za-z-]+?)(\d+)$", left_ident)
        _m_right = _re.match(r"^([A-Za-z-]+?)(\d+)$", right_ident)
        if _m_left and _m_right and _m_left.group(1) == _m_right.group(1):
            _l_num = int(_m_left.group(2))
            _r_num = int(_m_right.group(2))
            if _l_num != _r_num:
                return (_l_num > _r_num) - (_l_num < _r_num)
        return (left_ident > right_ident) - (left_ident < right_ident)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def get_current_version() -> str:
    """Return the installed app version, falling back to a constant."""
    try:
        return importlib.metadata.version("unscreen")
    except importlib.metadata.PackageNotFoundError:
        logger.debug("unscreen package metadata not found; using fallback version")
        return FALLBACK_APP_VERSION
