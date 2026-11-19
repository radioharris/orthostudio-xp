# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The documents under ``docs/specs`` a test reads: it skips where they are not there."""

from __future__ import annotations

from pathlib import Path

import pytest

SPECS = Path(__file__).resolve().parents[1] / "docs" / "specs"


def spec_text(name: str) -> str:
    """The text of ``docs/specs/<name>``; the test skips when the specifications are not here."""
    path = SPECS / name
    if not path.is_file():
        pytest.skip(f"docs/specs/{name}: the specifications are not in this checkout")
    return path.read_text(encoding="utf-8")
