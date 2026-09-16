"""Imagery: the web-mercator texture grid, the provider registry and the raw tile store.

Specifications: ``docs/specs/imagery-grid.md``, ``docs/specs/imagery-providers.md``,
``docs/specs/imagery-chunks.md``.
"""

from orthostudio.imagery.chunks import (
    ChunkContainer,
    ChunkEntry,
    ChunkStatus,
    ChunkStore,
)
from orthostudio.imagery.grid import (
    TextureId,
    parse_texture_name,
    quadkey,
    texture_at,
    texture_bbox,
    texture_name,
    texture_tiles,
    textures_covering,
    tile_to_wgs84,
    webmercator_pixel_size,
    wgs84_to_tile,
)
from orthostudio.imagery.providers import (
    PlaceholderRule,
    Provider,
    is_placeholder,
    load_registry,
    tile_url,
)

__all__ = [
    "ChunkContainer",
    "ChunkEntry",
    "ChunkStatus",
    "ChunkStore",
    "PlaceholderRule",
    "Provider",
    "TextureId",
    "is_placeholder",
    "load_registry",
    "parse_texture_name",
    "quadkey",
    "texture_at",
    "texture_bbox",
    "texture_name",
    "texture_tiles",
    "textures_covering",
    "tile_to_wgs84",
    "tile_url",
    "webmercator_pixel_size",
    "wgs84_to_tile",
]
