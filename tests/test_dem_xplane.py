"""X-Plane 12's relief (``custom_dem = "XP12"``, decision 0007): reader, assembly, rule, wiring.

Spec: ``docs/specs/dem.md`` section 12. Found flying ``+46+006``: the ``dem1`` archives of
viewfinderpanoramas answer 404, the tile's own cell degraded to 0 m, and the Jura was flat.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from orthostudio.dem.dem import Dem
from orthostudio.dem.raster import NODATA, build_combined_raster, resample_posts, shared_border
from orthostudio.dem.rule import DEM_RULE, DemJob, DemParams, dem_job
from orthostudio.dem.sources import (
    CellState,
    EnsureOptions,
    NegativeMemo,
    elevation_path,
    no_download,
)
from orthostudio.dem.xplane import XP12_INPUTS, XP12_SOURCE, elevation_posts, read_elevation_posts
from orthostudio.dsf.xp12 import global_scenery_dsf
from orthostudio.errors import OsxpError
from orthostudio.graph import Executor, Node, Source, Store, key_for
from orthostudio.model import TileRef
from orthostudio.pipeline.build import (
    RELIEF_SOURCES,
    BuildSpec,
    default_relief,
    dem_declaration,
    source_ref,
    stage_choices,
)

GLOBAL_SCENERY = Path(
    os.environ.get(
        "OSXP_GLOBAL_SCENERY",
        str(Path.home() / "X-Plane 12" / "Global Scenery" / "X-Plane 12 Global Scenery"),
    )
)
GERMANY = TileRef(50, 10)
"""A land block of 3" viewfinderpanoramas cells (``M32``): the View path reads 1201 posts."""


# -- synthetic Global Scenery -------------------------------------------------------------


def _atom(name: str, payload: bytes) -> bytes:
    return name.encode()[::-1] + struct.pack("<I", 8 + len(payload)) + payload


def xp12_dsf(
    south_up: np.ndarray,
    *,
    names: bytes = b"elevation\0sea_level\0",
    flags: int = 5,
    scale: float = 1.0,
    offset: float = 0.0,
) -> bytes:
    """A minimal Global Scenery DSF: DEMN plus an elevation and a sea_level raster."""
    h, w = south_up.shape
    info = _atom("DEMI", struct.pack("<BBHIIff", 1, 2, flags, w, h, scale, offset))
    dems = info + _atom("DEMD", south_up.astype("<i2").tobytes())
    dems += info + _atom("DEMD", np.zeros_like(south_up).astype("<i2").tobytes())
    defn = _atom("TERT", b"terrain_Water\0") + _atom("DEMN", names)
    body = b"XPLNEDSF" + struct.pack("<I", 1) + _atom("HEAD", _atom("PROP", b"sim/west\x005\x00"))
    body += _atom("DEFN", defn) + _atom("GEOD", b"") + _atom("CMDS", b"") + _atom("DEMS", dems)
    return body + hashlib.md5(body).digest()


def _write_dsf(root: Path, cell: TileRef, north_up: np.ndarray) -> Path:
    path = global_scenery_dsf(root, cell)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(xp12_dsf(north_up[::-1]))
    return path


def _block(root: Path, center: TileRef, value: Any, *, skip: tuple[TileRef, ...] = ()) -> None:
    for dlat in (-1, 0, 1):
        for dlon in (-1, 0, 1):
            cell = center.neighbour(dlat, dlon)
            if cell not in skip:
                _write_dsf(root, cell, np.full((1201, 1201), value(cell), dtype=np.int16))


def _opts(tmp_path: Path, gs: Path | None) -> EnsureOptions:
    return EnsureOptions(
        elevation_dir=tmp_path / "Elevation_data",
        download=no_download,
        memo=NegativeMemo(),
        global_scenery_dir=gs,
    )


# -- reading ------------------------------------------------------------------------------


def test_the_reader_turns_the_south_up_posts_north_up() -> None:
    south_up = np.arange(12, dtype=np.int16).reshape(3, 4)
    alt = elevation_posts(xp12_dsf(south_up), tile=GERMANY)
    assert alt.dtype == np.float32
    assert np.array_equal(alt, south_up[::-1].astype(np.float32))


def test_the_reader_applies_scale_and_offset_and_keeps_voids() -> None:
    south_up = np.array([[0, 10], [-32768, 20]], dtype=np.int16)
    alt = elevation_posts(xp12_dsf(south_up, scale=0.5, offset=100.0), tile=GERMANY)
    assert alt.tolist() == [[NODATA, 110.0], [100.0, 105.0]]


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"names": b"sea_level\0bathymetry\0"}, "no 'elevation' raster"),
        ({"flags": 1}, "pixel-centred"),
        ({"names": b"elevation\0"}, "1 raster names, 2 DEMI"),
    ],
)
def test_a_dsf_without_a_usable_elevation_raster_is_corrupted(kw: Any, reason: str) -> None:
    with pytest.raises(OsxpError) as excinfo:
        elevation_posts(xp12_dsf(np.zeros((3, 3), np.int16), **kw), tile=GERMANY)
    assert excinfo.value.code == "DSF_SOURCE_CORRUPTED"
    assert reason in excinfo.value.context["reason"]


def test_other_post_counts_are_bilinear_on_the_same_square() -> None:
    posts = np.array([[0, 10, 20], [30, 40, 50], [60, 70, 80]], dtype=np.float32)
    out = resample_posts(posts, 3601)
    assert (out[0, 0], out[0, -1], out[-1, 0], out[-1, -1]) == (0, 20, 60, 80)
    assert out[1800, 1800] == 40
    assert out[0, 900] == pytest.approx(5.0)
    assert out[900, 0] == pytest.approx(15.0)


# -- assembly -----------------------------------------------------------------------------


def test_the_xplane_block_is_bit_identical_to_the_same_posts_read_as_hgt(tmp_path: Path) -> None:
    """Same 1201 posts, once as nine ``.hgt`` files and once as nine DSFs: the same raster.

    Every border line is shared exactly here, so the shared border changes nothing.
    """
    rng = np.random.default_rng(7)
    gs = tmp_path / "gs"
    base = rng.integers(200, 4000, (3 * 1200 + 1, 3 * 1200 + 1)).astype(np.int16)
    for dlat in (-1, 0, 1):
        for dlon in (-1, 0, 1):
            cell = GERMANY.neighbour(dlat, dlon)
            r0, c0 = (1 - dlat) * 1200, (dlon + 1) * 1200
            north_up = base[r0 : r0 + 1201, c0 : c0 + 1201]
            hgt = elevation_path("View", tmp_path / "Elevation_data", cell.lat, cell.lon)
            hgt.parent.mkdir(parents=True, exist_ok=True)
            hgt.write_bytes(north_up.astype(">i2").tobytes())
            _write_dsf(gs, cell, north_up)
    view = build_combined_raster("View", GERMANY.lat, GERMANY.lon, _opts(tmp_path, None))
    xp12 = build_combined_raster(XP12_SOURCE, GERMANY.lat, GERMANY.lon, _opts(tmp_path, gs))
    assert all(c.state is CellState.LOCAL for c in view.cells)
    assert all(c.state is CellState.LOCAL for c in xp12.cells)
    assert xp12.alt_dem.tobytes() == view.alt_dem.tobytes()


def test_a_missing_land_neighbour_is_reported_and_the_sea_is_ocean(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    marseille = TileRef(43, 5)
    _block(gs, marseille, lambda c: 100, skip=(TileRef(42, 5), TileRef(44, 5)))
    events: list[OsxpError] = []
    combined = build_combined_raster(
        XP12_SOURCE, 43, 5, _opts(tmp_path, gs), on_event=events.append
    )
    states = {c.cell: c.state for c in combined.cells}
    assert states["N42E005"] is CellState.OCEAN
    assert states["N44E005"] is CellState.MISSING
    assert states["N43E005"] is CellState.LOCAL
    assert np.all(combined.alt_dem[:36, 36:-36] == 0)  # the missing north margin
    codes = [e.code for e in events]
    assert "DEM_CELL_ASSUMED_OCEAN" in codes and "DEM_NEIGHBOUR_UNAVAILABLE" in codes


def test_a_dsf_wins_over_the_world_bitmap(tmp_path: Path) -> None:
    """``world_tiles`` calls ``+42+005`` open sea; a DSF there is still read."""
    gs = tmp_path / "gs"
    _block(gs, TileRef(43, 5), lambda c: 7)
    combined = build_combined_raster(XP12_SOURCE, 43, 5, _opts(tmp_path, gs))
    states = {c.cell: c.state for c in combined.cells}
    assert states["N42E005"] is CellState.LOCAL
    assert np.allclose(combined.alt_dem[-36:, 36:-36], 7, atol=1e-4)  # Ortho4XP's upsampling


def test_no_dsf_for_the_tile_itself_is_refused(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    _block(gs, GERMANY, lambda c: 300, skip=(GERMANY,))
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(GERMANY, _opts(tmp_path, gs), custom_dem=XP12_SOURCE)
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    assert excinfo.value.context["source"] == XP12_SOURCE
    assert excinfo.value.context["cell"] == "N50E010"


def test_an_unusable_dsf_for_the_tile_itself_is_refused(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    _block(gs, GERMANY, lambda c: 300)
    global_scenery_dsf(gs, GERMANY).write_bytes(b"XPLNEDSF not really")
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(GERMANY, _opts(tmp_path, gs), custom_dem=XP12_SOURCE)
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    assert "DSF_SOURCE_CORRUPTED" in excinfo.value.context["reason"]


# -- shared border ------------------------------------------------------------------------


def _posts(value: float, n: int = 5) -> np.ndarray:
    return np.full((n, n), value, dtype=np.float32)


def test_a_border_line_is_the_mean_of_the_two_tiles_and_a_corner_of_the_four() -> None:
    posts = {
        (0, 0): _posts(100),
        (0, 1): _posts(140),  # east
        (1, 0): _posts(60),  # north
        (1, 1): _posts(0),  # north-east
    }
    out = shared_border(posts)
    assert np.all(out[1:-1, -1] == 120)  # east line: (100 + 140) / 2
    assert np.all(out[0, 1:-1] == 80)  # north line: (100 + 60) / 2
    assert out[0, -1] == 75  # north-east corner: (100 + 140 + 60 + 0) / 4
    assert out[0, 0] == 80  # north-west corner: the west tiles have no DSF
    assert np.all(out[1:-1, 1:-1] == 100)  # the inside is the tile's own
    assert np.all(out[-1, :-1] == 100) and np.all(out[1:, 0] == 100)


def test_two_adjacent_tiles_compute_the_same_border() -> None:
    """Built from either side, the shared line is the same, to the bit."""
    rng = np.random.default_rng(3)
    grid = {
        (r, c): rng.normal(1000, 300, (7, 7)).astype(np.float32) for r in range(3) for c in range(4)
    }

    def around(r: int, c: int) -> dict[tuple[int, int], np.ndarray]:
        out = {}
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                key = (r - dlat, c + dlon)  # grid rows run north to south
                if key in grid:
                    out[(dlat, dlon)] = grid[key]
        return out

    west = shared_border(around(1, 1))
    east = shared_border(around(1, 2))
    south = shared_border(around(2, 1))
    assert west[:, -1].tobytes() == east[:, 0].tobytes()
    assert west[-1, :].tobytes() == south[0, :].tobytes()


# -- rule ---------------------------------------------------------------------------------


def _sources(gs: Path, center: TileRef) -> dict[str, Source | None]:
    out: dict[str, Source | None] = {}
    for name, (dlat, dlon) in XP12_INPUTS.items():
        path = global_scenery_dsf(gs, center.neighbour(dlat, dlon))
        out[name] = Source.from_path(path) if path.is_file() else None
    return out


def test_the_rule_reads_only_its_inputs_and_is_keyed_by_them(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    _block(gs, GERMANY, lambda c: (c.lat - 49) * 100 + (c.lon - 9) * 10)
    inputs = _sources(gs, GERMANY)
    inputs["xp12_n"] = None  # its DSF exists on disk, but the node does not declare it
    params = DemParams(tile=GERMANY.name, custom_dem=XP12_SOURCE)
    job = DemJob(
        tile=GERMANY,
        elevation_dir=tmp_path / "Elevation_data",
        download=no_download,
        memo_path=None,
        global_scenery_dir=gs,
    )
    with dem_job(job):
        out = Executor(Store(tmp_path / "store")).run(Node(DEM_RULE, params, inputs)).target
    meta = json.loads((out.path / "meta.json").read_text())
    assert meta["source"] == XP12_SOURCE
    states = {c["cell"]: c["state"] for c in meta["cells"]}
    assert states["N51E010"] == "missing"
    assert states["N50E010"] == "local"
    dem = Dem.load(out.path)
    assert dem.alt((0.5, 0.5)) == pytest.approx(110.0)

    digests = {k: (v.digest if v else None) for k, v in inputs.items()}
    reference = key_for(DEM_RULE, params, digests)[0]
    _write_dsf(gs, TileRef(51, 11), np.full((1201, 1201), 999, dtype=np.int16))
    moved = {**digests, "xp12_ne": Source.from_path(global_scenery_dsf(gs, TileRef(51, 11))).digest}
    assert key_for(DEM_RULE, params, moved)[0] != reference
    view = DemParams(tile=GERMANY.name)
    assert key_for(DEM_RULE, view, dict.fromkeys(XP12_INPUTS))[0] != reference


# -- pipeline -----------------------------------------------------------------------------


def _spec(tmp_path: Path, **kw: Any) -> BuildSpec:
    return BuildSpec(
        tile=GERMANY,
        provider="BI",
        zl=16,
        out_dir=tmp_path / "out",
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        **kw,
    )


def _global_source(gs: Path) -> Any:
    def source(tile: TileRef) -> Any:
        path = global_scenery_dsf(gs, tile)
        return source_ref(path, label="global_scenery") if path.is_file() else None

    return source


def test_the_default_relief_is_xplane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OSXP_RELIEF", raising=False)
    assert default_relief() == "xplane"
    assert _spec(tmp_path).relief == "xplane"
    monkeypatch.setenv("OSXP_RELIEF", "View")
    assert _spec(tmp_path).relief == "view"
    assert RELIEF_SOURCES == ("xplane", "view")


def test_the_pipeline_declares_the_nine_dsfs(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    _block(gs, GERMANY, lambda c: 300, skip=(TileRef(51, 11),))
    params, inputs = dem_declaration(
        _spec(tmp_path, relief="xplane"), {"custom_dem": ""}, _global_source(gs), gs
    )
    assert params["custom_dem"] == XP12_SOURCE and params["tile"] == GERMANY.name
    assert set(inputs) == set(XP12_INPUTS)
    center = inputs["xp12"]
    assert center is not None and getattr(center, "path", None) == global_scenery_dsf(gs, GERMANY)
    assert inputs["xp12_ne"] is None
    assert sum(v is not None for v in inputs.values()) == 8


def test_view_and_an_explicit_custom_dem_declare_no_dsf(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    _block(gs, GERMANY, lambda c: 300)
    for spec, cfg in (
        (_spec(tmp_path, relief="view"), {"custom_dem": ""}),
        (_spec(tmp_path, relief="xplane"), {"custom_dem": "/data/mine.tif"}),
    ):
        params, inputs = dem_declaration(spec, cfg, _global_source(gs), gs)
        assert params["custom_dem"] == cfg["custom_dem"]
        assert all(v is None for v in inputs.values())


def test_the_pipeline_refuses_a_tile_x_plane_has_no_dsf_for(tmp_path: Path) -> None:
    gs = tmp_path / "gs"
    (gs / "Earth nav data").mkdir(parents=True)
    with pytest.raises(OsxpError) as excinfo:
        dem_declaration(_spec(tmp_path, relief="xplane"), {}, _global_source(gs), gs)
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    assert "--relief view" in excinfo.value.remedy
    tile = _spec(tmp_path, relief="xplane").tile
    assert excinfo.value.context["tile"] == tile.name
    assert excinfo.value.context["path"] == str(global_scenery_dsf(gs, tile))  # the file named
    with pytest.raises(OsxpError, match="no X-Plane 12 Global Scenery folder"):
        dem_declaration(_spec(tmp_path, relief="xplane"), {}, lambda t: None, None)


def test_x_planes_relief_does_not_queue_on_the_network_slot(tmp_path: Path) -> None:
    """The elevations of a batch queued one after the other on the imagery's network slot, and
    each vector stage waited for its own: X-Plane's relief, read from its DSFs, runs in a
    subprocess slot; a downloaded relief still takes the network slot."""
    from orthostudio.pipeline.build import BuildEnv, declare, make_scheduler

    gs = tmp_path / "gs"
    _block(gs, GERMANY, lambda c: 300)
    for relief, kind in (("xplane", "subprocess"), ("view", "net")):
        spec = _spec(tmp_path, relief=relief, global_scenery_dir=gs, osm_fetch=False)
        spec.overlay = spec.xp12_rasters = False
        env = BuildEnv.create([spec])
        (g,) = declare([spec], make_scheduler(env.store, env), env, planning=True)
        assert g.dem is not None and g.dem.kind == kind, relief


def test_an_unknown_relief_is_refused_before_anything_runs(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as excinfo:
        stage_choices([_spec(tmp_path, relief="copernicus")])
    assert excinfo.value.code == "CFG_VALUE_INVALID"
    assert excinfo.value.context["name"] == "relief"


# -- the user's X-Plane 12 ----------------------------------------------------------------


@pytest.mark.skipif(
    not (GLOBAL_SCENERY / "Earth nav data").is_dir(), reason="no X-Plane 12 Global Scenery"
)
def test_geneva_from_the_real_global_scenery(tmp_path: Path) -> None:
    tile = TileRef(46, 6)
    events: list[OsxpError] = []
    dem = Dem.build(
        tile, _opts(tmp_path, GLOBAL_SCENERY), custom_dem=XP12_SOURCE, on_event=events.append
    )
    assert all(c.state is CellState.LOCAL for c in dem.cells)
    posts = read_elevation_posts(global_scenery_dsf(GLOBAL_SCENERY, tile), 46, 6)
    inside = dem.alt_dem[36:-36, 36:-36]
    # X-Plane's own posts, wherever they are not on a shared border
    assert np.array_equal(inside[3:-3:3, 3:-3:3], posts[1:-1, 1:-1])
    assert 330 <= inside.min() <= 345 and 3200 <= inside.max() <= 3240
    assert dem.alt((0.109, 0.2381)) == pytest.approx(416, abs=15)  # LSGG
    assert dem.alt((0.0994, 0.4254)) == pytest.approx(1666, abs=60)  # La Dole
    # the east border with +46+007 is the mean of the two rasters
    east = read_elevation_posts(global_scenery_dsf(GLOBAL_SCENERY, TileRef(46, 7)), 46, 7)
    mean = (posts[1:-1, -1].astype(np.float64) + east[1:-1, 0]) / 2
    assert np.array_equal(inside[3:-3:3, -1], mean.astype(np.float32))
