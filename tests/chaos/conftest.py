"""Chaos-suite session plumbing: the aggregated findings report.

A session-scoped ``chaos_report`` fixture collects fragments from every
chaos test and writes one JSON + text report at session end — even when
tests fail, since fixture teardown runs regardless. Point
``UNSCREEN_CHAOS_REPORT`` at a directory to relocate the report (default:
``%TEMP%\\unscreen_chaos\\``).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from chaos_helpers import Baseline, write_chaos_report

pytestmark = pytest.mark.chaos


@pytest.fixture(scope="session")
def chaos_report() -> Generator[list, None, None]:
    """Accumulate per-test finding fragments; write the report at session end."""
    fragments: list = []
    yield fragments
    baseline = Baseline()
    write_chaos_report(fragments, baseline.keys)
