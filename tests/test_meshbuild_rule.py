"""Tests for orthostudio.mesh.build and orthostudio.mesh.rule: command line, DEM window,
rule, retry.

Spec: docs/specs/mesh-build.md sections 2.2, 4 and 9. Only the end-to-end tests need the
Triangle4XP binary.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, RunContext, key_for
from orthostudio.mesh import triangle_io as tio
from orthostudio.mesh.build import (
    DemSpec,
    MeshBuildOptions,
    build_mesh_native,
    mesh_file_name,
    steiner_budget,
    triangle_command,
    triangle_switches,
)
from orthostudio.mesh.rule import OSXP_MESH, MeshParams, run_mesh, triangle_binary
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
TRIANGLE = triangle_binary()
needs_binary = pytest.mark.skipif(
    not TRIANGLE.is_file(), reason="Triangle4XP not built (native/triangle4xp/build)"
)


# -- the command line --------------------------------------------------------------------------


def test_steiner_budget_matches_ortho4xp() -> None:
    assert steiner_budget(3.0, 247282) == 1331665.3684210528
    assert steiner_budget(0, 10) == 5e6 / 1.9 - 10  # <= 0 falls back to 5 M triangles
    assert steiner_budget(50, 10) == 5e6 / 1.9 - 10  # >= 50 M too
    assert steiner_budget(1.0, 10_000_000) == 5e5  # never below 500 000
    assert steiner_budget("oops", 10) == 5e6 / 1.9 - 10  # type: ignore[arg-type]


def test_switches_and_positional_parameters() -> None:
    dem = DemSpec(Path("a.alt"), 3673, 3673, -0.01, -0.01, 1.01, 1.01, -32768.0)
    options = MeshBuildOptions()
    assert triangle_switches(options, 247282, binary=True) == "-pq10AuYBQPbS1331665.3684210528"
    assert triangle_switches(options, 247282, binary=False) == "-pq10AuYBQPS1331665.3684210528"
    assert triangle_switches(options, 247282, binary=True, min_angle=0.0).startswith("-pq0A")
    refine = triangle_switches(MeshBuildOptions(iterate=1), 10, binary=False)
    assert refine.startswith("-pq10r")  # -r instead of -A
    argv = triangle_command(
        "/bin/Triangle4XP",
        triangle_switches(options, 247282, binary=True),
        lat=43,
        dem=dem,
        curvature_tol=2.0,
        alt_path="a.alt",
        weight_path="a.weight",
        poly_path="a.poly",
    )
    assert len(argv) == 15
    assert argv[2:12] == [
        f"{math.pi * 6378137 / 180 * math.cos(math.pi * 43 / 180):.9g}",
        "111319.491",
        "3673",
        "3673",
        "-0.01",
        "-0.01",
        "1.01",
        "1.01",
        "-32768",
        "2",
    ]
    assert argv[-3:] == ["a.alt", "a.weight", "a.poly"]
    # the retry must move the switch string, never the 11th positional (nodata) -- Ortho4XP's bug
    retried = list(argv)
    retried[1] = triangle_switches(options, 247282, binary=True, min_angle=0.0)
    assert retried[10] == argv[10] == "-32768"
    assert retried[1] != argv[1]


def test_mesh_file_name() -> None:
    assert mesh_file_name(TILE) == "Data+43+005.mesh"


# -- the DEM window ----------------------------------------------------------------------------


def _fake_alt(path: Path, side: int) -> Path:
    path.write_bytes(np.zeros((side, side), dtype=np.float32).tobytes())
    return path


def test_dem_spec_inference(tmp_path: Path) -> None:
    _fake_alt(tmp_path / "Data+43+005.alt", 3673)
    spec = DemSpec.from_dir(tmp_path, TILE)
    assert (spec.nxdem, spec.nydem, spec.x0, spec.x1, spec.nodata) == (
        3673,
        3673,
        -0.01,
        1.01,
        -32768.0,
    )
    spec.check(TILE.name)
    plain = DemSpec.infer(tmp_path / "Data+43+005.alt")
    assert plain.nxdem == 3673


def test_dem_spec_from_json_wins(tmp_path: Path) -> None:
    _fake_alt(tmp_path / "Data+43+005.alt", 3673)
    (tmp_path / "dem.json").write_text(
        json.dumps(
            {
                "format": "osxp-dem-1",
                "nxdem": 3672,
                "nydem": 3672,
                "x0": -0.0099,
                "y0": -0.0099,
                "x1": 1.0099,
                "y1": 1.0099,
                "nodata": -9999.0,
            }
        ),
        encoding="utf-8",
    )
    spec = DemSpec.from_dir(tmp_path, TILE)
    assert (spec.nxdem, spec.nodata) == (3672, -9999.0)
    spec.check(TILE.name)  # 3672^2 * 4 < the 3673^2 * 4 bytes on disk: not short
    (tmp_path / "dem.json").write_text('{"format": "nope"}', encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        DemSpec.from_dir(tmp_path, TILE)


def test_dem_spec_errors(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as missing:
        DemSpec.infer(tmp_path / "nothing.alt")
    assert missing.value.code == "MESH_INPUT_MISSING"
    (tmp_path / "odd.alt").write_bytes(b"\x00" * 4 * 100)  # 10 x 10, not a 1" grid
    with pytest.raises(OsxpError, match="1"):
        DemSpec.infer(tmp_path / "odd.alt")
    short = DemSpec(_fake_alt(tmp_path / "s.alt", 10), 3673, 3673, -0.01, -0.01, 1.01, 1.01, -1)
    with pytest.raises(OsxpError) as truncated:
        short.check(TILE.name)
    assert truncated.value.code == "MESH_INPUT_MISSING"
    assert "needs" in truncated.value.message


# -- the rule declaration ----------------------------------------------------------------------


def test_rule_declares_exactly_what_it_consumes() -> None:
    assert OSXP_MESH.name == "orthostudio.mesh"
    assert OSXP_MESH.version == 1
    assert OSXP_MESH.kind == "dir"
    assert OSXP_MESH.inputs == ("coastline", "dem", "vectors")
    assert OSXP_MESH.ram_mb == 900
    assert set(OSXP_MESH.consumed) == {
        "tile",
        "curvature_tol",
        "apt_curv_tol",
        "apt_curv_ext",
        "coast_curv_tol",
        "coast_curv_ext",
        "limit_tris",
        "min_angle",
        "sea_smoothing_mode",
        "water_smoothing",
        "iterate",
        "skip_multiples_of_ten",
        "water_in_set_order",
    }
    # the elevation and the OSM snapshot are inputs, not parameters
    assert "custom_dem" not in OSXP_MESH.consumed
    assert "fill_nodata" not in OSXP_MESH.consumed
    assert "mesh_zl" not in OSXP_MESH.consumed
    assert "mask_zl" not in OSXP_MESH.consumed


def test_params_are_a_subset_of_a_wider_configuration() -> None:
    config = {
        "tile": "+43+005",
        "curvature_tol": 1.5,
        "custom_dem": "something",  # ignored: not consumed
        "mask_zl": 16,  # ignored
    }
    params = MeshParams.subset_of(config)
    assert params.curvature_tol == 1.5
    assert params.water_smoothing == 10  # default
    assert params.options().curvature_tol == 1.5
    with pytest.raises(ValueError):
        MeshParams(tile="+43+005", curvature_tol=1.5, unknown=3)  # type: ignore[call-arg]


def test_key_changes_with_every_consumed_parameter() -> None:
    base = MeshParams(tile="+43+005")
    digests = {"vectors": "a" * 64, "dem": "b" * 64, "coastline": "c" * 64}
    key, _ = key_for(OSXP_MESH, base, digests)
    seen = {key}
    for name, value in (
        ("tile", "+44+005"),
        ("curvature_tol", 1.0),
        ("apt_curv_tol", 0.4),
        ("apt_curv_ext", 0.4),
        ("coast_curv_tol", 1.5),
        ("coast_curv_ext", 0.4),
        ("limit_tris", 5.0),
        ("min_angle", 5.0),
        ("sea_smoothing_mode", "mean"),
        ("water_smoothing", 3),
        ("iterate", 1),
        ("skip_multiples_of_ten", False),
        ("water_in_set_order", False),
    ):
        other, _ = key_for(OSXP_MESH, base.model_copy(update={name: value}), digests)
        assert other not in seen, name
        seen.add(other)
    # an input digest also changes the key
    moved, _ = key_for(OSXP_MESH, base, {**digests, "coastline": "d" * 64})
    assert moved not in seen


def test_triangle_binary_honours_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSXP_TRIANGLE4XP", "/tmp/whatever/Triangle4XP")
    assert triangle_binary() == Path("/tmp/whatever/Triangle4XP")
    monkeypatch.delenv("OSXP_TRIANGLE4XP")
    assert triangle_binary().name.startswith("Triangle4XP")


# -- end to end on a synthetic square ------------------------------------------------------------

_DEM = dict(nx=11, ny=11, x0=-0.01, y0=-0.01, x1=1.01, y1=1.01, nodata=-32768.0)


def _write_square(vectors: Path, rng: np.random.Generator, n_inner: int = 60) -> None:
    corners = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    xy = np.vstack([corners, rng.random((n_inner, 2))])
    alt = rng.random(len(xy)) * 100.0
    with open(vectors / "Data+43+005.node", "w") as f:
        f.write(f"{len(xy)} 2 1 0\n")
        for i, (x, y, a) in enumerate(np.column_stack([xy, alt]), start=1):
            f.write(f"{i} {x:.17g} {y:.17g} {a:.17g}\n")
        f.write("# test\n")
    with open(vectors / "Data+43+005.poly", "w") as f:
        f.write("0 2 1 0\n\n4 1\n")
        for i, (a, b) in enumerate([(1, 2), (2, 3), (3, 4), (4, 1)], start=1):
            f.write(f"{i} {a} {b} 1\n")
        f.write("\n0\n\n1\n1 0.500000000000000 0.500000000000000 1\n")


@pytest.fixture
def synthetic(tmp_path: Path) -> dict:
    rng = np.random.default_rng(7)
    vectors = tmp_path / "vectors"
    dem_dir = tmp_path / "dem"
    vectors.mkdir()
    dem_dir.mkdir()
    _write_square(vectors, rng)
    (rng.random((_DEM["ny"], _DEM["nx"])) * 100).astype(np.float32).tofile(
        dem_dir / "Data+43+005.alt"
    )
    (dem_dir / "dem.json").write_text(
        json.dumps(
            {
                "format": "osxp-dem-1",
                **{k: v for k, v in _DEM.items() if k != "nx"},
                "nxdem": _DEM["nx"],
                "nydem": _DEM["ny"],
            }
        ),
        encoding="utf-8",
    )
    return {"root": tmp_path, "vectors": vectors, "dem": dem_dir}


@needs_binary
def test_end_to_end_on_a_square(synthetic: dict) -> None:
    out = synthetic["root"] / "out"
    dem = DemSpec.from_dir(synthetic["dem"], TILE)
    result = build_mesh_native(
        TILE,
        synthetic["vectors"],
        dem,
        MeshBuildOptions(),
        TRIANGLE,
        synthetic["root"] / "work",
        out_dir=out,
        coast_nodes=np.array([[5.5, 43.5]]),
    )
    for name in ("Data+43+005.mesh", "mesh.npz", "water_tris.npz", "stats.json"):
        assert (out / name).is_file(), name
    assert result.mesh.n_triangles > 50
    assert result.mesh.vertices[:, 0].min() >= 5.0 and result.mesh.vertices[:, 0].max() <= 6.0
    assert result.mesh.vertices[:, 1].min() >= 43.0
    stats = json.loads((out / "stats.json").read_text())
    assert stats["tile"] == "+43+005"
    assert stats["weight_cells_changed"] > 0  # the single coastline node refined a window
    assert stats["retried_without_min_angle"] is False
    assert (synthetic["root"] / "work" / "Data+43+005.weight").stat().st_size == 4 * 1001 * 1001


@needs_binary
def test_missing_vector_input_is_a_coded_error(synthetic: dict) -> None:
    (synthetic["vectors"] / "Data+43+005.poly").unlink()
    with pytest.raises(OsxpError) as err:
        build_mesh_native(
            TILE,
            synthetic["vectors"],
            DemSpec.from_dir(synthetic["dem"], TILE),
            MeshBuildOptions(),
            TRIANGLE,
            synthetic["root"] / "work",
        )
    assert err.value.code == "MESH_INPUT_MISSING"


def _program(path: Path, source: str) -> Path:
    """``source`` as a program the mesh stage can start: an executable script on POSIX, a ``.cmd``
    that runs it with this interpreter on Windows (which cannot start a ``.py`` directly)."""
    script = path.with_suffix(".py")
    if os.name == "nt":
        script.write_text(source, encoding="utf-8")
        wrapper = path.with_suffix(".cmd")
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        return wrapper
    script.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@needs_binary
def test_retry_relaxes_min_angle_and_not_nodata(synthetic: dict) -> None:
    """Wanted difference (spec 4.1): Ortho4XP overwrites ``nodata``; OrthoStudio XP rebuilds
    the switches."""
    root = synthetic["root"]
    marker = root / "failed_once"
    wrapper = _program(
        root / "flaky_triangle",
        "import os, subprocess, sys\n"
        f"marker = {str(marker)!r}\n"
        "if not os.path.exists(marker):\n"
        "    open(marker, 'w').write(' '.join(sys.argv[1:]))\n"
        "    sys.exit(3)\n"
        f"sys.exit(subprocess.run([{str(TRIANGLE)!r}] + sys.argv[1:]).returncode)\n",
    )
    result = build_mesh_native(
        TILE,
        synthetic["vectors"],
        DemSpec.from_dir(synthetic["dem"], TILE),
        MeshBuildOptions(),
        wrapper,
        root / "work2",
        out_dir=root / "out2",
    )
    argv_first = marker.read_text().split()
    assert result.stats["retried_without_min_angle"] is True
    assert "MESH_QUALITY_RELAXED" in [w.code for w in result.warnings]
    assert argv_first[0].startswith("-pq10")  # the first attempt
    assert result.argv[1].startswith("-pq0")  # the retry relaxed the angle
    assert result.argv[10] == argv_first[9] == "-32768"  # ... and left nodata alone
    assert (root / "out2" / "Data+43+005.mesh").is_file()


@needs_binary
def test_triangulation_failure_is_a_coded_error(synthetic: dict) -> None:
    root = synthetic["root"]
    wrapper = _program(root / "always_fails", "import sys\nsys.exit(4)\n")
    with pytest.raises(OsxpError) as err:
        build_mesh_native(
            TILE,
            synthetic["vectors"],
            DemSpec.from_dir(synthetic["dem"], TILE),
            MeshBuildOptions(),
            wrapper,
            root / "work3",
        )
    assert err.value.code == "MESH_TRIANGULATION_FAILED"
    assert err.value.context["returncode"] == 4


@needs_binary
def test_binary_exchange_is_refused_while_refining(synthetic: dict) -> None:
    with pytest.raises(ValueError, match="-b"):
        build_mesh_native(
            TILE,
            synthetic["vectors"],
            DemSpec.from_dir(synthetic["dem"], TILE),
            MeshBuildOptions(iterate=1),
            TRIANGLE,
            synthetic["root"] / "work4",
            binary=True,
        )


@needs_binary
def test_rule_runs_through_a_run_context(synthetic: dict) -> None:
    root = synthetic["root"]
    out = root / "artefact"
    scratch = root / "scratch"
    out.mkdir()
    scratch.mkdir()
    params = MeshParams(tile="+43+005")
    ctx = RunContext(
        rule=OSXP_MESH,
        key="0" * 64,
        params=params,
        inputs={
            "vectors": ResolvedInput("vectors", "a" * 64, synthetic["vectors"]),
            "dem": ResolvedInput("dem", "b" * 64, synthetic["dem"]),
            "coastline": ResolvedInput("coastline", None, None),
        },
        out=out,
        scratch=scratch,
    )
    result = run_mesh(ctx)
    assert (out / "Data+43+005.mesh").is_file()
    assert (out / "water_tris.npz").is_file()
    # neither a coastline nor an airport input: both degraded warnings, and the build goes on
    assert [w.code for w in result.warnings] == [
        "MESH_WEIGHT_MAP_INCOMPLETE",  # airports
        "MESH_WEIGHT_MAP_INCOMPLETE",  # coastline
    ]
    assert {w.context["reason"] for w in result.warnings} == {
        "no airport boundary available",
        "no coastline layer given",
    }
    assert result.stats["n_coast_nodes"] == 0
    OSXP_MESH.fn(ctx)  # the rule body itself runs and rewrites the artefact


@needs_binary
def test_rule_reads_a_coastline_input(synthetic: dict) -> None:
    from orthostudio.mesh.weights import write_coastline_nodes

    root = synthetic["root"]
    coast_dir = root / "coast"
    coast_dir.mkdir()
    write_coastline_nodes(coast_dir / "coastline.npz", [(5.5, 43.5), (5.51, 43.5)])
    out = root / "artefact2"
    scratch = root / "scratch2"
    out.mkdir()
    scratch.mkdir()
    ctx = RunContext(
        rule=OSXP_MESH,
        key="0" * 64,
        params=MeshParams(tile="+43+005"),
        inputs={
            "vectors": ResolvedInput("vectors", "a" * 64, synthetic["vectors"]),
            "dem": ResolvedInput("dem", "b" * 64, synthetic["dem"]),
            "coastline": ResolvedInput("coastline", "c" * 64, coast_dir),
        },
        out=out,
        scratch=scratch,
    )
    result = run_mesh(ctx)
    assert result.stats["n_coast_nodes"] == 2
    assert [w.context["reason"] for w in result.warnings] == ["no airport boundary available"]
    assert result.stats["weight_cells_changed"] > 0


def test_input_files_are_read_by_the_sniffing_readers(synthetic: dict, tmp_path: Path) -> None:
    """The stage accepts both the Ortho4XP text PSLG and the OrthoStudio XP binary one."""
    nodes = tio.read_node(synthetic["vectors"] / "Data+43+005.node")
    pslg = tio.read_poly(synthetic["vectors"] / "Data+43+005.poly")
    binary = tmp_path / "bin"
    binary.mkdir()
    tio.write_node_binary(binary / "Data+43+005.node", nodes)
    tio.write_poly_binary(binary / "Data+43+005.poly", pslg)
    assert tio.read_node(binary / "Data+43+005.node").n_vertices == nodes.n_vertices
    assert tio.read_poly(binary / "Data+43+005.poly").n_segments == pslg.n_segments


def test_subprocess_is_never_run_through_a_shell() -> None:
    """``subprocess.run`` on a list, never a string: a path with a space cannot inject."""
    source = Path(build_mesh_native.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert re.search(r"subprocess\.run\(\s*argv,", source)
    assert subprocess.run  # the module is the one we think it is
