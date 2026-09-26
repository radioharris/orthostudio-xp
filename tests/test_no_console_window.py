# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Every program OrthoStudio XP runs is started without a console window of its own.

The Windows app runs in ``pythonw``, which has no console: each console program it started
(DSFTool, Triangle4XP, tasklist, mklink) opened a window that flashed on the screen, and DSFTool
ended with 0xC000013A (``STATUS_CONTROL_C_EXIT``) when that window closed under it, failing the
overlays of every tile (user report, 2026-09-15). ``fsutil.NO_CONSOLE_WINDOW`` is
``CREATE_NO_WINDOW`` there and 0 elsewhere.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from orthostudio import fsutil

SRC = Path(__file__).resolve().parents[1] / "src" / "orthostudio"
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}


def test_the_flag_hides_the_window_on_windows_only() -> None:
    assert getattr(subprocess, "CREATE_NO_WINDOW", 0) == fsutil.NO_CONSOLE_WINDOW


def test_every_program_launch_asks_for_no_console_window() -> None:
    missing: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if node.func.attr not in LAUNCHERS or not (
                isinstance(owner, ast.Name) and owner.id == "subprocess"
            ):
                continue
            if not any(k.arg == "creationflags" for k in node.keywords):
                missing.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert missing == [], f"subprocess launches without creationflags: {missing}"
