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
    assert built_facts(spec) == {
        "version": __version__,
        "relief_asked": ["HRDEM"],
        "patches": ["CBH2.patch.osm"],
        "zones": [{"zl": 17, "provider": "BI"}, {"zl": 18, "provider": "BI"}],
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
