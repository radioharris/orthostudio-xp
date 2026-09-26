# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the vector stage publishes about the airports of a tile.

Spec: ``docs/specs/airports-integration.md``.

``airports.npz`` + ``airports.wkb.json`` is OrthoStudio XP's record: WKB geometries and a JSON
sidecar of the scalars. Nothing evaluates, nothing is pickled, and a file written by another
version is rejected by its ``format`` string rather than half-read. The high-resolution airport
cover of the DSF reads it (``orthostudio.dsf.zones.airport_covers``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.model import AirportSet
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

__all__ = [
    "AIRPORTS_JSON",
    "AIRPORTS_NPZ",
    "read_airports",
    "write_airports",
]

AIRPORTS_NPZ = "airports.npz"
"""The geometries, as WKB blobs (one entry per airport and per field)."""
AIRPORTS_JSON = "airports.wkb.json"
"""The scalars and the field index of :data:`AIRPORTS_NPZ`."""
RECORD_FORMAT = "osxp-airports-1"

_GEOMETRY_FIELDS = ("boundary", "runway", "taxiway", "apron", "hangar")
"""The five geometries of one airport, in the order the sidecar lists them."""


def write_airports(
    directory: Path,
    airports: AirportSet,
    tile: TileRef,
    *,
    npz_path: Path | None = None,
    json_path: Path | None = None,
) -> None:
    """Write :data:`AIRPORTS_NPZ` and :data:`AIRPORTS_JSON` into ``directory``.

    Shapely's WKB carries the geometry exactly (it is the same double precision the ``.poly``
    is written from), and the sidecar carries what WKB cannot: the key, how the key was
    obtained, the name, the representative node, the way ids and the runway axes.

    ``npz_path`` / ``json_path`` replace the two file names, for a caller that stages them
    under ``.part`` names and renames the set at the end (review 6).
    """
    directory = Path(directory)
    npz_path = npz_path if npz_path is not None else directory / AIRPORTS_NPZ
    json_path = json_path if json_path is not None else directory / AIRPORTS_JSON
    blobs: dict[str, np.ndarray] = {}
    records: list[dict[str, Any]] = []
    for index, airport in enumerate(airports):
        built = airport.areas
        geometries = {
            "boundary": airport.boundary,
            "runway": built.runway,
            "taxiway": built.taxiway,
            "apron": built.apron,
            "hangar": built.hangar,
        }
        for name in _GEOMETRY_FIELDS:
            blobs[f"{index}.{name}"] = _wkb(geometries[name])
        for kind, parts in (("area", built.runway_as_area), ("line", built.runway_as_line)):
            for position, part in enumerate(parts):
                blobs[f"{index}.runway_{kind}.{position}"] = _wkb(part.polygon)
        records.append(
            {
                "key": list(airport.key) if isinstance(airport.key, tuple) else airport.key,
                "key_is_point": isinstance(airport.key, tuple),
                "key_type": airport.key_type,
                "name": airport.name,
                "repr_node": [float(v) for v in airport.repr_node],
                "smoothing_pix": airport.smoothing_pix,
                "ways": {name: list(ids) for name, ids in airport.ways.items()},
                "runway_as_rel": list(airport.runway_rels),
                "runways": [
                    {
                        "kind": kind,
                        "start": [float(v) for v in part.start],
                        "end": [float(v) for v in part.end],
                        "width": float(part.width),
                    }
                    for kind, parts in (
                        ("area", built.runway_as_area),
                        ("line", built.runway_as_line),
                    )
                    for part in parts
                ],
            }
        )
    with npz_path.open("wb") as handle:
        # ``**blobs`` confuses mypy with the ``compress`` keyword of the stub; the mapping is
        # what numpy documents and what ``np.load`` reads back.
        np.savez_compressed(handle, **blobs)  # type: ignore[arg-type]
    json_path.write_text(
        json.dumps(
            {"format": RECORD_FORMAT, "tile": tile.name, "airports": records},
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _wkb(geometry: BaseGeometry | None) -> np.ndarray:
    """One geometry as a byte array; an absent one is an empty blob, not a crash."""
    if geometry is None:
        return np.frombuffer(b"", dtype=np.uint8)
    return np.frombuffer(shapely.to_wkb(geometry), dtype=np.uint8)


def read_airports(directory: Path) -> list[dict[str, Any]]:
    """Read the record back: one dictionary per airport, geometries included.

    The consumer of this is OrthoStudio XP itself (a later stage, a report, a test). A file whose
    ``format`` is not the one this module writes is refused rather than guessed at.
    """
    directory = Path(directory)
    sidecar = json.loads((directory / AIRPORTS_JSON).read_text(encoding="utf-8"))
    if sidecar.get("format") != RECORD_FORMAT:
        raise OsxpError(
            "SYS_INTERNAL_ERROR",
            context={
                "type": "ValueError",
                "detail": f"{directory / AIRPORTS_JSON}: format {sidecar.get('format')!r}",
            },
            remedy=f"Rebuild the vector stage of this tile; the record format is {RECORD_FORMAT}.",
        )
    with np.load(directory / AIRPORTS_NPZ) as blobs:
        out = []
        for index, record in enumerate(sidecar["airports"]):
            entry = dict(record)
            entry["key"] = tuple(record["key"]) if record["key_is_point"] else record["key"]
            for name in _GEOMETRY_FIELDS:
                entry[name] = _geometry(blobs, f"{index}.{name}")
            for position, runway in enumerate(entry["runways"]):
                kind = runway["kind"]
                same = [r for r in entry["runways"][: position + 1] if r["kind"] == kind]
                runway["polygon"] = _geometry(blobs, f"{index}.runway_{kind}.{len(same) - 1}")
            out.append(entry)
    return out


def _geometry(blobs: Mapping[str, Any], name: str) -> BaseGeometry | None:
    raw = bytes(np.asarray(blobs[name], dtype=np.uint8).tobytes())
    return shapely.from_wkb(raw) if raw else None
