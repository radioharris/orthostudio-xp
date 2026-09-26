# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Adversarial review 5 of P4 wave 1: wiring, cancellation, coded errors.

These tests pin down what the wiring of ``src/orthostudio/vectors/layers.py``,
``src/orthostudio/vectors/rule.py`` and ``src/orthostudio/pipeline/build.py`` does at the edges. As
written by review 5, a test whose name started with ``test_defect_`` asserted the *current* (wrong)
behaviour on purpose, so that the finding was reproducible and the test turned red the day it was
fixed. The "P4 wave 1 correctives" chantier fixed those findings, so each such test has been turned
round: it now asserts the **corrected** behaviour and its docstring says what the defect was and
what replaced it. The tests that were already green are untouched.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from orthostudio.dem.dem import Dem
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.sources.osm import LAYERS, OsmSnapshot, SnapshotStore
from orthostudio.vectors import layers as layers_mod
from orthostudio.vectors.patches import PATCH_SUFFIX, build_patch_layers

TILE = TileRef.parse("+43+005")

PATCH_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='review5'>
  <node id='1' lat='43.5000000' lon='5.5000000' />
  <node id='2' lat='43.5010000' lon='5.5000000' />
  <node id='3' lat='43.5010000' lon='5.5010000' />
  <node id='4' lat='43.5000000' lon='5.5010000' />
  <way id='10'>
    <nd ref='1' />
    <nd ref='2' />
    <nd ref='3' />
    <nd ref='4' />
    <nd ref='1' />
    <tag k='altitude' v='120' />
  </way>
</osm>
"""


class FlatDem:
    """The smallest thing satisfying ``orthostudio.vectors.assemble.Elevation``."""

    def __init__(self, value: float = 0.0) -> None:
        self.alt_dem = np.full((4, 4), value, dtype=np.float32)

    def alt_vec(self, way):
        return np.zeros(len(np.asarray(way, dtype=np.float64).reshape(-1, 2)), dtype=np.float64)


def flat_dem(value: float = 0.0) -> Dem:
    """What ``build_layers`` needs since wave 2: a raster **and its window**, not a sampler.

    The airport smoothing rebuilds the elevation over the aerodromes, so the stage takes an
    ``orthostudio.dem@1`` artefact (``airports-integration.md`` R-I1); ``FlatDem`` above is still
    what the individual layer builders take.
    """
    return Dem(
        tile=TILE,
        alt_dem=np.full((4, 4), value, dtype=np.float32),
        x0=-0.01,
        y0=-0.01,
        x1=1.01,
        y1=1.01,
        nxdem=4,
        nydem=4,
    )


class Request:
    """A duck-typed ``orthostudio.vectors.rule.LayerRequest`` (``build_layers`` reads it
    by getattr)."""

    def __init__(self, osm: Path, **kw) -> None:
        self.tile = TILE
        self.params = kw.pop("params", None) or Params()
        self.osm = osm
        self.dem = kw.pop("dem", None) or flat_dem()
        self.patches = kw.pop("patches", None)
        self.airports = kw.pop("airports", None)
        self.cancel = kw.pop("cancel", None)


class Params:
    road_level = 0
    min_area = 0.001
    max_area = 200.0
    water_simplification = 0.0
    clean_bad_geometries = True
    apt_smoothing_pix = 8


def _empty_layer_cache(root: Path, layers=("coastline", "water", "big_roads", "airports")) -> Path:
    """An OSM snapshot store holding valid but empty layers."""
    store = SnapshotStore(root)
    for layer in layers:
        store.save(
            OsmSnapshot(
                tile=TILE,
                layer=layer,
                selectors=LAYERS[layer].selectors,
                query="q",
                mirror="test",
                fetched_at="",
                generator="",
                osm_base="",
                nodes=(),
                ways=(),
                relations=(),
                digest="0" * 64,
            )
        )
    return root


# -- 1. the patches input is resolved twice --------------------------------------------------


def test_a_patch_of_the_tile_reaches_the_layers(tmp_path: Path) -> None:
    """Was ``test_defect_the_patches_input_is_turned_into_a_patch_dir_twice``.

    The defect: the pipeline resolved the input to the tile's own patch folder and
    ``build_layers`` appended ``Patches/<tile>`` to it a second time; ``build_patch_layers``
    answered the missing folder with an empty result and no error, so every patch was dropped
    in silence. Fixed in ``layers.py`` (the input *is* the folder) and reported in
    ``patches.py`` (a directory that does not exist is now an event, not silence). The
    assertions below are the ones the old test said would turn red.
    """
    leaf = tmp_path / "patches"
    leaf.mkdir()
    (leaf / f"hill{PATCH_SUFFIX}").write_text(PATCH_OSM)

    direct = build_patch_layers(leaf, TILE, FlatDem())
    assert direct.counts["files"] == 1
    assert direct.layers, "the patch file does produce a layer"

    cache = _empty_layer_cache(tmp_path / "osm")
    built = layers_mod.build_layers(Request(cache, patches=leaf))
    assert len(built.layers.patches) == len(direct.layers) == 1


def test_a_patch_directory_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    """A missing folder must not be indistinguishable from an empty one (review 5)."""
    seen: list[str] = []
    result = build_patch_layers(
        tmp_path / "nowhere", TILE, FlatDem(), on_event=lambda err: seen.append(err.code)
    )
    assert result.counts["files"] == 0
    assert seen == ["OSM_PATCH_INVALID"]


# -- 2. an airports input is accepted and thrown away ----------------------------------------


def test_an_airport_input_is_refused_rather_than_silently_discarded(tmp_path: Path) -> None:
    """Was ``test_defect_an_airport_input_is_accepted_then_discarded_without_a_word``.

    The defect: ``_airport_areas`` / ``_airport_bounds`` returned the wave-1 value on *both*
    branches, so a wave-2 artefact would have been consumed, keyed into the cache and silently
    ignored. Fixed: an ``airports`` input raises ``SYS_INTERNAL_ERROR``. Wave 2 builds the
    aerodromes **inside** the stage, from the ``aeroway`` layer of the ``osm`` input
    (``O4_Vector_Map.py:184-197``), so the declared input still has no reader and the refusal
    stands -- with a message that now says why rather than "wave 2".
    """
    cache = _empty_layer_cache(tmp_path / "osm")
    airports = tmp_path / "airports"
    airports.mkdir()
    request = Request(cache, airports=airports)
    for call in (layers_mod._no_airport_artefact, layers_mod.build_layers):
        with pytest.raises(OsxpError) as err:
            call(request)
        assert err.value.code == "SYS_INTERNAL_ERROR"
        assert err.value.context["detail"] == "the airports input has no reader"
    # and with no airports input the stage builds, with an empty airport family: the cache
    # above holds a valid but empty aeroway layer, so this tile has no aerodrome
    built = layers_mod.build_layers(Request(cache))
    assert built.layers.airports == ()
    assert built.layers.airport_bounds is not None
    assert len(built.layers.airport_bounds) == 0


# -- 3. cancellation ---------------------------------------------------------------------------


def test_cancellation_is_seen_between_families_but_not_inside_one(tmp_path: Path) -> None:
    """``build_layers`` checks the token between families; the road builder does not.

    Amended by the review-6 fixes: the noder now reads the token before every pass
    (``node_layers(..., cancel=)``, review 6 measured 47 s of latency on 324 aerodromes), so
    it is no longer on the list of uninterruptible modules; the road builder still is.
    """
    import inspect

    cache = _empty_layer_cache(tmp_path / "osm")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OsxpError) as err:
        layers_mod.build_layers(Request(cache, cancel=cancel))
    assert err.value.code == "SYS_CANCELLED"

    # Review 5 measured a latency of about two seconds and asked for a checkpoint around the
    # noding: ``AssemblyParams.cancel`` is it. The noder and the road builder are still
    # uninterruptible inside one pass, which is documented in ``vectors-assembly.md`` 7.
    assert "cancel" in inspect.getsource(__import__("orthostudio.vectors.assemble", fromlist=["x"]))
    assert "cancel" in inspect.getsource(__import__("orthostudio.vectors.noding", fromlist=["x"]))
    for module in ("orthostudio.vectors.roads",):
        mod = __import__(module, fromlist=["x"])
        assert "cancel" not in inspect.getsource(mod), (
            f"{module} has no cancellation point: the longest single step of the stage "
            "(noding, then the road banking scan) cannot be interrupted"
        )


def test_the_rule_refuses_to_start_when_the_token_is_already_set(tmp_path: Path) -> None:
    from orthostudio.vectors.rule import VectorsJob, VectorsParams, run_vectors

    cancel = threading.Event()
    cancel.set()

    class Ctx:
        params = VectorsParams(tile=TILE.name)
        out = tmp_path
        inputs: ClassVar[dict] = {}

        def input_path(self, name: str) -> Path:
            return tmp_path

    from orthostudio.vectors.rule import vectors_job

    with vectors_job(VectorsJob(cancel=cancel)), pytest.raises(OsxpError) as err:
        run_vectors(Ctx())  # type: ignore[arg-type]
    assert err.value.code == "SYS_CANCELLED"


# -- 4. every error code the vector modules raise is in the registry --------------------------


def test_every_error_code_used_by_the_vector_modules_is_registered() -> None:
    import re

    from orthostudio.errors import REGISTRY

    # Review 6: the scan covered ``orthostudio.vectors`` only and missed the airport package of wave
    # 2 and its emission helpers (``_event``, ``_report``); both are included now.
    paths = sorted(Path("src/orthostudio/vectors").glob("*.py")) + sorted(
        Path("src/orthostudio/airports_vec").glob("*.py")
    )
    used: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'OsxpError\(\s*"([A-Z0-9_]+)"', text))
        used |= set(re.findall(r'on_skip\(\s*"([A-Z0-9_]+)"', text))
        used |= set(re.findall(r'_skip\([^,]+,\s*"([A-Z0-9_]+)"', text))
        used |= set(re.findall(r'_event\(\s*on_event,\s*"([A-Z0-9_]+)"', text))
        used |= set(re.findall(r'_report\(\s*on_event,\s*"([A-Z0-9_]+)"', text))
    assert used, "the scan found no code at all"
    assert "OSM_AIRPORT_SMOOTHING_INVALID" in used and "OSM_RUNWAY_REJECTED" in used
    unknown = sorted(code for code in used if code not in REGISTRY)
    assert unknown == [], (
        f"codes raised by orthostudio.vectors but absent from the registry: {unknown}"
    )


# -- 5. open_osm_source ------------------------------------------------------------------------


def test_open_osm_source_refuses_an_unrelated_directory(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(OsxpError) as err:
        layers_mod.open_osm_source(empty, TILE)
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"


def test_a_store_without_its_index_or_its_coastline_is_refused(tmp_path: Path) -> None:
    """``open_osm_source`` recognises a store by its ``snapshot.json`` or its coastline layer.

    A directory holding only the water snapshot is neither, and is refused with the remedy
    that names the ``orthostudio.osm@1`` artefact.
    """
    from orthostudio.sources.osm import SnapshotStore

    root = tmp_path / "snap"
    water = SnapshotStore(root).path_for(TILE, "water")
    water.parent.mkdir(parents=True)
    water.write_bytes(b"")
    with pytest.raises(OsxpError) as err:
        layers_mod.open_osm_source(root, TILE)
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"
    assert "orthostudio.osm@1" in (err.value.remedy or "")


def test_layer_store_reports_a_missing_required_layer_with_a_code(tmp_path: Path) -> None:
    cache = _empty_layer_cache(tmp_path / "osm", layers=("water",))
    source = layers_mod.OsmSource(cache, TILE)
    with pytest.raises(OsxpError) as err:
        layers_mod.layer_store(source, "coastline")
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"
    assert layers_mod.layer_store(source, "coastline", required=False) is None


def test_no_assert_is_left_between_the_caller_and_a_missing_layer() -> None:
    """Was ``test_defect_a_tile_with_no_coastline_cache_dies_where_the_water_one_does_not``.

    The defect: two ``assert store is not None`` were the only thing between the caller and a
    ``None``; under ``python -O`` they vanish and the stage died with an ``AttributeError``
    inside shapely instead of ``OSM_LAYER_UNAVAILABLE``. Fixed: ``layers._required`` raises the
    coded error, and no ``assert`` is left in the wiring.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(layers_mod))
    assert [n for n in ast.walk(tree) if isinstance(n, ast.Assert)] == []
    assert "def _required(" in inspect.getsource(layers_mod)
