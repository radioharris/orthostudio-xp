"""Parameters consumed by the DSF stage (Ortho4XP defaults, ``O4_Config_Utils.py:163-338``).

Spec: ``docs/specs/dsf-encoding.md`` section 2. A frozen pydantic ``RuleParams`` so that
``DsfParams.subset_of(tile_cfg)`` picks the fields out of a full tile configuration and the
canonical dump enters the artefact key.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from orthostudio.graph.rule import RuleParams
from orthostudio.textures.ter import TerParams

__all__ = ["DsfParams", "WaterTech", "Zone"]

WaterTech = Literal["XP11 + bathy", "XP12"]

Zone = tuple[list[float], int, str]
"""One ``zone_list`` entry: ``([lat0, lon0, lat1, lon1, ...], zl, provider)`` (Ortho4XP format)."""


class DsfParams(RuleParams):
    """Tile parameters read by ``build_dsf`` (``O4_DSF_Utils.py``, ``O4_Bathymetry.py``).

    ``sea_texture_blur`` (listed by the P2a contract) is deliberately absent: the DSF does not
    consume it, so it must not enter the DSF key (``graph-keys.md`` K2); it belongs to the
    textures node (``pipeline-build.md`` section 7).
    """

    water_tech: WaterTech = "XP11 + bathy"
    ratio_bathy: float = 1.0
    ratio_water: float = 0.25
    normal_map_strength: float = 1.0
    terrain_casts_shadows: bool = True
    use_decal_on_terrain: bool = False
    imprint_masks_to_dds: bool = True
    use_masks_for_inland: bool = False
    mesh_zl: int = 19
    mask_zl: int = 14
    default_zl: int = 16
    default_website: str = "BI"
    zone_list: list[Zone] = Field(default_factory=list)
    cover_airports_with_highres: str = "False"
    """``False`` / ``True`` / ``ICAO`` / ``Existing`` (a string in Ortho4XP, kept as such)."""
    cover_zl: int = 18
    cover_extent: float = 1.0
    overlay_lod: float = 25000.0
    use_test_texture: bool = False
    """Ortho4XP module global ``use_test_texture`` (``O4_DSF_Utils.py:25``): test DDS in every
    .ter."""

    def ter_params(self) -> TerParams:
        """The subset consumed by ``orthostudio.textures.ter.ter_text``."""
        return TerParams(
            water_tech=self.water_tech,
            imprint_masks_to_dds=self.imprint_masks_to_dds,
            mask_zl=self.mask_zl,
            use_decal_on_terrain=self.use_decal_on_terrain,
            terrain_casts_shadows=self.terrain_casts_shadows,
            use_test_texture=self.use_test_texture,
        )

    @property
    def quad_capacity(self) -> int:
        """Pool capacity: 35 000 with inland masks, else 50 000 (``O4_DSF_Utils.py:495-498``)."""
        return 35000 if self.use_masks_for_inland else 50000
