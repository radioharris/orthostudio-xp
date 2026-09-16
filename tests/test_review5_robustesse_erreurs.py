"""Adversarial review 5 of P4 wave 1: refusals, corrupt inputs, atomicity.

Every failure mode a user can hit without the network: a truncated file, a corrupt snapshot,
a wrong ``--dem`` value, a half-written artefact.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.pipeline.native import resolve_stages
from orthostudio.vectors import layers as layers_mod
from orthostudio.vectors.osmdata import OsmData

TILE = TileRef.parse("+43+005")
CFG_OK: dict = {}


def _cfg(**kw) -> dict:
    return dict(kw)


# -- 1. the settings the stages refuse ------------------------------------------------------


def test_the_airport_cover_is_not_a_refusal(tmp_path: Path) -> None:
    """Was ``test_vectors_native_is_refused_when_the_tile_covers_airports_with_highres``.

    ``cover_airports_with_highres`` reads the airport record the vector stage publishes
    (``airports-integration.md`` 3 and 4), so it is a setting like any other.
    """
    triangle = tmp_path / "Triangle4XP"
    triangle.write_bytes(b"")
    for value in ("True", "ICAO"):
        choice = resolve_stages(_cfg(cover_airports_with_highres=value), triangle=triangle)
        assert choice.dem == "vectors"


def test_a_missing_triangle_is_a_coded_refusal_not_a_downgrade(tmp_path: Path) -> None:
    """Was ``test_auto_still_downgrades_the_mesh_when_triangle_is_missing``: the mesh has no
    other engine to fall back on, so the build stops before it starts and says how to build
    the program."""
    with pytest.raises(OsxpError) as err:
        resolve_stages(_cfg(), triangle=tmp_path / "missing")
    assert err.value.code == "SYS_TOOL_MISSING"
    assert "cmake" in (err.value.remedy or "")


def test_an_unknown_dem_value_is_a_coded_refusal() -> None:
    with pytest.raises(OsxpError) as err:
        resolve_stages(_cfg(), dem="nativ")
    assert err.value.code == "CFG_VALUE_INVALID"
    assert err.value.context["name"] == "dem"


def test_dem_vectors_means_what_it_says(tmp_path: Path) -> None:
    """Was ``test_vectors_native_says_so_when_it_overrides_an_explicit_dem_vectors``.

    Review 5's defect was that ``--dem vectors`` silently became ``dem=native``. The vector
    stage publishes its own smoothed ``Data<tile>.alt``, so both values mean what they say
    (``airports-integration.md`` 3).
    """
    triangle = tmp_path / "Triangle4XP"
    triangle.write_bytes(b"")
    assert resolve_stages(_cfg(), dem="vectors", triangle=triangle).to_dict() == {"dem": "vectors"}
    assert resolve_stages(_cfg(), dem="native", triangle=triangle).to_dict() == {"dem": "native"}


# -- 2. corrupt or truncated inputs ------------------------------------------------------------


def test_a_file_without_a_closing_osm_tag_raises_a_coded_error(tmp_path: Path) -> None:
    """A file cut short: Ortho4XP would build a half tile, OrthoStudio XP refuses."""
    path = tmp_path / "patch.osm"
    path.write_text(
        "<?xml version='1.0'?>\n<osm version='0.6'>\n<node id='1' lat='43.5' lon='5.5' />\n",
        encoding="utf-8",
    )
    with pytest.raises(OsxpError) as err:
        OsmData.load(path, layer="water", tile=TILE)
    assert err.value.code == "OSM_CACHE_UNREADABLE"
    assert "truncated" in str(err.value.context.get("reason", ""))


def test_a_way_referencing_a_node_the_cache_does_not_carry_raises_a_coded_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "patch.osm"
    path.write_text(
        "<?xml version='1.0'?>\n<osm version='0.6'>\n"
        "<node id='1' lat='43.5' lon='5.5' />\n"
        "<way id='7'>\n<nd ref='1' />\n<nd ref='99' />\n</way>\n</osm>\n",
        encoding="utf-8",
    )
    with pytest.raises(OsxpError) as err:
        OsmData.load(path, layer="water", tile=TILE)
    assert err.value.code == "OSM_CACHE_UNREADABLE"


def test_a_corrupt_osm_snapshot_file_raises_a_coded_error(tmp_path: Path) -> None:
    path = tmp_path / "layer.osm.json.zst"
    path.write_bytes(b"not zstd at all")
    with pytest.raises(OsxpError) as err:
        OsmData.load(path, layer="water", tile=TILE)
    assert err.value.code == "OSM_CACHE_UNREADABLE"


def test_the_snapshot_path_has_the_same_error_envelope_as_the_file_path(tmp_path: Path) -> None:
    """Was ``test_defect_the_snapshot_path_has_no_error_envelope_of_its_own``.

    The defect: ``_feed_file`` wrapped its reader in ``OSM_CACHE_UNREADABLE`` and
    ``_feed_snapshot`` did not, so an ``OsmSnapshot`` handed over in memory -- what
    ``layer_store`` does on the snapshot branch -- walked the builder with no envelope and any
    malformation raised a bare Python exception. Fixed: ``OsmData.update`` wraps both branches
    in the same ``_unreadable`` context manager, ``TypeError`` and ``AttributeError``
    included.
    """

    class BadNode:
        id, lon, lat = 1, None, 43.5
        tags: ClassVar[dict] = {}

    class BadSnapshot:
        tile = TILE
        nodes = (BadNode(),)
        ways: tuple = ()
        relations: tuple = ()

    with pytest.raises(OsxpError) as err:
        OsmData.load(BadSnapshot(), layer="water", tile=TILE)
    assert err.value.code == "OSM_CACHE_UNREADABLE"
    assert "TypeError" in str(err.value.context.get("reason", ""))


def test_open_osm_source_refuses_a_file_instead_of_a_directory(tmp_path: Path) -> None:
    handle = tmp_path / "something.txt"
    handle.write_text("x")
    with pytest.raises(OsxpError) as err:
        layers_mod.open_osm_source(handle, TILE)
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"


def test_the_rule_refuses_a_dem_input_that_is_not_an_osxp_dem_artefact(tmp_path: Path) -> None:
    from orthostudio.vectors.rule import VectorsParams, run_vectors

    class Ctx:
        params = VectorsParams(tile=TILE.name)
        out = tmp_path
        inputs: ClassVar[dict] = {}

        def input_path(self, name: str) -> Path:
            return tmp_path

    with pytest.raises(OsxpError) as err:
        run_vectors(Ctx())  # type: ignore[arg-type]
    assert err.value.code == "DEM_FILE_UNREADABLE"
    assert "--dem native" in (err.value.remedy or "")


# -- 3. the artefact is written file by file ---------------------------------------------------


def test_a_failure_halfway_through_write_leaves_no_artefact_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_defect_a_failure_halfway_through_write_leaves_a_partial_artefact``.

    The defect: ``AssembledVectors.write`` wrote four files in a row, none of them atomically,
    so a full disk left a ``.node`` with no ``.poly`` behind. Harmless inside the graph store
    (it commits a temporary directory with one rename) but ``write`` is public. Fixed: every
    file is staged under a ``.part`` name and the set is renamed into place at the end.
    """
    from orthostudio.vectors import assemble as assemble_mod
    from orthostudio.vectors.assemble import AssemblyParams, VectorLayers, assemble_vectors

    class FlatDem:
        alt_dem = np.full((4, 4), 120.0, dtype=np.float32)

        def alt_vec(self, way):
            return np.zeros(len(np.asarray(way).reshape(-1, 2)))

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(assemble_mod, "write_poly_file", boom)
    with pytest.raises(OSError, match="No space left"):
        assemble_vectors(
            VectorLayers(), TILE, FlatDem(), AssemblyParams(mesh_zl=16), out_dir=tmp_path
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_cancelled_assembly_writes_nothing(tmp_path: Path) -> None:
    """The checkpoint review 5 asked for: the token is seen around the noding as well."""
    import threading

    from orthostudio.errors import OsxpError as _OsxpError
    from orthostudio.vectors.assemble import AssemblyParams, VectorLayers, assemble_vectors

    class FlatDem:
        alt_dem = np.full((4, 4), 120.0, dtype=np.float32)

        def alt_vec(self, way):
            return np.zeros(len(np.asarray(way).reshape(-1, 2)))

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(_OsxpError) as err:
        assemble_vectors(
            VectorLayers(),
            TILE,
            FlatDem(),
            AssemblyParams(mesh_zl=16, cancel=cancel),
            out_dir=tmp_path,
        )
    assert err.value.code == "SYS_CANCELLED"
    assert list(tmp_path.iterdir()) == []


# -- 4. the native vector stage demands a layer it never reads ---------------------------------


def test_the_pipeline_demands_the_airports_cache_the_stage_now_reads(tmp_path: Path) -> None:
    """Was ``test_the_pipeline_no_longer_demands_the_airports_cache_the_stage_never_reads``.

    Review 5's defect was the mirror image of today's rule: the pipeline demanded the
    ``airports`` layer while wave 1 never opened it, and refused a perfectly buildable tile.
    Wave 2 opens it -- it is where the aerodromes come from (``O4_Vector_Map.py:184-197``) --
    so the demand is correct again and the exclusion had to go, on both sides: the layer the
    stage reads. What must **not** come back is the old remedy: the message still names what is
    missing, and it points at OrthoStudio XP's own OSM client, never at Ortho4XP's downloader.
    """
    import inspect

    from orthostudio.sources.osm import layers_for
    from orthostudio.vectors.layers import build_layers

    assert "airports" in [s.name for s in layers_for(1)]
    read_by_the_stage = set(
        re.findall(r'layer_store\(\s*source,\s*"(\w+)"', inspect.getsource(build_layers))
    )
    assert "airports" in read_by_the_stage

    import orthostudio.pipeline.build as build_mod

    wiring = inspect.getsource(build_mod._vectors_osm_input)
    assert 'if s.name != "airports"' not in wiring
    # Amended (flight-test build of LSGG, 2026-09-13): this line used to require the remedy to let
    # Ortho4XP download the layers. That is the path the project avoids on purpose (3 of its 4
    # Overpass servers are gone, 5 min 40 per failed request), so it froze a defect. The remedy
    # points at OrthoStudio XP's own client; tests/test_build_cold_tile.py pins the wording.
    assert "Ortho4XP" not in wiring
    assert "--osm-refresh" in wiring
