# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where OrthoStudio XP Live keeps its pack and its imagery, and what it has built.

Both are heavy, so they live in OrthoStudio XP's data folder (:func:`orthostudio.home.data_root`),
under ``live/``:
- ``live/pack``, the pack X-Plane reads through the link ``z_osxp_live`` in Custom Scenery;
- ``live/chunks.sqlite``, the downloaded image pieces.

The pack's textures are APFS clones of one neutral file until they are built, so the data folder
must be on an APFS volume. ``_live/ortho.json`` in the pack records the level each texture is built
to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orthostudio.home import data_root

LINK_NAME = "z_osxp_live"
MARKER = Path("_live") / "ortho.json"


def live_root() -> Path:
    return data_root() / "live"


def default_pack_dir() -> Path:
    return live_root() / "pack"


def default_cache_path() -> Path:
    return live_root() / "chunks.sqlite"


def load_built(pack_dir: Path) -> dict[str, Any]:
    path = pack_dir / MARKER
    return json.loads(path.read_text()) if path.exists() else {}
