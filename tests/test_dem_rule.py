"""Rule ``orthostudio.dem@1``: consumed params, key stability, artefact contents.

Spec: ``docs/specs/dem.md`` section 9.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dem.dem import Dem, alt_file_name
from orthostudio.dem.rule import DEM_RULE, DemJob, DemParams, build_dem, dem_job, dem_params
from orthostudio.dem.sources import no_download
from orthostudio.dem.xplane import XP12_INPUTS
from orthostudio.errors import OsxpError
from orthostudio.graph import Executor, Node, Store, key_for
from orthostudio.model import TileRef

TILE = TileRef(46, 5)
NO_XP12 = dict.fromkeys(XP12_INPUTS)
"""The nine X-Plane inputs, all absent: the relief comes from ``Elevation_data/``."""


@pytest.fixture
def elevation(tmp_path: Path) -> Path:
    root = tmp_path / "Elevation_data"
    for lat in (45, 46, 47):
        for lon in (4, 5, 6):
            path = root / "+40+000" / f"N{lat:02d}E{lon:03d}.hgt"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = np.full((3601, 3601), 100 * (lat - 45) + lon, dtype=np.int16)
            path.write_bytes(data.astype(">i2").tobytes())
    return root


def _job(elevation: Path, tmp_path: Path) -> DemJob:
    return DemJob(
        tile=TILE,
        elevation_dir=elevation,
        download=no_download,
        memo_path=tmp_path / "misses.json",
    )


# -- declaration ---------------------------------------------------------------------------


def test_rule_declaration() -> None:
    assert DEM_RULE.name == "orthostudio.dem"
    assert DEM_RULE.version == 1
    assert DEM_RULE.kind == "dir"
    assert set(DEM_RULE.inputs) == set(XP12_INPUTS)
    assert DEM_RULE.ram_mb == 600
    assert set(DEM_RULE.consumed) == {
        "tile",
        "custom_dem",
        "fill_nodata",
        "dem1_local_fallback",
        "own_stamp",
    }


def test_params_are_frozen_and_closed() -> None:
    params = dem_params(TILE)
    with pytest.raises(Exception, match=r"frozen"):
        params.tile = "+00+000"  # type: ignore[misc]
    with pytest.raises(Exception, match="extra"):
        DemParams(tile="+43+005", masking_mode="sand")  # type: ignore[call-arg]


def test_fill_maps_the_ortho4xp_boolean() -> None:
    assert DemParams(fill_nodata=True).fill == "nearest"
    assert DemParams(fill_nodata=False).fill == "zero"


def test_an_unconsumed_setting_does_not_move_the_key() -> None:
    base = {"tile": "+43+005", "custom_dem": "", "fill_nodata": True}
    one = DEM_RULE.bind({**base, "road_level": 1})
    two = DEM_RULE.bind({**base, "road_level": 3, "mask_zl": 16})
    assert key_for(DEM_RULE, one, NO_XP12)[0] == key_for(DEM_RULE, two, NO_XP12)[0]


def test_every_consumed_setting_moves_the_key() -> None:
    base = dem_params(TILE)
    reference = key_for(DEM_RULE, base, NO_XP12)[0]
    for changed in (
        base.model_copy(update={"tile": "+43+005"}),
        base.model_copy(update={"custom_dem": "SRTM"}),
        base.model_copy(update={"fill_nodata": False}),
        base.model_copy(update={"dem1_local_fallback": True}),
    ):
        assert key_for(DEM_RULE, changed, NO_XP12)[0] != reference


# -- running ---------------------------------------------------------------------------------


def test_build_dem_needs_no_store(elevation: Path, tmp_path: Path) -> None:
    dem = build_dem(dem_params(TILE), _job(elevation, tmp_path))
    assert dem.nxdem == 3673
    assert dem.alt_dem[1836, 1836] == 100 + 5


def test_the_rule_writes_the_three_files(elevation: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    executor = Executor(store)
    with dem_job(_job(elevation, tmp_path)):
        report = executor.run(Node(DEM_RULE, dem_params(TILE), NO_XP12))
    out = report.target.path
    assert sorted(p.name for p in out.iterdir()) == [
        alt_file_name(TILE),
        "dem.npy",
        "meta.json",
    ]
    assert (out / alt_file_name(TILE)).stat().st_size == 4 * 3673 * 3673
    meta = json.loads((out / "meta.json").read_text())
    assert meta["tile"] == TILE.name
    assert meta["source"] == "View"
    assert len(meta["cells"]) == 9
    loaded = Dem.load(out)
    assert loaded.nxdem == 3673


def test_the_second_run_is_a_cache_hit(elevation: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    executor = Executor(store)
    with dem_job(_job(elevation, tmp_path)):
        first = executor.run(Node(DEM_RULE, dem_params(TILE), NO_XP12)).target
        second = executor.run(Node(DEM_RULE, dem_params(TILE), NO_XP12)).target
    assert first.key == second.key
    assert first.digest == second.digest
    assert second.status == "hit"


def test_the_alt_file_is_exactly_the_npy_content(elevation: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    executor = Executor(store)
    with dem_job(_job(elevation, tmp_path)):
        out = executor.run(Node(DEM_RULE, dem_params(TILE), NO_XP12)).target.path
    raw = np.fromfile(out / alt_file_name(TILE), dtype=np.float32)
    npy = np.load(out / "dem.npy")
    assert raw.tobytes() == npy.astype(np.float32).tobytes()


def test_the_rule_refuses_to_run_without_a_job() -> None:
    store_less = DemParams(tile=TILE.name)
    with pytest.raises(RuntimeError, match="needs a job"):
        build_dem(store_less, __import__("orthostudio.dem.rule", fromlist=["_job"])._job())


def test_a_tile_mismatch_is_refused(elevation: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"params\.tile"):
        build_dem(dem_params(TileRef(43, 5)), _job(elevation, tmp_path))


def test_the_memo_is_persisted_by_the_job(tmp_path: Path) -> None:
    from orthostudio.dem.sources import Download

    memo_path = tmp_path / "misses.json"
    calls: list[str] = []

    def download(url: str) -> Download:
        calls.append(url)
        return Download(url, status=404)

    job = DemJob(
        tile=TILE,
        elevation_dir=tmp_path / "elevation",
        download=download,
        memo_path=memo_path,
    )
    # Every cell answers 404, the tile's own included: the build is refused (decision 0007),
    # and the refusals are still remembered.
    with pytest.raises(OsxpError, match="DEM_TILE_UNAVAILABLE"):
        build_dem(dem_params(TILE), job)
    assert memo_path.is_file()
    first = len(calls)
    assert first >= 1
    with pytest.raises(OsxpError, match="DEM_TILE_UNAVAILABLE"):
        build_dem(dem_params(TILE), job)
    assert len(calls) == first, "the second build asks the server nothing"
