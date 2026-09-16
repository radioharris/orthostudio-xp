"""``build_overlay``: one overlay DSF from the Global Scenery DSF of a tile (spec section 5).

Four steps in a private work directory: materialise the source (7z -> DSF), ``dsf2text``,
filter the text, ``text2dsf``; then the result is put in place atomically under
``<out_root>/yOrthoStudio_Overlays/Earth nav data/<10x10>/<tile>.dsf``.
"""

from __future__ import annotations

import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_link_or_copy
from orthostudio.model import OVERLAY_PACK
from orthostudio.overlays.dsftool import run_dsftool
from orthostudio.overlays.exclusions import OverlayExclusions
from orthostudio.overlays.source import materialize_source, overlay_source_path
from orthostudio.overlays.textfilter import FilterStats, filter_dsf_text
from orthostudio.tilefiles.paths import round_latlon, short_latlon

__all__ = [
    "OVERLAY_PACK",
    "OverlayResult",
    "OverlayStats",
    "TileLike",
    "build_overlay",
    "build_overlay_detailed",
    "overlay_dsf_path",
]


class TileLike(Protocol):
    """What the builder needs of a tile: ``orthostudio.model.TileRef`` or the local fallback."""

    @property
    def lat(self) -> int: ...

    @property
    def lon(self) -> int: ...


@dataclass(slots=True)
class OverlayStats:
    """Sizes, counts and timings of one build."""

    tile: str
    source: str
    source_size: int
    compressed: bool
    extracted_size: int | None
    text_size: int
    filtered_text_size: int
    output_size: int
    seconds_extract: float
    seconds_dsf2text: float
    seconds_filter: float
    seconds_text2dsf: float
    seconds_total: float
    filter: FilterStats = field(default_factory=FilterStats)
    dsftool_output: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "tile": self.tile,
            "source": self.source,
            "source_size": self.source_size,
            "compressed": self.compressed,
            "extracted_size": self.extracted_size,
            "text_size": self.text_size,
            "filtered_text_size": self.filtered_text_size,
            "output_size": self.output_size,
            "seconds": {
                "extract": round(self.seconds_extract, 3),
                "dsf2text": round(self.seconds_dsf2text, 3),
                "filter": round(self.seconds_filter, 3),
                "text2dsf": round(self.seconds_text2dsf, 3),
                "total": round(self.seconds_total, 3),
            },
            "filter": self.filter.to_dict(),
        }


@dataclass(slots=True)
class OverlayResult:
    """The overlay DSF written and how it was made."""

    path: Path
    stats: OverlayStats


def overlay_dsf_path(out_root: Path, tile: TileLike) -> Path:
    """``<out_root>/yOrthoStudio_Overlays/Earth nav data/+40+000/+43+005.dsf``."""
    return (
        Path(out_root)
        / OVERLAY_PACK
        / "Earth nav data"
        / round_latlon(tile.lat, tile.lon)
        / (short_latlon(tile.lat, tile.lon) + ".dsf")
    )


def build_overlay_detailed(
    global_scenery_dir: Path,
    tile: TileLike,
    exclusions: OverlayExclusions | None = None,
    *,
    dsftool: Path,
    out_root: Path,
    workdir: Path,
    timeout_s: float = 600.0,
    keep_workdir: bool = False,
) -> OverlayResult:
    """Build the overlay DSF of ``tile`` and return its path with the build statistics.

    ``workdir`` receives one private sub-directory per call, removed at the end (success or
    failure) unless ``keep_workdir``. Errors are ``OsxpError`` (``DSF_OVERLAY_SOURCE_MISSING``,
    ``DSF_SOURCE_DECOMPRESS_FAILED``, ``DSF_SOURCE_CORRUPTED``, ``DSF_OVERLAY_TOOL_FAILED``,
    ``DSF_ACTIVATION_FAILED``).
    """
    exclusions = exclusions if exclusions is not None else OverlayExclusions()
    name = short_latlon(tile.lat, tile.lon)
    src = overlay_source_path(global_scenery_dir, tile.lat, tile.lon)
    final = overlay_dsf_path(out_root, tile)
    work = Path(workdir) / f"{name}-{os.getpid()}-{secrets.token_hex(4)}"
    work.mkdir(parents=True, exist_ok=False)
    t_start = time.perf_counter()
    try:
        t0 = time.perf_counter()
        info = materialize_source(src, work, tile=name)
        t_extract = time.perf_counter() - t0

        text = work / f"{name}.txt"
        run1 = run_dsftool(dsftool, "dsf2text", info.path, text, timeout_s=timeout_s)

        t0 = time.perf_counter()
        filtered = work / f"{name}_overlay.txt"
        fstats = filter_dsf_text(text, filtered, exclusions)
        t_filter = time.perf_counter() - t0
        text_size = text.stat().st_size
        text.unlink()  # 170 MB for a Global Scenery tile: free it before text2dsf

        out_dsf = work / f"{name}_overlay.dsf"
        run2 = run_dsftool(dsftool, "text2dsf", filtered, out_dsf, timeout_s=timeout_s)

        try:
            atomic_link_or_copy(out_dsf, final, link=False)
        except OSError as exc:
            raise _activation_error(name, final, exc) from exc
        output_size = final.stat().st_size
        total = time.perf_counter() - t_start
        stats = OverlayStats(
            tile=name,
            source=str(info.source),
            source_size=info.size,
            compressed=info.compressed,
            extracted_size=info.extracted,
            text_size=text_size,
            filtered_text_size=filtered.stat().st_size,
            output_size=output_size,
            seconds_extract=t_extract,
            seconds_dsf2text=run1.seconds,
            seconds_filter=t_filter,
            seconds_text2dsf=run2.seconds,
            seconds_total=total,
            filter=fstats,
            dsftool_output=run1.stdout + run2.stdout,
        )
        return OverlayResult(final, stats)
    finally:
        if not keep_workdir:
            shutil.rmtree(work, ignore_errors=True)


def _activation_error(name: str, final: Path, exc: OSError) -> OsxpError:
    """``DSF_ACTIVATION_FAILED`` for the overlay DSF (same code as the tile DSF)."""
    return OsxpError(
        "DSF_ACTIVATION_FAILED",
        context={"tile": name, "path": str(final), "reason": f"{type(exc).__name__}: {exc}"},
        message=f"Overlay DSF of tile {name} could not be moved into place at {final} ({exc}).",
    )


def build_overlay(
    global_scenery_dir: Path,
    tile: TileLike,
    exclusions: OverlayExclusions | None = None,
    *,
    dsftool: Path,
    out_root: Path,
    workdir: Path,
) -> Path:
    """P2 contract entry point: the overlay DSF path
    (``<out_root>/yOrthoStudio_Overlays/Earth nav data/<10x10>/<tile>.dsf``)."""
    return build_overlay_detailed(
        global_scenery_dir,
        tile,
        exclusions,
        dsftool=dsftool,
        out_root=out_root,
        workdir=workdir,
    ).path
