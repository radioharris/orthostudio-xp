# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Dette D1: the codes that dsf/sched used to borrow from SYS_*.

Before this chantier a mesh triangle outside the tile and a node skipped after an upstream
failure were reported as ``SYS_INTERNAL_ERROR`` or ``SYS_CANCELLED``. A batch report was then
unreadable: a run the user stopped and a run broken by one bad tile looked the same.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orthostudio.errors import REGISTRY, Action, OsxpError, Severity

NEW_CODES = ("DSF_MESH_OUTSIDE_TILE", "SYS_UPSTREAM_FAILED")


def test_the_codes_are_registered_with_a_message_and_a_remedy() -> None:
    for code in NEW_CODES:
        spec = REGISTRY[code]
        assert spec.message.strip() and spec.remedy.strip(), code
        assert isinstance(spec.severity, Severity) and isinstance(spec.action, Action), code


def test_severities_say_what_the_caller_must_do() -> None:
    # a mesh outside the tile is a hard failure
    assert REGISTRY["DSF_MESH_OUTSIDE_TILE"].severity is Severity.BLOCKING
    assert REGISTRY["DSF_MESH_OUTSIDE_TILE"].action is Action.STOP
    # a skipped node is not itself a failure: info, but the node still stops
    assert REGISTRY["SYS_UPSTREAM_FAILED"].severity is Severity.INFO
    assert REGISTRY["SYS_UPSTREAM_FAILED"].action is Action.STOP


def test_messages_render_with_the_context_the_call_sites_pass() -> None:
    skipped = OsxpError(
        "SYS_UPSTREAM_FAILED",
        context={"node": "+43+005/dsf", "root": "+43+005/mesh", "code": "MESH_INPUT_MISSING"},
    )
    doc = json.loads(skipped.to_json())
    assert doc["code"] == "SYS_UPSTREAM_FAILED" and doc["severity"] == "info"
    assert doc["context"]["root"] == "+43+005/mesh"


def test_the_spec_table_documents_them() -> None:
    text = Path(__file__).resolve().parents[1].joinpath("docs/specs/errors.md").read_text("utf-8")
    for code in NEW_CODES:
        assert f"| {code} |" in text, code


@pytest.mark.parametrize("code", NEW_CODES)
def test_every_new_code_is_raisable_without_context(code: str) -> None:
    err = OsxpError(code)
    assert err.code == code and err.message and err.remedy
