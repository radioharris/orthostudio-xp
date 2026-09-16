"""Adversarial fidelity review (P2a): the overlay text filter against a statement-by-statement
transcription of Ortho4XP ``build_overlay`` (``O4_Overlay_Utils.py:101-172``), and the wiring of
the Ortho4XP ``ovl_exclude_pol`` / ``ovl_exclude_net`` settings into ``osxp build``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.overlays.exclusions import OverlayExclusions
from orthostudio.overlays.textfilter import filter_dsf_bytes
from orthostudio.pipeline.build import BuildSpec, OverlayParams

# --------------------------------------------------------------------- transcription of Ortho4XP


def reference_select_overlays(text: str, ovl_exclude_pol: list, ovl_exclude_net: list) -> str:
    """``O4_Overlay_Utils.py:101-172`` on a text (line loop kept verbatim, ``f``/``g`` become
    lists)."""
    lines = iter(text.splitlines(keepends=True))
    out: list[str] = []

    def readline() -> str:
        return next(lines, "")

    line = readline()
    out.append("PROPERTY sim/overlay 1\n")
    pol_type = 0
    pol_dict: dict[int, str] = {}
    exclude_set_updated = False
    full_ovl_exclude_pol: set = set(ovl_exclude_pol)
    while line:
        if "PROPERTY" in line:
            out.append(line)
        elif "POLYGON_DEF" in line:
            pol_dict[pol_type] = line.split()[1]
            pol_type += 1
            out.append(line)
        elif "NETWORK_DEF" in line:
            out.append(line)
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
                    out.append(line)
                    line = readline()
                out.append(line)
            else:
                while line and ("END_POLYGON" not in line):
                    line = readline()
        elif "BEGIN_SEGMENT" in line:
            road_type = int(line.split()[2])
            if (
                road_type not in ovl_exclude_net
                and "" not in ovl_exclude_net
                and "*" not in ovl_exclude_net
            ):
                while line and ("END_SEGMENT" not in line):
                    out.append(line)
                    line = readline()
                out.append(line)
            else:
                while line and ("END_SEGMENT" not in line):
                    line = readline()
        line = readline()
    return "".join(out)


SOURCE = """A
800
DSF2TEXT

# file: +43+005.dsf
PROPERTY sim/west 5
PROPERTY sim/east 6
PROPERTY sim/north 44
PROPERTY sim/south 43
PROPERTY sim/planet earth
PROPERTY sim/creation_agent X-Plane Scenery Creator 0.9a
PROPERTY laminar/internal_revision 1
TERRAIN_DEF terrain_Water
TERRAIN_DEF lib/g10/terrain10/wetl_tmp_sdry_lo.ter
POLYGON_DEF lib/g12/beaches.bch
POLYGON_DEF lib/g8/fruit_tmp_sdry.for
POLYGON_DEF lib/g10/autogen/urban_med_res.ags
POLYGON_DEF lib/g10/autogen/facade_a.fac
NETWORK_DEF lib/g10/roads_EU.net
OBJECT_DEF lib/airport/beacons/beacon_airport.obj
RASTER_DEF elevation
RASTER_DEF sea_level
DIVISIONS 32
HEIGHTS 0
RASTER_DATA version=1 bpp=2 flags=3 width=1201 height=1201 scale=1.0 offset=0.0 e.raw
BEGIN_PATCH 1 0.000000 -1.000000 1 7
BEGIN_PRIMITIVE 0
PATCH_VERTEX 5.000000000 43.000000000 12.000000 0.5 0.5 0.0 0.0
PATCH_VERTEX 5.000100000 43.000000000 12.000000 0.5 0.5 0.0 0.0
PATCH_VERTEX 5.000000000 43.000100000 12.000000 0.5 0.5 0.0 0.0
END_PRIMITIVE
END_PATCH
BEGIN_PATCH 1 0.000000 -1.000000 1 7
BEGIN_POLYGON 0 65535 2
BEGIN_WINDING
POLYGON_POINT 5.100000000 43.100000000
POLYGON_POINT 5.100100000 43.100000000
POLYGON_POINT 5.100000000 43.100100000
END_WINDING
END_POLYGON
BEGIN_POLYGON 1 255 2
BEGIN_WINDING
POLYGON_POINT 5.200000000 43.200000000
POLYGON_POINT 5.200100000 43.200000000
POLYGON_POINT 5.200000000 43.200100000
END_WINDING
END_POLYGON
BEGIN_POLYGON 3 0 4
BEGIN_WINDING
POLYGON_POINT 5.300000000 43.300000000 0 0
POLYGON_POINT 5.300100000 43.300000000 0 0
END_WINDING
END_POLYGON
BEGIN_SEGMENT 0 22001 1 5.400000000 43.400000000 0.000000
SHAPE_POINT 5.400100000 43.400100000 0.000000
END_SEGMENT 2 5.400200000 43.400200000 0.000000
BEGIN_SEGMENT 0 3 3 5.500000000 43.500000000 0.000000
END_SEGMENT 4 5.500200000 43.500200000 0.000000
OBJECT 0 5.600000000 43.600000000 90.000000
OBJECT_MSL 0 5.610000000 43.610000000 1.000000 90.000000
END_PATCH
"""


def _osxp(text: str, **kw: object) -> str:
    pieces, _stats = filter_dsf_bytes(text.encode("utf-8"), OverlayExclusions(**kw))
    return b"".join(pieces).decode("utf-8")


@pytest.mark.parametrize(
    "pol, net",
    [
        ([0], []),
        ([], []),
        ([0, ".for"], [22001]),
        (["!.for"], ["*"]),
        ([".ags", 3], [3]),
        ([""], []),
    ],
)
def test_filter_equals_the_ortho4xp_loop_when_objects_are_dropped(pol: list, net: list) -> None:
    """With ``keep_objects=False`` and a source without ``sim/overlay``, the OrthoStudio XP filter
    must give the same text as Ortho4XP for every exclusion grammar form (index, substring, ``!``,
    ``""``, ``"*"``, road type)."""
    reference = reference_select_overlays(SOURCE, pol, net)
    ours = _osxp(SOURCE, ovl_exclude_pol=pol, ovl_exclude_net=net, keep_objects=False)
    assert ours == reference


def test_default_keeps_objects_where_ortho4xp_dropped_them() -> None:
    """Documented deviation D3 (``overlays.md`` 6): the default ``keep_objects=True`` keeps
    ``OBJECT_DEF`` / ``OBJECT`` / ``OBJECT_MSL``; Ortho4XP dropped them. The XP12 Global Scenery
    tiles sampled hold 0 objects, so a comparison on them cannot see it; a source with objects
    gives a different overlay DSF than Ortho4XP."""
    reference = reference_select_overlays(SOURCE, [0], [])
    ours = _osxp(SOURCE, ovl_exclude_pol=[0], ovl_exclude_net=[])
    assert ours != reference
    extra = [ln for ln in ours.splitlines() if ln not in reference.splitlines()]
    assert extra == [
        "OBJECT_DEF lib/airport/beacons/beacon_airport.obj",
        "OBJECT 0 5.600000000 43.600000000 90.000000",
        "OBJECT_MSL 0 5.610000000 43.610000000 1.000000 90.000000",
    ]


def test_source_sim_overlay_property_is_dropped_where_ortho4xp_copied_it() -> None:
    """D4: a source ``PROPERTY sim/overlay 0`` line is copied by Ortho4XP (substring match) and
    dropped by OrthoStudio XP; DSFTool would otherwise see the property twice."""
    src = SOURCE.replace(
        "PROPERTY sim/planet earth\n", "PROPERTY sim/planet earth\nPROPERTY sim/overlay 0\n"
    )
    reference = reference_select_overlays(src, [0], [])
    ours = _osxp(src, ovl_exclude_pol=[0], ovl_exclude_net=[], keep_objects=False)
    assert reference.count("PROPERTY sim/overlay") == 2
    assert ours.count("PROPERTY sim/overlay") == 1 and ours.startswith("PROPERTY sim/overlay 1\n")


def test_by_name_default_equals_index_0_only_when_definition_0_is_the_beaches() -> None:
    """D7: the OrthoStudio XP default excludes ``lib/g12/beaches.bch`` by name. On a source whose
    definition 0 is not the beaches (an HD mesh, an XP11 source with another order) the two defaults
    diverge: Ortho4XP ``[0]`` drops definition 0, OrthoStudio XP drops the beaches wherever
    they are."""
    swapped = SOURCE.replace(
        "POLYGON_DEF lib/g12/beaches.bch\nPOLYGON_DEF lib/g8/fruit_tmp_sdry.for\n",
        "POLYGON_DEF lib/g8/fruit_tmp_sdry.for\nPOLYGON_DEF lib/g12/beaches.bch\n",
    )
    reference = reference_select_overlays(swapped, [0], [])
    ours = _osxp(swapped, keep_objects=False)  # default ovl_exclude_pol (by name)
    assert "BEGIN_POLYGON 0 " not in reference and "BEGIN_POLYGON 1 " in reference
    assert "BEGIN_POLYGON 0 " in ours and "BEGIN_POLYGON 1 " not in ours


# ------------------------------------------------------- wiring of the Ortho4XP settings in build


def _spec(tmp_path: Path, **config: object) -> BuildSpec:
    from orthostudio.model import TileRef

    return BuildSpec(TileRef(43, 5), "BI", 14, tmp_path, config=dict(config))


def test_overlay_exclusions_are_accepted_by_config_and_set(tmp_path: Path) -> None:
    """``ovl_exclude_pol`` / ``ovl_exclude_net`` are Ortho4XP *application* variables
    (``O4_Config_Utils.py:93-108``, ``list_app_vars``), not tile variables. P2a review finding
    (major): they were unreachable from ``osxp build``. Now ``BuildSpec.config`` and ``--set``
    accept them (typed like an Ortho4XP cfg line, no eval), ``BuildSpec`` has a field for each,
    and the overlay node is built from them (``overlay_settings``). The defaults are OrthoStudio
    XP's: an ``Ortho4XP.cfg`` is not read for them any more (decision 0010)."""
    from orthostudio.cli import _parse_sets

    assert _parse_sets(["ovl_exclude_net=[22001]", "ovl_exclude_pol=[0, '.for']"]) == {
        "ovl_exclude_net": [22001],
        "ovl_exclude_pol": [0, ".for"],
    }
    assert _parse_sets(["keep_objects=False"]) == {"keep_objects": False}
    with pytest.raises(OsxpError) as err:
        _parse_sets(["ovl_exclude_net=power"])
    assert err.value.code == "CFG_VALUE_INVALID"
    assert {f for f in BuildSpec.__dataclass_fields__ if "ovl" in f} == {
        "ovl_exclude_pol",
        "ovl_exclude_net",
    }
    cfg = _spec(tmp_path).tile_config()
    assert "ovl_exclude_net" not in cfg  # absent: the OrthoStudio XP default
    params = OverlayParams.subset_of({**cfg, "tile": "+43+005"})
    assert params.ovl_exclude_net == [] and params.keep_objects is True
    assert params.ovl_exclude_pol == list(OverlayExclusions().ovl_exclude_pol)


def test_overlay_exclusions_reach_the_overlay_node(tmp_path: Path) -> None:
    spec = _spec(tmp_path, ovl_exclude_net=[22001], ovl_exclude_pol=[".for"])
    cfg = spec.tile_config()
    params = OverlayParams.subset_of({**cfg, "tile": "+43+005"})
    assert params.ovl_exclude_net == [22001] and params.ovl_exclude_pol == [".for"]
