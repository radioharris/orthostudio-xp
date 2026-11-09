# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live: ortho imagery streamed into X-Plane while it flies, without FUSE.

The textures are ordinary files on the disk. The streamer builds each one at the sharpness X-Plane
will read, before X-Plane reads it, following the aircraft and the camera. A small X-Plane plugin
holds X-Plane on its loading screen at a flight's start until the area is built. The design, the
measurements behind it and the users' guide are in ``docs/specs/live.md``.

It is switched on by default (:data:`ENABLED_BY_DEFAULT`), and its own file
``live.toml``, beside ``config.toml`` in ``$OSXP_HOME``, switches it off with ``enabled = false``
(:func:`enabled`). OrthoStudio XP's settings screen never saves that file, so the switch stays as
set. It runs on macOS (Apple Silicon and Intel), Windows and Linux: its X-Plane plugin is built for
each (``orthostudio.live.plugin``), it reads each system's own list of programs and starts X-Plane
as each does (``orthostudio.live.scenery``), and its files work on every disk kind
(``orthostudio.live.filesystem``).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from orthostudio.home import osxp_home

__all__ = ["ENABLED_BY_DEFAULT", "enabled", "settings_file", "supported"]

ENABLED_BY_DEFAULT = True
"""Live's switch where ``live.toml`` does not set it: on. It was off before, while Live was tried on
a branch of its own; the tests keep it off unless they switch it on (``tests/conftest.py``)."""


def supported() -> bool:
    """Whether OrthoStudio XP Live can run here: where X-Plane 12 runs, macOS, Windows and Linux
    (its plugin, its list of programs)."""
    return sys.platform in ("darwin", "win32") or sys.platform.startswith("linux")


def settings_file() -> Path:
    return osxp_home() / "live.toml"


def enabled() -> bool:
    """Whether Live is switched on: ``enabled`` in ``live.toml``, else :data:`ENABLED_BY_DEFAULT`
    (no file, a file that does not parse, or no such key)."""
    try:
        data = tomllib.loads(settings_file().read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ENABLED_BY_DEFAULT
    switch = data.get("enabled")
    return switch if isinstance(switch, bool) else ENABLED_BY_DEFAULT
