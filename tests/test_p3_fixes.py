# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Behaviour added by the review-4 fix pass of P3 (integration side).

Everything here is new behaviour, not a regression guard for a finding already pinned by
``tests/test_review4_*``: what the OSM report says, and the refusal of an unknown multiprocessing
start method. No network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.masks.build import start_method
from orthostudio.model import TileRef
from orthostudio.pipeline.build import OsmOutcome, _osm_report

TILE = TileRef(43, 5)


def test_the_osm_report_tells_a_failure_from_a_skip() -> None:
    """A failed download used to be reported as ``skipped``, next to a real skip."""
    err = OsxpError("NET_CONNECTION_FAILED", context={"host": "overpass", "reason": "refused"})
    assert _osm_report(OsmOutcome(TILE, error=err))["status"] == "failed"
    assert _osm_report(OsmOutcome(TILE, skipped="--no-osm-fetch"))["status"] == "skipped"
    assert _osm_report(OsmOutcome(TILE, artefact=Path("/x")))["status"] == "built"
    assert _osm_report(OsmOutcome(TILE, artefact=Path("/x"), hit=True))["status"] == "hit"
    assert _osm_report(None) == {}


def test_an_unknown_start_method_is_refused_with_a_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """``OSXP_MASKS_START_METHOD=fork`` on Windows used to raise a bare ``ValueError``."""
    monkeypatch.setenv("OSXP_MASKS_START_METHOD", "telepathy")
    with pytest.raises(OsxpError) as exc:
        start_method()
    assert exc.value.code == "CFG_VALUE_INVALID"
    monkeypatch.delenv("OSXP_MASKS_START_METHOD")
    assert start_method() == "spawn"
