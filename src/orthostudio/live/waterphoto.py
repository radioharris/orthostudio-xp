# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The water of Live's own mesh: a water photo for the water that fades, one grey border for the
rivers and lakes that do not.

The water overlays of Live's mesh fade the photo into X-Plane's water near the shore, through a
mask of each texture. Given to X-Plane as their ``BORDER_TEX``, a PNG beside the photo, that mask
came after the photo: for 2 to 2.5 s at a cold start, 2 to 5 s in Live each time a new photo
appeared, the water showed large light triangles (the user's Rhône camera, 2026-11-17). Measured
there, X-Plane cold: the mask in the photo's alpha (Build's way), right from 0 s; a separate mask
PNG, 2 s; the same mask as a DDS, 2.5 s, so the format does not help; a water photo of its own,
the mask in its alpha, given as the overlay's ``BASE_TEX`` with no border, right from 0 s at 4096,
2048, 1024 and 512 px. Close at the bank, 512 and 1024 px left a straight line, 2048 was clean; at
ZL17/19 a water photo of 2048 px (3.3 m a pixel on the ZL16 grid's textures) looks the same as one
half the land's side: 2048 px at most, whatever the zoom.

So each texture of Live's mesh whose water fades has its water photo ``<texture>_water.dds``
beside it in the pack's ``textures``, its alpha the mask, and its water terrain names it instead
of the photo and the mask (``orthostudio.live.names``). Rivers and lakes fade so only when the
pilot asks (``LiveSettings.rivers_fade``); otherwise they are drawn as XPME and AutoOrtho draw
them, one uniform grey as their border, 150 (:data:`EVEN_BORDER`: in X-Plane the same look as
XPME's ``ratio_water`` 0.25), and no mask. The sea and lakes over 200 km² keep their masks either
way.

Measured with a prototype at Stockholm, every water body masked (6,111 water photos in a
ten-minute flight, the worst case): the same frame rate (38 against 37), Live's queue as often
empty (92 %), Live's processor +20 %, its writes +43 % (1.70 to 2.43 GB), X-Plane's memory with
its graphics +3 GB; a 2048 px water photo encoded in 43 ms against 291 ms for the 4096 px land
photo (2026-11-17).
"""

from __future__ import annotations

import functools
import io

SUFFIX = "_water.dds"
EVEN_BORDER = "osxp_rivers_even.png"
"""The border of rivers and lakes that do not fade: uniform grey :data:`EVEN_GREY`, Live's own name,
which no region uses."""
EVEN_GREY = 150
NO_BORDER = "osxp_rivers_none.png"
"""The border of rivers and lakes that fade but have no mask: all 0, X-Plane's water alone, as
before the water photos."""
BORDER_SIDE = 16


def water_name(name: str) -> str:
    """The water photo of a texture (``23264_33856_BI16.dds``: ``23264_33856_BI16_water.dds``)."""
    return name[: -len(".dds")] + SUFFIX


def is_water_name(name: str) -> bool:
    """Whether a file of the pack's ``textures`` is a water photo, not a texture: a texture's name
    with a suffix, which ``geo.parse_texture_name`` reads as the texture's."""
    return name.endswith(SUFFIX)


@functools.cache
def border_png(grey: int) -> bytes:
    """A small uniform grey PNG (8-bit grey), for a water terrain's ``BORDER_TEX``."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("L", (BORDER_SIDE, BORDER_SIDE), grey).save(out, format="PNG")
    return out.getvalue()


def borders() -> dict[str, bytes]:
    """The two borders of rivers and lakes, by their file's name."""
    return {EVEN_BORDER: border_png(EVEN_GREY), NO_BORDER: border_png(0)}
