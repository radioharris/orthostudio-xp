# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Compatibility re-export of :mod:`orthostudio.home` (the roots moved down a layer).

Spec ``docs/specs/pipeline-textures.md`` section 2. The implementation lives in ``orthostudio.home``
so that low modules (``orthostudio.sources``, ``orthostudio.dem``) can ask for the roots without
importing the pipeline package; every ``from orthostudio.pipeline.home import ...`` in the tree
keeps working unchanged.
"""

from __future__ import annotations

from orthostudio.home import (
    OSXP_HOME_ENV,
    check_data_dir,
    data_root,
    data_root_missing,
    default_chunks_root,
    default_mapcache_root,
    default_patches_dir,
    default_store_root,
    default_tiles_root,
    default_work_root,
    make_patches_dir,
    osxp_home,
    require_data_root,
)

__all__ = [
    "OSXP_HOME_ENV",
    "check_data_dir",
    "data_root",
    "data_root_missing",
    "default_chunks_root",
    "default_mapcache_root",
    "default_patches_dir",
    "default_store_root",
    "default_tiles_root",
    "default_work_root",
    "make_patches_dir",
    "osxp_home",
    "require_data_root",
]
