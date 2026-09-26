# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Overlay build mechanics with a fake DSFTool: sources, 7z, errors, atomic output, paths."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

import py7zr
import pytest

from orthostudio.errors import OsxpError
from orthostudio.overlays import (
    OverlayExclusions,
    TileRef,
    build_overlay,
    build_overlay_detailed,
    find_dsftool,
    materialize_source,
    overlay_dsf_path,
    overlay_source_path,
    resolve_global_scenery_dir,
    run_dsftool,
)
from orthostudio.overlays.dsftool import dsftool_platform_dir
from orthostudio.overlays.source import SEVENZIP_MAGIC

TILE = TileRef(43, 5)
FAKE_DSF = b"XPLNEDSF" + b"\x01\x00\x00\x00" + b"fake atoms" * 100

FAKE_TEXT = b"""A
800 written by fake DSFTool
DSF2TEXT

PROPERTY sim/west 5
PROPERTY sim/creation_agent fake
TERRAIN_DEF terrain_Water
POLYGON_DEF lib/g12/beaches.bch
POLYGON_DEF lib/g8/broad_tmp_sdry.for
NETWORK_DEF lib/g10/roads_EU.net
BEGIN_PATCH 0 0.0 -1.0 1 7
BEGIN_PRIMITIVE 0
PATCH_VERTEX 5.0 43.0 0.0 0.0 0.0 1.0 1.0
END_PRIMITIVE
BEGIN_POLYGON 0 255 2
BEGIN_WINDING
POLYGON_POINT 5.1 43.1
END_WINDING
END_POLYGON
BEGIN_POLYGON 1 255 2
BEGIN_WINDING
POLYGON_POINT 5.2 43.2
END_WINDING
END_POLYGON
BEGIN_SEGMENT 0 10 1 5.0 43.0 0.0
END_SEGMENT 2 5.1 43.1 0.0
END_PATCH
# Result code: 0
"""

FAKE_TOOL = r"""
import hashlib, os, sys, time
mode = os.environ.get("FAKE_DSFTOOL_MODE", "ok")
op, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
if mode == "exit1":
    print("fake crash"); sys.exit(1)
if mode == "hang":
    time.sleep(30)
data = open(src, "rb").read()
if op == "--dsf2text":
    assert data.startswith(b"XPLNEDSF"), "not a DSF"
    text = open(os.environ["FAKE_DSFTOOL_TEXT"], "rb").read()
    if mode == "resultcode1":
        text = text.replace(b"# Result code: 0", b"# Result code: 1")
    if mode == "noresult":
        text = text.replace(b"# Result code: 0\n", b"")
    open(dst, "wb").write(text)
    open(dst + ".elevation.raw", "wb").write(b"\0" * 16)
    print("File %s had 1 ter, 0 obj, 2 pol, 1 net." % src)
elif op == "--text2dsf":
    if mode == "nooutput":
        sys.exit(0)
    magic = b"NOTADSF!" if mode == "nomagic" else b"XPLNEDSF"
    open(dst, "wb").write(magic + hashlib.sha256(data).digest() + data)
    print("Converted %s to %s" % (src, dst))
else:
    sys.exit(2)
"""


def expected_output(filtered_text: bytes) -> bytes:
    return b"XPLNEDSF" + hashlib.sha256(filtered_text).digest() + filtered_text


@pytest.fixture
def fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_dsftool.py"
    script.write_text(FAKE_TOOL)
    text = tmp_path / "fake_text.txt"
    text.write_bytes(FAKE_TEXT)
    monkeypatch.setenv("FAKE_DSFTOOL_TEXT", str(text))
    monkeypatch.delenv("FAKE_DSFTOOL_MODE", raising=False)
    if os.name == "nt":  # pragma: no cover - CI smoke only
        tool = tmp_path / "DSFTool.cmd"
        tool.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        tool = tmp_path / "DSFTool"
        tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return tool


def make_source(root: Path, payload: bytes = FAKE_DSF, *, compress: bool = True) -> Path:
    src = overlay_source_path(root, TILE.lat, TILE.lon)
    src.parent.mkdir(parents=True, exist_ok=True)
    if compress:
        with py7zr.SevenZipFile(src, "w") as z:
            z.writestr(payload, "+43+005.dsf")
        assert src.read_bytes().startswith(SEVENZIP_MAGIC)
    else:
        src.write_bytes(payload)
    return src


@pytest.fixture
def scenery(tmp_path: Path) -> Path:
    root = tmp_path / "Global Scenery" / "X-Plane 12 Global Scenery"
    make_source(root)
    return root


# ------------------------------------------------------------------------------- paths


def test_overlay_paths() -> None:
    assert overlay_source_path(Path("/gs"), 43, 5) == Path("/gs/Earth nav data/+40+000/+43+005.dsf")
    assert overlay_dsf_path(Path("/out"), TILE) == Path(
        "/out/yOrthoStudio_Overlays/Earth nav data/+40+000/+43+005.dsf"
    )
    assert overlay_dsf_path(Path("/out"), TileRef(-34, -58)) == Path(
        "/out/yOrthoStudio_Overlays/Earth nav data/-40-060/-34-058.dsf"
    )
    assert overlay_dsf_path(Path("/out"), TileRef(0, -1)).name == "+00-001.dsf"


def test_resolve_global_scenery_dir_accepts_the_xplane_root(tmp_path: Path, scenery: Path) -> None:
    assert resolve_global_scenery_dir(scenery) == scenery
    assert resolve_global_scenery_dir(tmp_path) == scenery
    assert overlay_source_path(tmp_path, 43, 5) == overlay_source_path(scenery, 43, 5)
    other = tmp_path / "nothing"
    assert resolve_global_scenery_dir(other) == other


# ------------------------------------------------------------------------------- source


def test_materialize_7z_source(tmp_path: Path, scenery: Path) -> None:
    src = overlay_source_path(scenery, 43, 5)
    info = materialize_source(src, tmp_path / "work", tile="+43+005")
    assert info.compressed and info.path == tmp_path / "work" / "+43+005.dsf"
    assert info.path.read_bytes() == FAKE_DSF
    assert info.size == src.stat().st_size and info.extracted == len(FAKE_DSF)
    assert info.source == src


def test_materialize_plain_source_in_place(tmp_path: Path) -> None:
    root = tmp_path / "gs"
    src = make_source(root, compress=False)
    info = materialize_source(src, tmp_path / "work", tile="+43+005")
    assert not info.compressed and info.path == src and info.extracted is None
    assert not (tmp_path / "work").exists()


def test_materialize_errors(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as info:
        materialize_source(tmp_path / "absent.dsf", tmp_path / "w", tile="+43+005")
    assert info.value.code == "DSF_OVERLAY_SOURCE_MISSING"
    assert info.value.context["tile"] == "+43+005"

    bad_plain = tmp_path / "plain.dsf"
    bad_plain.write_bytes(b"NOTADSF!" * 4)
    with pytest.raises(OsxpError) as info:
        materialize_source(bad_plain, tmp_path / "w", tile="+43+005")
    assert info.value.code == "DSF_SOURCE_CORRUPTED"

    truncated = tmp_path / "trunc.dsf"
    with py7zr.SevenZipFile(truncated, "w") as z:
        z.writestr(FAKE_DSF, "+43+005.dsf")
    truncated.write_bytes(truncated.read_bytes()[:64])
    with pytest.raises(OsxpError) as info:
        materialize_source(truncated, tmp_path / "w1", tile="+43+005")
    assert info.value.code == "DSF_SOURCE_DECOMPRESS_FAILED"

    two = tmp_path / "two.dsf"
    with py7zr.SevenZipFile(two, "w") as z:
        z.writestr(FAKE_DSF, "a.dsf")
        z.writestr(FAKE_DSF, "b.dsf")
    with pytest.raises(OsxpError) as info:
        materialize_source(two, tmp_path / "w2", tile="+43+005")
    assert info.value.code == "DSF_SOURCE_DECOMPRESS_FAILED"
    assert "2 members" in str(info.value.context["reason"])

    not_dsf = tmp_path / "notdsf.dsf"
    with py7zr.SevenZipFile(not_dsf, "w") as z:
        z.writestr(b"hello world", "+43+005.dsf")
    with pytest.raises(OsxpError) as info:
        materialize_source(not_dsf, tmp_path / "w3", tile="+43+005")
    assert info.value.code == "DSF_SOURCE_CORRUPTED"


# ------------------------------------------------------------------------------ dsftool


def test_run_dsftool_ok(tmp_path: Path, fake_tool: Path) -> None:
    dsf = tmp_path / "in.dsf"
    dsf.write_bytes(FAKE_DSF)
    txt = tmp_path / "w" / "out.txt"
    txt.parent.mkdir()
    run = run_dsftool(fake_tool, "dsf2text", dsf, txt)
    assert run.returncode == 0 and run.seconds > 0 and "had 1 ter" in run.stdout
    assert run.command[1] == "--dsf2text"
    assert txt.read_bytes() == FAKE_TEXT
    out = tmp_path / "w" / "out.dsf"
    run2 = run_dsftool(fake_tool, "text2dsf", txt, out)
    assert run2.returncode == 0 and out.read_bytes() == expected_output(FAKE_TEXT)


@pytest.mark.parametrize(
    ("mode", "op", "reason"),
    [
        ("exit1", "dsf2text", "exit code 1"),
        ("resultcode1", "dsf2text", "result code 1"),
        ("noresult", "dsf2text", "no result code"),
        ("nomagic", "text2dsf", "not a DSF"),
        ("nooutput", "text2dsf", "no output file"),
    ],
)
def test_run_dsftool_failures(
    tmp_path: Path,
    fake_tool: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    op: str,
    reason: str,
) -> None:
    monkeypatch.setenv("FAKE_DSFTOOL_MODE", mode)
    src = tmp_path / "in.bin"
    src.write_bytes(FAKE_DSF if op == "dsf2text" else FAKE_TEXT)
    with pytest.raises(OsxpError) as info:
        run_dsftool(fake_tool, op, src, tmp_path / "out.bin")  # type: ignore[arg-type]
    err = info.value
    assert err.code == "DSF_OVERLAY_TOOL_FAILED"
    assert reason in str(err.context["reason"])
    assert err.context["path"] == str(src)
    if mode == "exit1":
        assert err.context["returncode"] == 1 and "fake crash" in str(err.context["output_tail"])
    if mode == "resultcode1":
        assert err.context["returncode"] == 1


def test_run_dsftool_timeout_and_missing_binary(
    tmp_path: Path, fake_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "in.dsf"
    src.write_bytes(FAKE_DSF)
    monkeypatch.setenv("FAKE_DSFTOOL_MODE", "hang")
    with pytest.raises(OsxpError) as info:
        run_dsftool(fake_tool, "dsf2text", src, tmp_path / "out.txt", timeout_s=0.5)
    assert info.value.code == "DSF_OVERLAY_TOOL_FAILED"
    assert "timeout" in str(info.value.context["reason"])
    with pytest.raises(OsxpError) as info:
        run_dsftool(tmp_path / "nope" / "DSFTool", "dsf2text", src, tmp_path / "out.txt")
    assert "not found" in str(info.value.context["reason"])


def test_find_dsftool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OrthoStudio XP's own binary (native/dsftool), and no other."""
    sub = dsftool_platform_dir()
    exe = "DSFTool.exe" if sub == "win" else "DSFTool"

    def tool_at(path: Path) -> Path:
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    own = tool_at(tmp_path / "native" / sub / exe)
    assert find_dsftool(own_dir=tmp_path / "native") == own
    assert find_dsftool(own_dir=tmp_path / "empty") is None
    # the repository's own copy, for this platform, runs
    shipped = find_dsftool()
    assert shipped is not None and shipped.parts[-4:-1] == ("native", "dsftool", sub)


# -------------------------------------------------------------------------------- build


def test_build_overlay_end_to_end(tmp_path: Path, scenery: Path, fake_tool: Path) -> None:
    out_root = tmp_path / "out"
    work = tmp_path / "work"
    result = build_overlay_detailed(
        scenery, TILE, OverlayExclusions(), dsftool=fake_tool, out_root=out_root, workdir=work
    )
    final = out_root / "yOrthoStudio_Overlays" / "Earth nav data" / "+40+000" / "+43+005.dsf"
    assert result.path == final and final.is_file()
    filtered = (
        b"PROPERTY sim/overlay 1\n"
        b"PROPERTY sim/west 5\n"
        b"PROPERTY sim/creation_agent fake\n"
        b"POLYGON_DEF lib/g12/beaches.bch\n"
        b"POLYGON_DEF lib/g8/broad_tmp_sdry.for\n"
        b"NETWORK_DEF lib/g10/roads_EU.net\n"
        b"BEGIN_POLYGON 1 255 2\nBEGIN_WINDING\nPOLYGON_POINT 5.2 43.2\nEND_WINDING\nEND_POLYGON\n"
        b"BEGIN_SEGMENT 0 10 1 5.0 43.0 0.0\nEND_SEGMENT 2 5.1 43.1 0.0\n"
    )
    assert final.read_bytes() == expected_output(filtered)
    s = result.stats
    assert s.tile == "+43+005" and s.compressed and s.extracted_size == len(FAKE_DSF)
    assert s.text_size == len(FAKE_TEXT) and s.filtered_text_size == len(filtered)
    assert s.output_size == final.stat().st_size
    assert s.filter.polygons_dropped == 1 and s.filter.segments_kept == 1
    assert s.filter.excluded_polygon_indices == (0,)
    assert s.seconds_total >= s.seconds_dsf2text + s.seconds_text2dsf
    assert "had 1 ter" in s.dsftool_output and "Converted" in s.dsftool_output
    d = s.to_dict()
    seconds = d["seconds"]
    assert isinstance(seconds, dict) and seconds["total"] > 0
    assert isinstance(d["filter"], dict) and d["filter"]["polygon_defs"][0] == "lib/g12/beaches.bch"
    # work directory removed whole, nothing next to the final file but the file
    assert work.is_dir() and list(work.iterdir()) == []
    assert [p.name for p in final.parent.iterdir()] == ["+43+005.dsf"]


def test_build_overlay_contract_entry_point_and_rebuild(
    tmp_path: Path, scenery: Path, fake_tool: Path
) -> None:
    out_root = tmp_path / "out"
    p1 = build_overlay(scenery, TILE, dsftool=fake_tool, out_root=out_root, workdir=tmp_path / "w")
    first = p1.read_bytes()
    p2 = build_overlay(
        scenery,
        TILE,
        OverlayExclusions(ovl_exclude_pol=[]),
        dsftool=fake_tool,
        out_root=out_root,
        workdir=tmp_path / "w",
    )
    assert p1 == p2 and p2.read_bytes() != first  # replaced in place, atomically
    assert [p.name for p in p2.parent.iterdir()] == ["+43+005.dsf"]


def test_build_overlay_accepts_xplane_root_and_plain_dsf(tmp_path: Path, fake_tool: Path) -> None:
    make_source(tmp_path / "Global Scenery" / "X-Plane 12 Global Scenery", compress=False)
    r = build_overlay_detailed(
        tmp_path, TILE, dsftool=fake_tool, out_root=tmp_path / "o", workdir=tmp_path / "w"
    )
    assert r.path.is_file() and not r.stats.compressed and r.stats.extracted_size is None


def test_build_overlay_keep_workdir(tmp_path: Path, scenery: Path, fake_tool: Path) -> None:
    work = tmp_path / "work"
    r = build_overlay_detailed(
        scenery, TILE, dsftool=fake_tool, out_root=tmp_path / "o", workdir=work, keep_workdir=True
    )
    kept = list(work.iterdir())
    assert len(kept) == 1 and kept[0].name.startswith("+43+005-")
    names = sorted(p.name for p in kept[0].iterdir())
    # the extracted DSF, the filtered text, the DSF built from it; the 170 MB dsf2text output
    # is deleted before text2dsf, its raster sidecars stay with the kept work directory
    assert [n for n in names if not n.endswith(".raw")] == [
        "+43+005.dsf",
        "+43+005_overlay.dsf",
        "+43+005_overlay.txt",
    ]
    assert "+43+005.txt.elevation.raw" in names
    assert r.path.is_file()


def test_build_overlay_missing_source(tmp_path: Path, fake_tool: Path) -> None:
    work = tmp_path / "work"
    with pytest.raises(OsxpError) as info:
        build_overlay(
            tmp_path / "gs", TILE, dsftool=fake_tool, out_root=tmp_path / "o", workdir=work
        )
    assert info.value.code == "DSF_OVERLAY_SOURCE_MISSING"
    assert (
        Path(info.value.context["path"]).as_posix().endswith("Earth nav data/+40+000/+43+005.dsf")
    )
    assert list(work.iterdir()) == []
    assert not (tmp_path / "o").exists()


def test_build_overlay_tool_failure_cleans_up(
    tmp_path: Path, scenery: Path, fake_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DSFTOOL_MODE", "exit1")
    work = tmp_path / "work"
    with pytest.raises(OsxpError) as info:
        build_overlay(scenery, TILE, dsftool=fake_tool, out_root=tmp_path / "o", workdir=work)
    assert info.value.code == "DSF_OVERLAY_TOOL_FAILED"
    assert list(work.iterdir()) == []
    assert not (tmp_path / "o").exists()
