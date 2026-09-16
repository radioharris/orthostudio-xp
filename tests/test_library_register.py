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
