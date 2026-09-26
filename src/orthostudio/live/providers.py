# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where imagery pieces come from: OrthoStudio XP's imagery sources, for 256 x 256 web-mercator
pieces.

The sources are OrthoStudio XP's own (:mod:`orthostudio.imagery.providers`): the registry it ships,
Bing Maps first, and the sources a user added in its Plan (``sources.toml``), with their URL
grammar, their answers for "no imagery here" and their limits. Live adds two things a stream needs:

- **Bing Maps beyond a country's source.** A source that covers one country (Spain's PNOA, the
  Netherlands' PDOK...) has nothing past its border, and a flight crosses borders: a texture
  without imagery would stay the neutral colour, green-grey. Outside the source's bounds, and
  wherever it answers "no imagery", the piece is Bing's (:func:`fallback_for`).
- **Its own place in the imagery cache** (:func:`cache_key`): the source's, which a source of the
  user's makes out of its address, and Bing's beside it for a country's source (``SP+BI``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from orthostudio.imagery.grid import quadkey
from orthostudio.imagery.providers import Provider, cache_name, is_placeholder, load_registry
from orthostudio.imagery.providers import tile_url as _tile_url

__all__ = [
    "FALLBACK",
    "Provider",
    "cache_key",
    "covers",
    "fallback_for",
    "listing",
    "no_imagery",
    "provider",
    "quadkey",
    "registry",
    "tile_url",
]

FALLBACK = "BI"  # Bing Maps: beyond the bounds of a country's source


def registry() -> dict[str, Provider]:
    """OrthoStudio XP's sources, then the user's own, by code."""
    return load_registry()


def provider(code: str) -> Provider:
    sources = registry()
    if code not in sources:
        raise ValueError(f"unknown imagery source {code!r}; known: {', '.join(sources)}")
    return sources[code]


def tile_url(p: Provider, x: int, y: int, zoom: int, shift: int = 0) -> str:
    """URL of piece (x, y) at ``zoom``; ``{switch:...}`` spreads pieces over the listed servers, and
    ``shift`` picks the next one along, to ask a piece again somewhere else."""
    return _tile_url(p, x, y, zoom, switch=x + y + shift)


def no_imagery(p: Provider, headers: Mapping[str, str], body: bytes = b"") -> bool:
    """Whether an answer is the source's word that it has no imagery there."""
    return is_placeholder(p, headers, body) is not None


def _lat(y: int, zoom: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2**zoom))))


def covers(p: Provider, x: int, y: int, zoom: int) -> bool:
    """Whether piece (x, y) at ``zoom`` meets the source's bounds (always, for the whole world)."""
    if p.extent_bounds is None:
        return True
    lon0, lat0, lon1, lat1 = p.extent_bounds
    west, east = x / 2**zoom * 360 - 180, (x + 1) / 2**zoom * 360 - 180
    north, south = _lat(y, zoom), _lat(y + 1, zoom)
    return west < lon1 and east > lon0 and south < lat1 and north > lat0


def fallback_for(p: Provider) -> Provider | None:
    """Bing Maps for a source that covers one country; None for a source of the whole world."""
    if p.extent_bounds is None or p.code == FALLBACK:
        return None
    return provider(FALLBACK)


def cache_key(code: str) -> str:
    """The name a source's pieces are kept under in the imagery cache."""
    p = provider(code)
    fallback = fallback_for(p)
    return cache_name(p) + (f"+{cache_name(fallback)}" if fallback is not None else "")


def listing() -> list[dict[str, Any]]:
    """The sources as the Live window lists them, as OrthoStudio XP's engine gives them to its Plan
    (``GET /api/providers``). A source that is the very same imagery as another is listed once."""
    from orthostudio.api.app import provider_json

    return [provider_json(p) for p in registry().values() if p.same_as is None]
