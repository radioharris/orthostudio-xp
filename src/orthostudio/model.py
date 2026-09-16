"""Small value types shared by every P2 module (tiles, artefact references).

This module has no dependency on the rest of ``orthostudio`` beyond the standard library so that
every layer (DSF, overlays, install, scheduler, CLI) can import it freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

__all__ = ["OVERLAY_PACK", "PACK_PREFIX", "ArtifactRef", "TileRef", "ZoomSpec", "pack_dir_name"]

_TILE_NAME = re.compile(r"^([+-])(\d{2})([+-])(\d{3})$")

PACK_PREFIX = "zOrthoStudio_"
"""The folder of a tile pack is ``zOrthoStudio_<tile>``: the ``z`` keeps it below the packs that
must draw over photos in X-Plane's alphabetical order."""
OVERLAY_PACK = "yOrthoStudio_Overlays"
"""The one pack that holds the overlay DSF of every tile OrthoStudio XP built."""


class TileRef(NamedTuple):
    """A 1° x 1° X-Plane tile, addressed by its south-west corner (integer degrees).

    ``TileRef(43, 5).name`` is ``"+43+005"`` and ``.folder`` is ``"+40+000"``: the same
    formatting as Ortho4XP (``O4_File_Names.py:24-41``: ``short_latlon``,
    ``round_latlon``, ``long_latlon``; folders are 10° cells floored towards minus infinity).
    """

    lat: int
    lon: int

    @property
    def name(self) -> str:
        """``"+43+005"`` (sign, 2-digit latitude, sign, 3-digit longitude)."""
        return f"{self.lat:+03d}{self.lon:+04d}"

    @property
    def folder(self) -> str:
        """Name of the 10° cell directory under ``Earth nav data`` (``"+40+000"``)."""
        return TileRef((self.lat // 10) * 10, (self.lon // 10) * 10).name

    @property
    def dsf_relpath(self) -> Path:
        """``Earth nav data/+40+000/+43+005.dsf`` relative to a scenery pack root."""
        return Path("Earth nav data") / self.folder / f"{self.name}.dsf"

    @classmethod
    def parse(cls, name: str) -> TileRef:
        """Inverse of :attr:`name` (``"+43+005"`` -> ``TileRef(43, 5)``)."""
        m = _TILE_NAME.match(name.strip())
        if m is None:
            raise ValueError(f"not a tile name: {name!r} (expected e.g. '+43+005')")
        lat = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
        lon = int(m.group(4)) * (1 if m.group(3) == "+" else -1)
        return cls(lat, lon)

    def neighbour(self, dlat: int, dlon: int) -> TileRef:
        """The tile ``dlat`` rows north and ``dlon`` columns east, longitude wrapped."""
        lon = self.lon + dlon
        if lon >= 180:
            lon -= 360
        elif lon < -180:
            lon += 360
        return TileRef(self.lat + dlat, lon)

    def __str__(self) -> str:
        return self.name


class ZoomSpec(NamedTuple):
    """An imagery choice: provider code and zoom level (``("BI", 16)``)."""

    provider: str
    zl: int


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A committed artefact of the store: how to find it (``key``) and what it is (``digest``).

    Returned by every node run and given to dependants as their resolved inputs. ``path`` is
    the artefact on disk (a file or a directory, per ``kind``).
    """

    key: str
    digest: str
    path: Path
    rule: str
    kind: Literal["file", "dir"]
    size: int = 0

    def __post_init__(self) -> None:
        for label, value in (("key", self.key), ("digest", self.digest)):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"ArtifactRef.{label} must be 64 lowercase hex, got {value!r}")


def pack_dir_name(tile: TileRef) -> str:
    """``zOrthoStudio_+43+005``."""
    return f"{PACK_PREFIX}{tile.name}"
