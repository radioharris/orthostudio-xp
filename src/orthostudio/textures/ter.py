# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""X-Plane ``.ter`` terrain definitions, one per (texture, triangle type, overlay flag).

Byte-for-byte port of Ortho4XP ``create_terrain_file`` (``src/O4_DSF_Utils.py:261-357``);
behaviour spec in ``docs/specs/textures-ter.md``. Pure functions: writing the file, copying
``water_transition.png`` and the DSF ``TERT`` entry belong to the caller.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Literal

from orthostudio.decals import DEFAULT_DECAL, decal_lib
from orthostudio.imagery.grid import TextureId, texture_name, tile_to_wgs84, webmercator_pixel_size

__all__ = [
    "DECAL_LIB",
    "TEST_TEXTURE",
    "WATER_TRANSITION_PNG",
    "TerKind",
    "TerParams",
    "border_mask_filename",
    "load_center_size",
    "sea_kind",
    "takes_decal",
    "ter_center",
    "ter_filename",
    "ter_kind",
    "ter_text",
    "texture_dds_name",
    "with_decal",
]

DECAL_LIB = decal_lib(DEFAULT_DECAL)
"""Ortho4XP's decal, the one ``ter_text`` writes when asked (its byte fidelity)."""
WATER_TRANSITION_PNG = "water_transition.png"
"""Copied by Ortho4XP from ``Utils/`` into ``textures/`` whenever an inland-water overlay exists."""
TEST_TEXTURE = "test_texture.dds"

WaterTech = Literal["XP12", "XP11 + bathy"]


class TerKind(enum.StrEnum):
    """Terrain kinds = (``tri_type``, ``is_overlay``) pairs of Ortho4XP.

    ``WATER`` (XP12 inland water, no overlay) is supported by ``create_terrain_file`` but never
    produced by Ortho4XP, which always overlays inland water; land is never an overlay.
    """

    LAND = "land"
    WATER = "water"
    WATER_OVERLAY = "water_overlay"
    SEA = "sea"
    SEA_OVERLAY = "sea_overlay"

    @property
    def tri_type(self) -> int:
        """0 land, 1 inland water, 2 sea (``tri_types`` of the mesh)."""
        return {"land": 0, "water": 1, "sea": 2}[self.value.split("_")[0]]

    @property
    def overlay(self) -> bool:
        return self.value.endswith("_overlay")

    @property
    def is_water(self) -> bool:
        return self.tri_type in (1, 2)

    @property
    def suffix(self) -> str:
        """File-name suffix: ``""``, ``_water``, ``_sea``, plus ``_overlay``."""
        base = {0: "", 1: "_water", 2: "_sea"}[self.tri_type]
        return base + ("_overlay" if self.overlay else "")

    @classmethod
    def of(cls, tri_type: int, overlay: bool) -> TerKind:
        for kind in cls:
            if kind.tri_type == tri_type and kind.overlay == overlay:
                return kind
        raise ValueError(f"no terrain kind for tri_type={tri_type}, overlay={overlay}")


@dataclass(frozen=True, slots=True)
class TerParams:
    """Tile parameters consumed by the ``.ter`` text (Ortho4XP defaults, ``O4_Config_Utils.py``)."""

    water_tech: WaterTech = "XP11 + bathy"
    imprint_masks_to_dds: bool = True
    mask_zl: int = 14
    use_decal_on_terrain: bool = False
    decal_on_sea: bool = False
    terrain_casts_shadows: bool = True
    use_test_texture: bool = False
    """Ortho4XP module global ``use_test_texture``: every terrain points at ``test_texture.dds``."""


def sea_kind(params: TerParams) -> TerKind:
    """Kind of a masked sea terrain (``O4_DSF_Utils.py:702-706``)."""
    overlay = params.water_tech == "XP11 + bathy" or not params.imprint_masks_to_dds
    return TerKind.SEA_OVERLAY if overlay else TerKind.SEA


def texture_dds_name(t: TextureId) -> str:
    """``<til_y>_<til_x>_<provider><zl>.dds``."""
    return texture_name(t) + ".dds"


def border_mask_filename(t: TextureId) -> str:
    """External mask PNG when masks are not imprinted (``FNAMES.mask_file``)."""
    return f"{t.til_y}_{t.til_x}_ZL{t.zl}.png"


def ter_filename(t: TextureId, kind: TerKind) -> str:
    """``<til_y>_<til_x>_<provider><zl>[_water|_sea][_overlay].ter``."""
    return f"{texture_name(t)}{kind.suffix}.ter"


def ter_center(t: TextureId) -> tuple[float, float]:
    """``(lat_med, lon_med)`` = top-left corner of chunk ``(til_x + 8, til_y + 8)`` at ``zl``.

    Ortho4XP ``GEO.gtile_to_wgs84`` (``O4_Geo_Utils.py:66-78``) = ``grid.tile_to_wgs84``.
    """
    return tile_to_wgs84(t.til_x + 8, t.til_y + 8, t.zl)


def load_center_size(lat_med: float, zl: int) -> int:
    """Ground size in metres of the 4096 px texture: ``int(webmercator_pixel_size * 4096)``."""
    return int(webmercator_pixel_size(lat_med, zl) * 4096)


def ter_text(
    t: TextureId, kind: TerKind, *, lat_med: float, lon_med: float, params: TerParams
) -> str:
    """Text of the ``.ter`` file, byte-identical to Ortho4XP (``\\n`` endings, trailing newline)."""
    size = load_center_size(lat_med, t.zl)
    texture = TEST_TEXTURE if params.use_test_texture else texture_dds_name(t)
    tri = kind.tri_type
    lines = ["A", "800", "TERRAIN", ""]
    lines.append(f"LOAD_CENTER {lat_med:.5f} {lon_med:.5f} {size} 4096")
    lines.append(f"BASE_TEX_NOWRAP ../textures/{texture}")
    if kind.is_water and not kind.overlay:  # XP12 water
        lines.append("WATER_COLOR_MASK")
    elif tri == 1:  # inland water overlay: constant transparency LUT
        lines.append(f"BORDER_TEX ../textures/{WATER_TRANSITION_PNG}")
    elif tri == 2 and not params.imprint_masks_to_dds:  # sea overlay, external mask
        border = 4096 // 2 ** (t.zl - params.mask_zl)
        lines.append(f"LOAD_CENTER_BORDER {lat_med:.5f} {lon_med:.5f} {size} {border}")
        lines.append(f"BORDER_TEX ../textures/{border_mask_filename(t)}")
    if params.use_decal_on_terrain and takes_decal(kind, on_sea=params.decal_on_sea):
        lines.append(f"DECAL_LIB {DECAL_LIB}")
    lines.append("WET" if kind.is_water else "NO_ALPHA")
    if kind.is_water or not params.terrain_casts_shadows:
        lines.append("NO_SHADOW")
    return "\n".join(lines) + "\n"


def takes_decal(kind: TerKind, *, on_sea: bool) -> bool:
    """Whether a terrain of ``kind`` gets the decal: land always, the sea with ``on_sea``.

    Ortho4XP writes the decal on land and sea alike (``tri_type != 1``), despite its own comment.
    A user asked for the land alone (2026-09-17): the sea has it only with ``decal_on_sea``, and
    inland water never, as in Ortho4XP.
    """
    return kind.tri_type == 0 or (kind.tri_type == 2 and on_sea)


def ter_kind(filename: str) -> TerKind:
    """The kind a terrain file's name says (the suffix ``ter_filename`` gives it).

    A texture's name ends with its level's digits, so no suffix is ever mistaken for part of it.
    """
    stem = filename.removesuffix(".ter")
    for kind in sorted(TerKind, key=lambda k: -len(k.suffix)):
        if kind.suffix and stem.endswith(kind.suffix):
            return kind
    return TerKind.LAND


def with_decal(text: str, decal: str) -> str:
    """``text`` of a terrain file naming ``decal`` on its ``DECAL_LIB`` line, or none when empty.

    The line goes where ``ter_text`` puts it, before ``WET`` or ``NO_ALPHA``, and any line already
    there goes. The pack writes the decals this way, so that turning them on or off or choosing
    another rewrites its terrain files alone, never the DSF nor the textures: what setdecal does
    to a tile already built (its author asked for the choice, 2026-09-25). A file that needs no
    change comes back byte for byte.
    """
    lines = [ln for ln in text.splitlines(keepends=True) if not ln.startswith("DECAL_LIB ")]
    if decal:
        at = next(
            (i for i, ln in enumerate(lines) if ln.rstrip("\r\n") in ("WET", "NO_ALPHA")),
            len(lines),
        )
        lines.insert(at, f"DECAL_LIB {decal_lib(decal)}\n")
    return "".join(lines)
