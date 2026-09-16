"""Adversarial review of P3 (native stages): cancellation, pools, RAM, atomicity, errors.

Written to assert what the code did *before* the fix pass, so every fix flipped an assertion.
The tests below were therefore AMENDED after the corrections: each docstring says what the
finding was, what the fix does and what the test now guards. Nothing is written outside
``tmp_path``; no network, no Ortho4XP.
"""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import orthostudio.masks.rule as masks_rule
import orthostudio.pipeline.native as native
from orthostudio.dem.rule import DemJob
from orthostudio.dem.sources import CellState, EnsureOptions, NegativeMemo, ensure_elevation
from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, RunContext, Store
from orthostudio.imagery.grid import tile_to_wgs84
from orthostudio.masks.build import build_masks, worker_count
from orthostudio.masks.rule import MASKS, MasksParams
from orthostudio.mesh.build import build_mesh_native
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz
from orthostudio.model import TileRef
from orthostudio.pipeline import build as buildmod
from orthostudio.pipeline.build import BuildEnv, BuildSpec, declare, make_scheduler, run_osm_phase
from orthostudio.sched import Node, Scheduler
from orthostudio.sources.osm import (
    LAYERS,
    OsmNode,
    OsmSnapshot,
    OsmWay,
    layers_for,
)

SRC = Path(buildmod.__file__).resolve().parents[1]
TILE = TileRef(43, 5)
ZL = 14


# -- fixtures shared with the masks tests -------------------------------------------------


def _sea_mesh(cells: list[tuple[int, int]]) -> MeshData:
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
        tri_attr=np.full(len(tris), 2, dtype=np.uint8),
    )


def _snapshot(layer: str, lon: float = 5.0) -> OsmSnapshot:
    ns = (OsmNode(id=1, lat=43.0, lon=lon, tags={}),)
    return OsmSnapshot(
        tile=TILE,
        layer=layer,
        selectors=LAYERS[layer].selectors,
        query="q",
        mirror="test",
        fetched_at="2026-09-12T00:00:00Z",
        generator="test",
        osm_base="2026-09-12T00:00:00Z",
        nodes=ns,
        ways=(OsmWay(id=10, nodes=(1,), tags={"natural": layer}),),
        relations=(),
        digest="0" * 64,
    )


def _spec(tmp_path: Path, **kw: Any) -> BuildSpec:
    kw.setdefault("out_dir", tmp_path / "out")
    return BuildSpec(
        tile=TILE,
        provider="BI",
        zl=ZL,
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        **kw,
    )


# -- 1. cancellation ----------------------------------------------------------------------


def test_the_masks_node_wires_the_cancel_hook() -> None:
    """FINDING C1, FIXED (test amended). The module-level ``_Cancel`` hook -- which nothing ever
    set, and which two concurrent nodes would have shared -- is replaced by a context binding
    (``masks_job(MasksJob(workers=..., cancel=...))``) made by ``pipeline.build._masks_run``, the
    same pattern ``orthostudio.dem`` and ``orthostudio.osm`` use."""
    assert not hasattr(masks_rule, "_Cancel")
    build_src = (SRC / "pipeline" / "build.py").read_text(encoding="utf-8")
    assert "masks_job(job)" in build_src
    assert "MasksJob(workers=workers, cancel=cast(Any, ctx.cancel_event))" in build_src
    assert masks_rule.current_masks_job() is None  # nothing bound outside a node


def test_the_masks_rule_passes_a_none_cancel_even_when_the_node_is_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING C1 (functional half), FIXED (test amended): run the rule body and capture what
    it forwards, unbound (nothing to forward) and under a bound :class:`MasksJob`."""
    mesh_dir = tmp_path / "mesh"
    mesh_dir.mkdir()
    write_mesh_npz(mesh_dir / "mesh.npz", _sea_mesh([(8432, 6000)]))
    seen: dict[str, Any] = {}

    def fake_build_masks(out_dir: Path, *a: Any, **kw: Any) -> Any:
        seen.update(kw)
        (out_dir / "index.json").write_text("{}")
        return None

    monkeypatch.setattr(masks_rule, "build_masks", fake_build_masks)
    out = tmp_path / "out"
    out.mkdir()
    inputs = {name: ResolvedInput(name, None, None) for name in MASKS.inputs}
    inputs["mesh"] = ResolvedInput("mesh", "d", mesh_dir)
    MASKS.fn(
        RunContext(
            rule=MASKS,
            key="ab" * 32,
            params=MasksParams(tile=TILE.name),
            inputs=inputs,
            out=out,
            scratch=tmp_path / "scratch",
        )
    )
    assert seen["cancel"] is None  # unbound (no node): both stay None...
    assert seen["workers"] is None
    cancel = threading.Event()
    with masks_rule.masks_job(masks_rule.MasksJob(workers=3, cancel=cancel)):
        MASKS.fn(
            RunContext(
                rule=MASKS,
                key="ab" * 32,
                params=MasksParams(tile=TILE.name),
                inputs=inputs,
                out=out,
                scratch=tmp_path / "scratch",
            )
        )
    assert seen["cancel"] is cancel  # ...and both reach the pool when the node binds them
    assert seen["workers"] == 3


def test_a_cancelled_build_masks_raises_instead_of_writing_a_truncated_artefact(
    tmp_path: Path,
) -> None:
    """FINDING C2, FIXED (test amended). ``build_masks`` used to ``break`` out of its loops,
    write ``index.json`` and return normally, so ``run_p0_rule`` committed a mask directory
    missing cells under the key of the complete one. It now raises ``SYS_CANCELLED`` and
    writes ``index.json`` only after the last cell, so the store aborts its staging."""
    mesh = _sea_mesh([(8432, 6000), (8448, 6000)])
    full = build_masks(tmp_path / "full", {TILE: mesh}, TILE, workers=1)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OsxpError) as exc:
        build_masks(tmp_path / "partial", {TILE: mesh}, TILE, workers=1, cancel=cancel)
    assert len(full.masks) == 2
    assert exc.value.code == "SYS_CANCELLED"
    assert not (tmp_path / "partial" / "index.json").exists()  # nothing committable
    assert not list((tmp_path / "partial").glob("*.png"))


def test_the_osm_job_forwards_its_cancel_token_and_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FINDING C3, FIXED (test amended): ``OsmJob.run`` refuses to start on an armed token and
    hands ``cancel`` / ``timeout_s`` to the client, which polls the token while the layers are
    in flight and cancels the requests (``OverpassClient.fetch_tile``)."""
    seen: dict[str, Any] = {}

    def fake_fetch_tile_sync(self: Any, tile: TileRef, **kw: Any) -> dict[str, OsmSnapshot]:
        seen.update(kw)
        seen["ran"] = True
        return {"coastline": _snapshot("coastline")}

    monkeypatch.setattr(native.OverpassClient, "fetch_tile_sync", fake_fetch_tile_sync)
    cancel = threading.Event()
    cancel.set()
    job = native.OsmJob(cancel=cancel, timeout_s=0.001)
    with pytest.raises(OsxpError) as exc:
        job.run(TILE, layers_for(1))
    assert exc.value.code == "SYS_CANCELLED" and "ran" not in seen
    cancel.clear()
    assert job.run(TILE, layers_for(1))
    assert seen["cancel"] is cancel and seen["timeout_s"] == 0.001


def test_the_dem_job_carries_its_cancel_token_all_the_way_down() -> None:
    """FINDING C3 (elevation half), FIXED (test amended): ``DemJob.cancel`` now reaches
    :class:`EnsureOptions`, which is polled before every download and between the nine cells
    of the 3x3 block (``dem.raster.build_combined_raster``)."""
    assert "cancel" in {f.name for f in dataclasses.fields(DemJob)}
    assert "cancel" in {f.name for f in dataclasses.fields(EnsureOptions)}
    readers = sorted(
        p.name
        for p in (SRC / "dem").rglob("*.py")
        if re.search(r"cancel", p.read_text(encoding="utf-8"))
    )
    assert readers == ["raster.py", "rule.py", "sources.py"]
    event = threading.Event()
    event.set()
    opts = EnsureOptions(elevation_dir=Path("/nowhere"), cancel=event)
    with pytest.raises(OsxpError) as exc:
        opts.check_cancelled()
    assert exc.value.code == "SYS_CANCELLED"


def test_the_native_mesh_can_be_interrupted() -> None:
    """FINDING C4, FIXED (test amended): ``build_mesh_native`` takes the scheduler's token
    (bound by ``pipeline.build._mesh_run`` through ``mesh_job``), polls the sidecar instead of
    blocking in ``subprocess.run`` and kills it on cancellation."""
    sig = __import__("inspect").signature(build_mesh_native)
    assert "cancel" in sig.parameters
    assert sig.parameters["timeout_s"].default == 1800
    src = (SRC / "mesh" / "build.py").read_text(encoding="utf-8")
    assert "_kill_sidecar" in src and "proc.poll()" in src
    build_src = (SRC / "pipeline" / "build.py").read_text(encoding="utf-8")
    assert "mesh_job(MeshJob(cancel=cast(Any, ctx.cancel_event)))" in build_src


def test_a_subprocess_started_by_a_node_outlives_the_cancelled_scheduler(
    tmp_path: Path,
) -> None:
    """FINDING C4 (mechanism), UNCHANGED and not a defect of P3: ``subprocess`` nodes run in
    threads, so cancelling the scheduler returns at once and a rule that ignores the token
    leaves its child running -- which is why the fix pass gave the two native nodes that own a
    child a token they poll (``mesh.build._run_sidecar`` kills Triangle4XP,
    ``masks.build`` cancels the pending cells). This rule deliberately ignores it."""
    import asyncio

    from orthostudio.graph import RuleParams, rule

    holder: dict[str, Any] = {}

    @rule(name="review4.sleeper", version=1, params=RuleParams, inputs=(), kind="file")
    def sleeper(ctx: RunContext) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        holder["proc"] = proc
        proc.wait()
        ctx.out.write_bytes(b"x")

    store = Store(tmp_path / "store", fsync=False)
    sched = Scheduler(store, workdir=tmp_path / "w", cancel_grace_s=0.2)
    sched.add(Node("sleeper", sleeper, RuleParams(), {}, kind="subprocess"))

    async def drive() -> None:
        task = asyncio.ensure_future(sched.run(["sleeper"]))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if "proc" in holder:
                break
        sched.cancel()
        await asyncio.wait([task], timeout=10)

    t0 = time.perf_counter()
    asyncio.run(drive())
    elapsed = time.perf_counter() - t0
    proc = holder["proc"]
    try:
        assert proc.poll() is None  # still running after the scheduler gave up
        assert elapsed < 10  # the scheduler itself came back promptly
    finally:
        proc.kill()
        proc.wait()


# -- 2. pools and the RAM budget ------------------------------------------------------------


def test_the_masks_node_budgets_for_the_worker_count_the_rule_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING M1, FIXED (test amended): ``_masks_ram_mb`` budgets
    ``300 + 800 x masks_workers(env)`` and the node now *passes that number* to the rule
    (``MasksJob.workers``), which passes it to ``build_masks``. The default of
    ``worker_count`` is capped at ``MAX_WORKERS`` too, so even an unbound rule cannot start
    one process per core."""
    monkeypatch.delenv("OSXP_MASKS_WORKERS", raising=False)
    env = BuildEnv.create([_spec(tmp_path)])
    env.workers = 2
    declared = buildmod._masks_ram_mb(env)
    budgeted_workers = buildmod.masks_workers(env)
    assert budgeted_workers == 2 and declared == 300 + 800 * 2
    with masks_rule.masks_job(masks_rule.MasksJob(workers=budgeted_workers)):
        job = masks_rule.current_masks_job()
        assert job is not None
        assert worker_count(64, job.workers) == budgeted_workers
    assert worker_count(64, None) == min(8, os.cpu_count() or 4)  # the capped default


def test_the_same_env_var_is_parsed_once_for_everybody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING M2, FIXED (test amended): ``OSXP_MASKS_WORKERS`` has one parser,
    ``masks.build.env_workers``; the pipeline and the pool call it. ``0``, a negative value
    and junk mean *unset* (documented in ``masks-build.md`` 6), so the budget and the pool can
    never be computed from two different numbers."""
    from orthostudio.masks.build import env_workers

    env = BuildEnv.create([_spec(tmp_path)])
    env.workers = 8
    for raw in ("0", "-4", "x"):
        monkeypatch.setenv("OSXP_MASKS_WORKERS", raw)
        assert env_workers() is None
        assert buildmod.masks_workers(env) == 8  # the batch's workers, capped at MAX_WORKERS
        assert worker_count(64, buildmod.masks_workers(env)) == 8
    monkeypatch.setenv("OSXP_MASKS_WORKERS", "3")
    assert env_workers() == 3
    assert buildmod.masks_workers(env) == 3 and worker_count(64, 3) == 3


def test_an_oversized_node_still_runs_alone_instead_of_deadlocking(tmp_path: Path) -> None:
    """NOT a finding: checked because ``_masks_ram_mb`` can exceed the budget of a 8 GB
    machine (6.7 GB against 0.6 x 8 GB). The admission rule lets a lone node through."""
    import asyncio

    from orthostudio.graph import RuleParams, rule

    @rule(name="review4.fat", version=1, params=RuleParams, inputs=(), kind="file")
    def fat(ctx: RunContext) -> None:
        ctx.out.write_bytes(b"x")

    store = Store(tmp_path / "store", fsync=False)
    sched = Scheduler(store, workdir=tmp_path / "w", ram_budget_mb=1000)
    sched.add(Node("fat", fat, RuleParams(), {}, kind="subprocess", ram_mb=6700))
    refs = asyncio.run(sched.run(["fat"]))
    assert "fat" in refs


def test_the_ram_budget_is_found_on_windows_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """FINDING M3 (portability), FIXED (test amended; was ``test_the_ram_budget_is_simply_absent_
    on_windows``): ``physical_memory_mb`` falls back to ``GlobalMemoryStatusEx`` on Windows
    (``_windows_memory_mb``), and ``make_scheduler`` warns when no budget could be found at all.
    Without ``os.sysconf`` a POSIX platform has no answer; Windows has one."""
    monkeypatch.delattr(os, "sysconf", raising=False)
    if os.name == "nt":
        found = buildmod.physical_memory_mb()
        assert found is not None and found == buildmod._windows_memory_mb()
    else:
        assert buildmod.physical_memory_mb() is None
    src = (SRC / "pipeline" / "build.py").read_text(encoding="utf-8")
    assert "GlobalMemoryStatusEx" in src and "RAM budget" in src


# -- 3. the OSM phase and the coastline layer ------------------------------------------------


def test_osm_refresh_downloads_again(tmp_path: Path) -> None:
    """FINDING B1 (blocking), FIXED, then made moot: ``--osm-refresh`` used to re-label the
    download while Ortho4XP's stage 1 rebuilt on the stale ``.osm.bz2`` files. Neither Ortho4XP
    nor its cache takes part in a build any more (decisions 0009 and 0010): the refresh label
    enters the key of the snapshot, so a tile whose snapshot is stored downloads a fresh one."""
    spec = _spec(tmp_path, osm_fetch=True)
    env = BuildEnv.create([spec])
    calls: list[str] = []

    def fetch(tile: TileRef, specs: Any) -> dict[str, OsmSnapshot]:
        calls.append(tile.name)
        return {s.name: _snapshot(s.name, lon=6.5) for s in specs}

    with native.osm_job(native.OsmJob(fetch=fetch)):
        first = run_osm_phase([spec], env)[TILE]
        again = run_osm_phase([spec], env)[TILE]
        spec.osm_refresh = "2026-09-12"
        refreshed = run_osm_phase([spec], env)[TILE]
    assert calls == [TILE.name, TILE.name]  # the second run reused the stored snapshot
    assert first.ref is not None and again.ref is not None and refreshed.ref is not None
    assert again.ref.key == first.ref.key and refreshed.ref.key != first.ref.key


def test_an_empty_coastline_layer_is_accepted_as_a_coastline(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """FINDING B4: a valid but *empty* coastline layer (Overpass answered nothing) yields zero
    nodes, which the mesh treats exactly like "no coast". It is accepted -- a tile far from the
    sea has none -- and the coastline node now says so in the log."""
    from orthostudio.mesh.weights import read_coastline_nodes
    from orthostudio.sources.osm import SnapshotStore

    empty = dataclasses.replace(_snapshot("coastline"), nodes=(), ways=())
    assert native.coastline_nodes_from_snapshot(empty) == []
    osm_dir = tmp_path / "osm"
    SnapshotStore(osm_dir).save(empty)
    out = tmp_path / "coastline.npz"
    ctx = RunContext(
        rule=native.COASTLINE_RULE,
        key="a1" * 32,
        params=native.CoastlineParams(tile=TILE.name),
        inputs={"osm": ResolvedInput("osm", "d", osm_dir)},
        out=out,
        scratch=tmp_path / "scratch",
    )
    with caplog.at_level("WARNING"):
        native.COASTLINE_RULE.fn(ctx)
    assert read_coastline_nodes(out).shape[0] == 0
    assert "coastline layer is empty" in caplog.text


# -- 4. silent degradation ------------------------------------------------------------------


def test_a_tile_without_any_osm_source_is_refused_instead_of_meshed_without_its_coast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING D1 (major), FIXED, then made structural: with no OSM data for the tile, the
    coastline node used to be dropped and the mesh built without its coastal term (reported
    as a downgrade after the fix). The vector stage demands the ``coastline`` layer at every road
    level and the coastline node reads the same OSM data, so such a tile is refused before
    anything runs: a mesh without its coast can no longer be built."""
    triangle = tmp_path / "Triangle4XP"
    triangle.write_text("#!/bin/sh\nexit 0\n")
    triangle.chmod(0o755)
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(triangle))
    spec = _spec(tmp_path, osm_fetch=False)
    env = BuildEnv.create([spec])  # no snapshot in the store, and none will be downloaded
    with pytest.raises(OsxpError) as exc:
        declare([spec], make_scheduler(env.store, env), env)
    assert exc.value.code == "OSM_LAYER_UNAVAILABLE"
    assert "coastline" in exc.value.context["layer"]


def test_a_failed_osm_download_is_reported_as_failed(tmp_path: Path) -> None:
    """FINDING D1 (same, through phase 0), FIXED (test amended): a failed download is now
    reported as ``status="failed"`` instead of ``"skipped"``; ``build_tiles`` then leaves the
    tile out of the build with that error, as the previous test shows for ``declare``."""
    spec = _spec(tmp_path, osm_fetch=True)
    env = BuildEnv.create([spec])

    def boom(tile: TileRef, specs: Any) -> dict[str, OsmSnapshot]:
        raise OsxpError("NET_CONNECTION_FAILED", context={"url": "overpass", "attempts": 4})

    with native.osm_job(native.OsmJob(fetch=boom)):
        outcomes = run_osm_phase([spec], env)
    assert outcomes[TILE].ref is None
    assert outcomes[TILE].error is not None
    report = buildmod._osm_report(outcomes[TILE])
    assert report["status"] == "failed" and report["error"]["code"] == "NET_CONNECTION_FAILED"


# -- 5. atomicity of the shared elevation cache ----------------------------------------------


def test_elevation_cells_are_written_atomically_and_a_stump_is_fetched_again(
    tmp_path: Path,
) -> None:
    """FINDING A1 (major), FIXED (test amended): every elevation file OrthoStudio XP writes goes
    through ``_atomic_write_bytes`` (tmp + ``os.replace``), and a local NED cell that does not
    start like a TIFF -- what a killed download leaves behind -- is fetched again instead of
    being accepted for ever. The ``Elevation_data`` of an Ortho4XP folder, which the build once
    read too, is no longer looked at (decision 0010)."""
    src = (SRC / "dem" / "sources.py").read_text(encoding="utf-8")
    assert "path.write_bytes(got.body)" not in src
    assert "_atomic_write_bytes(path, got.body)" in src  # _ensure_ned
    assert "_atomic_write_bytes(out, src.read())" in src  # extract_view_zip
    assert "os.replace(tmp, path)" in src

    from orthostudio.dem.sources import Download, elevation_path

    own = tmp_path / "elevation"
    stump = elevation_path("NED1", own, 43, -105)
    stump.parent.mkdir(parents=True, exist_ok=True)
    stump.write_bytes(b"truncated")  # what a killed download leaves behind
    downloads: list[str] = []

    def download(url: str) -> Any:
        downloads.append(url)
        return Download(url, b"II*\x00" + b"\0" * 64, 200)

    opts = EnsureOptions(
        elevation_dir=own, download=download, memo=NegativeMemo(tmp_path / "memo.json")
    )
    res = ensure_elevation("NED1", 43, -105, opts)
    assert downloads and res.state is CellState.DOWNLOADED
    assert res.path == stump and res.path.read_bytes().startswith(b"II*\x00")
    assert not list(res.path.parent.glob("*.part-*"))

    downloads.clear()
    again = ensure_elevation("NED1", 43, -105, opts)  # a complete cell is read as it is
    assert again.state is CellState.LOCAL and again.path == stump and not downloads


def test_the_build_reads_and_writes_its_own_elevation_directory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING A1 (the sharing half), then decision 0010: the build used to read the
    ``Elevation_data`` of a named Ortho4XP folder besides its own. It now reads and writes the
    one directory OrthoStudio XP's settings name (``$OSXP_ELEVATION_DIR``, else its home)."""
    from orthostudio.dem.sources import default_elevation_dir

    monkeypatch.setenv("OSXP_ELEVATION_DIR", str(tmp_path / "elevation"))
    assert default_elevation_dir() == tmp_path / "elevation"
    build_src = (SRC / "pipeline" / "build.py").read_text(encoding="utf-8")
    assert "elevation_dir=default_elevation_dir()," in build_src  # where the cells are read
    assert "read_only" not in (SRC / "dem" / "sources.py").read_text(encoding="utf-8")
    assert not hasattr(buildmod, "read_only_elevation_dirs")


# -- 6. duplication --------------------------------------------------------------------------


def test_the_triangle_binary_is_resolved_by_one_copy() -> None:
    """FINDING R1 (minor), FIXED (test amended): ``pipeline.native.triangle_binary`` now calls
    the rule's own resolver, so the binary that decides the recipe (``native_reasons``) and
    the one the rule runs can no longer diverge."""
    from orthostudio.mesh.rule import triangle_binary as mesh_copy

    a = (SRC / "pipeline" / "native.py").read_text(encoding="utf-8")
    b = (SRC / "mesh" / "rule.py").read_text(encoding="utf-8")
    assert a.count('shutil.which("Triangle4XP")') == 0
    assert b.count('shutil.which("Triangle4XP")') == 1
    assert native.triangle_binary() == mesh_copy()  # the same function, not a twin


# -- 7. oversubscription of the native masks pool --------------------------------------------


def test_three_masks_nodes_may_run_at_once_each_with_its_own_process_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING M1 (the batch half), FIXED (test amended): ``orthostudio.masks`` still takes ONE
    ``subprocess`` slot and starts a pool of its own, but the pool is the one the node
    budgeted for, so what a batch can really use is what the scheduler was told
    (``slots x (300 + 800 x workers)`` MB) and its RAM budget can do its job."""
    monkeypatch.delenv("OSXP_MASKS_WORKERS", raising=False)
    from orthostudio.masks.water import mask_cells

    slots = buildmod.default_subprocess_slots()
    cpus = os.cpu_count() or 4
    env = BuildEnv.create([_spec(tmp_path)])
    budgeted = slots * buildmod._masks_ram_mb(env)
    # a coastal tile at mask_zl 16: 13 x 17 cells of the mask grid, so the cap is cpu_count
    rng = mask_cells(TILE, 16)
    cells = ((rng.til_x_max - rng.til_x_min) // 16 + 1) * (
        (rng.til_y_max - rng.til_y_min) // 16 + 1
    )
    real_workers = worker_count(cells, buildmod.masks_workers(env))
    real = slots * (300 + 800 * real_workers)
    assert real_workers == min(cells, cpus, 8, env.workers)
    assert real == budgeted  # the budget now describes the pool that starts
    print(f"slots={slots} cells@zl16={cells} workers={real_workers} {budgeted=} MB {real=} MB")


# -- 8. error codes of the new modules --------------------------------------------------------


def test_every_error_code_the_new_modules_raise_is_registered() -> None:
    """Checked, not a finding: all codes of ``dem``/``masks``/``mesh``/``sources``/``native``
    exist in the registry, so none of them renders as an unknown code."""
    from orthostudio.errors import REGISTRY

    pattern = re.compile(r'OsxpError\(\s*"([A-Z0-9_]+)"')
    used: set[str] = set()
    for sub in ("dem", "masks", "mesh", "sources"):
        for path in (SRC / sub).rglob("*.py"):
            used |= set(pattern.findall(path.read_text(encoding="utf-8")))
    used |= set(pattern.findall((SRC / "pipeline" / "native.py").read_text(encoding="utf-8")))
    assert used, "the scan found nothing: the pattern is wrong"
    assert sorted(c for c in used if c not in REGISTRY) == []


def test_a_reader_error_inside_a_node_keeps_its_own_code(tmp_path: Path) -> None:
    """FINDING B3 (consequence), FIXED (test amended): ``wrap`` leaves an ``OsxpError`` alone,
    so the page shows ``OSM_CACHE_UNREADABLE`` and its remedy instead of the generic
    ``SYS_INTERNAL_ERROR`` the brief asks never to show."""
    from orthostudio.errors import wrap
    from orthostudio.vectors.osmdata import OsmData

    path = tmp_path / "c.osm"
    path.write_bytes(b"\xff\xfenot text")
    try:
        OsmData.load(path, layer="coastline", tile=TILE)
        raise AssertionError("expected a failure")
    except OsxpError as exc:
        assert wrap(exc).code == "OSM_CACHE_UNREADABLE"


# -- 9. the key really changes when the coastline is dropped ----------------------------------


def test_the_mesh_and_the_vector_stage_read_the_same_osm_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING D1 (the proof half), made moot: the same tile, with the same parameters, could be
    meshed with or without a coastline input, and the report did not say which. The coastline
    node now reads the one OSM source the vector stage reads -- the snapshot phase 0 stored --
    so a tile has one recipe."""
    triangle = tmp_path / "Triangle4XP"
    triangle.write_text("#!/bin/sh\nexit 0\n")
    triangle.chmod(0o755)
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(triangle))
    spec = _spec(tmp_path, osm_fetch=True, overlay=False, xp12_rasters=False)
    env = BuildEnv.create([spec])
    with native.osm_job(native.OsmJob(fetch=lambda t, ss: {s.name: _snapshot(s.name) for s in ss})):
        outcomes = run_osm_phase([spec], env)
    ref = outcomes[TILE].ref
    assert ref is not None
    g = declare([spec], make_scheduler(env.store, env), env, osm_artefacts={TILE: ref})[0]
    assert g.vectors.inputs["osm"] is ref
    assert g.coastline is not None and g.coastline.inputs == {"osm": ref}
    assert g.mesh.inputs["coastline"] is g.coastline


# -- 10. phase 0 runs before the options are validated, and outside the SIGINT handler ---------


def test_the_stage_options_are_validated_before_the_osm_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING V1, FIXED (test amended): ``build_tiles`` resolves every spec's stage choice
    (``stage_choices``) *before* phase 0 and hands the result to ``declare``, so a setting no
    stage supports is refused before a single Overpass query."""
    triangle = tmp_path / "Triangle4XP"
    triangle.write_text("#!/bin/sh\nexit 0\n")
    triangle.chmod(0o755)
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(triangle))
    spec = _spec(tmp_path, osm_fetch=True, config={"iterate": 1})
    env = BuildEnv.create([spec])
    calls: list[str] = []

    def fetch(tile: TileRef, specs: Any) -> dict[str, OsmSnapshot]:
        calls.append(tile.name)
        return {s.name: _snapshot(s.name) for s in specs}

    with native.osm_job(native.OsmJob(fetch=fetch)), pytest.raises(OsxpError) as exc:
        buildmod.build_tiles([spec], env=env, handle_sigint=False)
    assert exc.value.code == "CFG_VALUE_INVALID"
    assert exc.value.context["name"] == "iterate"
    assert calls == []  # refused before the download


SIGINT_SCRIPT = """
import os, signal, sys, threading, time
from pathlib import Path
from orthostudio.model import TileRef
from orthostudio.pipeline import native
from orthostudio.pipeline.build import BuildEnv, BuildSpec, run_osm_phase
from orthostudio.sources.osm import LAYERS, OsmNode, OsmSnapshot, OsmWay

tmp = Path(sys.argv[1])
tile = TileRef(43, 5)

def snap(layer):
    return OsmSnapshot(tile=tile, layer=layer, selectors=LAYERS[layer].selectors, query="q",
        mirror="t", fetched_at="2026-09-12T00:00:00Z", generator="t",
        osm_base="2026-09-12T00:00:00Z", nodes=(OsmNode(id=1, lat=43.0, lon=5.0, tags={}),),
        ways=(OsmWay(id=2, nodes=(1,), tags={}),), relations=(), digest="0"*64)

def fetch(t, specs):
    print("FETCHING", flush=True)
    time.sleep(6.0)          # a slow Overpass mirror
    print("FETCH-DONE", flush=True)
    return {s.name: snap(s.name) for s in specs}

spec = BuildSpec(tile=tile, provider="BI", zl=14, out_dir=tmp/"out", store_root=tmp/"store",
                 chunks_root=tmp/"chunks", workdir=tmp/"work", osm_fetch=True)
env = BuildEnv.create([spec])
try:
    with native.osm_job(native.OsmJob(fetch=fetch)):
        run_osm_phase([spec], env)
except BaseException as exc:
    print("RAISED", type(exc).__name__, flush=True)
print("EXIT", flush=True)
"""


@pytest.mark.skipif(os.name == "nt", reason="a child cannot be sent SIGINT on Windows")
def test_ctrl_c_during_the_osm_phase_stops_the_phase(tmp_path: Path) -> None:
    """FINDING V2, FIXED (test amended): phase 0 now installs a SIGINT handler of its own
    (``_run_phase0``), so a Ctrl-C cancels its scheduler and ``run_osm_phase`` *returns* --
    no ``KeyboardInterrupt`` escapes and the phase is over (``EXIT``) long before this
    deliberately uninterruptible stub finishes (``FETCH-DONE``).

    What is left is not a defect of OrthoStudio XP: a stub that sleeps 6 s in a pool thread cannot
    be interrupted, so the interpreter still waits for that thread at exit. The real client is given
    the cancellation token (``OsmJob.run`` -> ``OverpassClient.fetch_tile``) and drops its requests
    at once."""
    script = tmp_path / "sigint.py"
    script.write_text(SIGINT_SCRIPT)
    proc = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "FETCHING"
    time.sleep(0.3)
    t0 = time.perf_counter()
    proc.send_signal(2)  # SIGINT, exactly what Ctrl-C sends
    try:
        out = proc.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    elapsed = time.perf_counter() - t0
    print(f"after SIGINT: {elapsed:.2f} s, output: {out.splitlines()[-4:]}")
    lines = out.splitlines()
    assert "RAISED KeyboardInterrupt" not in out  # the signal no longer escapes the phase
    assert "EXIT" in lines and "FETCH-DONE" in lines
    assert lines.index("EXIT") < lines.index("FETCH-DONE")  # the phase returned first


# -- 12. the EDT works in float64 -------------------------------------------------------------


def test_the_distance_mask_allocates_a_float64_field_it_immediately_narrows() -> None:
    """FINDING M4 (minor), FIXED (test amended): ``edt_px`` now writes into a caller-owned
    float64 array (``distances=``), which saves one full-size allocation per cell; the
    measured peak of one 4230 x 4230 cell (0.8 GB, dominated by SciPy's internal index
    arrays) is written down in ``masks-build.md`` 4 so the node's budget is justifiable."""
    from scipy import ndimage

    small = np.zeros((64, 64), dtype=bool)
    small[32:, :] = True
    assert ndimage.distance_transform_edt(small).dtype == np.float64
    from orthostudio.masks.distance import edt_px

    assert edt_px(small).dtype == np.float32
    src = (SRC / "masks" / "distance.py").read_text(encoding="utf-8")
    assert "distances=out" in src


def test_a_cancelled_pool_run_commits_nothing_at_all(tmp_path: Path) -> None:
    """FINDING C2 (pool path), FIXED (test amended). The workers write the PNGs themselves, so
    a cancelled run could leave files the index did not list, and the artefact's two readers
    (``textures.imprint.masks_dir_lookup`` globs, ``pipeline.build._distance_lookup`` trusts
    the index) disagreed. The run now raises before any ``index.json`` exists -- whatever a
    worker had already written stays inside the staging directory the store throws away --
    and the remaining cells are cancelled (``shutdown(cancel_futures=True)``)."""
    mesh = _sea_mesh([(8432, 6000), (8448, 6000)])
    cancel = threading.Event()
    cancel.set()
    out = tmp_path / "partial"
    with pytest.raises(OsxpError) as exc:
        build_masks(out, {TILE: mesh}, TILE, workers=2, cancel=cancel)
    assert exc.value.code == "SYS_CANCELLED"
    assert not (out / "index.json").exists()


def test_default_subprocess_slots_follow_the_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cores less one, 3 at most: a 4-core Windows virtual machine ran a batch's relief,
    vector and mesh stages one at a time with a quarter of its cores (2026-09-15)."""
    for cores, slots in ((1, 1), (2, 1), (3, 2), (4, 3), (14, 3), (None, 3)):
        monkeypatch.setattr(buildmod.os, "cpu_count", lambda cores=cores: cores)
        assert buildmod.default_subprocess_slots() == slots, cores
