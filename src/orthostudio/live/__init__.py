# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live: ortho imagery streamed into X-Plane while it flies, without FUSE.

The textures are ordinary files on the disk. The streamer builds each one at the sharpness X-Plane
will read, before X-Plane reads it, following the aircraft and the camera. A small X-Plane plugin
holds X-Plane on its loading screen at a flight's start until the area is built. The design, the
measurements behind it and the users' guide are in ``docs/specs/live.md``.

It is switched off until its launch, and changes nothing in the rest of OrthoStudio XP: its own file
``live.toml``, beside ``config.toml`` in ``$OSXP_HOME``, turns it on with ``enabled = true``
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

__all__ = ["enabled", "settings_file", "supported"]


def supported() -> bool:
    """Whether OrthoStudio XP Live can run here: where X-Plane 12 runs, macOS, Windows and Linux
    (its plugin, its list of programs)."""
    return sys.platform in ("darwin", "win32") or sys.platform.startswith("linux")


def settings_file() -> Path:
    return osxp_home() / "live.toml"


def enabled() -> bool:
    """Whether ``live.toml`` says ``enabled = true``."""
    try:
        data = tomllib.loads(settings_file().read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    return data.get("enabled") is True
