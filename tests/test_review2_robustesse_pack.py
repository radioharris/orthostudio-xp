"""P2a adversarial review, robustness lens: the pack effect and the installation.

No network, no Ortho4XP. The first four tests were ``xfail(strict=True)`` findings of the review
(``pipeline-build.md`` 2.3, ``install.md`` 4), fixed in the P2a correction round: no ``.dds.bak``,
one level of a tile per output directory, a dangling link is replaced, one ``scenery_packs.ini``
save per install.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.imagery.providers import load_registry
from orthostudio.install import packs
from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.model import TileRef
from orthostudio.pipeline.build import BuildEnv, BuildSpec, declare, make_scheduler
from orthostudio.pipeline.pack import (
    PackManifest,
    install_receipt,
    pack_dir_name,
    pack_is_intact,
    write_pack,
)

T = TileRef(43, 5)
INI = (
    b"I\n1000 Version\nSCENERY\n\n"
    b"SCENERY_PACK Custom Scenery/Airport A/\n"
    b"SCENERY_PACK *GLOBAL_AIRPORTS*\n"
    b"SCENERY_PACK Custom Scenery/yAutoOrtho_Overlays/\n"
    b"SCENERY_PACK Custom Scenery/z_autoortho/\n"
)


def _artefacts(root: Path, *, dds: bytes, dsf: bytes, tag: str) -> tuple[Path, Path, Path]:
    dsf_dir = root / f"dsf-{tag}"
    (dsf_dir / "terrain").mkdir(parents=True)
    (dsf_dir / f"{T.name}.dsf").write_bytes(dsf)
    (dsf_dir / "terrain" / f"6000_8448_{tag}.ter").write_text("A\n800\nTERRAIN\n")
    tex_dir = root / f"tex-{tag}"
    (tex_dir / "textures").mkdir(parents=True)
    (tex_dir / "textures" / f"6000_8448_{tag}.dds").write_bytes(dds)
    overlay = root / f"overlay-{tag}.dsf"
    overlay.write_bytes(b"XPLNEDSF" + b"\2" * 50)
    return dsf_dir, tex_dir, overlay


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for p in root.rglob("*"):
        try:
            st = p.lstat()
        except OSError:
            continue
        out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


# -- findings of the review, fixed --------------------------------------------------------------


def test_replaced_dds_leaves_no_bak_in_the_pack(tmp_path: Path) -> None:
    dsf_dir, tex_dir, overlay = _artefacts(
        tmp_path, dds=b"DDS " + b"\1" * 64, dsf=b"D1", tag="BI14"
    )
    out = tmp_path / "out"
    write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay, link=True)
    # the coastal texture is re-encoded (masks_width changed): a new store artefact (new
    # inode) under the same name; the pack still hard-links the old one
    dds = tex_dir / "textures" / "6000_8448_BI14.dds"
    dds.unlink()
    dds.write_bytes(b"DDS " + b"\7" * 64)
    write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay, link=True)
    textures = out / pack_dir_name(T) / "textures"
    assert sorted(p.name for p in textures.iterdir()) == ["6000_8448_BI14.dds"]


def test_same_tile_at_two_levels_in_one_out_dir_is_refused(tmp_path: Path) -> None:
    # 1. the collision itself: two "levels" packed into one out dir wreck each other
    d14, t14, ov = _artefacts(tmp_path, dds=b"DDS14" * 8, dsf=b"DSF14", tag="BI14")
    d15, t15, _ = _artefacts(tmp_path, dds=b"DDS15" * 8, dsf=b"DSF15", tag="BI15")
    out = tmp_path / "out"
    f14 = write_pack(out, T, dsf_dir=d14, textures_dir=t14, overlay_file=ov)
    m14 = PackManifest(T.name, "BI", 14, {}, {
        "dsf": T.dsf_relpath.as_posix(), "dsf_size": f14.dsf_size,
        "textures": f14.textures, "terrain": f14.terrain, "overlay": "",
    })  # fmt: skip
    (f14.pack_dir / "orthostudio.toml").write_text(m14.to_toml())
    assert pack_is_intact(f14.pack_dir, m14)
    write_pack(out, T, dsf_dir=d15, textures_dir=t15, overlay_file=ov)
    # the ZL15 pack destroyed the ZL14 pack: its DSF is replaced, its DDS removed as stale
    # (pack_is_intact only compares sizes and counts, which the two levels share here)
    assert f14.dsf.read_bytes() == b"DSF15"
    assert not (f14.pack_dir / "textures" / "6000_8448_BI14.dds").exists()

    # 2. what the build must do: refuse the two specs (or give them distinct pack dirs)
    gs = tmp_path / "Global Scenery"
    p = gs / T.dsf_relpath
    p.parent.mkdir(parents=True)
    p.write_bytes(b"XPLNEDSF")
    env = BuildEnv(
        store=Store(tmp_path / "store", fsync=False), store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks", workdir=tmp_path / "work",
        global_scenery=gs, dsftool=None, registry=load_registry(), workers=1, library_path=None,
    )  # fmt: skip
    specs = [
        BuildSpec(
            tile=T,
            provider="BI",
            zl=zl,
            out_dir=out,
            store_root=tmp_path / "store",
            chunks_root=tmp_path / "chunks",
            workdir=tmp_path / "work",
        )
        for zl in (14, 15)
    ]
    with pytest.raises(OsxpError):
        declare(specs, make_scheduler(env.store, env, cpu_workers=1, cpu_in_threads=True), env)


def test_install_replaces_a_dangling_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    cs = tmp_path / "Custom Scenery"
    cs.mkdir()
    (cs / "scenery_packs.ini").write_bytes(INI)
    pack = tmp_path / "new" / pack_dir_name(T)
    (pack / "Earth nav data" / "+40+000").mkdir(parents=True)
    (pack / "Earth nav data" / "+40+000" / f"{T.name}.dsf").write_bytes(b"XPLNEDSF")
    os.symlink(tmp_path / "old" / pack_dir_name(T), cs / pack_dir_name(T))
    target = packs.install_pack(pack, cs)
    assert os.path.realpath(target) == os.path.realpath(pack)


# -- checks that pass -------------------------------------------------------------------------


def test_install_writes_only_under_custom_scenery_and_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    dsf_dir, tex_dir, overlay = _artefacts(tmp_path, dds=b"DDS" * 20, dsf=b"D", tag="BI14")
    out = tmp_path / "out"
    files = write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay)
    manifest = PackManifest(T.name, "BI", 14, {}, {
        "dsf": T.dsf_relpath.as_posix(), "dsf_size": files.dsf_size, "textures": 1,
        "terrain": 1, "overlay": f"../yOrthoStudio_Overlays/{T.dsf_relpath.as_posix()}",
    })  # fmt: skip
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    cs.mkdir(parents=True)
    (cs / "scenery_packs.ini").write_bytes(INI)
    library = tmp_path / "home" / "library.sqlite"

    before = _snapshot(tmp_path)
    receipt = install_receipt(files.pack_dir, cs, tile=T, library_path=library)
    after = _snapshot(tmp_path)

    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    allowed = ("X-Plane 12/Custom Scenery", "home")
    outside = sorted(k for k in changed if not k.startswith(allowed))
    assert not outside, f"install touched files outside its targets: {outside}"
    assert (cs / "scenery_packs.ini.bak").is_file()
    names = SceneryPacks.load(cs / "scenery_packs.ini").names()
    assert (
        names.index("yOrthoStudio_Overlays")
        < names.index(pack_dir_name(T))
        < names.index("z_autoortho")
    )
    assert names.index("yAutoOrtho_Overlays") < names.index("yOrthoStudio_Overlays")
    assert receipt["overlay_target"] is not None
    # idempotent: a second install changes nothing at all
    again = _snapshot(tmp_path)
    install_receipt(files.pack_dir, cs, tile=T, library_path=library)
    diff = {
        k
        for k in set(again) | set(_snapshot(tmp_path))
        if again.get(k) != _snapshot(tmp_path).get(k)
    }
    assert all(k.startswith("home") for k in diff), diff  # only the library's updated_at


def test_ini_backup_is_the_state_before_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    dsf_dir, tex_dir, overlay = _artefacts(tmp_path, dds=b"DDS" * 20, dsf=b"D", tag="BI14")
    files = write_pack(
        tmp_path / "out", T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay
    )
    manifest = PackManifest(T.name, "BI", 14, {}, {
        "dsf": T.dsf_relpath.as_posix(), "dsf_size": files.dsf_size, "textures": 1,
        "terrain": 1, "overlay": f"../yOrthoStudio_Overlays/{T.dsf_relpath.as_posix()}",
    })  # fmt: skip
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    cs = tmp_path / "Custom Scenery"
    cs.mkdir()
    (cs / "scenery_packs.ini").write_bytes(INI)
    install_receipt(files.pack_dir, cs, tile=T, library_path=tmp_path / "lib.sqlite")
    assert (cs / "scenery_packs.ini.bak").read_bytes() == INI


def test_install_refuses_while_xplane_runs_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    dsf_dir, tex_dir, overlay = _artefacts(tmp_path, dds=b"DDS" * 20, dsf=b"D", tag="BI14")
    files = write_pack(
        tmp_path / "out", T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay
    )
    (files.pack_dir / "orthostudio.toml").write_text(PackManifest(T.name, "BI", 14).to_toml())
    cs = tmp_path / "Custom Scenery"
    cs.mkdir()
    (cs / "scenery_packs.ini").write_bytes(INI)
    before = _snapshot(cs)
    with pytest.raises(OsxpError) as exc:
        install_receipt(files.pack_dir, cs, tile=T, library_path=tmp_path / "lib.sqlite")
    assert exc.value.code == "XP_RUNNING"
    assert _snapshot(cs) == before
    assert not (tmp_path / "lib.sqlite").exists()


def test_pack_rewrite_is_per_file_atomic_and_keeps_dsf_bak(tmp_path: Path) -> None:
    dsf_dir, tex_dir, overlay = _artefacts(tmp_path, dds=b"DDS" * 20, dsf=b"DSF-1", tag="BI14")
    out = tmp_path / "out"
    write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay)
    (dsf_dir / f"{T.name}.dsf").unlink()  # a new DSF artefact: new inode
    (dsf_dir / f"{T.name}.dsf").write_bytes(b"DSF-2")
    files = write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=overlay)
    assert files.dsf.read_bytes() == b"DSF-2"
    assert files.dsf.with_name(files.dsf.name + ".bak").read_bytes() == b"DSF-1"
    leftovers = [p.name for p in files.pack_dir.rglob("*.tmp-*")]
    assert not leftovers, leftovers
