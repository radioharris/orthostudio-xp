# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where imagery pieces come from: a URL for each 256 x 256 web-mercator piece.

Bing's tiles are addressed by quadkey (Microsoft's published tile system): one digit per zoom level,
0-3 for the quadrant. Its answer when it has no imagery for a piece is a small PNG with the header
``X-VE-Tile-Info: no-tile``; its tile service wants a ``g`` parameter (without it: 400).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

_SWITCH = re.compile(r"\{switch:([^}]*)\}")


@dataclass(frozen=True)
class Provider:
    code: str
    url_template: str  # {zoom} {x} {y} {quadkey} {switch:a,b,c}
    max_zl: int
    max_in_flight: int = 64
    headers: dict[str, str] = field(default_factory=dict)
    no_imagery_header: tuple[str, str] | None = None  # (name, value) of a "no imagery here" answer
    attribution: str = ""


def quadkey(x: int, y: int, zoom: int) -> str:
    digits = []
    for i in range(zoom, 0, -1):
        mask = 1 << (i - 1)
        digits.append(str((1 if x & mask else 0) + (2 if y & mask else 0)))
    return "".join(digits)


def tile_url(p: Provider, x: int, y: int, zoom: int, shift: int = 0) -> str:
    """URL of piece (x, y) at ``zoom``; ``{switch:...}`` spreads pieces over the listed servers, and
    ``shift`` picks the next one along, to ask a piece again somewhere else."""

    def switch(m: re.Match) -> str:
        options = m.group(1).split(",")
        return options[(x + y + shift) % len(options)]

    url = _SWITCH.sub(switch, p.url_template)
    return (
        url.replace("{quadkey}", quadkey(x, y, zoom))
        .replace("{zoom}", str(zoom))
        .replace("{x}", str(x))
        .replace("{y}", str(y))
    )


def no_imagery(p: Provider, headers: Mapping[str, str]) -> bool:
    """Whether an answer is the provider's word that it has no imagery there."""
    if p.no_imagery_header is None:
        return False
    name, value = p.no_imagery_header
    got = {k.lower(): v for k, v in headers.items()}.get(name.lower())
    return got is not None and got.strip().lower() == value.lower()


PROVIDERS: dict[str, Provider] = {
    "BI": Provider(
        code="BI",
        url_template="https://ecn.t{switch:0,1,2,3}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=1",
        max_zl=19,
        max_in_flight=96,
        no_imagery_header=("X-VE-Tile-Info", "no-tile"),
        attribution="Microsoft Bing Maps aerial imagery",
    ),
}


def provider(code: str) -> Provider:
    if code not in PROVIDERS:
        raise SystemExit(
            f"unknown imagery provider {code!r}; known: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[code]
