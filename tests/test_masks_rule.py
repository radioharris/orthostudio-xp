# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Rule ``orthostudio.masks@1``: declaration, keys, artefact read back by its consumers.

Spec: ``docs/specs/masks-build.md`` sections 3 and 5.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from orthostudio.errors import OsxpError
from orthostudio.graph import Executor, Node, Source, Store, key_for
from orthostudio.imagery.grid import TextureId, tile_to_wgs84
from orthostudio.masks.build import MASKS_INDEX_FORMAT
from orthostudio.masks.rule import MASKS, MasksParams, read_mesh_artifact
from orthostudio.masks.water import NEIGHBOUR_OFFSETS
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz, write_mesh_text
from orthostudio.model import TileRef
from orthostudio.pipeline.build import _distance_lookup
from orthostudio.textures.imprint import mask_for_texture, masks_dir_lookup

TILE = TileRef(43, 5)
ZL = 14


def _mesh_for(cells: list[tuple[int, int]]) -> MeshData:
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


@pytest.fixture
def mesh_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "mesh"
    directory.mkdir()
    write_mesh_text(directory / "Data+43+005.mesh", _mesh_for([(8432, 6000)]))
    return directory


def _params(**kw: object) -> MasksParams:
    return MasksParams(tile="+43+005", **kw)  # type: ignore[arg-type]


def _inputs(mesh: object, **kw: object) -> dict[str, object]:
    inputs: dict[str, object] = {"mesh": mesh, "dem": None, "custom_extent": None}
    inputs.update(dict.fromkeys(NEIGHBOUR_OFFSETS))
    inputs.update(kw)
    return inputs


# -- declaration -------------------------------------------------------------------------


def test_rule_declaration() -> None:
    assert MASKS.name == "orthostudio.masks"
    assert MASKS.version == 1
    assert MASKS.kind == "dir"
    # review 4 (minor): the decorator declares the base plus ONE worker; the pipeline node
    # declares base + 0.8 GB per worker of the pool it sizes (masks-build.md 3).
    assert MASKS.ram_mb == 1100
    assert set(MASKS.inputs) == {"mesh", "dem", "custom_extent", *NEIGHBOUR_OFFSETS}


def test_consumed_parameters_are_exactly_what_the_stage_reads() -> None:
    assert set(MASKS.consumed) == {
        "tile",
        "mask_zl",
        "masks_width",
        "masking_mode",
        "use_masks_for_inland",
        "ratio_water",
        "masks_use_DEM_too",
        "distance_masks_too",
        "masks_custom_extent",
    }
    # the elevation reaches the rule as an artefact, never as a parameter
    assert "custom_dem" not in MASKS.consumed
    assert "fill_nodata" not in MASKS.consumed


def test_every_parameter_changes_the_key() -> None:
    base = _params()
    digests = dict.fromkeys(MASKS.inputs, "aa" * 32)
    reference = key_for(MASKS, base, digests)[0]
    for name, value in (
        ("tile", "+44+005"),
        ("mask_zl", 16),
        ("masks_width", 200),
        ("masking_mode", "rocks"),
        ("use_masks_for_inland", True),
        ("ratio_water", 0.5),
        ("masks_use_DEM_too", True),
        ("distance_masks_too", True),
        ("masks_custom_extent", "Foo"),
    ):
        changed = base.model_copy(update={name: value})
        assert key_for(MASKS, changed, digests)[0] != reference, name


def test_an_absent_neighbour_keys_differently_from_a_present_one() -> None:
    params = _params()
    absent = dict.fromkeys(MASKS.inputs, None) | {"mesh": "aa" * 32}
    present = absent | {"nb_w": "bb" * 32}
    assert key_for(MASKS, params, absent)[0] != key_for(MASKS, params, present)[0]


def test_an_unknown_masking_mode_is_refused(tmp_path: Path, mesh_dir: Path) -> None:
    store = Store(tmp_path / "store")
    node = Node(MASKS, _params(masking_mode="glass"), _inputs(Source.from_path(mesh_dir)))
    with pytest.raises(Exception, match="CFG_VALUE_INVALID"):
        Executor(store).run(node)


def test_dem_and_custom_extent_are_refused_until_their_rules_exist(
    tmp_path: Path, mesh_dir: Path
) -> None:
    store = Store(tmp_path / "store")
    source = Source.from_path(mesh_dir)
    with pytest.raises(Exception, match="DEM_FILE_UNREADABLE"):
        Executor(store).run(Node(MASKS, _params(masks_use_DEM_too=True), _inputs(source)))
    with pytest.raises(Exception, match="MASK_CUSTOM_EXTENT_INVALID"):
        Executor(store).run(Node(MASKS, _params(masks_custom_extent="Foo"), _inputs(source)))


# -- artefact ----------------------------------------------------------------------------


def test_the_artifact_is_read_by_its_consumers(tmp_path: Path, mesh_dir: Path) -> None:
    store = Store(tmp_path / "store")
    node = Node(MASKS, _params(distance_masks_too=True), _inputs(Source.from_path(mesh_dir)))
    out = Executor(store).run(node).target.path
    doc = json.loads((out / "index.json").read_text())
    assert doc["format"] == MASKS_INDEX_FORMAT
    assert doc["masks"] and doc["masks"][0]["file"] == "6000_8432.png"

    lookup = masks_dir_lookup(out)
    assert lookup(8432, 6000) is not None
    assert lookup(8400, 6000) is None
    # a ZL16 texture inside the transition band of the ZL14 cell 8432/6000
    inner = TextureId(4 * 8432 + 16, 4 * 6000 + 16, 16, "BI")
    crop = mask_for_texture(inner, ZL, lookup)
    assert crop is not None and crop.shape == (4096, 4096)

    distance = _distance_lookup(out)
    assert distance is not None and distance(8432, 6000) is not None


def test_the_rule_is_a_cache_hit_the_second_time(tmp_path: Path, mesh_dir: Path) -> None:
    store = Store(tmp_path / "store")
    executor = Executor(store)
    source = Source.from_path(mesh_dir)
    first = executor.run(Node(MASKS, _params(), _inputs(source))).target
    second = executor.run(Node(MASKS, _params(), _inputs(source))).target
    assert first.key == second.key and first.digest == second.digest


# -- mesh artefact reader ----------------------------------------------------------------


def test_read_mesh_artifact_prefers_the_npz(tmp_path: Path) -> None:
    mesh = _mesh_for([(8432, 6000)])
    directory = tmp_path / "mesh"
    directory.mkdir()
    write_mesh_text(directory / "Data+43+005.mesh", mesh)
    write_mesh_npz(directory / "mesh.npz", mesh)
    assert read_mesh_artifact(directory).n_triangles == mesh.n_triangles
    assert read_mesh_artifact(directory / "mesh.npz").n_triangles == mesh.n_triangles
    assert read_mesh_artifact(directory / "Data+43+005.mesh").n_triangles == mesh.n_triangles


def test_read_mesh_artifact_reports_a_missing_mesh(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(OsxpError, match="MESH_INPUT_MISSING"):
        read_mesh_artifact(empty)
