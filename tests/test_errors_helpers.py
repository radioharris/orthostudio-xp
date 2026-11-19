# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Shared helpers for the ``test_errors_*`` modules: parse the inventory of error codes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from doc_text import spec_text
from orthostudio.errors import CODE_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[1]

ORIGIN_PATTERN = re.compile(r"([A-Za-z0-9_]+\.py):(\d+)(?:-(\d+))?")


@dataclass(frozen=True)
class SpecRow:
    """One inventory row of the error table."""

    code: str
    origin: str
    condition: str
    effect: str
    severity: str
    action: str
    remedy: str

    def origins(self) -> list[tuple[str, int, int]]:
        """``(file, first_line, last_line)`` for every origin token; empty for ``new``."""
        return [
            (m.group(1), int(m.group(2)), int(m.group(3) or m.group(2)))
            for m in ORIGIN_PATTERN.finditer(self.origin)
        ]


def parse_spec_rows() -> list[SpecRow]:
    """Return every table row whose first cell is an error code."""
    rows: list[SpecRow] = []
    for line in spec_text("errors.md").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not CODE_PATTERN.match(cells[0]):
            continue
        rows.append(SpecRow(*cells))
    return rows
