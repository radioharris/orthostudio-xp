# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The tile's folder holds its clouds (``clouds.md`` 2.1): the pack places its textures'
description and their ``clouds/`` with the textures, before the manifest naming them; what the
tile holds is read from the folder alone, for the textures the manifest names; the store may lose
the artefact."""

from __future__ import annotations

import json
from pathlib import Path

from orthostudio.clouds.tile import read_tile
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import (
    CLOUDS_DIR,
    ArtefactEntry,
    PackManifest,
    _place_clouds,
    _sweep_clouds,
    write_manifest,
)

TILE = TileRef(43, 5)
NAME = "1488_2096_T12"


def _textures(root: Path, key: str, cloud: str | None) -> Path:
    """A textures artefact's folder as the textures step leaves it."""
    art = root / key
    applied = []
    if cloud is not None:
        part = art / CLOUDS_DIR / "parts" / f"{cloud}.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(b"part " + cloud.encode())
        original = art / CLOUDS_DIR / "originals" / "textures" / f"{NAME}.dds"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"DDS original")
        applied.append(
            {
                "id": cloud,
                "source": "EOX",
                "match": [[1, 1, 1], [0, 0, 0]],
                "rings": [[43.1, 5.1, 43.1, 5.2, 43.2, 5.2, 43.1, 5.1]],
                "parts": {NAME: [12, cloud]},
                "images": {NAME: "c"},
                "colours": {},
            }
        )
    art.mkdir(parents=True, exist_ok=True)
    doc = {
        "textures": [{"name": NAME, "chunks": "c"}],
        "colours": {"tile": [0, 0, 0], "zones": []},
        "clouds": {"applied": applied, "undone": {}},
    }
    (art / "manifest.json").write_text(json.dumps(doc))
    return art


def _name(folder: Path, key: str) -> None:
    """The pack's manifest naming the textures ``key``, written last."""
    manifest = PackManifest(TILE.name, "BI", 12, {"textures": ArtefactEntry(key, "d", "r@1")})
    write_manifest(folder, TILE, manifest)


def test_the_folder_holds_the_clouds_its_manifest_names(tmp_path: Path) -> None:
    folder = tmp_path / "zOrthoStudio_+43+005"
    first = _textures(tmp_path / "store", "k1", "aaaa1111")
    _place_clouds(first, folder, "k1", link=True)
    _name(folder, "k1")
    _sweep_clouds(first, folder, "k1")
    held = read_tile(folder)
    assert held is not None and held.described and [c["id"] for c in held.applied] == ["aaaa1111"]
    assert (held.parts_dir / "aaaa1111.part").read_bytes() == b"part aaaa1111"
    assert (held.originals_dir / f"{NAME}.dds").is_file()
    # the store loses the artefact: the tile keeps its clouds
    for f in sorted(first.rglob("*"), reverse=True):
        f.unlink() if f.is_file() else f.rmdir()
    first.rmdir()
    again = read_tile(folder)
    assert again is not None and [c["id"] for c in again.applied] == ["aaaa1111"]
    assert (again.parts_dir / "aaaa1111.part").is_file()


def test_a_pack_stopped_before_its_manifest_reads_the_clouds_before(tmp_path: Path) -> None:
    folder = tmp_path / "zOrthoStudio_+43+005"
    first = _textures(tmp_path / "store", "k1", "aaaa1111")
    _place_clouds(first, folder, "k1", link=True)
    _name(folder, "k1")
    _sweep_clouds(first, folder, "k1")
    second = _textures(tmp_path / "store", "k2", None)
    _place_clouds(second, folder, "k2", link=True)  # stopped here: the manifest still names k1
    held = read_tile(folder)
    assert held is not None and held.textures_key == "k1"
    assert [c["id"] for c in held.applied] == ["aaaa1111"]
    assert (held.parts_dir / "aaaa1111.part").is_file()
    _name(folder, "k2")
    _sweep_clouds(second, folder, "k2")
    held = read_tile(folder)
    assert held is not None and held.textures_key == "k2" and held.applied == ()
    assert sorted(p.name for p in (folder / CLOUDS_DIR).iterdir()) == ["k2.json"]


def test_a_folder_built_before_it_described_its_textures_holds_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "zOrthoStudio_+43+005"
    folder.mkdir()
    _name(folder, "k1")
    assert read_tile(folder) is None
