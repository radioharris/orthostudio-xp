"""Installation into X-Plane 12: detection, ``scenery_packs.ini``, pack links, library.

Spec: ``docs/specs/install.md``. Everything here is stdlib.
"""

from orthostudio.install._model import TileRef
from orthostudio.install.library import (
    Library,
    LibraryEntry,
    default_library_path,
    pack_tile,
    tile_from_name,
)
from orthostudio.install.packs import install_pack, is_link, uninstall_pack
from orthostudio.install.scenery_packs import (
    GLOBAL_AIRPORTS,
    OVERLAY_PACK_NAME,
    SceneryPackEntry,
    SceneryPacks,
    pack_kind,
)
from orthostudio.install.xplane import (
    custom_scenery_dir,
    detect_xplane,
    global_scenery_dir,
    is_xplane_dir,
    other_xplane_dirs,
    packs_of_their_own,
    xplane_running,
)

__all__ = [
    "GLOBAL_AIRPORTS",
    "OVERLAY_PACK_NAME",
    "Library",
    "LibraryEntry",
    "SceneryPackEntry",
    "SceneryPacks",
    "TileRef",
    "custom_scenery_dir",
    "default_library_path",
    "detect_xplane",
    "global_scenery_dir",
    "install_pack",
    "is_link",
    "is_xplane_dir",
    "other_xplane_dirs",
    "pack_kind",
    "pack_tile",
    "packs_of_their_own",
    "tile_from_name",
    "uninstall_pack",
    "xplane_running",
]
