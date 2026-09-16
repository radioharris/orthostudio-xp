"""Readers of the ``.ter`` files written by Ortho4XP (``O4_DSF_Utils.py:261-357``).

Spec: ``docs/specs/tile-files.md`` section 3.1. A ``.ter`` is named after its texture
(``{til_y}_{til_x}_{provider}{zl}``) plus a suffix giving the triangle type and overlay flag;
its ``LOAD_CENTER`` line is recomputed from the parsed name to make the ``{provider}{zl}``
split unambiguous and to check the file against its name.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from orthostudio.errors import OsxpError
from orthostudio.tilefiles._grid import TextureId, load_center

__all__ = [
    "TerFile",
    "TerKind",
    "list_textures",
    "parse_ter_name",
    "read_ter",
    "ter_stem",
]


class TerKind(StrEnum):
    """Terrain kind encoded in the ``.ter`` suffix (O4_DSF_Utils.py:275-279)."""

    LAND = "land"
    WATER = "water"
    WATER_OVERLAY = "water_overlay"
    SEA = "sea"
    SEA_OVERLAY = "sea_overlay"

    @property
    def suffix(self) -> str:
        """The file-name suffix (``""`` for land, ``_sea_overlay`` ...)."""
        return "" if self is TerKind.LAND else "_" + self.value

    @property
    def is_water(self) -> bool:
        """True for every kind but land (``tri_type`` 1 or 2)."""
        return self is not TerKind.LAND

    @property
    def is_overlay(self) -> bool:
        """True for the overlay kinds (second DSF pass, ``is_overlay`` in Ortho4XP)."""
        return self in (TerKind.WATER_OVERLAY, TerKind.SEA_OVERLAY)


_SUFFIX_TO_KIND = {k.suffix: k for k in TerKind}
# Order of kinds in the tuples returned by list_textures.
_KIND_ORDER = {k: i for i, k in enumerate(TerKind)}
# The provider code may contain "_" (user .lay files, g2xpl_16): the non-greedy group stops at
# the first point where a known suffix (or none) completes the name, so "My_Prov14_sea.ter"
# splits into "My_Prov14" + "_sea"; the zoom level is then separated by _candidates.
_NAME_RE = re.compile(
    r"^(?P<til_y>\d+)_(?P<til_x>\d+)_(?P<provider_zl>.+?)"
    r"(?P<suffix>(?:_water|_sea)?(?:_overlay)?)\.ter$"
)
_LOAD_CENTER_TOL = 5e-5  # the file holds 5 decimals
_ZL_MIN, _ZL_MAX = 1, 22


def _corrupted(path: Path | None, name: str, reason: str) -> OsxpError:
    where = str(path) if path is not None else name
    return OsxpError(
        "DSF_SOURCE_CORRUPTED",
        context={"path": where, "reason": reason},
        message=f"Terrain file {where} is not an Ortho4XP .ter file: {reason}.",
        remedy="Rebuild the tile with Ortho4XP or remove the stray file.",
    )


def _candidates(til_x: int, til_y: int, provider_zl: str) -> list[tuple[str, int]]:
    """Possible ``(provider, zl)`` splits of ``{provider}{zl}``, longest provider first."""
    out: list[tuple[str, int]] = []
    for digits in (1, 2):
        if len(provider_zl) <= digits or not provider_zl[-digits:].isdigit():
            continue
        provider, zl = provider_zl[:-digits], int(provider_zl[-digits:])
        if not _ZL_MIN <= zl <= _ZL_MAX or (provider_zl[-digits] == "0" and digits == 2):
            continue
        if til_x % 16 or til_y % 16 or til_x > 2**zl - 16 or til_y > 2**zl - 16:
            continue
        out.append((provider, zl))
    return out


def parse_ter_name(
    name: str, *, zl: int | None = None, lon_med: float | None = None
) -> tuple[TextureId, TerKind]:
    """Split a ``.ter`` file name into its texture and kind.

    ``zl`` or ``lon_med`` (the ``LOAD_CENTER`` longitude) removes the ambiguity of a provider
    code ending with digits; with neither, a two-digit zoom level in 10..19 is preferred.
    """
    m = _NAME_RE.match(name)
    if not m:
        raise _corrupted(None, name, "name is not {til_y}_{til_x}_{provider}{zl}[_kind].ter")
    til_y, til_x = int(m["til_y"]), int(m["til_x"])
    candidates = _candidates(til_x, til_y, m["provider_zl"])
    if zl is not None:
        candidates = [c for c in candidates if c[1] == zl]
    if lon_med is not None:
        candidates = [
            c
            for c in candidates
            if abs(load_center(TextureId(til_x, til_y, c[1], c[0]))[1] - lon_med)
            <= _LOAD_CENTER_TOL
        ]
    if not candidates:
        raise _corrupted(None, name, "no zoom level is consistent with the tile indices")
    if len(candidates) > 1:
        preferred = [c for c in candidates if 10 <= c[1] <= 19]
        candidates = preferred or candidates[-1:]
    provider, zl_found = candidates[0]
    return TextureId(til_x, til_y, zl_found, provider), _SUFFIX_TO_KIND[m["suffix"]]


def ter_stem(t: TextureId) -> str:
    """``{til_y}_{til_x}_{provider}{zl}`` (O4_File_Names.py:413-440, non g2xpl)."""
    return f"{t.til_y}_{t.til_x}_{t.provider}{t.zl}"


@dataclass(frozen=True, slots=True)
class TerFile:
    """One parsed ``.ter``: what the P1 imagery stage needs from it."""

    path: Path
    texture: TextureId
    kind: TerKind
    lat_med: float
    lon_med: float
    size_m: int
    base_tex: str
    border_tex: str | None = None
    directives: tuple[str, ...] = field(default_factory=tuple)

    @property
    def texture_stem(self) -> str:
        """Stem of the DDS referenced by ``BASE_TEX_NOWRAP``."""
        return Path(self.base_tex).stem


def read_ter(path: Path) -> TerFile:
    """Parse an Ortho4XP ``.ter`` and check its content against its name (spec 3.1)."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise _corrupted(path, path.name, f"unreadable ({exc})") from exc
    if lines[:3] != ["A", "800", "TERRAIN"]:
        raise _corrupted(path, path.name, "header is not A / 800 / TERRAIN")
    fields: dict[str, list[str]] = {}
    directives: list[str] = []
    for line in lines[3:]:
        if not line.strip():
            continue
        key, _, rest = line.partition(" ")
        fields.setdefault(key, []).append(rest.strip())
        directives.append(line.rstrip())
    if "LOAD_CENTER" not in fields or "BASE_TEX_NOWRAP" not in fields:
        raise _corrupted(path, path.name, "LOAD_CENTER or BASE_TEX_NOWRAP missing")
    try:
        lat_s, lon_s, size_s, res_s = fields["LOAD_CENTER"][0].split()
        lat_med, lon_med, size_m, res = float(lat_s), float(lon_s), int(size_s), int(res_s)
    except ValueError as exc:
        raise _corrupted(path, path.name, "LOAD_CENTER is not 'lat lon size 4096'") from exc
    texture, kind = parse_ter_name(path.name, lon_med=lon_med)
    exp_lat, exp_lon, exp_size = load_center(texture)
    if abs(exp_lat - lat_med) > _LOAD_CENTER_TOL or res != 4096 or abs(exp_size - size_m) > 1:
        raise _corrupted(
            path,
            path.name,
            f"LOAD_CENTER {lat_med} {lon_med} {size_m} {res} does not match "
            f"{exp_lat:.5f} {exp_lon:.5f} {exp_size} 4096",
        )
    base_tex = fields["BASE_TEX_NOWRAP"][0]
    if Path(base_tex).stem != ter_stem(texture) and Path(base_tex).name != "test_texture.dds":
        raise _corrupted(path, path.name, f"BASE_TEX_NOWRAP {base_tex} does not match the name")
    border = fields.get("BORDER_TEX")
    return TerFile(
        path=path,
        texture=texture,
        kind=kind,
        lat_med=lat_med,
        lon_med=lon_med,
        size_m=size_m,
        base_tex=base_tex,
        border_tex=border[0] if border else None,
        directives=tuple(directives),
    )


def _terrain_dir(build_dir: Path) -> Path:
    terrain = Path(build_dir) / "terrain"
    if not terrain.is_dir():
        raise OsxpError(
            "SYS_WORKING_DIR_INVALID",
            context={"path": str(build_dir)},
            message=f"Directory {build_dir} has no terrain/ folder: not an Ortho4XP tile build.",
            remedy="Run steps 1-3 of Ortho4XP on the tile first.",
        )
    return terrain


def read_all_ter(build_dir: Path) -> list[TerFile]:
    """Every ``terrain/*.ter`` of a build, sorted by file name."""
    return [read_ter(p) for p in sorted(_terrain_dir(build_dir).glob("*.ter"))]


def list_textures(build_dir: Path) -> list[tuple[TextureId, tuple[TerKind, ...]]]:
    """Textures of an Ortho4XP build with the terrain kinds each one carries.

    Deduced from ``terrain/*.ter`` (spec 3.1); sorted by ``(zl, til_y, til_x, provider)``,
    kinds in the order of ``TerKind``.
    """
    kinds: defaultdict[TextureId, set[TerKind]] = defaultdict(set)
    for ter in read_all_ter(build_dir):
        kinds[ter.texture].add(ter.kind)
    return [
        (t, tuple(sorted(kinds[t], key=_KIND_ORDER.__getitem__)))
        for t in sorted(kinds, key=lambda t: (t.zl, t.til_y, t.til_x, t.provider))
    ]
