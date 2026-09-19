"""Overlay text filter: kept/dropped line classes, exclusion grammar, Ortho4XP differential."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pydantic import ValidationError

from orthostudio.errors import OsxpError
from orthostudio.overlays import (
    DEFAULT_EXCLUDED_POLYGONS,
    FilterStats,
    OverlayExclusions,
    exclusions_by_name,
    filter_dsf_bytes,
    filter_dsf_text,
    resolve_network_exclusions,
    resolve_polygon_exclusions,
)
from orthostudio.overlays.exclusions import networks_all_excluded

HEADER = b"""A
800 written by DSFTool 2.3.0-b2
DSF2TEXT

# file: +43+005.dsf
# pool  0: p=5 s= 1163  0.12500 5.00000 PROPERTY in a comment
PROPERTY sim/west 5
PROPERTY sim/east 6
PROPERTY sim/creation_agent X-Plane Scenery Creator 0.9a
TERRAIN_DEF terrain_Water
TERRAIN_DEF lib/g10/terrain10/wetl_tmp_sdry_lo.ter
OBJECT_DEF lib/objects/tower.obj
POLYGON_DEF lib/g12/beaches.bch
POLYGON_DEF lib/g8/broad_tmp_sdry.for
POLYGON_DEF lib/g10/autogen/urban_low_broken_0.ags
POLYGON_DEF lib/g10/facades/warehouse.fac
NETWORK_DEF lib/g10/roads_EU.net
RASTER_DEF elevation
RASTER_DATA version=1 bpp=2 flags=3 width=1201 height=1201 scale=1.0 offset=0.0 x.elevation.raw
DIVISIONS 8
HEIGHTS 1.00000 -32768.0  # max encodeable 32767.00000
"""

POLY0 = b"""BEGIN_POLYGON 0 255 2
BEGIN_WINDING
POLYGON_POINT 5.067893111 43.892727169
POLYGON_POINT 5.069010834 43.892277028
POLYGON_POINT 5.067883574 43.894306477
END_WINDING
END_POLYGON
"""
POLY1 = b"""BEGIN_POLYGON 1 255 2
BEGIN_WINDING
POLYGON_POINT 5.1 43.1
POLYGON_POINT 5.2 43.1
POLYGON_POINT 5.2 43.2
END_WINDING
END_POLYGON
"""
POLY2 = b"""BEGIN_POLYGON 2 65535 4
BEGIN_WINDING
POLYGON_POINT 5.3 43.3 0.0 0.0
POLYGON_POINT 5.4 43.3 0.0 0.0
END_WINDING
END_POLYGON
"""
POLY3 = b"""BEGIN_POLYGON 3 12 2
BEGIN_WINDING
POLYGON_POINT 5.5 43.5
POLYGON_POINT 5.6 43.5
END_WINDING
END_POLYGON
"""
PATCH_A = b"""BEGIN_PATCH 0 0.000000 -1.000000 1 7
BEGIN_PRIMITIVE 1
PATCH_VERTEX 5.000000000 43.000000000 0.000000000 -0.000015259 -0.000015259 1.000000000 1.000000000
PATCH_VERTEX 5.000000000 43.016666667 0.000000000 -0.000015259 -0.000015259 1.000000000 1.000000000
PATCH_VERTEX 5.016664759 43.000000000 0.000000000 -0.000015259 -0.000015259 1.000000000 1.000000000
END_PRIMITIVE
END_PATCH
BEGIN_PATCH 1 0.000000 -1.000000 1 7
BEGIN_PRIMITIVE 0
PATCH_VERTEX 5.1 43.1 1.0 0.0 0.0 1.0 1.0
PATCH_VERTEX 5.1 43.2 1.0 0.0 0.0 1.0 1.0
PATCH_VERTEX 5.2 43.1 1.0 0.0 0.0 1.0 1.0
END_PRIMITIVE
"""
OBJECT = b"OBJECT 0 5.500000000 43.500000000 90.000000\n"
SEG_ROAD = b"""BEGIN_SEGMENT 0 10 102670 5.801874600 43.241647800 -0.000000000
SHAPE_POINT 5.801008786 43.242376927 1.000000000
SHAPE_POINT 5.800444600 43.242592400 -0.000000000
END_SEGMENT 32665 5.973909400 43.157672100 -0.000000000
"""
SEG_POWER = b"""BEGIN_SEGMENT 0 22001 99625 5.653792800 43.515074600 -0.000000000
SHAPE_POINT 5.66 43.52 0.0
END_SEGMENT 99930 5.659975800 43.521998858 -0.000000000
"""
# The last patch of a DSFTool text is closed at the very end of the file, after the polygons,
# objects and segments (observed on +43+005): END_PATCH comes after the last END_SEGMENT.
TAIL = b"END_PATCH\n# Result code: 0\n"

TEXT = HEADER + POLY0 + POLY1 + PATCH_A + POLY2 + OBJECT + POLY3 + SEG_ROAD + SEG_POWER + TAIL

PROPS = b"PROPERTY sim/overlay 1\n" + b"".join(
    line + b"\n" for line in HEADER.splitlines() if line.startswith(b"PROPERTY")
)
DEFS_NO_OBJ = b"".join(
    line + b"\n"
    for line in HEADER.splitlines()
    if line.startswith((b"POLYGON_DEF", b"NETWORK_DEF"))
)
DEFS = b"OBJECT_DEF lib/objects/tower.obj\n" + DEFS_NO_OBJ
POL_DEFS = [
    "lib/g12/beaches.bch",
    "lib/g8/broad_tmp_sdry.for",
    "lib/g10/autogen/urban_low_broken_0.ags",
    "lib/g10/facades/warehouse.fac",
]


def run(data: bytes, exclusions: OverlayExclusions) -> tuple[bytes, FilterStats]:
    pieces, stats = filter_dsf_bytes(data, exclusions)
    return b"".join(pieces), stats


def reference_filter(text: str, ovl_exclude_pol: list, ovl_exclude_net: list) -> str:
    """Literal port of ``O4_Overlay_Utils.py:115-173`` (text mode, substring tests)."""
    f = io.StringIO(text)
    g = io.StringIO()
    line = f.readline()
    g.write("PROPERTY sim/overlay 1\n")
    pol_type = 0
    pol_dict: dict[int, str] = {}
    exclude_set_updated = False
    full_ovl_exclude_pol: set = set(ovl_exclude_pol)
    while line:
        if "PROPERTY" in line:
            g.write(line)
        elif "POLYGON_DEF" in line:
            pol_dict[pol_type] = line.split()[1]
            pol_type += 1
            g.write(line)
        elif "NETWORK_DEF" in line:
            g.write(line)
        elif "BEGIN_POLYGON" in line:
            if not exclude_set_updated:
                tmp: set = set()
                for item in full_ovl_exclude_pol:
                    if isinstance(item, int):
                        tmp.add(item)
                    elif isinstance(item, str):
                        if item and item[0] == "!":
                            item = item[1:]
                            tmp = tmp.union([k for k in pol_dict if item not in pol_dict[k]])
                        else:
                            tmp = tmp.union([k for k in pol_dict if item in pol_dict[k]])
                full_ovl_exclude_pol = tmp
                exclude_set_updated = True
            pol_type = int(line.split()[1])
            if pol_type not in full_ovl_exclude_pol:
                while line and ("END_POLYGON" not in line):
                    g.write(line)
                    line = f.readline()
                g.write(line)
            else:
                while line and ("END_POLYGON" not in line):
                    line = f.readline()
        elif "BEGIN_SEGMENT" in line:
            road_type = int(line.split()[2])
            if (
                road_type not in ovl_exclude_net
                and "" not in ovl_exclude_net
                and "*" not in ovl_exclude_net
            ):
                while line and ("END_SEGMENT" not in line):
                    g.write(line)
                    line = f.readline()
                g.write(line)
            else:
                while line and ("END_SEGMENT" not in line):
                    line = f.readline()
        line = f.readline()
    return g.getvalue()


# ----------------------------------------------------------------------------- line classes


def test_default_keeps_everything_but_mesh_and_beaches() -> None:
    out, stats = run(TEXT, OverlayExclusions())
    assert out == PROPS + DEFS + POLY1 + POLY2 + OBJECT + POLY3 + SEG_ROAD + SEG_POWER
    assert stats.polygon_defs == POL_DEFS
    assert stats.network_defs == ["lib/g10/roads_EU.net"]
    assert stats.object_defs == ["lib/objects/tower.obj"]
    assert stats.excluded_polygon_indices == (0,)
    assert (stats.polygons_kept, stats.polygons_dropped) == (3, 1)
    assert (stats.segments_kept, stats.segments_dropped) == (2, 0)
    assert (stats.objects_kept, stats.objects_dropped) == (1, 0)
    assert (stats.patches_dropped, stats.primitives_dropped) == (2, 2)
    assert stats.properties == 3
    assert stats.lines_in == TEXT.count(b"\n")
    assert stats.bytes_in == len(TEXT) and stats.bytes_out == len(out)


def test_the_airport_border_line_goes_whatever_the_settings_say() -> None:
    """``apt_border_<climate>.lin`` feathers the edge of X-Plane's airport grass into the terrain.
    Copied over a photograph it is a painted outline around every airfield, and no user could
    guess he has to exclude it (X-Plane.Org topic 349619; fifteen of them in our own +46+006,
    four in +35-117). It goes even when the exclusion list is empty."""
    header = HEADER.replace(
        b"POLYGON_DEF lib/g8/broad_tmp_sdry.for\n",
        b"POLYGON_DEF lib/g10/terrain10/apt_border_hot_dry.lin\n",
    )
    text = header + POLY0 + POLY1 + POLY2 + POLY3 + TAIL  # POLY1 is now the border line
    for asked in ([], [0], ["lib/g12/beaches.bch"], [2]):
        out, stats = run(text, OverlayExclusions(ovl_exclude_pol=asked))
        assert b"BEGIN_POLYGON 1 " not in out, asked
        assert 1 in stats.excluded_polygon_indices
        assert b"BEGIN_POLYGON 3 " in out  # the rest of the overlay is untouched


def test_first_line_is_the_overlay_property_and_source_one_is_not_duplicated() -> None:
    src = TEXT.replace(b"PROPERTY sim/east 6\n", b"PROPERTY sim/overlay 1\nPROPERTY sim/east 6\n")
    out, stats = run(src, OverlayExclusions())
    assert out.startswith(b"PROPERTY sim/overlay 1\nPROPERTY sim/west 5\n")
    assert out.count(b"PROPERTY sim/overlay") == 1
    assert stats.properties == 3


def test_comment_lines_mentioning_a_keyword_are_dropped() -> None:
    out, _ = run(TEXT, OverlayExclusions())
    assert b"# pool" not in out and b"comment" not in out
    assert b"TERRAIN_DEF" not in out and b"RASTER" not in out
    assert b"DIVISIONS" not in out and b"HEIGHTS" not in out
    assert b"PATCH" not in out and b"PRIMITIVE" not in out
    assert b"Result code" not in out and b"DSF2TEXT" not in out


def test_keep_objects_false_gives_the_ortho4xp_behaviour() -> None:
    out, stats = run(TEXT, OverlayExclusions(keep_objects=False))
    assert out == PROPS + DEFS_NO_OBJ + POLY1 + POLY2 + POLY3 + SEG_ROAD + SEG_POWER
    assert stats.object_defs == ["lib/objects/tower.obj"]
    assert (stats.objects_kept, stats.objects_dropped) == (0, 1)


def test_kept_blocks_are_byte_identical_and_crlf_is_normalised() -> None:
    crlf = TEXT.replace(b"\n", b"\r\n")
    out_lf, _ = run(TEXT, OverlayExclusions())
    out_crlf, stats = run(crlf, OverlayExclusions())
    assert out_crlf == out_lf
    assert b"\r" not in out_crlf
    assert stats.bytes_in == len(TEXT)


def test_last_line_without_newline_gets_one() -> None:
    out, _ = run(b"PROPERTY sim/west 5", OverlayExclusions())
    assert out == b"PROPERTY sim/overlay 1\nPROPERTY sim/west 5\n"


def test_segments_after_the_last_begin_patch_are_kept() -> None:
    # The regression that motivated the primitive-level skip: a patch stays open until the
    # end of the file, so segments between BEGIN_PATCH and END_PATCH must be seen.
    src = HEADER + PATCH_A + SEG_ROAD + TAIL
    out, stats = run(src, OverlayExclusions())
    assert out.endswith(SEG_ROAD)
    assert stats.segments_kept == 1 and stats.primitives_dropped == 2


# ------------------------------------------------------------------------ exclusion grammar


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([0], {0}),
        ([1, 3], {1, 3}),
        ([".for"], {1}),
        (["!.for"], {0, 2, 3}),
        (["lib/g12/beaches.bch"], {0}),
        (["beaches.bch", ".ags"], {0, 2}),
        ([0, ".fac"], {0, 3}),
        ([""], {0, 1, 2, 3}),
        (["!"], set()),
        ([], set()),
        ([99], {99}),
        (["lib/g8/beaches.bch"], set()),
        (list(DEFAULT_EXCLUDED_POLYGONS), {0}),
    ],
)
def test_resolve_polygon_exclusions(items: list, expected: set[int]) -> None:
    assert resolve_polygon_exclusions(POL_DEFS, items) == frozenset(expected)


def test_polygon_exclusion_by_index_substring_negation_and_union() -> None:
    body = PROPS + DEFS
    out, _ = run(TEXT, OverlayExclusions(ovl_exclude_pol=[1]))
    assert out == body + POLY0 + POLY2 + OBJECT + POLY3 + SEG_ROAD + SEG_POWER
    out, _ = run(TEXT, OverlayExclusions(ovl_exclude_pol=[".for"]))
    assert out == body + POLY0 + POLY2 + OBJECT + POLY3 + SEG_ROAD + SEG_POWER
    out, _ = run(TEXT, OverlayExclusions(ovl_exclude_pol=["!.for"]))
    assert out == body + POLY1 + OBJECT + SEG_ROAD + SEG_POWER
    out, stats = run(TEXT, OverlayExclusions(ovl_exclude_pol=[""]))
    assert out == body + OBJECT + SEG_ROAD + SEG_POWER
    assert stats.polygons_dropped == 4 and stats.excluded_polygon_indices == (0, 1, 2, 3)
    out, _ = run(TEXT, OverlayExclusions(ovl_exclude_pol=[0, ".ags"]))
    assert out == body + POLY1 + OBJECT + POLY3 + SEG_ROAD + SEG_POWER
    out, _ = run(TEXT, OverlayExclusions(ovl_exclude_pol=[]))
    assert out == body + POLY0 + POLY1 + POLY2 + OBJECT + POLY3 + SEG_ROAD + SEG_POWER


def test_network_exclusion_by_road_type_and_all() -> None:
    body = PROPS + DEFS + POLY1 + POLY2 + OBJECT + POLY3
    out, stats = run(TEXT, OverlayExclusions(ovl_exclude_net=[22001]))
    assert out == body + SEG_ROAD
    assert (stats.segments_kept, stats.segments_dropped) == (1, 1)
    all_items: list[list[str | int]] = [["*"], [""], [10, 22001]]
    for items in all_items:
        out, stats = run(TEXT, OverlayExclusions(ovl_exclude_net=items))
        assert out == body, items
        assert stats.segments_dropped == 2
    assert b"NETWORK_DEF lib/g10/roads_EU.net\n" in out  # the definition stays (Ortho4XP too)
    assert networks_all_excluded(["*"]) and networks_all_excluded([""])
    assert not networks_all_excluded([10])
    assert resolve_network_exclusions([10, "*", 22001]) == frozenset({10, 22001})


def test_exclusions_validation() -> None:
    with pytest.raises(ValidationError):
        OverlayExclusions(ovl_exclude_net=["22001"])
    with pytest.raises(ValidationError):
        OverlayExclusions(ovl_exclude_net=["power"])
    with pytest.raises(ValidationError):
        OverlayExclusions(ovl_exclude_pol=[-1])
    with pytest.raises(ValidationError):
        OverlayExclusions(ovl_exclude_pol=[1.5])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        OverlayExclusions(ovl_exclude_net=[True])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        OverlayExclusions(unknown=1)  # type: ignore[call-arg]
    e = OverlayExclusions(ovl_exclude_pol=[0, ".for"], ovl_exclude_net=[22001, "*"])
    assert e.ovl_exclude_pol == [0, ".for"] and e.ovl_exclude_net == [22001, "*"]
    assert OverlayExclusions().ovl_exclude_pol == list(DEFAULT_EXCLUDED_POLYGONS)
    assert OverlayExclusions().ovl_exclude_net == [] and OverlayExclusions().keep_objects
    assert OverlayExclusions().canonical() == {
        "ovl_exclude_pol": list(DEFAULT_EXCLUDED_POLYGONS),
        "ovl_exclude_net": [],
        "keep_objects": True,
    }


def test_exclusions_by_name_translates_ortho4xp_indices() -> None:
    ortho4xp_default = OverlayExclusions(ovl_exclude_pol=[0])
    by_name = exclusions_by_name(ortho4xp_default, POL_DEFS)
    assert by_name.ovl_exclude_pol == ["lib/g12/beaches.bch"]
    assert by_name.ovl_exclude_net == [] and by_name.keep_objects
    mixed = exclusions_by_name(OverlayExclusions(ovl_exclude_pol=[3, ".for", 0, 3, 99]), POL_DEFS)
    assert mixed.ovl_exclude_pol == ["lib/g10/facades/warehouse.fac", ".for", "lib/g12/beaches.bch"]
    assert resolve_polygon_exclusions(POL_DEFS, by_name.ovl_exclude_pol) == frozenset({0})


def test_default_exclusion_matches_index_zero_on_a_global_scenery_like_source() -> None:
    out_idx, s_idx = run(TEXT, OverlayExclusions(ovl_exclude_pol=[0]))
    out_name, s_name = run(TEXT, OverlayExclusions())
    assert out_idx == out_name
    assert s_idx.excluded_polygon_indices == s_name.excluded_polygon_indices == (0,)


def test_no_polygon_at_all_still_reports_the_resolved_set() -> None:
    _, stats = run(HEADER + SEG_ROAD + TAIL, OverlayExclusions(ovl_exclude_pol=[".for", 7]))
    assert stats.excluded_polygon_indices == (1, 7)


# ---------------------------------------------------------------------- Ortho4XP differential


@pytest.mark.parametrize(
    ("pol", "net"),
    [
        ([0], []),
        ([], []),
        ([1, 3], [10]),
        ([".for"], [22001]),
        (["!.for"], ["*"]),
        ([""], [""]),
        (["!"], [10, 22001]),
        ([0, ".ags", 99], []),
        (["beaches.bch"], []),
    ],
)
def test_matches_the_ortho4xp_loop_on_the_synthetic_text(
    pol: list[str | int], net: list[str | int]
) -> None:
    # Ortho4XP drops objects and never sees a source sim/overlay line: compare on that footing.
    src = TEXT.replace(b"# pool  0: p=5 s= 1163  0.12500 5.00000 PROPERTY in a comment\n", b"")
    expected = reference_filter(src.decode("ascii"), pol, net).encode("ascii")
    out, _ = run(
        src, OverlayExclusions(ovl_exclude_pol=pol, ovl_exclude_net=net, keep_objects=False)
    )
    assert out == expected


# ------------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    "src",
    [
        HEADER + POLY1[: POLY1.index(b"END_POLYGON")],
        HEADER + SEG_ROAD[: SEG_ROAD.index(b"END_SEGMENT")],
        HEADER + PATCH_A[: PATCH_A.index(b"END_PRIMITIVE")],
        HEADER + b"BEGIN_POLYGON x 255 2\nEND_POLYGON\n",
        HEADER + b"BEGIN_SEGMENT 0\nEND_SEGMENT\n",
    ],
)
def test_malformed_text_is_dsf_source_corrupted(src: bytes) -> None:
    with pytest.raises(OsxpError) as info:
        run(src, OverlayExclusions())
    assert info.value.code == "DSF_SOURCE_CORRUPTED"
    assert info.value.context["line"] > 0


# ----------------------------------------------------------------------------------- file


def test_filter_dsf_text_writes_the_file_atomically(tmp_path: Path) -> None:
    src = tmp_path / "t.txt"
    dst = tmp_path / "o.txt"
    src.write_bytes(TEXT)
    stats = filter_dsf_text(src, dst, OverlayExclusions())
    expected, _ = run(TEXT, OverlayExclusions())
    assert dst.read_bytes() == expected
    assert stats.bytes_out == len(expected)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["o.txt", "t.txt"]


def test_filter_dsf_text_failure_leaves_no_temporary(tmp_path: Path) -> None:
    src = tmp_path / "t.txt"
    dst = tmp_path / "o.txt"
    src.write_bytes(HEADER + b"BEGIN_POLYGON 0 255 2\n")
    with pytest.raises(OsxpError):
        filter_dsf_text(src, dst, OverlayExclusions())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.txt"]
