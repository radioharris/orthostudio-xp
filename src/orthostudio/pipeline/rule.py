"""The artefact-graph rule ``texture.dds``: one 4096² DDS from chunks, mask and parents.

Spec: ``docs/specs/pipeline-textures.md`` section 6. The rule is a pure function of its
inputs (the chunk container, the mask PNG and its crop window, the parents blob) and of its
parameters (provider, zoom level, encoder, imprint settings); the texture position is not a
parameter, so identical inputs share one artefact.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from orthostudio.errors import OsxpError
from orthostudio.graph import Node, RuleParams, RunContext, Source, key_for, rule
from orthostudio.imagery.chunks import ChunkContainer
from orthostudio.imagery.grid import TextureId
from orthostudio.pipeline.parents import read_parents_blob
from orthostudio.textures.assemble import assemble_texture_detailed, parent_fallback
from orthostudio.textures.colour import adjust_photo, adjust_photo_inside, photo_unchanged
from orthostudio.textures.encode import EncoderUnavailableError, encode_dds
from orthostudio.textures.imprint import imprint, load_mask, mask_crop

__all__ = [
    "RULE_NAME",
    "RULE_VERSION",
    "BuildInfo",
    "TextureDdsParams",
    "dds_key",
    "dds_node",
    "take_build_info",
    "texture_dds",
]

RULE_NAME = "texture.dds"
RULE_VERSION = 1

TEXTURE_RAM_MB = 400
"""What one texture holds while it is built, for the scheduler to count on.

Measured on a 4096 texture taking the heaviest path there is -- the square's colours, a zone
over the whole of it, the sea blur, a mask and BC3 -- at **288 MB** of arrays above the
photograph itself, which is 48 MB of that. It was declared as 250, and several textures are
built at once: on a machine with little memory the promise was what decided how many, so it
over-committed and the build swapped (found in review, 2026-09-23). The number above adds a
little for the interpreter and the encoder's own buffers.
"""


class TextureDdsParams(RuleParams):
    """Parameters the DDS depends on (frozen, closed)."""

    provider: str
    zl: int
    encoder: str
    encoder_version: str
    mip_mode: str = "gamma22"
    refine_passes: int = 0
    mask_zl: int = 14
    mask_crop: tuple[int, int, int] | None = None
    """``(x0, y0, side)`` window of the ``mask_zl`` mask, ``None`` when the texture is unmasked."""
    sea_texture_blur: float = 0.0
    clean_halo: bool = False
    parent_levels: int = 5
    photo_brightness: float = 0.0
    photo_contrast: float = 0.0
    photo_saturation: float = 0.0
    """Colours of the photo (``textures/colour.py``, a user asked 2026-09-18). Zero changes
    nothing, and :meth:`canonical` then leaves the three out of the key, so every texture built
    before they existed stays a hit."""
    photo_shapes: tuple[tuple[tuple[float, ...], float, float, float], ...] = ()
    """Colours of the zones that reach into this texture, each as ``(ring, brightness, contrast,
    saturation)`` where ``ring`` is ``x0, y0, x1, y1, ...`` in the texture's own pixels.

    A zone used to colour whole texture files, the one holding its centre: a zone smaller than a
    texture (6.4 km at ZL16) then changed nothing at all, and said nothing either (a user,
    2026-09-23). The colours are a calculation on the pixels, so they follow the shape drawn, with
    a soft edge so the join does not show."""
    photo_feather_px: int = 24
    """Width of that soft edge, about 40 m at ZL16 in mid-latitudes."""

    def canonical(self) -> dict[str, Any]:
        doc = super().canonical()
        if photo_unchanged(self.photo_brightness, self.photo_contrast, self.photo_saturation):
            for name in ("photo_brightness", "photo_contrast", "photo_saturation"):
                doc.pop(name, None)
        if not self.photo_shapes:  # every texture built before zones had a shape stays a hit
            doc.pop("photo_shapes", None)
            doc.pop("photo_feather_px", None)
        return doc


@dataclass(slots=True)
class BuildInfo:
    """What the rule learnt while building (read back by the worker, not part of the artefact)."""

    fmt: str
    from_fallback: int
    unfilled: int
    seconds_assemble: float
    seconds_mask: float
    seconds_encode: float
    corrupted: tuple[int, ...] = ()
    """``OK`` chunks whose body did not decode; the pipeline marks them ``ERROR`` on disk."""


_LAST_BUILDS: dict[str, BuildInfo] = {}


def take_build_info(key: str) -> BuildInfo | None:
    """Build statistics of ``key`` in this process (``None`` after a hit), consumed once."""
    return _LAST_BUILDS.pop(key, None)


@rule(
    name=RULE_NAME,
    version=RULE_VERSION,
    params=TextureDdsParams,
    inputs=("chunks", "mask", "parents"),
    ram_mb=TEXTURE_RAM_MB,
    kind="file",
)
def texture_dds(ctx: RunContext) -> None:
    """Assemble, imprint, mip and encode one texture into ``ctx.out``."""
    params = ctx.params
    assert isinstance(params, TextureDdsParams)
    t0 = time.perf_counter()
    container = ChunkContainer.from_bytes(ctx.input_path("chunks").read_bytes())
    fallback = None
    parents_input = ctx.inputs["parents"]
    if parents_input.present:
        assert parents_input.path is not None
        t_blob, parents = read_parents_blob(parents_input.path.read_bytes())
        t_like = TextureId(t_blob.til_x, t_blob.til_y, params.zl, params.provider)
        fallback = parent_fallback(
            t_like, lambda x, y, zl: parents.get((x, y, zl)), max_levels=params.parent_levels
        )
    assembled = assemble_texture_detailed(container, fallback)
    # The photo's colours, before the mask: X-Plane's water keeps its own (2026-09-18).
    delivered = assembled.rgb
    rgb = adjust_photo(
        delivered,
        brightness=params.photo_brightness,
        contrast=params.photo_contrast,
        saturation=params.photo_saturation,
    )
    for ring, brightness, contrast, saturation in params.photo_shapes:
        # from the photograph as delivered, not from what the square already did to it: a zone
        # replaces the square's colours where it covers, which is what the page paints
        rgb = adjust_photo_inside(
            rgb,
            ring,
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            feather_px=params.photo_feather_px,
            source=delivered,
        )
    t1 = time.perf_counter()
    mask_input = ctx.inputs["mask"]
    image: np.ndarray
    if mask_input.present:
        assert mask_input.path is not None and params.mask_crop is not None
        x0, y0, side = params.mask_crop
        alpha = mask_crop(load_mask(mask_input.path), x0, y0, side)
        image = imprint(
            rgb,
            alpha,
            sea_texture_blur=params.sea_texture_blur,
            zl=params.zl,
            clean_halo=params.clean_halo,
        )
        fmt = "bc3"
    else:
        image = rgb
        fmt = "bc1"
    t2 = time.perf_counter()
    try:
        data = encode_dds(
            image,
            fmt,
            mips=True,
            mip_mode=params.mip_mode,  # type: ignore[arg-type]
            encoder=params.encoder,  # type: ignore[arg-type]
            refine_passes=params.refine_passes,
        )
    except EncoderUnavailableError as exc:
        raise OsxpError("TEX_ENCODER_UNAVAILABLE", context={"reason": str(exc)}) from exc
    ctx.out.write_bytes(data)
    t3 = time.perf_counter()
    _LAST_BUILDS[ctx.key] = BuildInfo(
        fmt,
        len(assembled.from_fallback),
        len(assembled.unfilled),
        t1 - t0,
        t2 - t1,
        t3 - t2,
        tuple(assembled.corrupted),
    )


def dds_node(
    params: TextureDdsParams,
    *,
    chunks_path: Path,
    chunks_digest: str,
    mask_path: Path | None,
    mask_digest: str | None,
    parents_path: Path | None,
    parents_digest: str | None,
) -> Node:
    """The graph node of one texture; ``chunks_digest`` is ``ChunkContainer.digest()``."""
    inputs: dict[str, Source | None] = {
        "chunks": Source(chunks_digest, chunks_path, "chunks"),
        "mask": Source(mask_digest, mask_path, "mask") if mask_digest is not None else None,
        "parents": Source(parents_digest, parents_path, "parents")
        if parents_digest is not None
        else None,
    }
    return Node(texture_dds, params, inputs)


def dds_key(
    params: TextureDdsParams,
    *,
    chunks_digest: str,
    mask_digest: str | None,
    parents_digest: str | None,
) -> str:
    """Key of the artefact without building a node (what the parent process checks)."""
    key, _recipe = key_for(
        texture_dds,
        params,
        {"chunks": chunks_digest, "mask": mask_digest, "parents": parents_digest},
    )
    return key
