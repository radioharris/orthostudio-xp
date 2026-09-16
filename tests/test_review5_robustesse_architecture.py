"""Adversarial review 5 of P4 wave 1: ADR compliance, duplication, required layers.

Nothing here needs the network or a reference build.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import numpy as np
import pytest

from orthostudio.model import TileRef

SRC = Path("src/orthostudio")
VECTORS = SRC / "vectors"
NEW_MODULES = (
    "assemble",
    "geom",
    "coast",
    "grid",
    "layers",
    "osmdata",
    "patches",
    "roads",
    "seeds",
    "tags",
    "water",
    "rule",
)
TILE = TileRef.parse("+43+005")


AIRPORTS = SRC / "airports_vec"
"""Wave 2's package: the same ADRs apply to it (``airports-integration.md``)."""


def _module_paths() -> list[Path]:
    return [VECTORS / f"{name}.py" for name in NEW_MODULES] + sorted(AIRPORTS.glob("*.py"))


# -- 1. ADR compliance ---------------------------------------------------------------------


def test_no_vector_module_imports_ortho4xp_code() -> None:
    """ADR 0002: the native modules never import the Ortho4XP sources."""
    offenders = []
    for path in _module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("O4_") or "Ortho4XP" in name:
                    offenders.append(f"{path}:{node.lineno} imports {name}")
    assert offenders == []


def test_no_vector_module_uses_eval_exec_or_pickle_on_untrusted_input() -> None:
    """No vector module evaluates anything, and none reads a pickle.

    Wave 2 had added one ``pickle.dump`` in ``orthostudio.airports_vec.artefact``, the
    ``Data<tile>.apt`` bridge Ortho4XP's own stage 2 read (arbitration B3); it went with Ortho4XP's
    stages (decision 0009), and ``tests/test_p4v2_stage.py::test_nothing_in_the_engine_pickles``
    keeps it out. A docstring that *mentions* a pickle is not a use, hence the code-only patterns.
    """
    reading = (
        r"\beval\(",
        r"\bexec\(",
        r"^\s*import pickle\b",
        r"pickle\.load",
        r"pickle\.Unpickler",
        r"allow_pickle\s*=\s*True",
    )
    offenders = []
    for path in _module_paths():
        text = path.read_text(encoding="utf-8")
        for pattern in reading:
            for match in re.finditer(pattern, text, re.MULTILINE):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path}:{line} {match.group(0)}")
    assert offenders == []


def test_defect_the_replay_file_is_loaded_without_pinning_allow_pickle() -> None:
    """``np.load`` defaults to ``allow_pickle=False``; the writer never says so explicitly.

    ``_write_layers_npz`` uses ``np.savez_compressed`` and nothing in OrthoStudio XP reads it back,
    so the artefact is only ever consumed by a tool the user points at it. Recording the default
    here so that a future reader cannot quietly turn it on.
    """
    text = (VECTORS / "assemble.py").read_text(encoding="utf-8")
    assert "savez_compressed" in text
    assert "allow_pickle" not in text


def test_every_new_vector_module_has_a_spec_that_names_it() -> None:
    """ "Spec before code": each module's docstring must point at a ``docs/specs`` file."""
    specs = {p.name: p.read_text(encoding="utf-8") for p in Path("docs/specs").glob("*.md")}
    missing = []
    for path in _module_paths():
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        named = re.findall(r"docs/specs/([a-z0-9-]+\.md)", doc)
        if not named:
            missing.append(f"{path}: docstring names no spec")
            continue
        for name in named:
            if name not in specs:
                missing.append(f"{path}: names a spec that does not exist ({name})")
    assert missing == []


def test_no_private_symbol_of_the_noder_is_imported_across_modules() -> None:
    """Was ``test_a_private_symbol_of_the_noder_is_imported_across_modules``.

    The finding: ``assemble.py`` reached into ``noding._linework`` -- the only private
    cross-import of P4 -- so an internal rename of the P0 core would have broken the assembly
    with nothing to warn. Fixed: ``noding.linework`` is public and carries the contract in its
    docstring; ``_linework`` stays as a private alias for the P0 benchmarks.
    """
    text = (VECTORS / "assemble.py").read_text(encoding="utf-8")
    assert "from orthostudio.vectors.noding import linework as _geometry_parts" in text
    from orthostudio.vectors import noding

    assert noding.linework.__doc__ and "Public contract" in noding.linework.__doc__
    assert noding._linework is noding.linework

    offenders = []
    for path in _module_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("from OrthoStudio XP.") and " import " in line:
                imported = line.split(" import ", 1)[1]
                offenders += [
                    f"{path}: {line}"
                    for name in imported.replace("(", "").split(",")
                    if name.strip().startswith("_")
                ]
    assert offenders == []


# -- 2. duplication ----------------------------------------------------------------------------


def test_ensure_multipolygon_is_transcribed_once(tmp_path: Path) -> None:
    """Was ``test_defect_ensure_multipolygon_is_transcribed_three_times``.

    The finding: ``O4_Vector_Utils.py:779-793`` had three independent copies in
    ``orthostudio.vectors`` (coast, water, roads) that agreed today, which is the state in which one
    of them drifts unnoticed. Fixed: :mod:`orthostudio.vectors.geom` holds the single transcription,
    ``ensure_multipolygon`` for the ``MultiPolygon`` form and ``polygons_of`` for the list
    form, and the three builders import it. Same answers as before, one body.
    """
    from shapely import geometry

    from orthostudio.vectors import coast, roads, water
    from orthostudio.vectors.geom import ensure_multipolygon, polygons_of

    assert coast.ensure_multipolygon is ensure_multipolygon
    for module in (water, roads):
        source = inspect.getsource(module)
        assert "def _as_polygons" not in source and "def _polygons_of" not in source

    collection = geometry.GeometryCollection(
        [geometry.Polygon([(0, 0), (1, 0), (1, 1)]), geometry.LineString([(0, 0), (1, 1)])]
    )
    assert len(ensure_multipolygon(collection).geoms) == 1
    assert len(polygons_of(collection)) == 1
    line = geometry.LineString([(0, 0), (1, 1)])
    assert ensure_multipolygon(line).is_empty
    assert polygons_of(line) == []


def test_cut_to_tile_exists_once_with_one_signature() -> None:
    """Was ``test_defect_cut_to_tile_exists_twice_with_two_different_signatures``.

    The finding: ``coast.cut_to_tile`` and ``water.cut_to_tile`` were two transcriptions of
    ``:739-777`` with different signatures -- the water one took the window as four arguments,
    the coast one hard-coded the unit square -- so calling the wrong one was a silent no-op on
    the reference tile and a wrong cut on any other window. Fixed: one function in
    ``orthostudio.vectors.geom``, the window optional, re-exported by both modules.
    """
    from orthostudio.vectors.coast import cut_to_tile as coast_cut
    from orthostudio.vectors.geom import cut_to_tile as geom_cut
    from orthostudio.vectors.water import cut_to_tile as water_cut

    assert coast_cut is water_cut is geom_cut
    assert list(inspect.signature(geom_cut).parameters) == [
        "geom",
        "xmin",
        "xmax",
        "ymin",
        "ymax",
        "strictly_inside",
    ]


def test_the_wiring_no_longer_repeats_the_defaults_of_the_rule_parameters() -> None:
    """Was ``test_defect_the_wiring_repeats_every_default_of_the_rule_parameters``.

    The finding: ``layers.build_layers`` read the parameters with
    ``getattr(params, name, <default>)``, so every default was spelled a second time and a
    change to ``VectorsParams`` was not picked up by the wiring for a caller passing a partial
    params object. Fixed: the wiring reads the attributes directly, which is why -- as the old
    test put it -- this test has become a check that there is nothing left to check.
    """
    source = inspect.getsource(
        __import__("orthostudio.vectors.layers", fromlist=["x"]).build_layers
    )
    assert re.findall(r'getattr\(params,\s*"(\w+)"', source) == []
    from orthostudio.vectors.rule import VectorsParams

    for name in VectorsParams.model_fields:
        # the tile is the caller's; ``mesh_zl`` and ``exact_grid_order`` are the assembler's
        if name in ("tile", "mesh_zl", "exact_grid_order"):
            continue
        assert f"params.{name}" in source, name


# -- 3. every layer of the road level is required -----------------------------------------------


def test_a_road_level_above_one_refuses_to_build_with_fewer_layers(tmp_path: Path) -> None:
    """Was ``test_defect_a_road_level_above_one_silently_builds_with_fewer_layers``.

    The finding: ``layer_store(..., required=name == "big_roads")`` meant a tile whose
    ``small_roads`` cache is absent was built with the large roads only -- a different tile, no
    error, no warning above ``log.info``. The pipeline guarded it, the public builder did not.
    Fixed: every layer of ``layers_for(road_level)`` is required, which is Ortho4XP's semantics
    (it would download the missing one).
    """
    from orthostudio.errors import OsxpError
    from orthostudio.sources.osm import LAYERS, OsmSnapshot, SnapshotStore, layers_for
    from orthostudio.vectors import layers as layers_mod

    for layer in ("airports", "big_roads", "coastline", "water"):  # the layers of road level 1
        SnapshotStore(tmp_path).save(
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
    assert "small_roads" in [s.name for s in layers_for(3)]
    source = layers_mod.open_osm_source(tmp_path, TILE)
    assert not source.has("small_roads"), "the store has no small_roads snapshot"
    with pytest.raises(OsxpError) as err:
        layers_mod.layer_store(source, "small_roads")
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"
    assert 'required=name == "big_roads"' not in inspect.getsource(layers_mod.build_layers)


# -- 4. a file cut short -------------------------------------------------------------------------


def test_a_truncated_osm_file_is_refused(tmp_path: Path) -> None:
    """Was ``test_defect_the_two_readers_of_an_ortho4xp_cache_disagree_on_a_truncated_file``.

    The finding: two readers of the same OSM file disagreed on a download cut short, one raised
    ``OSM_CACHE_UNREADABLE``, the other quietly returned the nodes it had read. The second reader
    left with decision 0010; the one that remains still refuses a file without its ``</osm>``.
    """
    from orthostudio.errors import OsxpError
    from orthostudio.vectors.osmdata import OsmData

    body = (
        "<?xml version='1.0'?>\n<osm version='0.6'>\n"
        "<node id='1' lat='43.5' lon='5.5' />\n"
        "<node id='2' lat='43.6' lon='5.6' />\n"
    )
    truncated = tmp_path / "cut.osm"
    truncated.write_text(body, encoding="utf-8")
    with pytest.raises(OsxpError) as err:
        OsmData.load(truncated, layer="coastline", tile=TILE)
    assert err.value.code == "OSM_CACHE_UNREADABLE"
    whole = tmp_path / "whole.osm"
    whole.write_text(body + "</osm>\n", encoding="utf-8")
    assert OsmData.load(whole, layer=None, tile=TILE).counts["nodes"] == 2


# -- 5. the insertion order of the ways rests on CPython's set iteration ------------------------


def test_the_way_order_of_the_pslg_depends_on_cpython_set_iteration(tmp_path: Path) -> None:
    """``OsmData.first["w"]`` is a ``set``; iterating it is what fixes the insertion order.

    The order decides which layer wins a shared vertex's altitude, so the tile's altitudes
    depend on the interpreter's set implementation. It is Ortho4XP's behaviour and it is
    documented in ``coast.way_set_order``, but nothing in the suite pins it, and a change
    of interpreter (or of CPython's small-int hashing) would silently move altitudes.
    """
    from orthostudio.vectors.coast import way_set_order
    from orthostudio.vectors.osmdata import OsmData

    count = 40
    body = ["<?xml version='1.0'?>\n<osm version='0.6'>\n"]
    for i in range(1, count + 1):
        body.append(f"<node id='{i}' lat='43.{i:03d}' lon='5.{i:03d}' />\n")
    for w in range(count - 1):
        body.append(
            f"<way id='{100 + w}'>\n<nd ref='{w + 1}' />\n<nd ref='{w + 2}' />\n"
            "<tag k='natural' v='coastline' />\n</way>\n"
        )
    body.append("</osm>\n")
    path = tmp_path / "c.osm"
    path.write_text("".join(body), encoding="utf-8")

    data = OsmData.load(path, layer="coastline", tile=TILE)
    observed = [-w.id - 1 for w in data.ways_with()]
    assert observed == way_set_order(count - 1), (
        "the store's set order and coast.way_set_order must agree, "
        "they are two reconstructions of the same Ortho4XP accident"
    )
    assert observed != sorted(observed), "the order really is not the insertion order"


# -- 6. the coupling that justifies the reversed build order is inert --------------------------


def test_the_sea_equiv_pass_through_is_declared_for_what_it_is() -> None:
    """Was ``test_defect_the_sea_equiv_pass_through_changes_nothing_in_the_coast_result``.

    The finding: the module docstring and ``vectors-assembly.md`` section 7 justified building
    water *before* the coast by the ``sea_equiv`` coupling, and that coupling is inert --
    ``build_sea_layers`` only stores the argument, nothing reads it back. Fixed as
    documentation, not as code: the field is declared "reserved for wave 2, not read in
    wave 1" in both places, and the build order keeps Ortho4XP's own reason. The behaviour below
    is unchanged and pinned.
    """
    from shapely import geometry

    from orthostudio.vectors.coast import CoastParams, CoastResult, build_sea_layers

    ring = np.array([[5.4, 43.4], [5.6, 43.4], [5.6, 43.6], [5.4, 43.6], [5.4, 43.4]])
    lake = geometry.MultiPolygon([geometry.Polygon([(0.1, 0.1), (0.3, 0.1), (0.3, 0.3)])])
    plain = build_sea_layers([ring], TILE, CoastParams())
    carried = build_sea_layers([ring], TILE, CoastParams(), sea_equiv=lake)
    assert plain.sea_lines.equals_exact(carried.sea_lines, 0)
    assert np.array_equal(plain.seeds, carried.seeds)
    assert plain.sea_polygons.equals_exact(carried.sea_polygons, 0)
    assert plain.stats == carried.stats
    assert carried.sea_equiv is lake  # the only difference

    field_doc = CoastResult.__doc__ or ""
    annotation = inspect.getsource(CoastResult)
    assert "not read in wave 1" in annotation, field_doc
    assert "not read in wave 1" in (
        Path("docs/specs/vectors-assembly.md").read_text(encoding="utf-8")
    )
