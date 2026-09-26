# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The installers' build script, ``tools/package/build.py`` (pure parts, and the pruning on a fake
Python): ``docs/specs/packaging.md``."""

from __future__ import annotations

import plistlib
import re
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "tools" / "package"
sys.path.insert(0, str(PACKAGE))

import build  # noqa: E402
import checkout_app  # noqa: E402
import icon  # noqa: E402

MAC = build.Target("macos", "arm64")
INTEL_MAC = build.Target("macos", "x86_64")
WINDOWS = build.Target("windows", "x64")
LINUX = build.Target("linux", "x86_64")


def test_the_installer_names() -> None:
    assert build.artefact_name("0.1.0", MAC) == "OrthoStudio-XP-0.1.0-macos-arm64.dmg"
    assert build.artefact_name("0.1.0", INTEL_MAC) == "OrthoStudio-XP-0.1.0-macos-x86_64.dmg"
    assert build.artefact_name("0.1.0", WINDOWS) == "OrthoStudio-XP-0.1.0-windows-x64-setup.exe"
    assert build.artefact_name("0.1.0", LINUX) == "OrthoStudio-XP-0.1.0-linux-x86_64.tar.gz"


def test_the_launchers_run_the_python_inside_by_a_relative_path() -> None:
    for text in (build.macos_launcher(), build.linux_launcher()):
        assert "-m orthostudio.desktop" in text and '"$@"' in text
        assert "/Users/" not in text and "/home/" not in text
    assert "$CONTENTS/Resources/python/bin/python3" in build.macos_launcher()
    assert '"$HERE/python/bin/python3"' in build.linux_launcher()


def test_a_macos_launcher_becomes_the_process_that_holds_the_window() -> None:
    # the window must be the app's own process: macOS knows it by the bundle it was started from,
    # and a window opened by a process started aside is called Python (measured, 2026-09-20)
    for text in (build.macos_launcher(), checkout_app.LAUNCHER):
        assert "exec " in text and " &\n" not in text
        assert '-m orthostudio.desktop "$@"' in text


def test_the_windows_installer_offers_webview2_only_when_it_is_missing() -> None:
    script = build.inno_setup_script(
        "0.1.9", Path("/b"), Path("/i.ico"), Path("/o"), "out", Path("/w") / build.WEBVIEW2_EXE
    )
    task = next(ln for ln in script.splitlines() if ln.startswith('Name: "webview2"'))
    # offered, ticked, and only on a machine that has none: Flags: unchecked would hide it away,
    # and no Check would offer it to everyone
    assert "Check: WebView2Missing" in task and "unchecked" not in task
    assert "function WebView2Missing" in script  # the Pascal that answers it travels with it
    assert "F3017226-FE2A-4295-8BDF-00C3A9A7E4C5" in script  # Microsoft's own key
    assert f'Filename: "{{tmp}}\\{build.WEBVIEW2_EXE}"' in script
    assert "Tasks: webview2" in script


def test_the_windows_uninstaller_removes_the_program_and_nothing_else(tmp_path: Path) -> None:
    script = build.inno_setup_script(
        "0.1.9",
        tmp_path / "bundle",
        tmp_path / "orthostudio.ico",
        tmp_path,
        "setup",
        tmp_path / build.WEBVIEW2_EXE,
    )
    code = script[script.index("\n[Code]\n") :]
    after = code[code.index("procedure CurUninstallStepChanged") :]
    # the tiles installed into X-Plane are junctions into that folder, not copies: taking it away
    # empties X-Plane of every tile built here, so the uninstaller takes nothing and says where
    assert "DelTree" not in after and "DeleteFile" not in after
    assert "junctions" in after and ".orthostudio" in code
    assert "MsgBox" in after and "mbInformation" in after
    # a data folder chosen on another disk is named too, so the room is not lost track of
    assert "ChosenDataDir" in after


def test_the_installers_pascal_is_commented_the_way_pascal_is(tmp_path: Path) -> None:
    """A line opened with ``;`` is a comment in an installer's other sections and nothing at all
    in ``[Code]``, where Inno Setup asked for a BEGIN and stopped (CI, 2026-09-20). The Pascal
    comments with ``//``."""
    script = build.inno_setup_script(
        "0.1.9",
        tmp_path / "bundle",
        tmp_path / "orthostudio.ico",
        tmp_path,
        "setup",
        tmp_path / build.WEBVIEW2_EXE,
    )
    code = script[script.index("\n[Code]\n") :]
    assert [line for line in code.splitlines() if line.lstrip().startswith(";")] == []
    for part in (build.INNO_CODE, build.INNO_WEBVIEW2, build.INNO_UNINSTALL):
        assert not part.read_text(encoding="utf-8").startswith(";"), part.name


def test_the_app_bundle_describes_itself() -> None:
    info = build.macos_info_plist("0.1.0", "14.0")
    assert plistlib.loads(plistlib.dumps(info))["CFBundleExecutable"] == "orthostudio"
    assert (
        info["LSMinimumSystemVersion"] == "14.0" and info["CFBundleShortVersionString"] == "0.1.0"
    )


def test_the_intel_app_may_run_under_rosetta_and_the_apple_silicon_one_may_not() -> None:
    """A user asked for Intel Macs (2026-09-17). The Apple Silicon app declares its architecture
    and refuses Rosetta, whose Intel preference its universal programs would inherit; the Intel
    app runs under Rosetta on Apple Silicon, where its check runs."""
    arm = build.macos_info_plist("0.1.0", "14.0", "arm64")
    assert arm["LSArchitecturePriority"] == ["arm64"] and arm["LSRequiresNativeExecution"] is True
    assert build.macos_info_plist("0.1.0", "14.0") == arm
    intel = build.macos_info_plist("0.1.0", "14.0", "x86_64")
    assert (
        intel["LSArchitecturePriority"] == ["x86_64"] and "LSRequiresNativeExecution" not in intel
    )


def test_the_python_asked_of_uv_names_the_mac_architecture() -> None:
    assert MAC.python_request("3.14") == "cpython-3.14-macos-aarch64-none"
    assert INTEL_MAC.python_request("3.14") == "cpython-3.14-macos-x86_64-none"
    assert WINDOWS.python_request("3.14") == "3.14" and LINUX.python_request("3.14") == "3.14"


def test_only_macos_builds_for_another_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    import platform
    import subprocess

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert build.this_target() == WINDOWS
    with pytest.raises(SystemExit, match="only macOS"):
        build.this_target("x86_64")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert build.this_target() == INTEL_MAC  # an Intel Mac builds its own app
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert build.this_target() == MAC

    def rosetta(returncode: int):  # type: ignore[no-untyped-def]
        def fake(cmd, **kwargs):  # type: ignore[no-untyped-def]
            assert cmd == ["arch", "-x86_64", "/usr/bin/true"]
            return subprocess.CompletedProcess(cmd, returncode)

        return fake

    monkeypatch.setattr(build.subprocess, "run", rosetta(0))
    assert build.this_target("x86_64") == INTEL_MAC
    monkeypatch.setattr(build.subprocess, "run", rosetta(1))
    with pytest.raises(SystemExit, match="install-rosetta"):
        build.this_target("x86_64")


def test_an_app_whose_wheels_ask_for_a_newer_macos_than_the_readme_is_refused() -> None:
    """Built on macOS 26, the Intel app took pyproj's wheel for macOS 15 (2026-09-17): each app is
    held to the macOS the README gives."""
    build.check_oldest_macos("arm64", "14.0")
    build.check_oldest_macos("x86_64", "15.0")
    build.check_oldest_macos("x86_64", "11.0")
    with pytest.raises(SystemExit, match=r"build it on macOS 14\.0"):
        build.check_oldest_macos("arm64", "15.0")
    with pytest.raises(SystemExit, match=r"ask for macOS 15\.1"):
        build.check_oldest_macos("x86_64", "15.1")


def test_the_oldest_macos_is_the_most_demanding_wheel() -> None:
    tags = ["cp314-cp314-macosx_14_0_arm64", "py3-none-any", "cp310-abi3-macosx_11_0_arm64"]
    assert build.minimum_macos(tags) == "14.0"
    assert build.minimum_macos(["cp314-cp314-macosx_10_15_universal2"]) == "11.0"
    assert build.minimum_macos([*tags, "cp314-cp314-macosx_15_0_arm64"]) == "15.0"


def test_the_windows_installer_needs_no_administrator(tmp_path: Path) -> None:
    script = build.inno_setup_script(
        "0.1.0",
        tmp_path / "bundle",
        tmp_path / "orthostudio.ico",
        tmp_path,
        "setup",
        tmp_path / build.WEBVIEW2_EXE,
    )
    assert "PrivilegesRequired=lowest" in script
    # the folder is asked for every time, upgrade or not: Inno would hide the page on an upgrade
    assert "DisableDirPage=no" in script
    # the app its last page opens would inherit RedirectionGuard, and install no tile (2026-09-15)
    assert "\nRedirectionGuard=no\n" in script
    assert "AppId={{7C8E0F52-3B1D-4E4A-9B67-2D4F1A6C9E31}" in script
    assert r"DefaultDirName={localappdata}\Programs\OrthoStudio XP" in script
    assert r'Filename: "{app}\python\pythonw.exe"; Parameters: "-m orthostudio.desktop"' in script
    assert "{cm:LaunchProgram,OrthoStudio XP}" in script


def test_the_windows_installer_stops_the_app_it_replaces(tmp_path: Path) -> None:
    """A user installing again read "DeleteFile failed; code 5" on python3.dll: the app was
    still running. The setup program and the uninstaller ask it to quit through the API the page
    quits with, on the engine's own port, then end what still runs from the folder."""
    from orthostudio.api.serve import DEFAULT_PORT

    script = build.inno_setup_script(
        "0.1.0",
        tmp_path / "bundle",
        tmp_path / "orthostudio.ico",
        tmp_path,
        "setup",
        tmp_path / build.WEBVIEW2_EXE,
    )
    code = script[script.index("\n[Code]\n") :]
    assert build.ENGINE_PORT == DEFAULT_PORT and "%PORT%" not in script
    assert f"'http://127.0.0.1:{DEFAULT_PORT}/api/quit'" in code
    assert "Http.Open('POST', EngineQuitUrl, False)" in code and "'{\"force\": true}'" in code
    assert "function PrepareToInstall(var NeedsRestart: Boolean): String;" in code
    assert "function InitializeUninstall(): Boolean;" in code
    assert code.count("StopOrthoStudio(ExpandConstant('{app}'))") == 2
    assert "Terminate()" in code and "python\\" in code  # only what runs from the folder
    check = build.check_installer.__code__.co_names
    assert "start_engine" in check and "engine_stopped" in check  # checked on Windows by CI


def test_the_linux_menu_entry_can_be_removed() -> None:
    script = build.linux_install_script()
    assert script.startswith("#!/bin/sh") and "--remove" in script
    assert 'Exec="$HERE/orthostudio-xp"' in script


def test_every_licence_file_exists() -> None:
    for source, _name in build.LICENCE_FILES:
        assert (build.REPO / source).is_file(), source


def _script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_the_pruning_keeps_what_the_app_runs(tmp_path: Path) -> None:
    root = tmp_path / "python"
    site = root / "lib" / "python3.14" / "site-packages"
    package = _script(site / "orthostudio" / "__init__.py", "").parent
    _script(site / "orthostudio" / "tilefiles" / "tests" / "keep.py", "")
    _script(site / "pip" / "__init__.py", "")
    (site / "pip-26.2.1.dist-info").mkdir()
    _script(site / "scipy" / "linalg" / "tests" / "test_x.py", "")
    _script(site / "scipy" / "linalg" / "__init__.py", "")
    _script(root / "bin" / "pip3", "#!/bin/sh\n'''exec' \"$(dirname -- \"$0\")/python3\"\n")
    _script(root / "bin" / "osxp", f"#!/bin/sh\n'''exec' '{root}/bin/python3' \"$0\" \"$@\"\n")
    _script(root / "bin" / "idle3", "#!/bin/sh\n'''exec' \"$(dirname -- \"$0\")/python3\"\n")

    build.prune(package)

    assert not (site / "pip").exists() and not (site / "pip-26.2.1.dist-info").exists()
    assert not (site / "scipy" / "linalg" / "tests").exists()
    assert (site / "scipy" / "linalg" / "__init__.py").is_file()
    assert (site / "orthostudio" / "tilefiles" / "tests" / "keep.py").is_file()
    assert sorted(p.name for p in (root / "bin").iterdir()) == ["idle3"]
    assert build.python_root_of(site) == root


@pytest.mark.parametrize("size", [16, 256])
def test_the_icons_are_drawn(tmp_path: Path, size: int) -> None:
    icon.make_png(tmp_path / "icon.png", size)
    icon.make_ico(tmp_path / "icon.ico")
    assert (tmp_path / "icon.png").stat().st_size > 0 and (tmp_path / "icon.ico").stat().st_size > 0


def test_the_standalone_python_is_the_newest_patch_not_the_minor_link(tmp_path: Path) -> None:
    for name in ("cpython-3.14.6-macos-aarch64-none", "cpython-3.14.7-macos-aarch64-none"):
        (tmp_path / name / "bin").mkdir(parents=True)
    (tmp_path / "cpython-3.15.0-macos-aarch64-none").mkdir()
    (tmp_path / "cpython-3.14-macos-aarch64-none").symlink_to(
        tmp_path / "cpython-3.14.6-macos-aarch64-none", target_is_directory=True
    )

    assert build.standalone_root(tmp_path, "3.14").name == "cpython-3.14.7-macos-aarch64-none"
    with pytest.raises(SystemExit):
        build.standalone_root(tmp_path, "3.13")


def test_the_uv_environment_is_never_the_one_packed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    assert "VIRTUAL_ENV" not in build.clean_env()


def test_the_check_reads_the_last_start_of_the_log(tmp_path: Path) -> None:
    log = tmp_path / "serve.log"
    log.write_text(
        '--- 2026-09-14 OrthoStudio XP: old\n{"old": 1}\n--- 2026-09-15 again\n{"new": 2}\n'
    )
    assert build.last_run(log) == '{"new": 2}'
    log.write_text("no header\n")
    assert build.last_run(log) == ""


# Inno Setup's directory constants, from its documentation. Anything else it refuses at run time,
# and only at run time: {userprofile} looks as ordinary as the rest, compiled without a word, and
# met a user mid-uninstall as "Internal error: Unknown constant" (2026-09-20). An environment
# variable is written {%NAME} and is a different thing.
_FROM_THE_DOCUMENTATION = """
    app win sys sysnative src sd commonpf commonpf32 commonpf64 commoncf commoncf32
    commoncf64
    tmp commonfonts dao dotnet11 dotnet20 dotnet2032 dotnet2064 dotnet40 dotnet4032 dotnet4064
    group localappdata userappdata commonappdata usercf userdesktop commondesktop userdocs
    commondocs userfavorites commonfavorites userfonts userpf usersavedgames usersendto
    userstartmenu commonstartmenu userprograms commonprograms userstartup commonstartup
    usertemplates commontemplates autopf autopf32 autopf64 autocf autocf32 autocf64 autoappdata
    autodesktop autodocs autofonts autoprograms autostartmenu autostartup autotemplates
    language cmd computername drive groupname hwnd wizardhwnd log srcexe uninstallexe
    sysuserinfoname sysuserinfoorg userinfoname userinfoorg userinfoserial username"""
INNO_CONSTANTS = frozenset(_FROM_THE_DOCUMENTATION.split())


def test_the_installer_only_expands_constants_inno_setup_knows(tmp_path: Path) -> None:
    script = build.inno_setup_script(
        "0.1.8",
        tmp_path,
        tmp_path / "icon.ico",
        tmp_path,
        "out",
        tmp_path / build.WEBVIEW2_EXE,
    )
    used = set(re.findall(r"ExpandConstant\('\{([^}\\']+)\}", script))
    assert used, "no constant found: the reading of the script is wrong, not the script"
    unknown = {c for c in used if not c.startswith("%") and c.split(":")[0] not in INNO_CONSTANTS}
    assert not unknown, (
        f"Inno Setup does not know {sorted(unknown)}; an environment variable is {{%NAME}}"
    )


def test_the_uninstaller_finds_the_users_folder_without_inventing_a_constant(
    tmp_path: Path,
) -> None:
    """The folder it names is the one orthostudio.home.osxp_home keeps on Windows."""
    script = build.inno_setup_script(
        "0.1.8", tmp_path, tmp_path / "icon.ico", tmp_path, "out", tmp_path / build.WEBVIEW2_EXE
    )
    home = script[script.index("function OsxpHome") : script.index("function ChosenDataDir")]
    assert "GetEnv('USERPROFILE')" in home and "\\.orthostudio" in home
    # and it says nothing at all rather than guessing when Windows will not say where home is
    assert "if Profile <> '' then" in home
    assert "if (OsxpHome = '') or (not DirExists(OsxpHome)) then" in script


def test_the_disk_image_is_asked_again_when_the_machine_is_busy() -> None:
    """The tag build of 0.1.9 failed on `hdiutil create: Resource busy`, minutes after the same
    commit had packaged cleanly on the same runner: Spotlight and the mounts of other jobs share
    that disk. The publish job depends on every installer, so one busy machine held the whole
    release back. The failure says more about the machine than about the image, so it is asked
    again rather than reported."""
    import build as packaging

    assert packaging.DMG_TRIES >= 2 and packaging.DMG_RETRY_S > 0
    source = Path(packaging.__file__).read_text(encoding="utf-8")
    dmg = source[source.index("def build_macos") :]
    dmg = dmg[: dmg.index("\ndef ", 1)]
    assert "run_again_if_it_fails(make, tries=DMG_TRIES" in dmg
    assert 'run(\n        [\n            "hdiutil", "create"' not in dmg  # not the plain call

    # it raises on the last try, so a machine that is really broken still stops the build
    calls: list[int] = []

    class Done:
        def __init__(self, code: int) -> None:
            self.returncode, self.stdout, self.stderr = code, "", "busy"

    def always_busy(*_args: object, **_kw: object) -> Done:
        calls.append(1)
        return Done(1)

    real_run, real_sleep = packaging.subprocess.run, packaging.time.sleep
    packaging.subprocess.run = always_busy  # type: ignore[assignment]
    packaging.time.sleep = lambda _s: None  # type: ignore[assignment]
    try:
        with pytest.raises(SystemExit):
            packaging.run_again_if_it_fails(["hdiutil"], tries=3, wait_s=0)
    finally:
        packaging.subprocess.run, packaging.time.sleep = real_run, real_sleep
    assert len(calls) == 3
