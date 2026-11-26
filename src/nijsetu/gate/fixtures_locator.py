"""Locates the tests/fixtures tree from an installed or editable checkout."""

from __future__ import annotations

from pathlib import Path


def find_fixtures_root() -> Path | None:
    candidates = [Path(__file__).resolve(), Path.cwd()]
    for start in candidates:
        for parent in [start, *start.parents]:
            candidate = parent / "tests" / "fixtures"
            if candidate.is_dir():
                return candidate
    return None
