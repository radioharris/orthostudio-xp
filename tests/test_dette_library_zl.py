# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Dette D4: a pack without a zoom level shows an em dash, not "ZL 0".

A ``yOrthoStudio_Overlays`` pack has no imagery and no zoom level; the library row carried 0 and
the page printed "0" in the ZL column. The API now sends ``null`` and the page prints an em
dash for anything that is not an ortho pack.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from orthostudio.api.app import _display_zl

UI = Path(__file__).resolve().parents[1] / "src" / "orthostudio" / "ui"


def test_only_an_ortho_pack_keeps_its_zoom_level() -> None:
    assert _display_zl("ortho", 16) == 16
    assert _display_zl("overlay", 0) is None
    assert _display_zl("overlay", 16) is None  # no kind but ortho carries a ZL
    assert _display_zl("ortho", 0) is None
    assert _display_zl("ortho", None) is None


def test_both_library_payloads_go_through_the_helper() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src" / "orthostudio" / "api" / "app.py"
    ).read_text("utf-8")
    assert src.count('"zl": _display_zl(r.kind, r.zl)') == 2  # /api/library and import-ortho4xp
    assert '"zl": r.zl,' not in src


def test_the_page_renders_a_dash_and_the_key_exists_in_both_languages() -> None:
    """The Library's Imagery cell prints an em dash for a row with no source and no zoom level,
    never "ZL0"; the overlay rows, which have no zoom level, are not rows of the Library."""
    app_js = (UI / "app.js").read_text("utf-8")
    cell = re.search(r"\nfunction imageryCell\(e\) \{.*?\n\}\n", app_js, re.S)
    assert cell is not None
    assert 't("library.no_zl")' in cell.group(0)
    assert "Number(e.zl) || 0" in cell.group(0)  # 0 or null: no detail level, no "ZL0"
    assert "libraryTiles(state.library)" in app_js
    i18n = (UI / "i18n.js").read_text("utf-8")
    assert i18n.count('"library.no_zl"') == 2  # fr + en
    for m in re.finditer(r'"library\.no_zl":\s*"([^"]*)"', i18n):
        assert json.loads(f'"{m.group(1)}"') == "—"


def test_the_mock_rows_keep_their_zoom_level() -> None:
    """The fixture's tile pack rows carry their zoom level; its overlay rows carry none (null),
    as ``_display_zl`` sends it."""
    rows = json.loads((UI / "mock" / "library.json").read_text("utf-8"))
    assert rows
    for r in rows:
        if r.get("kind", "ortho") == "ortho":
            assert r["zl"], r["tile"]
        else:
            assert r["zl"] is None, r["tile"]
