# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The ``yOrthoStudio_Overlays`` pack: X-Plane's own overlays, without the mesh.

Port of Ortho4XP step 4 (``O4_Overlay_Utils.py``) with checked subprocesses, in-process
7z decompression and by-name exclusions. Spec: ``docs/specs/overlays.md``.
"""

from __future__ import annotations

from orthostudio.model import TileRef
from orthostudio.overlays.build import (
    OVERLAY_PACK,
    OverlayResult,
    OverlayStats,
    TileLike,
    build_overlay,
    build_overlay_detailed,
    overlay_dsf_path,
)
from orthostudio.overlays.dsftool import DsfToolRun, find_dsftool, run_dsftool
from orthostudio.overlays.exclusions import (
    DEFAULT_EXCLUDED_POLYGONS,
    OverlayExclusions,
    exclusions_by_name,
    resolve_network_exclusions,
    resolve_polygon_exclusions,
)
from orthostudio.overlays.source import (
    SourceInfo,
    materialize_source,
    overlay_source_path,
    resolve_global_scenery_dir,
)
from orthostudio.overlays.textfilter import FilterStats, filter_dsf_bytes, filter_dsf_text

__all__ = [
    "DEFAULT_EXCLUDED_POLYGONS",
    "OVERLAY_PACK",
    "DsfToolRun",
    "FilterStats",
    "OverlayExclusions",
    "OverlayResult",
    "OverlayStats",
    "SourceInfo",
    "TileLike",
    "TileRef",
    "build_overlay",
    "build_overlay_detailed",
    "exclusions_by_name",
    "filter_dsf_bytes",
    "filter_dsf_text",
    "find_dsftool",
    "materialize_source",
    "overlay_dsf_path",
    "overlay_source_path",
    "resolve_global_scenery_dir",
    "resolve_network_exclusions",
    "resolve_polygon_exclusions",
    "run_dsftool",
]
