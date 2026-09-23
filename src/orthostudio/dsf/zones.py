"""Texture (zoom level, provider) of every mesh cell of a tile.

Port of ``zone_list_to_ortho_dico`` (``O4_DSF_Utils.py:110-257``): a 4096² priority image of
the zones, the airport upgrade to ``cover_zl``, the ``Existing`` mode, and the texture of a
mesh-grid cell at its zoom level. Spec: ``docs/specs/dsf-terrain-assignment.md`` 3.4.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import cos, pi
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw

from orthostudio.dsf.params import DsfParams
from orthostudio.errors import OsxpError
from orthostudio.imagery.grid import EARTH_RADIUS, TextureId, texture_at, tile_to_wgs84
from orthostudio.model import TileRef

log = logging.getLogger("orthostudio.dsf.zones")

__all__ = ["AirportCover", "TextureMap", "airport_covers", "texture_map"]

IMAGE_SIDE = 4096
_MAX_ZONES = 254  # "L" image: the base zone is 1, zones 2..255

M_TO_LAT = 1 / (pi * EARTH_RADIUS / 180)
"""Degrees of latitude per metre (``O4_Geo_Utils.py:5-6``)."""


def m_to_lon(lat: float) -> float:
    """Degrees of longitude per metre at ``lat`` (``O4_Geo_Utils.py:14-15``)."""
    return M_TO_LAT / cos(pi * lat / 180)


class AirportCover(NamedTuple):
    """Bounds of an airport boundary in tile-relative degrees (``boundary.bounds`` of step 1)."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float
    is_icao: bool


def airport_covers(vectors_dir: Path) -> list[AirportCover]:
    """The airports of a tile from the record its vector stage published in ``vectors_dir``.

    Returns the boundary bounds and whether the key is an ICAO code, in the record's order (the
    order of Ortho4XP's ``dico_airports``, ``O4_DSF_Utils.py:116-122``); an empty list when the
    tile has no airport, so no record.
    """
    from orthostudio.airports_vec.artefact import AIRPORTS_JSON, AIRPORTS_NPZ, read_airports

    directory = Path(vectors_dir)
    if not (directory / AIRPORTS_JSON).is_file() or not (directory / AIRPORTS_NPZ).is_file():
        return []
    out: list[AirportCover] = []
    try:
        for entry in read_airports(directory):
            boundary = entry["boundary"]
            if boundary is None:
                raise ValueError(f"airport {entry['key']!r} has no boundary")
            xmin, ymin, xmax, ymax = boundary.bounds
            out.append(AirportCover(xmin, ymin, xmax, ymax, entry["key_type"] == "icao"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OsxpError(
            "MESH_INPUT_MISSING",
            context={"path": str(directory / AIRPORTS_JSON), "reason": str(exc)},
            message=f"The airport record in {directory} cannot be read: {exc}.",
            remedy="Rebuild the vector stage of the tile, or disable the airport upgrade.",
        ) from exc
    return out


@dataclass(frozen=True)
class TextureMap:
    """Texture of every mesh cell: arrays over the ``mesh_zl`` grid covering the tile."""

    mesh_zl: int
    til_x_min: int
    til_y_min: int
    tex_x: np.ndarray
    """``(ny, nx)`` int32: ``til_x_text`` of the cell's texture."""
    tex_y: np.ndarray
    zl: np.ndarray
    """``(ny, nx)`` int16."""
    provider_idx: np.ndarray
    """``(ny, nx)`` int16, index into :attr:`providers`."""
    providers: tuple[str, ...]

    @property
    def shape(self) -> tuple[int, int]:
        return self.tex_x.shape  # type: ignore[return-value]

    def cell_index(self, til_x: np.ndarray, til_y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``(row, col)`` of mesh cells ``(til_x, til_y)``; raises when outside the grid."""
        col = (til_x - self.til_x_min) // 16
        row = (til_y - self.til_y_min) // 16
        ny, nx = self.shape
        bad = (col < 0) | (col >= nx) | (row < 0) | (row >= ny)
        if bad.any():
            k = int(np.argmax(bad))
            raise OsxpError(
                "DSF_MESH_OUTSIDE_TILE",
                context={"til_x": int(til_x[k]), "til_y": int(til_y[k]), "mesh_zl": self.mesh_zl},
                message=f"Mesh cell ({int(til_x[k])}, {int(til_y[k])}) at ZL{self.mesh_zl} is "
                "outside the tile (mesh outside tile).",
                remedy="The mesh must be clamped to the 1x1 degree tile; rebuild it.",
            )
        return row, col

    def lookup(
        self, til_x: np.ndarray, til_y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``(tex_x, tex_y, zl, provider_idx)`` arrays for mesh cells ``(til_x, til_y)``."""
        row, col = self.cell_index(til_x, til_y)
        return (
            self.tex_x[row, col],
            self.tex_y[row, col],
            self.zl[row, col],
            self.provider_idx[row, col],
        )

    def texture(self, tex_x: int, tex_y: int, zl: int, provider_idx: int) -> TextureId:
        return TextureId(int(tex_x), int(tex_y), int(zl), self.providers[int(provider_idx)])


def _zone_image(tile: TileRef, params: DsfParams) -> tuple[np.ndarray, list[tuple[int, str]]]:
    """Priority image (``:113``, ``:192-206``) and the ``(zl, provider)`` of each value."""
    zones = list(params.zone_list)
    if len(zones) > _MAX_ZONES:
        raise ValueError(f"zone_list holds {len(zones)} zones, at most {_MAX_ZONES} are supported")
    lat, lon = tile.lat, tile.lon
    base: tuple[list[float], int, str] = (
        [lat, lon, lat, lon + 1, lat + 1, lon + 1, lat + 1, lon, lat, lon],
        params.default_zl,
        params.default_website,
    )
    im = Image.new("L", (IMAGE_SIDE, IMAGE_SIDE), "black")
    draw = ImageDraw.Draw(im)
    values: list[tuple[int, str]] = [(0, "")]  # value 0 is never read (base covers the tile)
    for i, (coords, zl, provider) in enumerate([base, *zones[::-1]], start=1):
        pol = [
            (round((x - lon) * 4095), round((lat + 1 - y) * 4095))
            for (x, y) in zip(coords[1::2], coords[::2], strict=False)
        ]
        draw.polygon(pol, fill=i)
        values.append((int(zl), str(provider)))
    return np.asarray(im), values


def _airport_array(
    tile: TileRef, params: DsfParams, airports: Sequence[AirportCover]
) -> np.ndarray:
    """Boolean 4096² image of the airport rectangles snapped to ``cover_zl`` (``:114-173``)."""
    arr = np.zeros((IMAGE_SIDE, IMAGE_SIDE), dtype=np.bool_)
    lat, lon = tile.lat, tile.lon
    ext = 1000 * params.cover_extent
    for apt in airports:
        if params.cover_airports_with_highres == "ICAO" and not apt.is_icao:
            continue
        xmin = apt.xmin - ext * m_to_lon(lat)
        xmax = apt.xmax + ext * m_to_lon(lat)
        ymax = apt.ymax + ext * M_TO_LAT
        ymin = apt.ymin - ext * M_TO_LAT
        t_left = texture_at(ymax + lat, xmin + lon, params.cover_zl, "")
        ymax, xmin = tile_to_wgs84(t_left.til_x, t_left.til_y, params.cover_zl)
        ymax -= lat
        xmin -= lon
        t_right = texture_at(ymin + lat, xmax + lon, params.cover_zl, "")
        ymin, xmax = tile_to_wgs84(t_right.til_x + 16, t_right.til_y + 16, params.cover_zl)
        ymin -= lat
        xmax -= lon
        xmin, xmax = max(0, xmin), min(1, xmax)
        ymin, ymax = max(0, ymin), min(1, ymax)
        colmin, colmax = round(xmin * 4095), round(xmax * 4095)
        rowmax, rowmin = round((1 - ymin) * 4095), round((1 - ymax) * 4095)
        arr[rowmin : rowmax + 1, colmin : colmax + 1] = True
    return arr


def _say_the_zones_that_raise_nothing(
    tile: TileRef, params: DsfParams, values: Sequence[tuple[int, str]], claimed: set[int]
) -> None:
    """Name the zones that no mesh cell took, so they are not silently paid for.

    A zone's level is read at the centre of each mesh cell, which is Ortho4XP's rule and about
    850 m at ``mesh_zl`` 19. A zone thinner than that holds no centre and raises nothing, while
    the page draws the shape the user drew and the estimate charges for the textures it covers: a
    user set a 300 m band to a sharper level, built, and saw no change and no word (2026-09-23).

    The rule is kept, because it is the one the whole terrain assignment is a port of. What was
    missing was saying so.
    """
    zones = list(params.zone_list)
    for value in range(2, len(values)):  # 0 unused, 1 is the tile itself, then the zones
        if value in claimed:
            continue
        # ``_zone_image`` paints them reversed, so that the first of the list ends on top
        position = len(zones) + 1 - value
        zl, _provider = values[value]
        log.warning(
            "%s: the zone %d of the list (level %d) is finer than a mesh cell, about %d m here, "
            "so it raises nothing; draw it larger or leave it, but it changes nothing as it is",
            tile.name,
            position + 1,
            zl,
            _mesh_cell_metres(tile, params.mesh_zl),
        )


def _mesh_cell_metres(tile: TileRef, mesh_zl: int) -> int:
    """The side of one mesh cell here, in metres: 16 tiles of ``mesh_zl`` at this latitude."""
    around = 2 * pi * EARTH_RADIUS * cos(pi * (tile.lat + 0.5) / 180.0)
    return round(16 * around / 2**mesh_zl)


def texture_map(
    tile: TileRef,
    params: DsfParams,
    *,
    airports: Sequence[AirportCover] = (),
    existing_textures: Iterable[TextureId] = (),
) -> TextureMap:
    """The texture of every mesh cell of the tile (``zone_list_to_ortho_dico``).

    ``airports`` is consulted only when ``cover_airports_with_highres`` is ``True`` or
    ``ICAO``; ``existing_textures`` only when it is ``Existing``.
    """
    lat, lon, mesh_zl = tile.lat, tile.lon, params.mesh_zl
    zone_im, values = _zone_image(tile, params)
    upgrade = params.cover_airports_with_highres in ("True", "ICAO")
    apt_arr = _airport_array(tile, params, airports) if upgrade else None
    first = texture_at(lat + 1, lon, mesh_zl, "")
    last = texture_at(lat, lon + 1, mesh_zl, "")
    xs = range(first.til_x, last.til_x + 1, 16)
    ys = range(first.til_y, last.til_y + 1, 16)
    ny, nx = len(ys), len(xs)
    tex_x = np.zeros((ny, nx), dtype=np.int32)
    tex_y = np.zeros((ny, nx), dtype=np.int32)
    zl_arr = np.zeros((ny, nx), dtype=np.int16)
    prov_arr = np.zeros((ny, nx), dtype=np.int16)
    providers: dict[str, int] = {}
    claimed: set[int] = set()
    for col, til_x in enumerate(xs):
        for row, til_y in enumerate(ys):
            latp, lonp = tile_to_wgs84(til_x + 8, til_y + 8, mesh_zl)
            lonp = max(min(lonp, lon + 1), lon)
            latp = max(min(latp, lat + 1), lat)
            x = round((lonp - lon) * 4095)
            y = round((lat + 1 - latp) * 4095)
            chosen = int(zone_im[y, x])
            claimed.add(chosen)
            zl, provider = values[chosen]
            if apt_arr is not None and apt_arr[y, x]:
                zl = max(zl, params.cover_zl)
            factor = 2 ** (mesh_zl - zl)
            tex_x[row, col] = 16 * (int(til_x / factor) // 16)
            tex_y[row, col] = 16 * (int(til_y / factor) // 16)
            zl_arr[row, col] = zl
            prov_arr[row, col] = providers.setdefault(provider, len(providers))
    _say_the_zones_that_raise_nothing(tile, params, values, claimed)
    if params.cover_airports_with_highres == "Existing":
        for t in existing_textures:  # ``:231-257``: claims cells unless a higher zl is there
            if t.zl > mesh_zl:
                raise ValueError(f"existing texture {t} is above mesh_zl {mesh_zl}")
            factor = 2 ** (mesh_zl - t.zl)
            c0 = max(0, (t.til_x * factor - first.til_x) // 16)
            c1 = min(nx, ((t.til_x + 16) * factor - first.til_x + 15) // 16)
            r0 = max(0, (t.til_y * factor - first.til_y) // 16)
            r1 = min(ny, ((t.til_y + 16) * factor - first.til_y + 15) // 16)
            if c0 >= c1 or r0 >= r1:
                continue
            claim = zl_arr[r0:r1, c0:c1] <= t.zl
            tex_x[r0:r1, c0:c1][claim] = t.til_x
            tex_y[r0:r1, c0:c1][claim] = t.til_y
            zl_arr[r0:r1, c0:c1][claim] = t.zl
            prov_arr[r0:r1, c0:c1][claim] = providers.setdefault(t.provider, len(providers))
    return TextureMap(
        mesh_zl=mesh_zl,
        til_x_min=first.til_x,
        til_y_min=first.til_y,
        tex_x=tex_x,
        tex_y=tex_y,
        zl=zl_arr,
        provider_idx=prov_arr,
        providers=tuple(providers),
    )
