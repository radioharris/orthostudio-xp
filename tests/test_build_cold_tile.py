"""A cold tile: the estimate plans it, and a failure never points at Ortho4XP.

Found while building LSGG (+46+006) for a flight test. Two defects:

1. ``osxp build --dry-run`` on a tile with no OSM data raised ``OSM_LAYER_UNAVAILABLE``. A real
   build works, because phase 0 downloads the layers with OrthoStudio XP's own client *before*
   the graph is declared; the estimate declares the graph without running phase 0, so it must
   treat the missing layers as "to fetch", not as an error.
2. When the layers really are unavailable, the remedy told the user to let Ortho4XP download and
   cache every layer. That is the one path the project avoids on purpose: three of Ortho4XP's
   four Overpass servers are gone and a failed request costs it 5 min 40 of doubling retries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.pipeline.build import BuildSpec, _vectors_osm_input

COLD = TileRef(45, 6)


def _spec(tmp_path: Path, **kw: object) -> BuildSpec:
    return BuildSpec(
        tile=COLD,
        provider="BI",
        zl=16,
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        out_dir=tmp_path / "out",
        **kw,  # type: ignore[arg-type]
    )


def test_the_estimate_plans_a_cold_tile_as_to_fetch(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    ref = _vectors_osm_input(spec, {}, None, planning=True)
    assert ref.rule == "osm_to_fetch"
    assert len(ref.key) == 64 and ref.key == ref.digest
    # stable: the same tile plans to the same placeholder, so the estimate is reproducible
    assert _vectors_osm_input(spec, {}, None, planning=True).key == ref.key


def test_a_real_build_with_no_layers_still_raises(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as exc:
        _vectors_osm_input(_spec(tmp_path), {}, None)
    assert exc.value.code == "OSM_LAYER_UNAVAILABLE"


def test_the_remedy_never_sends_the_user_to_the_ortho4xp_downloader(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as exc:
        _vectors_osm_input(_spec(tmp_path), {}, None)
    remedy = exc.value.remedy.lower()
    assert "ortho4xp" not in remedy and "downloads and caches" not in remedy
    assert "--osm-refresh" in remedy


def test_with_no_osm_fetch_the_remedy_names_the_flag(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as exc:
        _vectors_osm_input(_spec(tmp_path, osm_fetch=False), {}, None)
    assert "--no-osm-fetch" in exc.value.remedy
