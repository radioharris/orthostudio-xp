"""The limit of open files a command raises (``fsutil.raise_open_files_limit``).

An app macOS starts from the Finder or the Dock gets launchd's 256 open files, where a terminal has
far more: an image download of 192 connections and the files it writes went past it, and a user's
textures failed with "Too many open files" (2026-09-15). Run in a child process, the test's own
limit untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows has no limit of open files")

AS_AN_APP = (
    "import resource\n"
    "soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
    "resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))\n"
)


def _run(code: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-c", AS_AN_APP + code],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return done.stdout.split()


def test_the_limit_rises_from_what_macos_gives_an_app() -> None:
    before, after, soft = _run(
        "from orthostudio.fsutil import raise_open_files_limit\n"
        "before, after = raise_open_files_limit()\n"
        "print(before, after, resource.getrlimit(resource.RLIMIT_NOFILE)[0])\n"
    )
    assert int(before) == 256 and int(after) == int(soft) >= 1024


def test_every_command_raises_it_the_engine_too() -> None:
    (soft,) = _run(
        "from typer.testing import CliRunner\n"
        "from orthostudio.cli import app\n"
        "assert CliRunner().invoke(app, ['version']).exit_code == 0\n"
        "print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])\n"
    )
    assert int(soft) >= 1024
