"""What a tile was built with, written into its manifest (``PackManifest.built``).

A user asked where to see which data a tile was built with (2026-09-21). Before this, a pack kept
its imagery source, its level and its colours, and nothing said which relief it stood on -- and a
lidar relief chosen where the lidar never flew had quietly fallen back to Copernicus
(2026-09-20). The facts are written at build time, from the relief artefact itself for the relief.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from orthostudio import __version__
from orthostudio.model import TileRef
from orthostudio.pipeline.build import BuildSpec, built_facts
from orthostudio.pipeline.pack import PackManifest, relief_read

T = TileRef(51, -116)


@dataclass
class Ref:
    name: str
    key: str | None


@dataclass
class Why:
    inputs: tuple[Ref, ...]


@dataclass
class FakeStore:
    """The two calls relief_read makes: the inputs of a key, and where a key lies."""

    edges: dict[str, tuple[Ref, ...]] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)

    def why(self, key: str) -> Why:
        return Why(self.edges.get(key, ()))

    def path(self, key: str) -> Path:
        return self.paths[key]


def _store(tmp_path: Path, meta: object) -> FakeStore:
    """dsf -> vectors -> dem, the way the build graph wires them."""
    dem_dir = tmp_path / "dem"
    dem_dir.mkdir()
    (dem_dir / "meta.json").write_text(
        meta if isinstance(meta, str) else json.dumps(meta), encoding="utf-8"
    )
    return FakeStore(
        edges={
            "dsf": (Ref("vectors", "vec"), Ref("mesh", "mesh")),
            "vec": (Ref("osm", "osm"), Ref("dem", "dem")),
        },
        paths={"dem": dem_dir},
    )


def test_the_relief_really_read_comes_from_the_relief_artefact(tmp_path: Path) -> None:
    laid = _store(tmp_path, {"source": "COP30", "laid_over": ["HRDEM"]})
    assert relief_read(laid, "dsf") == {"relief": "COP30", "relief_laid": ["HRDEM"]}  # type: ignore[arg-type]


def test_a_lidar_that_never_flew_there_is_asked_but_not_laid(tmp_path: Path) -> None:
    """Banff: Canada's lidar was chosen, the square has none, Copernicus answered alone. The
    relief stage leaves an overlay that had nothing out of ``laid_over``."""
    alone = _store(tmp_path, {"source": "COP30", "laid_over": []})
    assert relief_read(alone, "dsf") == {"relief": "COP30", "relief_laid": []}  # type: ignore[arg-type]


def test_no_relief_to_read_is_no_fact_rather_than_an_error(tmp_path: Path) -> None:
    assert relief_read(FakeStore(), "dsf") == {}  # type: ignore[arg-type]
    assert relief_read(FakeStore(), None) == {}  # type: ignore[arg-type]
    broken = _store(tmp_path, "not json")
    assert relief_read(broken, "dsf") == {}  # type: ignore[arg-type]


def test_the_build_says_what_it_asked_for(tmp_path: Path) -> None:
    patches = tmp_path / "Patches" / T.name
    patches.mkdir(parents=True)
    (patches / "CBH2.patch.osm").write_text("<osm/>", encoding="utf-8")
    spec = BuildSpec(
        tile=T,
        provider="BI",
        zl=16,
        out_dir=tmp_path / "tiles",
        config={
            "custom_dem": "COP30;HRDEM",
            "zone_list": [
                [[51.1, -115.6, 51.2, -115.6, 51.2, -115.5], 17, "BI"],
                [[51.1, -115.6, 51.2, -115.6, 51.2, -115.5], 18, ""],  # the tile's own source
                ["not", "a", "zone", "at all"],  # skipped rather than failing the build
            ],
        },
        patches_dir=tmp_path / "Patches",
    )
    from orthostudio.imagery.providers import load_registry

    assert built_facts(spec) == {
        "version": __version__,
        "relief_asked": ["HRDEM"],
        "patches": ["CBH2.patch.osm"],
        "zones": [{"zl": 17, "provider": "BI"}, {"zl": 18, "provider": "BI"}],
        "imagery_credit": load_registry()["BI"].attribution,
    }
    # the X-Plane relief with nothing over it asks for no overlay
    plain = BuildSpec(tile=T, provider="BI", zl=16, out_dir=tmp_path / "t", config={})
    assert built_facts(plain)["relief_asked"] == []


def test_the_facts_are_kept_in_the_manifest_and_read_back() -> None:
    facts = {
        "version": "0.1.10",
        "relief": "COP30",
        "relief_laid": [],
        "relief_asked": ["HRDEM"],
        "patches": ["CBH2.patch.osm"],
        "zones": [{"zl": 17, "provider": "BI"}],
    }
    manifest = PackManifest("+51-116", "BI", 16, built=facts)
    text = manifest.to_toml()
    assert "[built]" in text and 'zones = [{ provider = "BI", zl = 17 }]' in text
    assert PackManifest.from_toml(text).built == facts
    # nothing that changes from one identical build to the next: two builds, one manifest
    assert "date" not in text and "time" not in text


def test_a_pack_written_before_this_version_reads_as_no_facts() -> None:
    old = PackManifest("+51-116", "BI", 16).to_toml()
    assert "[built]" not in old
    assert PackManifest.from_toml(old).built == {}


def test_the_credit_of_the_imagery_travels_with_the_pack(tmp_path: Path) -> None:
    """A pack is a folder people pass around, and it carried the code of the source and nothing
    else. EOX's Sentinel-2 is CC BY-NC-SA, so its credit and its licence have to go with the tile
    (found in review, 2026-09-23)."""
    from orthostudio.imagery.providers import load_registry
    from orthostudio.pipeline.pack import CREDITS_NAME, _write_credits

    eox = load_registry()["EOX"]
    assert eox.licence, "the source says what it is given under"

    facts = built_facts(BuildSpec(tile=T, provider="EOX", zl=14, out_dir=tmp_path, config={}))
    assert facts["imagery_credit"] == eox.attribution
    assert facts["imagery_licence"] == eox.licence

    pack = tmp_path / "pack"
    pack.mkdir()
    _write_credits(pack, T, facts)
    text = (pack / CREDITS_NAME).read_text("utf-8")
    assert T.name in text
    assert "EOX IT Services GmbH" in text
    assert "non-commercial" in text
    assert "OpenStreetMap" in text and "ODbL" in text

    # written again, it is the same file: a pack must not change when it is repaired
    before = text
    _write_credits(pack, T, facts)
    assert (pack / CREDITS_NAME).read_text("utf-8") == before

    # a source that gives no credit leaves no file behind
    bare = tmp_path / "bare"
    bare.mkdir()
    _write_credits(bare, T, {"version": "0"})
    assert not (bare / CREDITS_NAME).exists()


def test_a_tile_built_without_installing_is_in_the_library(tmp_path: Path) -> None:
    """Only installing ever wrote a library row, so a user who built without installing read "No
    tile. Build one, or import your Ortho4XP tiles" on the same screen as "Data used by your 3
    tile(s) -- 4.09 GB", with nothing offering to install them and nothing saying where they
    were (found in review, 2026-09-23)."""
    import inspect

    from orthostudio.install import Library
    from orthostudio.pipeline.build import _remember_the_tile, _verify_effects
    from orthostudio.pipeline.pack import PackManifest

    library = tmp_path / "library.sqlite"
    pack_dir = tmp_path / "tiles" / "zOrthoStudio_+51+000"
    pack_dir.mkdir(parents=True)
    tile = TileRef(51, 0)
    from orthostudio.pipeline.pack import ArtefactEntry

    manifest = PackManifest(
        tile=tile.name,
        provider="BI",
        zl=16,
        artefacts={"dsf": ArtefactEntry("d" * 64, "e" * 64, "tile.dsf")},
    )

    class JustTheLibrary:  # all ``_remember_the_tile`` reads of the environment
        def __init__(self, path: Path) -> None:
            self.library_path = path

    env = JustTheLibrary(library)

    _remember_the_tile(_spec_for(tile, tmp_path), pack_dir, manifest, env)
    with Library(library) as lib:
        rows = lib.list(tile=tile)
    assert [(r.provider, r.zl, r.built_by, r.path) for r in rows] == [("BI", 16, "osxp", pack_dir)]

    # a tile imported from Ortho4XP and then built here keeps what it is, or Delete would stop
    # refusing a folder it did not make
    with Library(library) as lib:
        lib.register(tile, "BI", 16, pack_dir, "ortho4xp", None)
    _remember_the_tile(_spec_for(tile, tmp_path), pack_dir, manifest, env)
    with Library(library) as lib:
        assert lib.list(tile=tile)[0].built_by == "ortho4xp"

    # and a library that will not open never costs anyone his build
    broken = JustTheLibrary(tmp_path / "no" / "such.sqlite")
    _remember_the_tile(_spec_for(tile, tmp_path), pack_dir, manifest, broken)

    # the wiring, read rather than run: building a TileNodes costs more than the change itself
    body = inspect.getsource(_verify_effects)
    assert "if not installed:\n        _remember_the_tile(" in body


def _spec_for(tile: TileRef, tmp_path: Path) -> BuildSpec:
    return BuildSpec(tile=tile, provider="BI", zl=16, out_dir=tmp_path / "tiles", config={})
