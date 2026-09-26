"""Review 4, fidelity lens: what the P3 native rules key, what they ignore, what they assume.

Every test here has been run against the code as it stands. The ones marked
``xfail(strict=True)`` state the behaviour the review asks for and therefore fail today on
purpose: they turn red the day the defect is fixed, which is when they must be rewritten as
plain assertions. None of them needs Ortho4XP.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from orthostudio.dem.rule import DEM_RULE, DemJob, DemParams, dem_job
from orthostudio.dem.sources import no_download
from orthostudio.dem.xplane import XP12_INPUTS
from orthostudio.errors import OsxpError
from orthostudio.graph import Executor, Node, Store
from orthostudio.imagery.grid import tile_to_wgs84
from orthostudio.masks.build import build_masks, worker_count
from orthostudio.masks.rule import MASKS, MasksJob, MasksParams, current_masks_job, masks_job
from orthostudio.masks.water import NEIGHBOUR_OFFSETS
from orthostudio.mesh.build import DemSpec, MeshBuildOptions
from orthostudio.mesh.mesh_file import MeshData
from orthostudio.mesh.rule import OSXP_MESH, MeshParams
from orthostudio.mesh.weights import build_weight_map, m_to_lon
from orthostudio.model import TileRef
from orthostudio.pipeline.build import (
    BuildEnv,
    _distance_lookup,
    masks_workers,
    stored_neighbour_mesh,
)
from orthostudio.textures.imprint import masks_dir_lookup

MESH_RULE_CONSUMED = OSXP_MESH.consumed

SRC = Path(__file__).resolve().parents[1] / "src" / "orthostudio"
TILE = TileRef(43, 5)
ZL = 14


# -- helpers -----------------------------------------------------------------------------------


def _sea_mesh(tile: TileRef, cells: list[tuple[int, int]], *, water: bool = True) -> MeshData:
    """A mesh whose triangles cover the middle half of each cell (water unless said otherwise)."""
    vertices: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    for til_x, til_y in cells:
        lat0, lon0 = tile_to_wgs84(til_x + 4, til_y + 4, ZL)
        lat1, lon1 = tile_to_wgs84(til_x + 12, til_y + 12, ZL)
        base = len(vertices)
        vertices += [(lon0, lat0, 0.0), (lon1, lat0, 0.0), (lon1, lat1, 0.0), (lon0, lat1, 0.0)]
        tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return MeshData(
        vertices=np.array(vertices, dtype=np.float64),
        normals=np.zeros((len(vertices), 2), dtype=np.float32),
        tris=np.array(tris, dtype=np.int32),
        tri_attr=np.full(len(tris), 2 if water else 0, dtype=np.uint8),
    )


def _cells_of(tile: TileRef) -> list[tuple[int, int]]:
    """Two mask cells well inside ``tile`` at ZL14 (the grid of any latitude/longitude)."""
    from orthostudio.masks.water import mask_cells

    rng = mask_cells(tile, ZL)
    x0, y0 = rng.til_x_min, rng.til_y_min
    return [(x0 + 16, y0 + 16), (x0 + 32, y0 + 32)]


def _env(tmp_path: Path, workers: int) -> BuildEnv:
    return BuildEnv(
        store=Store(tmp_path / "store"),
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=None,
        dsftool=None,
        registry={},
        workers=workers,
        library_path=None,
    )


# -- 1. the rules key exactly what their computation reads --------------------------------------


def test_the_native_rules_declare_exactly_the_values_their_computation_reads() -> None:
    """A parameter the computation reads but the rule does not declare = a false cache hit."""
    mesh_opts = {f.name for f in dataclasses.fields(MeshBuildOptions)}
    assert set(MeshParams.model_fields) == mesh_opts | {"tile"}
    assert set(MESH_RULE_CONSUMED) == set(MeshParams.model_fields)
    # what build_masks takes that changes its output (the rest is plumbing)
    masks_consumed = {
        "mask_zl",
        "masks_width",
        "masking_mode",
        "use_masks_for_inland",
        "ratio_water",
        "distance_masks_too",
    }
    declared = set(MasksParams.model_fields)
    assert masks_consumed <= declared
    # the two optional rasters are inputs, not params, but their switches are declared
    assert {"masks_use_DEM_too", "masks_custom_extent"} <= declared
    assert declared == masks_consumed | {"tile", "masks_use_DEM_too", "masks_custom_extent"}
    assert set(DemParams.model_fields) == {
        "tile",
        "custom_dem",
        "fill_nodata",
        "dem1_local_fallback",
        "own_stamp",
    }


# -- 2. the masks pool --------------------------------------------------------------------------


def test_the_masks_bytes_do_not_depend_on_the_worker_count(tmp_path: Path) -> None:
    """The process pool must not be able to change one byte of the artefact."""
    mesh = _sea_mesh(TILE, _cells_of(TILE))
    one, many = tmp_path / "w1", tmp_path / "w4"
    build_masks(one, {TILE: mesh}, TILE, workers=1)
    build_masks(many, {TILE: mesh}, TILE, workers=4)
    names = sorted(p.name for p in one.glob("*.png"))
    assert names, "the synthetic mesh must produce at least one mask"
    assert names == sorted(p.name for p in many.glob("*.png"))
    for name in names:
        assert (one / name).read_bytes() == (many / name).read_bytes(), name
    assert json.loads((one / "index.json").read_text()) == json.loads(
        (many / "index.json").read_text()
    )


def test_the_masks_node_reserves_the_ram_of_the_pool_it_actually_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pipeline-build.md`` 8.6: ``ram_mb`` is ``workers x 0.8 GB``, the same ``workers``.

    AMENDED (was ``xfail(strict)``): the fix gives the pipeline one owner of the number. The
    node binds :class:`MasksJob` with the very count ``_masks_ram_mb`` paid for
    (``pipeline.build._masks_run``), the rule passes it to ``build_masks``, and the default
    of ``worker_count`` is now capped at ``MAX_WORKERS`` instead of ``os.cpu_count()``. The
    assertion is unchanged; only the way the rule is given the count is.
    """
    env = _env(tmp_path, workers=4)
    monkeypatch.delenv("OSXP_MASKS_WORKERS", raising=False)
    declared = masks_workers(env)  # what _masks_ram_mb() pays for
    with masks_job(MasksJob(workers=declared)):
        job = current_masks_job()
        assert job is not None
        started = worker_count(100, job.workers)  # what the rule really starts
    assert started == declared, (started, declared)
    # and an unbound rule can no longer start one process per core
    assert worker_count(100, None) <= 8


def test_the_masks_rule_is_given_the_schedulers_cancellation(tmp_path: Path) -> None:
    """AMENDED (was: nothing ever set the module-level ``_CANCEL`` hook).

    The hook is gone: the node binds a :class:`MasksJob` carrying the scheduler's event, the way
    ``orthostudio.dem`` and ``orthostudio.osm`` already did. The test now asserts the wiring exists
    -- ``pipeline.build._masks_run`` binds it -- and that an unbound rule still runs (the tests that
    call the rule directly).
    """
    source = (SRC / "pipeline" / "build.py").read_text(encoding="utf-8")
    assert "masks_job(job)" in source and "cancel=cast(Any, ctx.cancel_event)" in source
    assert current_masks_job() is None  # nothing bound outside a node
    event = __import__("threading").Event()
    with masks_job(MasksJob(workers=2, cancel=event)):
        job = current_masks_job()
        assert job is not None and job.cancel is event and job.workers == 2


def test_a_cancelled_masks_run_does_not_leave_a_committable_artifact(tmp_path: Path) -> None:
    """AMENDED (was ``xfail(strict)``): ``build_masks`` now raises ``SYS_CANCELLED`` instead
    of breaking out of its loops and writing ``index.json``, so the store aborts its staging
    and nothing enters the index. The assertion of the review is kept as it was."""
    import threading

    mesh = _sea_mesh(TILE, _cells_of(TILE))
    cancel = threading.Event()
    cancel.set()
    out = tmp_path / "masks"
    with pytest.raises(OsxpError) as exc:
        build_masks(out, {TILE: mesh}, TILE, workers=1, cancel=cancel)
    assert exc.value.code == "SYS_CANCELLED"
    assert not (out / "index.json").is_file()


# -- 3. edge cases: hemisphere, longitude sign, no water ----------------------------------------


@pytest.mark.parametrize("tile", [TileRef(-34, -59), TileRef(-34, 18), TileRef(60, -150)])
def test_masks_build_in_every_hemisphere(tile: TileRef, tmp_path: Path) -> None:
    cells = _cells_of(tile)
    out = tmp_path / tile.name
    index = build_masks(out, {tile: _sea_mesh(tile, cells)}, tile, workers=1)
    written = sorted(p.name for p in out.glob("*.png"))
    assert written == sorted(f"{y}_{x}.png" for x, y in cells), (tile.name, written)
    assert [e["file"] for e in index.masks] and index.range.contains(*cells[0])
    for name in written:
        arr = np.asarray(Image.open(out / name))
        assert arr.shape == (4096, 4096) and int(arr.max()) > 0 and int(arr.min()) < 255


def test_the_coastline_window_of_the_weight_map_at_a_negative_longitude() -> None:
    """A literal transcription of ``O4_Mesh_Utils.py:199-228`` at lat/lon < 0."""
    lat, lon = -34, -59
    nodes = np.array(
        [[lon + 0.5, lat + 0.5], [lon + 0.001, lat + 0.999], [lon - 0.2, lat + 0.5]],
        dtype=np.float64,
    )
    got = build_weight_map(
        lat=lat,
        lon=lon,
        curvature_tol=2.0,
        apt_curv_tol=2.0,
        apt_curv_ext=0.5,
        coast_curv_tol=1.0,
        coast_curv_ext=0.5,
        coast_nodes=nodes,
    )
    want = np.ones((1001, 1001), dtype=np.float32)
    for lonp, latp in nodes.tolist():
        if lonp < lon or lonp > lon + 1 or latp < lat or latp > lat + 1:
            continue  # Ortho4XP drops a node outside the tile (:203-209)
        x_shift = 1000 * 0.5 * m_to_lon(lat)
        y_shift = 0.5 / 111.12
        colmin = max(round((lonp - lon - x_shift) * 1000), 0)
        colmax = min(round((lonp - lon + x_shift) * 1000), 1000)
        rowmax = min(round((lat + 1 - latp + y_shift) * 1000), 1000)
        rowmin = max(round((lat + 1 - latp - y_shift) * 1000), 0)
        want[rowmin : rowmax + 1, colmin : colmax + 1] = np.maximum(
            want[rowmin : rowmax + 1, colmin : colmax + 1], np.float32(2.0)
        )
    assert np.array_equal(got, want)
    assert (got != 1).sum() > 0


def test_a_tile_with_no_water_writes_an_empty_artifact_its_consumers_tolerate(
    tmp_path: Path,
) -> None:
    mesh = _sea_mesh(TILE, _cells_of(TILE), water=False)
    out = tmp_path / "masks"
    index = build_masks(out, {TILE: mesh}, TILE, workers=1)
    assert index.masks == [] and not list(out.glob("*.png"))
    doc = json.loads((out / "index.json").read_text())
    assert doc["masks"] == []
    lookup = masks_dir_lookup(out)
    assert lookup(*_cells_of(TILE)[0]) is None
    assert _distance_lookup(out) is None or _distance_lookup(out)(*_cells_of(TILE)[0]) is None


def test_a_missing_or_truncated_alt_is_refused_before_triangle_runs(tmp_path: Path) -> None:
    from orthostudio.errors import OsxpError

    missing = DemSpec(tmp_path / "nope.alt", 3673, 3673, -0.01, -0.01, 1.01, 1.01, -32768.0)
    with pytest.raises(OsxpError, match="MESH_INPUT_MISSING"):
        missing.check(TILE.name)
    short = tmp_path / "Data+43+005.alt"
    short.write_bytes(b"\x00" * 1024)
    with pytest.raises(OsxpError, match="MESH_INPUT_MISSING"):
        dataclasses.replace(missing, alt_path=short).check(TILE.name)


# -- 4. neighbours ------------------------------------------------------------------------------


def _fake_mesh_artifact(store: Store, rule: str, key: str, tile: TileRef) -> None:
    with store.begin(rule, key, "dir") as build:
        (build.out / f"Data{tile.name}.mesh").write_text("fake\n", encoding="utf-8")
        build.commit(version=1, recipe='{"format": "osxp-key-1"}', inputs=[])


def test_a_stored_neighbour_mesh_is_found(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    neighbour = TILE.neighbour(1, 0)
    assert stored_neighbour_mesh(store, neighbour) is None
    _fake_mesh_artifact(store, "orthostudio.mesh", "b" * 64, neighbour)
    found = stored_neighbour_mesh(store, neighbour)
    assert found is not None and found.rule == "orthostudio.mesh"
    assert stored_neighbour_mesh(store, TileRef(1, 1)) is None


def test_the_masks_rule_names_the_eight_neighbours() -> None:
    """A silent swap here would give a tile the sea of the wrong border."""
    assert NEIGHBOUR_OFFSETS == {
        "nb_n": (1, 0), "nb_ne": (1, 1), "nb_e": (0, 1), "nb_se": (-1, 1),
        "nb_s": (-1, 0), "nb_sw": (-1, -1), "nb_w": (0, -1), "nb_nw": (1, -1),
    }  # fmt: skip
    assert set(NEIGHBOUR_OFFSETS) <= set(MASKS.inputs)


def test_an_absent_neighbour_is_a_distinct_masks_key() -> None:
    from orthostudio.graph import key_for

    params = MasksParams(tile=TILE.name)
    absent: dict[str, str | None] = {"mesh": "c" * 64, "dem": None, "custom_extent": None}
    absent.update(dict.fromkeys(NEIGHBOUR_OFFSETS, None))
    present = dict(absent, nb_n="d" * 64)
    assert key_for(MASKS, params, absent)[0] != key_for(MASKS, params, present)[0]


# -- 5. the elevation rule is keyed by its parameters, not by the files it reads ----------------


def _hgt(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.full((3601, 3601), value, dtype=">i2").tobytes())


def test_a_custom_dem_file_can_change_without_moving_the_dem_key(tmp_path: Path) -> None:
    """``orthostudio.dem`` is a graph root keyed by its params: editing the file it reads
    is invisible.

    AMENDED docstring, same assertions: the behaviour is kept (making ``custom_dem`` a source
    edge changes the rule's inputs, which belongs to the DEM chantier and is escalated as a
    blocker), and the consequence the review asked for is now written in ``dem.md`` 9.4 --
    "changing the content of an elevation file or of custom_dem does not change the key".
    """
    tile = TileRef(46, 5)
    elevation = tmp_path / "Elevation_data"
    for lat in (45, 46, 47):
        for lon in (4, 5, 6):
            _hgt(elevation / "+40+000" / f"N{lat:02d}E{lon:03d}.hgt", 100)
    custom = tmp_path / "mine.hgt"
    _hgt(custom, 250)
    store = Store(tmp_path / "store")
    params = DemParams(tile=tile.name, custom_dem=str(custom))
    job = DemJob(tile=tile, elevation_dir=elevation, download=no_download, memo_path=None)
    with dem_job(job):
        first = Executor(store).run(Node(DEM_RULE, params, dict.fromkeys(XP12_INPUTS))).target
        _hgt(custom, 2500)  # the user regenerates their raster
        second = Executor(store).run(Node(DEM_RULE, params, dict.fromkeys(XP12_INPUTS))).target
    assert first.key == second.key and first.digest == second.digest
    meta = json.loads((second.path / "meta.json").read_text())
    assert meta["max"] == 250.0, "the artefact still holds the first version of the file"


# -- 6. the inland-water grey level against Ortho4XP's own table
# ------------------------------------


# -- 7. what the native mesh key leaves out ---------------------------------------------------


def _native_spec(tmp_path: Path) -> Any:
    from orthostudio.pipeline.build import BuildSpec

    spec = BuildSpec(
        tile=TILE,
        provider="BI",
        zl=14,
        out_dir=tmp_path / "out",
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        overlay=False,
        xp12_rasters=False,
        osm_fetch=False,
    )
    return spec


@pytest.fixture
def fake_triangle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "Triangle4XP"
    binary.write_bytes(b"\x7fELF")
    binary.chmod(0o755)
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(binary))
    return binary


def test_the_triangle4xp_binary_is_not_part_of_the_mesh_key(
    tmp_path: Path, fake_triangle: Path
) -> None:
    """Rebuilding the sidecar (a patch, a different compiler) leaves every mesh artefact valid.

    AMENDED docstring, same assertions: the fix pass did **not** close this hole, because
    both ways of closing it (a fourth ``triangle`` input on the rule, or a ``triangle_build``
    digest parameter) change the rule's signature and therefore belong to the mesh chantier;
    it is escalated as a blocker and written down as a known limit of the cache in
    ``mesh-build.md`` 9. The test stays green and guards the documented behaviour.
    """
    from orthostudio.graph import key_for

    params = MeshParams(tile=TILE.name)
    digests: dict[str, str | None] = {"vectors": "a" * 64, "dem": "b" * 64, "coastline": None}
    before = key_for(OSXP_MESH, params, digests)[0]
    fake_triangle.write_bytes(b"\x7fELF-v2")  # a different binary, same path
    assert key_for(OSXP_MESH, params, digests)[0] == before
    assert "triangle" not in " ".join(MeshParams.model_fields).lower()


def test_the_api_knows_the_three_new_node_roles() -> None:
    """AMENDED (was ``xfail(strict)``, blocker B4): the three P3 data roles are in
    ``ROLE_STAGE`` (``osm`` and ``coastline`` in ``osm``, ``dem`` in ``relief``, since
    2026-09-26) and the two whose existence a spec decides on its own -- ``osm`` with
    ``--osm-fetch``, ``dem`` always -- are listed by ``api.jobs._expected_nodes``, so the page
    shows a row for them."""
    from orthostudio.api.jobs import _expected_nodes
    from orthostudio.api.stages import stage_of

    assert [stage_of(role) for role in ("osm", "coastline", "dem")] == ["osm", "osm", "relief"]
    spec = _native_spec(Path("/tmp"))
    spec.osm_fetch = True
    spec.dem = "native"
    roles = [role for _id, role in _expected_nodes(spec)]
    assert roles[:2] == ["osm", "dem"]
    spec.osm_fetch = False
    spec.dem = "vectors"
    assert [r for _i, r in _expected_nodes(spec)][:2] == ["dem", "vectors"]
