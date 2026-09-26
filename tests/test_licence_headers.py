# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Every source file of OrthoStudio XP names its licence and says where the NOTICE's terms are.

Section 7 of the GNU GPL v3 asks whoever adds terms to the licence to place, in the relevant
source files, a statement of them or a notice of where to find them. NOTICE adds three for
radioharris's material (the author's credit kept, a fork's own name, no right to the name), so
each of his files says so in its first lines, and a file added later must too (2026-09-26).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "orthostudio" / "ui"

LINES = (
    "OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.",
    "Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.",
)


def _sources() -> list[Path]:
    """radioharris's source files: the engine, the tests and the tools in Python, the page (the
    libraries it carries in ``vendor/`` excepted) and the Pascal of the Windows installer."""
    files = [p for folder in ("src", "tests", "tools") for p in (ROOT / folder).rglob("*.py")]
    files += [p for p in UI.iterdir() if p.suffix in {".js", ".css", ".html"}]
    files += (ROOT / "tools" / "package").glob("*.pas")
    return sorted(files)


def test_every_source_file_names_its_licence_and_the_notice() -> None:
    files = _sources()
    assert len(files) > 300, "the walk found the source files"
    missing = []
    for path in files:
        first = path.read_text(encoding="utf-8").splitlines()[:3]  # HTML: after its doctype
        if not all(any(line in head for head in first) for line in LINES):
            missing.append(path.relative_to(ROOT).as_posix())
    assert not missing, f"no licence lines at the top of {missing}"


def test_the_terms_it_points_to_are_in_the_notice() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright (C) 2026 radioharris" in notice
    assert "Additional terms (section 7 of the GNU GPL v3)" in notice
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.lstrip().startswith("GNU GENERAL PUBLIC LICENSE")
