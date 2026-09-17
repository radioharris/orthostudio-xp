"""The folder of hand-made mesh patches a user names in Settings (2026-09-17).

Ortho4XP reads its own ``Patches/<tile>/*.patch.osm``; OrthoStudio XP reads the folder the user
gives (decision 0010 stopped reading Ortho4XP's). The patch reader itself is
``tests/test_vectors_patches.py``: here, how a folder becomes an input of the vector node.
"""

from __future__ import annotations

from pathlib import Path

from orthostudio.model import TileRef
from orthostudio.pipeline.build import patches_ref

TILE = TileRef(43, 5)
OTHER = TileRef(46, 6)


def _patch(folder: Path, name: str = "a.patch.osm", alt: str = "42") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<osm version='0.6' generator='JOSM'>\n"
        "  <node id='-1' lat='43.2' lon='5.2' />\n"
        "  <way id='-1'>\n    <nd ref='-1'/>\n"
        f"    <tag k='cst_alt_abs' v='{alt}'/>\n  </way>\n</osm>",
        encoding="utf-8",
    )
    return path


def test_no_folder_no_patch(tmp_path: Path) -> None:
    assert patches_ref(None, TILE) is None
    assert patches_ref(tmp_path / "nowhere", TILE) is None
    # a folder of patches for another tile leaves this one alone
    _patch(tmp_path / "Patches" / OTHER.name)
    assert patches_ref(tmp_path / "Patches", TILE) is None
    # a tile directory without a *.patch.osm counts as none
    (tmp_path / "Patches" / TILE.name).mkdir(parents=True)
    (tmp_path / "Patches" / TILE.name / "notes.txt").write_text("hello", encoding="utf-8")
    assert patches_ref(tmp_path / "Patches", TILE) is None


def test_the_input_is_the_tile_folder_keyed_by_what_it_holds(tmp_path: Path) -> None:
    folder = tmp_path / "Patches" / TILE.name
    first = _patch(folder)
    ref = patches_ref(tmp_path / "Patches", TILE)
    assert ref is not None
    assert ref.path == folder and ref.kind == "dir" and ref.rule == "patches"
    assert ref.size == first.stat().st_size and ref.key == ref.digest
    # editing a patch changes the key: the tile is built again
    _patch(folder, alt="43")
    edited = patches_ref(tmp_path / "Patches", TILE)
    assert edited is not None and edited.digest != ref.digest
    # a second patch counts too, and removing it comes back to the first key
    _patch(folder, name="b.patch.osm")
    assert patches_ref(tmp_path / "Patches", TILE).digest != edited.digest  # type: ignore[union-attr]
    (folder / "b.patch.osm").unlink()
    assert patches_ref(tmp_path / "Patches", TILE).digest == edited.digest  # type: ignore[union-attr]
