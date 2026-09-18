"""Reader of the ``Ortho4XP_<tile>.cfg`` written by Ortho4XP (``O4_Config_Utils.py:523-604``).

Spec: ``docs/specs/tile-files.md`` section 3.2. Flat ``key=value`` lines; the declared
types of the 44 tile parameters are ported from ``cfg_vars`` (``O4_Config_Utils.py:16-352``).
Values are converted with the declared type or, for ``bool``/``list`` parameters, parsed with
``ast.literal_eval``; nothing is ever ``exec``'d or ``eval``'d.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, NamedTuple

from orthostudio.errors import OsxpError
from orthostudio.tilefiles.paths import short_latlon, tile_cfg_path

__all__ = [
    "TILE_PARAMETERS",
    "TileParameter",
    "parse_tile_cfg",
    "tile_cfg_text",
    "tile_cfg_values",
    "tile_config",
    "tile_defaults",
]


class TileParameter(NamedTuple):
    """Declared type and Ortho4XP default of one tile parameter."""

    type: type
    default: Any


# Order and content of list_tile_vars (O4_Config_Utils.py:373-430); types and defaults from
# cfg_vars. Quirks kept: masks_width is declared list with an int default;
# cover_airports_with_highres is a str among "False", "True", "ICAO", "Existing".
TILE_PARAMETERS: dict[str, TileParameter] = {
    # vector
    "apt_smoothing_pix": TileParameter(int, 8),
    "road_level": TileParameter(int, 1),
    "road_banking_limit": TileParameter(float, 0.5),
    "lane_width": TileParameter(float, 4),
    "max_levelled_segs": TileParameter(int, 200000),
    "water_simplification": TileParameter(float, 0),
    "min_area": TileParameter(float, 0.001),
    "max_area": TileParameter(float, 200),
    "clean_bad_geometries": TileParameter(bool, True),
    "mesh_zl": TileParameter(int, 19),
    # mesh
    "curvature_tol": TileParameter(float, 2),
    "apt_curv_tol": TileParameter(float, 0.5),
    "apt_curv_ext": TileParameter(float, 0.5),
    "coast_curv_tol": TileParameter(float, 1),
    "coast_curv_ext": TileParameter(float, 0.5),
    "limit_tris": TileParameter(float, 3),
    "min_angle": TileParameter(float, 10),
    "sea_smoothing_mode": TileParameter(str, "zero"),
    "water_smoothing": TileParameter(int, 10),
    "iterate": TileParameter(int, 0),
    # masks
    "mask_zl": TileParameter(int, 14),
    "masks_width": TileParameter(list, 100),
    "masking_mode": TileParameter(str, "sand"),
    "use_masks_for_inland": TileParameter(bool, False),
    "imprint_masks_to_dds": TileParameter(bool, True),
    "distance_masks_too": TileParameter(bool, False),
    "masks_use_DEM_too": TileParameter(bool, False),
    "masks_custom_extent": TileParameter(str, ""),
    # dsf / imagery
    "cover_airports_with_highres": TileParameter(str, "False"),
    "cover_extent": TileParameter(float, 1),
    "cover_zl": TileParameter(int, 18),
    "water_tech": TileParameter(str, "XP11 + bathy"),
    "ratio_bathy": TileParameter(float, 1.0),
    "ratio_water": TileParameter(float, 0.25),
    "overlay_lod": TileParameter(float, 25000),
    "sea_texture_blur": TileParameter(float, 0),
    "normal_map_strength": TileParameter(float, 1),
    "terrain_casts_shadows": TileParameter(bool, True),
    "use_decal_on_terrain": TileParameter(bool, False),
    # other
    "custom_dem": TileParameter(str, ""),
    "fill_nodata": TileParameter(bool, True),
    # tile
    "default_website": TileParameter(str, ""),
    "default_zl": TileParameter(int, 16),
    "zone_list": TileParameter(list, []),
}

OSXP_PARAMETERS: dict[str, TileParameter] = {
    "decal_on_sea": TileParameter(bool, False),
    "photo_brightness": TileParameter(float, 0.0),
    "photo_contrast": TileParameter(float, 0.0),
    "photo_saturation": TileParameter(float, 0.0),
    "photo_zones": TileParameter(list, []),
}
"""Settings of OrthoStudio XP's own, read and written like a tile variable but absent from
Ortho4XP: the decals on the sea (2026-09-17) and the colours of the photo (2026-09-18). They are
not part of the 44, so ``tile_cfg_text`` does not write them; ``--set`` and the page's overrides
accept them beside the 44."""

_LITERAL_TYPES = (bool, list)
_ZONE_APPEND = "zone_list.append("


def tile_defaults() -> dict[str, Any]:
    """The Ortho4XP defaults of the tile parameters (fresh copies of the mutable ones)."""
    return {
        k: (list(p.default) if isinstance(p.default, list) else p.default)
        for k, p in TILE_PARAMETERS.items()
    }


def _line_error(path: Path | None, lineno: int, reason: str) -> OsxpError:
    return OsxpError(
        "CFG_LINE_INVALID",
        context={"path": str(path) if path else "<text>", "line": lineno, "reason": reason},
    )


def _value_error(name: str, value: str, param: TileParameter) -> OsxpError:
    return OsxpError(
        "CFG_VALUE_INVALID",
        context={"name": name, "value": value, "type": param.type.__name__, "range": "any"},
    )


def _strip_quotes(value: str) -> str:
    # compatibility with config files from version <= 1.20 (O4_Config_Utils.py:549-552)
    if value and value[0] in "\"'":
        value = value[1:]
    if value and value[-1] in "\"'":
        value = value[:-1]
    return value


def _literal(text: str) -> Any:
    return ast.literal_eval(text.strip())


def _convert(name: str, raw: str, param: TileParameter) -> Any:
    value = _strip_quotes(raw)
    if param.type in _LITERAL_TYPES:
        try:
            return _literal(value)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
            raise _value_error(name, raw, param) from exc
    if param.type is str:
        return value
    try:
        return param.type(value)
    except ValueError as exc:
        raise _value_error(name, raw, param) from exc


def parse_tile_cfg(text: str, *, path: Path | None = None, strict: bool = False) -> dict[str, Any]:
    """Parse the text of an Ortho4XP tile configuration into typed values (spec 3.2).

    Unknown keys are kept as raw strings unless ``strict``; ``zone_list.append([...])`` lines
    (<= 1.20) are honoured with ``ast.literal_eval``.
    """
    out: dict[str, Any] = {}
    for lineno, line in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(_ZONE_APPEND):
            if not line.endswith(")"):
                raise _line_error(path, lineno, "unterminated zone_list.append(...)")
            try:
                zone = _literal(line[len(_ZONE_APPEND) : -1])
            except (ValueError, SyntaxError, TypeError) as exc:
                raise _line_error(path, lineno, "zone is not a Python literal") from exc
            out.setdefault("zone_list", []).append(zone)
            continue
        name, sep, raw = line.partition("=")
        name = name.strip()
        if not sep or not name:
            raise _line_error(path, lineno, "expected key=value")
        raw = raw.strip()
        param = TILE_PARAMETERS.get(name) or OSXP_PARAMETERS.get(name)
        if param is None:
            if strict:
                raise _line_error(path, lineno, f"unknown parameter {name}")
            out[name] = _strip_quotes(raw)
            continue
        out[name] = _convert(name, raw, param)
    return out


def tile_config(
    build_dir: Path,
    *,
    lat: int | None = None,
    lon: int | None = None,
    strict: bool = False,
    with_defaults: bool = False,
) -> dict[str, Any]:
    """Typed parameters of the tile configuration found in an Ortho4XP build directory.

    Looks for ``Ortho4XP_<tile>.cfg`` (``lat``/``lon`` given, else the single ``Ortho4XP_*.cfg``
    present), then ``Ortho4XP.cfg`` as Ortho4XP does (O4_Config_Utils.py:525-537); raises
    ``CFG_TILE_FILE_MISSING`` when none exists.
    """
    build_dir = Path(build_dir)
    candidates: list[Path] = []
    if lat is not None and lon is not None:
        candidates.append(tile_cfg_path(build_dir, lat, lon))
    else:
        candidates.extend(sorted(build_dir.glob("Ortho4XP_[+-]*[+-]*.cfg")))
    candidates.append(build_dir / "Ortho4XP.cfg")
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        tile = short_latlon(lat, lon) if lat is not None and lon is not None else build_dir.name
        raise OsxpError(
            "CFG_TILE_FILE_MISSING",
            context={"tile": tile, "path": str(build_dir)},
            message=f"No Ortho4XP_<tile>.cfg found in {build_dir} for tile {tile}.",
            remedy="Point at a directory built by Ortho4XP (it writes the tile cfg).",
        )
    values = parse_tile_cfg(path.read_text(encoding="utf-8"), path=path, strict=strict)
    if with_defaults:
        merged = tile_defaults()
        merged.update(values)
        return merged
    return values


def tile_cfg_values(
    *, provider: str, zl: int, overrides: dict[str, object] | None = None
) -> dict[str, Any]:
    """All 44 tile variables: Ortho4XP's defaults, then ``provider`` / ``zl``, then ``overrides``.

    Unknown names in ``overrides`` raise ``KeyError`` (a typo would be silently ignored by
    Ortho4XP's ``read_from_config``).
    """
    values = tile_defaults()
    values["default_website"] = provider
    values["default_zl"] = int(zl)
    for name, value in (overrides or {}).items():
        if name not in TILE_PARAMETERS:
            raise KeyError(f"{name!r} is not a tile variable")
        values[name] = value
    return values


def tile_cfg_text(values: dict[str, Any]) -> str:
    """The text of a tile's settings file, as Ortho4XP's ``Tile.write_to_config`` writes its
    ``Ortho4XP_<tile>.cfg`` (``O4_Config_Utils.py:585-604``).

    One ``name=str(value)`` line per ``list_tile_vars`` entry, in Ortho4XP's order; the other keys
    of ``values`` (the overlay settings) are not written.
    """
    missing = [name for name in TILE_PARAMETERS if name not in values]
    if missing:
        raise KeyError(f"tile cfg values are incomplete: {missing}")
    return "".join(f"{name}={values[name]}\n" for name in TILE_PARAMETERS)
