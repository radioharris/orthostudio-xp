"""The library stores absolute paths (review finding 9 of the library delete).

``osxp build --out tiles --install`` registered ``tiles/zOrthoStudio_<tile>`` as given, and
``osxp serve``, started from another folder, then looked for the pack there: the Library said the
tile's folder was gone, and a delete forgot the row without deleting the pack.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orthostudio.install import Library, packs
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import PackManifest, install_receipt, write_pack

T = TileRef(43, 5)


def test_a_relative_path_is_stored_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    src = tmp_path / "src"
    (src / "dsf").mkdir(parents=True)
    (src / "dsf" / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "tex" / "textures").mkdir(parents=True)
    build = tmp_path / "build"
    (build / "tiles").mkdir(parents=True)
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    cs.mkdir(parents=True)
    library = tmp_path / "library.sqlite"
    monkeypatch.chdir(build)  # osxp build --out tiles --install, run from ./build
    files = write_pack(Path("tiles"), T, dsf_dir=src / "dsf", textures_dir=src / "tex",
                       overlay_file=None)  # fmt: skip
    manifest = PackManifest(T.name, "BI", 16, {}, {"dsf": T.dsf_relpath.as_posix()})
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())

    install_receipt(files.pack_dir, cs, tile=T, library_path=library)
    with Library(library) as lib:
        lib.register(T, "BI", 16, Path("tiles") / "elsewhere", "osxp", {})

    monkeypatch.chdir(tmp_path)  # osxp serve, started from another folder
    with Library(library) as lib:
        paths = sorted(str(r.path) for r in lib.list())
    assert paths == [
        str(build / "tiles" / "elsewhere"),
        str(build / "tiles" / "zOrthoStudio_+43+005"),
    ]
    assert all(os.path.isabs(p) for p in paths)
    assert Path(paths[1]).is_dir()


def test_installing_a_tile_never_changes_who_built_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user copied tiles OrthoStudio XP had built into his Ortho4XP folder, imported them,
    added one to X-Plane and then deleted it: it was deleted inside the Ortho4XP folder
    (2026-09-20).

    Installing registered the row with ``built_by="osxp"`` whatever it held, so adding an
    imported tile to X-Plane turned it into a tile OrthoStudio XP claimed to have built, and
    Delete, which refuses what it did not build, no longer had anything to refuse. Who built a
    pack is a fact about the pack: it is written once and left alone."""
    from orthostudio.errors import OsxpError
    from orthostudio.pipeline.pack import delete_receipt

    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    src = tmp_path / "src"
    (src / "dsf").mkdir(parents=True)
    (src / "dsf" / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "tex" / "textures").mkdir(parents=True)
    ortho4xp = tmp_path / "Ortho4XP" / "Tiles"
    ortho4xp.mkdir(parents=True)
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    cs.mkdir(parents=True)
    library = tmp_path / "library.sqlite"

    files = write_pack(ortho4xp, T, dsf_dir=src / "dsf", textures_dir=src / "tex",
                       overlay_file=None)  # fmt: skip
    manifest = PackManifest(T.name, "BI", 16, {}, {"dsf": T.dsf_relpath.as_posix()})
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())

    with Library(library) as lib:  # as the import writes it
        lib.register(T, "BI", 16, files.pack_dir, "ortho4xp", None)
    install_receipt(files.pack_dir, cs, tile=T, library_path=library)
    with Library(library) as lib:
        rows = [r for r in lib.list(tile=T) if r.kind == "ortho"]
    assert [r.built_by for r in rows] == ["ortho4xp"], "installing rewrote who built it"

    # and so the delete still refuses it, and takes nothing away
    with pytest.raises(OsxpError) as raised:
        delete_receipt(files.pack_dir, tile=T, custom_scenery=cs, library_path=library)
    assert raised.value.code == "SYS_PACK_NOT_OSXP"
    assert files.pack_dir.is_dir()
