# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's tests on this Windows, as on the Mac: every tests/test_live_*.py, in parallel; first what
this computer is."""

import glob
import os
import platform
import sys

import pytest

from orthostudio.live import machine

cores, ram = machine.machine()
print(
    f"{platform.platform()} | Python {sys.version.split()[0]} {platform.machine()} | "
    f"{cores} cores, {ram:.0f} GB | {os.environ.get('OSXP_LIVE_XPL_HOST', '')}",
    flush=True,
)
sys.exit(
    pytest.main(
        [
            "-n",
            "auto",
            "-o",
            "addopts=",
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
            *sorted(glob.glob("tests/test_live_*.py")),
        ]
    )
)
