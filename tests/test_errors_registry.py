# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Registry invariants, spec/registry agreement and JSON rendering of ``orthostudio.errors``."""

from __future__ import annotations

import enum
import json
from pathlib import PurePosixPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orthostudio import errors
from orthostudio.errors import (
    CODE_PATTERN,
    DOMAINS,
    REGISTRY,
    SCHEMA_VERSION,
    Action,
    OsxpError,
    Severity,
    by_domain,
    codes,
    render_json,
    spec_for,
    wrap,
)
from test_errors_helpers import parse_spec_rows

# ----------------------------------------------------------------- registry


def test_registry_is_not_empty_and_large_enough() -> None:
    assert len(REGISTRY) >= 40


def test_codes_are_unique_and_keyed_by_themselves() -> None:
    assert len(set(codes())) == len(REGISTRY)
    for code, spec in REGISTRY.items():
        assert code == spec.code


def test_codes_follow_the_pattern_and_a_known_domain() -> None:
    for code, spec in REGISTRY.items():
        assert CODE_PATTERN.match(code), code
        assert spec.domain in DOMAINS, code
        assert code.startswith(spec.domain + "_")


def test_every_domain_has_at_least_one_code() -> None:
    for domain in DOMAINS:
        assert by_domain(domain), domain


def test_every_code_has_a_message_a_remedy_and_typed_fields() -> None:
    for spec in REGISTRY.values():
        assert spec.message.strip(), spec.code
        assert spec.remedy.strip(), spec.code
        assert isinstance(spec.severity, Severity), spec.code
        assert isinstance(spec.action, Action), spec.code


def test_templates_only_use_simple_placeholders() -> None:
    # No format specs or conversions: templates must survive missing context keys.
    for spec in REGISTRY.values():
        for template in (spec.message, spec.remedy):
            for _, field, fmt, conv in errors._FORMATTER.parse(template):
                if field is not None:
                    assert field.isidentifier(), (spec.code, field)
                    assert not fmt and conv is None, (spec.code, field)


def test_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        REGISTRY["NEW_CODE"] = spec_for("SYS_CANCELLED")  # type: ignore[index]


def test_codes_are_sorted_and_by_domain_rejects_unknown() -> None:
    assert list(codes()) == sorted(codes())
    with pytest.raises(ValueError, match="unknown error domain"):
        by_domain("FOO")


def test_blocking_codes_stop_except_cancellation() -> None:
    for spec in REGISTRY.values():
        if spec.severity is Severity.BLOCKING:
            assert spec.action is Action.STOP, spec.code
        if spec.severity is Severity.DEGRADED:
            assert spec.action is Action.CONTINUE, spec.code


# ------------------------------------------------------- spec <-> registry


def test_spec_document_lists_exactly_the_registered_codes() -> None:
    rows = parse_spec_rows()
    assert len(rows) >= 40
    doc_codes = [row.code for row in rows]
    assert len(doc_codes) == len(set(doc_codes)), "duplicate row in errors.md"
    assert set(doc_codes) == set(REGISTRY), (set(doc_codes) ^ set(REGISTRY),)


def test_spec_document_severity_and_action_match_the_registry() -> None:
    for row in parse_spec_rows():
        spec = REGISTRY[row.code]
        assert row.severity == spec.severity.value, row.code
        assert row.action == spec.action.value, row.code
        assert row.remedy.strip(), row.code
        assert row.origin == "new" or row.origins(), row.code


def test_spec_document_covers_the_headline_silent_failures() -> None:
    headline = {
        "IMG_TILE_MISSING",  # white texture
        "TEX_MISSING",
        "OSM_LAYER_UNAVAILABLE",  # tile without sea
        "OSM_COAST_OPEN_END",
        "DEM_DOWNLOAD_FAILED",  # altitude 0
        "OSM_AIRPORT_TAG_INVALID",  # airport ignored
        "DSF_GLOBAL_SCENERY_MISSING",  # DSF without DEMS
        "DSF_SOURCE_DECOMPRESS_FAILED",  # 7z absent
        "SYS_ROSETTA_MISSING",
        "NET_FORBIDDEN",  # dead or banning provider
        "OSM_MIRROR_UNREACHABLE",  # Overpass down
        "DEM_RASTER_LIBRARY_MISSING",  # GDAL absent
        "SYS_DISK_FULL",
        "XP_RUNNING",
        "CFG_LINE_INVALID",
        "CFG_ZONE_LIST_INVALID",
        "MASK_STALE",
        "MASK_NEIGHBOUR_MESH_MISSING",
    }
    assert headline <= set(REGISTRY)


# ------------------------------------------------------------- OsxpError


def test_error_renders_message_and_remedy_from_context() -> None:
    err = OsxpError("DEM_DOWNLOAD_FAILED", context={"cell": "N43E005", "source": "View"})
    assert err.code == "DEM_DOWNLOAD_FAILED"
    assert err.domain == "DEM"
    assert "N43E005" in err.message and "View" in err.message
    assert err.severity is Severity.BLOCKING
    assert err.action is Action.STOP
    assert str(err) == f"[DEM_DOWNLOAD_FAILED] {err.message}"
    assert err.spec is REGISTRY["DEM_DOWNLOAD_FAILED"]
    assert "DEM_DOWNLOAD_FAILED" in repr(err)


def test_missing_placeholders_are_left_visible() -> None:
    err = OsxpError("DEM_DOWNLOAD_FAILED", context={"cell": "N43E005"})
    assert "N43E005" in err.message
    assert "{source}" in err.message
    assert "{reason}" in err.message


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown error code"):
        OsxpError("OSM_DOES_NOT_EXIST")
    with pytest.raises(ValueError, match="unknown error code"):
        spec_for("dem_download_failed")


def test_explicit_message_remedy_severity_and_action_override_the_registry() -> None:
    err = OsxpError(
        "DEM_NEIGHBOUR_UNAVAILABLE",
        message="custom message",
        remedy="custom remedy",
        severity="blocking",
        action="stop",
    )
    assert err.message == "custom message"
    assert err.remedy == "custom remedy"
    assert err.severity is Severity.BLOCKING
    assert err.action is Action.STOP


def test_context_is_sanitized_to_json_types() -> None:
    class Colour(enum.Enum):
        RED = "red"

    err = OsxpError(
        "SYS_WRITE_FAILED",
        context={
            "path": PurePosixPath("/tmp/x"),
            "tags": {"b", "a"},
            "colour": Colour.RED,
            "raw": b"bytes",
            "nested": {"k": (1, 2)},
            3: None,
        },
    )
    assert err.context == {
        "path": "/tmp/x",
        "tags": ["a", "b"],
        "colour": "red",
        "raw": "bytes",
        "nested": {"k": [1, 2]},
        "3": None,
    }
    json.loads(err.to_json())


def test_error_is_an_exception_usable_with_raise_from() -> None:
    try:
        try:
            raise OSError(28, "No space left on device")
        except OSError as exc:
            raise OsxpError("SYS_DISK_FULL", context={"volume": "/"}) from exc
    except OsxpError as err:
        payload = err.to_dict()
        assert payload["cause"].startswith("OSError:")
        assert isinstance(err, Exception)


# ------------------------------------------------------------------- JSON


def test_to_json_is_stable_and_compact() -> None:
    err = OsxpError(
        "DSF_GLOBAL_SCENERY_MISSING",
        context={
            "tile": "+43+005",
            "path": "/X-Plane 12/Global Scenery/X-Plane 12 Global Scenery/Earth nav data"
            "/+40+000/+43+005.dsf",
        },
    )
    expected = (
        '{"action":"stop","cause":null,"code":"DSF_GLOBAL_SCENERY_MISSING",'
        '"context":{"path":"/X-Plane 12/Global Scenery/X-Plane 12 Global Scenery/Earth nav data'
        '/+40+000/+43+005.dsf","tile":"+43+005"},"domain":"DSF",'
        '"message":"X-Plane 12\'s own scenery for +43+005 was not found (its Global Scenery '
        'DSF).","remedy":"Install the region with +43+005 with X-Plane\'s installer (Add or '
        'Remove Scenery); if it is installed, set the X-Plane directory.","schema":1,'
        '"severity":"blocking"}'
    )
    assert err.to_json() == expected
    # Same input, same output, whatever the insertion order of the context.
    again = OsxpError(
        "DSF_GLOBAL_SCENERY_MISSING",
        context={
            "path": "/X-Plane 12/Global Scenery/X-Plane 12 Global Scenery/Earth nav data"
            "/+40+000/+43+005.dsf",
            "tile": "+43+005",
        },
    )
    assert again.to_json() == expected


def test_to_dict_has_the_documented_keys() -> None:
    payload = OsxpError("SYS_CANCELLED").to_dict()
    assert set(payload) == {
        "schema",
        "code",
        "domain",
        "severity",
        "action",
        "message",
        "remedy",
        "context",
        "cause",
    }
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["context"] == {}
    assert payload["cause"] is None


def test_to_json_keys_are_sorted_and_utf8_is_kept() -> None:
    err = OsxpError("CFG_LINE_INVALID", context={"path": "réglages.toml", "line": 3})
    text = err.to_json()
    assert "réglages" in text
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_render_json_wraps_a_foreign_exception_as_internal_error() -> None:
    payload = json.loads(render_json(KeyError("boom")))
    assert payload["code"] == "SYS_INTERNAL_ERROR"
    assert payload["severity"] == "blocking"
    assert payload["action"] == "stop"
    assert payload["context"] == {"type": "KeyError", "detail": "'boom'"}
    assert payload["cause"] == "KeyError: 'boom'"


def test_render_json_passes_an_osxp_error_through() -> None:
    err = OsxpError("NET_TIMEOUT", context={"host": "example.org", "timeout": 5})
    assert render_json(err) == err.to_json()
    assert wrap(err) is err


def test_every_code_renders_without_context() -> None:
    for code in codes():
        payload = json.loads(OsxpError(code).to_json())
        assert payload["code"] == code
        assert payload["message"]
        assert payload["remedy"]


@settings(max_examples=50, deadline=None)
@given(
    st.dictionaries(
        st.text(min_size=1, max_size=12),
        st.one_of(st.text(max_size=20), st.integers(), st.floats(allow_nan=False), st.none()),
        max_size=5,
    )
)
def test_any_text_context_yields_valid_json(context: dict[str, object]) -> None:
    err = OsxpError("SYS_INTERNAL_ERROR", context=context)
    payload = json.loads(err.to_json())
    assert payload["context"] == err.context


def test_every_error_supplies_what_its_words_need() -> None:
    """A card once read "OSM coastline of tile {tile} has a way with water on the wrong side":
    the program showing a user its own template, because the raise site did not pass the tile
    (found in review, 2026-09-23).

    The formatter leaves a gap visible on purpose, so this reads the source instead and says
    which site forgot what. A site that writes its own ``message`` and ``remedy`` is its own
    business, and one that builds its context elsewhere cannot be read from here; everything
    that names a code and spells its context out is checked.
    """
    import ast
    import string
    from pathlib import Path

    formatter = string.Formatter()

    def named(template: str) -> set[str]:
        return {field for _, field, _, _ in formatter.parse(template or "") if field}

    src = Path(__file__).resolve().parents[1] / "src"
    forgotten: list[str] = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            first = node.args[0]
            if called != "OsxpError" or not isinstance(first, ast.Constant):
                continue
            spec = REGISTRY.get(str(first.value))
            if spec is None:
                continue
            words = {k.arg: k.value for k in node.keywords}
            if "message" in words and "remedy" in words:
                continue
            context = words.get("context")
            if context is not None and not isinstance(context, ast.Dict):
                continue  # built somewhere else: not readable from here
            given = set()
            if isinstance(context, ast.Dict):
                if any(not isinstance(k, ast.Constant) for k in context.keys):
                    continue
                given = {str(k.value) for k in context.keys}  # type: ignore[union-attr]
            wanted: set[str] = set()
            if "message" not in words:
                wanted |= named(spec.message)
            if "remedy" not in words:
                wanted |= named(spec.remedy)
            if missing := sorted(wanted - given):
                where = path.relative_to(src.parent)
                forgotten.append(f"{where}:{first.lineno} {spec.code} needs {missing}")

    assert not forgotten, "these raise sites would show a user a placeholder:\n" + "\n".join(
        forgotten
    )
