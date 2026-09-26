# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""DSF stage: the X-Plane mesh file of a tile, encoded byte for byte like Ortho4XP.

Specs: ``docs/specs/dsf-encoding.md``, ``docs/specs/dsf-terrain-assignment.md``,
``docs/specs/dsf-xp12-rasters.md``.
"""

from orthostudio.dsf._mesh_reader import MeshData, MeshLike, read_mesh
from orthostudio.dsf.decode import DecodedDsf, Patch, decode_dsf
from orthostudio.dsf.encode import (
    BUILTIN_WATER,
    DsfBuild,
    MaskLookup,
    Terrain,
    TextureJob,
    build_dsf,
)
from orthostudio.dsf.params import DsfParams, Zone
from orthostudio.dsf.write import write_dsf
from orthostudio.dsf.xp12 import Xp12Rasters, extract_xp12_rasters, rasters_from_dsf
from orthostudio.dsf.zones import AirportCover, TextureMap, airport_covers, texture_map

__all__ = [
    "BUILTIN_WATER",
    "AirportCover",
    "DecodedDsf",
    "DsfBuild",
    "DsfParams",
    "MaskLookup",
    "MeshData",
    "MeshLike",
    "Patch",
    "Terrain",
    "TextureJob",
    "TextureMap",
    "Xp12Rasters",
    "Zone",
    "airport_covers",
    "build_dsf",
    "decode_dsf",
    "extract_xp12_rasters",
    "rasters_from_dsf",
    "read_mesh",
    "texture_map",
    "write_dsf",
]
