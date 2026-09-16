"""CLI of P2a: ``osxp plan --offline --json``, ``osxp install``, ``osxp import-ortho4xp``,
``osxp library``, ``osxp why`` (spec ``pipeline-build.md`` 5-6). No network, no Ortho4XP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orthostudio.cli import app
from orthostudio.graph import Executor, Node, Rule, RuleParams, RunContext, Store
from orthostudio.install import packs
from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.model import TileRef

runner = CliRunner()
T = TileRef(43, 5)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("OSXP_HOME", str(h))
    return h


@pytest.fixture
def global_scenery(tmp_path: Path) -> Path:
    gs = tmp_path / "Global Scenery"
    p = gs / T.dsf_relpath
    p.parent.mkdir(parents=True)
    p.write_bytes(b"XPLNEDSF")
    return gs


@pytest.fixture
def xplane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    xp = tmp_path / "X-Plane 12"
    (xp / "Resources").mkdir(parents=True)
    cs = xp / "Custom Scenery"
    cs.mkdir()
    (cs / "scenery_packs.ini").write_bytes(
        b"I\n1000 Version\nSCENERY\n\nSCENERY_PACK *GLOBAL_AIRPORTS*\n"
        b"SCENERY_PACK Custom Scenery/z_autoortho/\n"
    )
    return xp


def test_plan_offline_json(home: Path, global_scenery: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "plan", "--tile", "+43+005", "--provider", "BI", "--zl", "14", "--offline", "--json",
            "--global-scenery", str(global_scenery), "--out", str(tmp_path / "out"),
            "--set", "masks_width=200",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    (tile,) = doc["tiles"]
    assert tile["tile"] == "+43+005" and tile["zl"] == 14 and not tile["textures"]["exact"]
    assert tile["requests"] == 256 * tile["textures"]["total"] and doc["probe"] is None
    roles = {n["role"]: n["status"] for n in tile["nodes"]}
    assert roles["dem"] == "build" and roles["dsf"] == "unknown" and "pack" in roles
    assert doc["disk_free_gb"] > 0 and doc["assumptions"]["req_per_s"] == 400.0
    text = runner.invoke(
        app,
        ["plan", "--tile", "+43+005", "--zl", "14", "--global-scenery", str(global_scenery)],
    )
    assert text.exit_code == 0 and "network: your line" in text.stdout


def test_plan_rejects_bad_set_and_missing_global_scenery(home: Path, tmp_path: Path) -> None:
    bad = runner.invoke(app, ["plan", "--tile", "+43+005", "--set", "nope=1", "--zl", "14"])
    assert bad.exit_code == 1 and "CFG_VALUE_INVALID" in bad.output
    missing = runner.invoke(
        app, ["plan", "--tile", "+43+005", "--zl", "14", "--global-scenery", str(tmp_path / "x")]
    )
    assert missing.exit_code == 1 and "XP_GLOBAL_SCENERY_NOT_FOUND" in missing.output
    none = runner.invoke(app, ["plan", "--tile", "+4a+005", "--zl", "14"])
    assert none.exit_code == 1 and "CFG_LATLON_INVALID" in none.output


def test_build_dry_run_is_plan(home: Path, global_scenery: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "build", "--tile", "+43+005", "--zl", "14", "--dry-run", "--json",
            "--global-scenery", str(global_scenery), "--out", str(tmp_path / "out"),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["tiles"][0]["tile"] == "+43+005"


def _pack(tmp_path: Path, name: str = "zOrtho4XP_+43+005") -> Path:
    p = tmp_path / "builds" / name
    (p / "Earth nav data" / "+40+000").mkdir(parents=True)
    (p / "Earth nav data" / "+40+000" / "+43+005.dsf").write_bytes(b"XPLNEDSF")
    (p / "terrain").mkdir()
    return p


def test_install_an_ortho4xp_pack_and_library(home: Path, xplane: Path, tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    lib = tmp_path / "lib.sqlite"
    result = runner.invoke(
        app, ["install", str(pack), "--xplane", str(xplane), "--library", str(lib), "--json"]
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)
    target = Path(receipt["target"])
    assert target == xplane / "Custom Scenery" / "zOrtho4XP_+43+005" and target.is_symlink()
    names = SceneryPacks.load(xplane / "Custom Scenery" / "scenery_packs.ini").names()
    assert names == ["*GLOBAL_AIRPORTS*", "zOrtho4XP_+43+005", "z_autoortho"]
    listed = runner.invoke(app, ["library", "--library", str(lib), "--json"])
    rows = json.loads(listed.stdout)
    assert len(rows) == 1 and rows[0]["tile"] == "+43+005" and rows[0]["built_by"] == "ortho4xp"
    text = runner.invoke(app, ["library", "--library", str(lib)])
    assert "+43+005" in text.stdout and "ortho4xp" in text.stdout


def test_install_an_osxp_pack_with_manifest(home: Path, xplane: Path, tmp_path: Path) -> None:
    from orthostudio.pipeline.pack import ArtefactEntry, PackManifest

    pack = _pack(tmp_path, "zOrthoStudio_+43+005")
    m = PackManifest(
        "+43+005", "BI", 14, {"dsf": ArtefactEntry("a" * 64, "b" * 64, "tile.dsf@1")},
        {"dsf": "Earth nav data/+40+000/+43+005.dsf", "dsf_size": 8, "textures": 0,
         "terrain": 0, "overlay": ""},
    )  # fmt: skip
    (pack / "orthostudio.toml").write_text(m.to_toml())
    lib = tmp_path / "lib.sqlite"
    result = runner.invoke(
        app, ["install", str(pack), "--xplane", str(xplane), "--library", str(lib), "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(runner.invoke(app, ["library", "--library", str(lib), "--json"]).stdout)
    assert rows[0]["built_by"] == "osxp" and rows[0]["keys"] == {"dsf": "a" * 64}
    assert rows[0]["provider"] == "BI" and rows[0]["zl"] == 14


def test_import_ortho4xp(home: Path, tmp_path: Path) -> None:
    ortho4xp_dir = tmp_path / "Ortho4XP"
    (ortho4xp_dir / "Tiles").mkdir(parents=True)
    pack = ortho4xp_dir / "Tiles" / "zOrtho4XP_+43+005"
    (pack / "Earth nav data" / "+40+000").mkdir(parents=True)
    (pack / "Earth nav data" / "+40+000" / "+43+005.dsf").write_bytes(b"XPLNEDSF")
    (pack / "Ortho4XP_+43+005.cfg").write_text("default_website=BI\ndefault_zl=16\n")
    lib = tmp_path / "lib.sqlite"
    args = ["import-ortho4xp", str(ortho4xp_dir), "--library", str(lib), "--json"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(r["tile"], r["provider"], r["zl"], r["built_by"]) for r in rows] == [
        ("+43+005", "BI", 16, "ortho4xp")
    ]
    missing = runner.invoke(app, ["import-ortho4xp", str(tmp_path / "nope")])
    assert missing.exit_code == 1
    no_folder = runner.invoke(app, ["import-ortho4xp"])  # the folder is a required argument
    assert no_folder.exit_code == 2


class _P(RuleParams):
    tag: str = "x"


def _fn(ctx: RunContext) -> None:
    ctx.out.write_bytes(b"payload")


def test_why_by_key_prefix_and_path(home: Path, tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    r = Rule(name="demo.rule", version=1, fn=_fn, params=_P)
    with Store(store_root, fsync=False) as store:
        res = Executor(store).run(Node(r, _P(tag="hello"))).target
    out = runner.invoke(app, ["why", res.key[:12], "--store", str(store_root)])
    assert out.exit_code == 0 and "demo.rule@1" in out.stdout and '"tag": "hello"' in out.stdout
    by_path = runner.invoke(app, ["why", str(res.path), "--store", str(store_root)])
    assert by_path.exit_code == 0 and "demo.rule@1" in by_path.stdout
    nothing = runner.invoke(app, ["why", "deadbeef", "--store", str(store_root)])
    assert nothing.exit_code == 1


def test_why_a_pack_directory(home: Path, tmp_path: Path) -> None:
    from orthostudio.pipeline.pack import ArtefactEntry, PackManifest

    store_root = tmp_path / "store"
    r = Rule(name="tile.dsf", version=1, fn=_fn, params=_P)
    with Store(store_root, fsync=False) as store:
        res = Executor(store).run(Node(r, _P(tag="dsf"))).target
    pack = tmp_path / "zOrthoStudio_+43+005"
    pack.mkdir()
    m = PackManifest(
        "+43+005", "BI", 14,
        {"dsf": ArtefactEntry(res.key, res.digest, "tile.dsf@1"),
         "mesh": ArtefactEntry("c" * 64, "d" * 64, "orthostudio.mesh@1")},
        {"dsf": "x.dsf", "dsf_size": 7, "textures": 0, "terrain": 0, "overlay": ""},
    )  # fmt: skip
    (pack / "orthostudio.toml").write_text(m.to_toml())
    out = runner.invoke(app, ["why", str(pack), "--store", str(store_root)])
    assert out.exit_code == 0, out.output
    assert "[dsf]" in out.stdout and "tile.dsf@1" in out.stdout
    assert "[mesh]" in out.stdout and "no longer in the store" in out.stdout
