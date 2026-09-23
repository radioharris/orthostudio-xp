"""CLI test: ``osxp doctor --json --offline`` lists its checks (``pipeline-textures.md`` 10)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orthostudio.cli import app

runner = CliRunner()


def test_doctor_json_offline(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "doctor",
            "--json",
            "--offline",
            "--store",
            str(tmp_path / "s"),
            "--chunks",
            str(tmp_path / "c"),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    names = [c["name"] for c in report["checks"]]
    assert names == [
        "python",
        "encoder",
        "http_client",
        "disk",
        "xplane",
        "triangle4xp",
        "architecture",
        "junctions",
        "window",
        "bing",
        "map_data",
        "store",
        "chunks",
    ]
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["python"]["status"] == "ok"
    assert (
        by_name["encoder"]["status"] == "ok"
        and "ispc" in by_name["encoder"]["details"]["available"]
    )
    assert by_name["bing"]["status"] == "skip"
    assert by_name["map_data"]["status"] == "skip"  # offline asks no server anything
    assert by_name["disk"]["status"] in ("ok", "warn")
    text = runner.invoke(app, ["doctor", "--offline"])
    assert text.exit_code == 0 and "[skip] bing" in text.output


def test_the_architecture_check_says_when_programs_run_under_rosetta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found on the first installed app: macOS started its script launcher under Rosetta, and
    DSFTool and Triangle4XP inherited the Intel preference."""
    import subprocess

    from orthostudio import doctor

    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "arm64")
    for answer, status in (("1\n", "warn"), ("0\n", "ok")):
        monkeypatch.setattr(
            doctor.subprocess,
            "run",
            lambda *a, _out=answer, **k: subprocess.CompletedProcess(a, 0, stdout=_out, stderr=""),
        )
        check = doctor._architecture()
        assert check.status == status and check.details["translated"] is (status == "warn")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "x86_64")
    assert doctor._architecture().status == "skip"


def test_the_junctions_check_fails_under_redirection_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app the last page of its installer opened inherited Inno Setup's RedirectionGuard, and
    could install no tile (2026-09-15): the check says so, and what to do."""
    from orthostudio import doctor

    monkeypatch.setattr(doctor.sys, "platform", "win32")
    for guarded, status in ((True, "fail"), (False, "ok")):
        monkeypatch.setattr(doctor, "redirection_guard", lambda _g=guarded: _g)
        check = doctor._junctions()
        assert check.status == status and check.details["redirection_guard"] is guarded
    monkeypatch.setattr(doctor, "redirection_guard", lambda: True)
    assert "open it again from the Start menu" in doctor._junctions().summary
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    assert doctor._junctions().status == "skip"


@pytest.mark.skipif(os.name != "nt", reason="RedirectionGuard is Windows'")
def test_redirection_guard_is_read_from_the_process() -> None:
    from orthostudio.fsutil import redirection_guard

    assert redirection_guard() is False  # the test runner's own process
    code = (
        "import ctypes\n"
        "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "k.SetProcessMitigationPolicy.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]\n"
        "assert k.SetProcessMitigationPolicy(16, ctypes.byref(ctypes.c_uint32(1)), 4)\n"
        "from orthostudio.fsutil import redirection_guard\n"
        "print(redirection_guard())\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stdout.strip() == "True"


def test_the_xplane_check_finds_x_plane_where_every_command_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The doctor had its own list of folders (no X-Plane installer's list, $X_PLANE_DIR instead
    of $OSXP_XPLANE_DIR) and called X-Plane optional: on Windows it could miss the X-Plane every
    command found, and it told a user without X-Plane nothing about what to do."""
    from orthostudio import doctor

    xp = tmp_path / "X-Plane 12"
    (xp / "Resources").mkdir(parents=True)
    (xp / "Custom Scenery").mkdir()
    listed = tmp_path / "x-plane_install_12.txt"
    listed.write_text(f"{tmp_path / 'gone'}/\n{xp}/\n", encoding="utf-8")
    monkeypatch.delenv("OSXP_XPLANE_DIR", raising=False)
    monkeypatch.setattr(doctor, "xplane_candidates", lambda: [tmp_path / "gone", xp])
    check = doctor._xplane(None)
    assert check.status == "warn" and check.details["path"] == str(xp)  # no Global Scenery
    assert not check.details["global_scenery"]

    from orthostudio.install import xplane

    tried = xplane.xplane_candidates(install_file=listed, candidates=[], env={})
    assert tried == [tmp_path / "gone", xp]
    env = {"OSXP_XPLANE_DIR": str(tmp_path / "env")}
    assert xplane.xplane_candidates(install_file=listed, candidates=[], env=env)[0] == (
        tmp_path / "env"
    )

    monkeypatch.setattr(doctor, "xplane_candidates", lambda: [tmp_path / "gone"])
    check = doctor._xplane(None)
    assert check.status == "warn" and check.details["tried"] == [str(tmp_path / "gone")]
    assert check.summary.startswith("X-Plane 12 not found: choose its folder in Settings")
    assert "optional" not in check.summary and "X_PLANE_DIR" not in check.summary
    assert doctor._xplane(xp).details["path"] == str(xp)  # --xplane, or the Settings' folder


def test_the_window_check_says_what_to_install_when_there_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page lists every check of the doctor, so a system that opens the browser instead of a
    window says there what it lacks: OrthoStudio XP installs nothing on a system it does not own
    (``orthostudio.window.hint``)."""
    from orthostudio import doctor, window

    monkeypatch.setattr(window, "possible", lambda: False)
    monkeypatch.setattr(window, "hint", lambda: "install the frobnicator: https://example.invalid")
    monkeypatch.setattr(window, "install", lambda: "install the frobnicator")
    check = doctor._window()
    assert check.name == "window" and check.status == "warn"  # the app still works, in a browser
    assert "frobnicator" in check.summary and check.details["browser_only"] is True

    monkeypatch.setattr(window, "possible", lambda: True)
    assert doctor._window().status == "ok"


def test_the_chunks_check_reads_the_listings_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked of each container, the size opened every one on Windows, and a big cache behind an
    antivirus held the status, and the page's first screen, a long time (2026-09-22)."""
    from orthostudio import doctor

    root = tmp_path / "chunks"
    (root / "BI" / "16").mkdir(parents=True)
    (root / "BI" / "16" / "1_2.chunks").write_bytes(b"x" * 10)
    (root / "BI" / "3_4.chunks").write_bytes(b"y" * 20)
    (root / "BI" / "partial.tmp").write_bytes(b"z" * 5)
    real_stat = os.stat

    def stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        assert not os.fspath(path).endswith(".chunks"), "a container was asked its size"  # type: ignore[arg-type]
        return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", stat)
    check = doctor._chunks(root)
    assert check.status == "ok"
    assert check.details["containers"] == 2 and check.details["bytes"] == 30
