"""Strip the mesh from a DSFTool text file and apply the exclusions (spec section 3).

The filter is a single pass over the bytes of the ``dsf2text`` output. Blocks
(``BEGIN_POLYGON`` ... ``END_POLYGON``, ``BEGIN_SEGMENT`` ... ``END_SEGMENT``,
``BEGIN_PRIMITIVE`` ... ``END_PRIMITIVE``), each one DSF command, are located with
``bytes.find`` and copied or skipped as a whole, so the 2.8 million lines of a Global Scenery
tile cost a few hundred thousand C-level searches, not a Python test per line. Kept lines are
byte-identical to their source lines.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from orthostudio.errors import OsxpError
from orthostudio.overlays.exclusions import (
    OverlayExclusions,
    networks_all_excluded,
    resolve_network_exclusions,
    resolve_polygon_exclusions,
)

__all__ = ["OVERLAY_PROPERTY", "FilterStats", "filter_dsf_bytes", "filter_dsf_text"]

OVERLAY_PROPERTY = b"PROPERTY sim/overlay 1\n"

_END_POLYGON = b"\nEND_POLYGON"
_END_SEGMENT = b"\nEND_SEGMENT"
_END_PRIMITIVE = b"\nEND_PRIMITIVE"


@dataclass(slots=True)
class FilterStats:
    """What the filter saw and what it kept."""

    polygon_defs: list[str] = field(default_factory=list)
    network_defs: list[str] = field(default_factory=list)
    object_defs: list[str] = field(default_factory=list)
    excluded_polygon_indices: tuple[int, ...] = ()
    properties: int = 0
    polygons_kept: int = 0
    polygons_dropped: int = 0
    segments_kept: int = 0
    segments_dropped: int = 0
    objects_kept: int = 0
    objects_dropped: int = 0
    patches_dropped: int = 0
    primitives_dropped: int = 0
    lines_in: int = 0
    bytes_in: int = 0
    bytes_out: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "polygon_defs": list(self.polygon_defs),
            "network_defs": list(self.network_defs),
            "object_defs": list(self.object_defs),
            "excluded_polygon_indices": list(self.excluded_polygon_indices),
            "properties": self.properties,
            "polygons_kept": self.polygons_kept,
            "polygons_dropped": self.polygons_dropped,
            "segments_kept": self.segments_kept,
            "segments_dropped": self.segments_dropped,
            "objects_kept": self.objects_kept,
            "objects_dropped": self.objects_dropped,
            "patches_dropped": self.patches_dropped,
            "primitives_dropped": self.primitives_dropped,
            "lines_in": self.lines_in,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
        }


def _corrupted(reason: str, line_no: int, source: str) -> OsxpError:
    return OsxpError(
        "DSF_SOURCE_CORRUPTED",
        context={"path": source, "line": line_no, "reason": reason},
        message=f"DSFTool text {source} is malformed at line {line_no}: {reason}.",
        remedy="Reinstall the Global Scenery tile in X-Plane; report the source DSF if it "
        "persists.",
    )


def _block_end(data: bytes, start: int, terminator: bytes) -> int:
    """Index just past the terminator line of the block starting at ``start``, or -1."""
    k = data.find(terminator, start)
    if k < 0:
        return -1
    nl = data.find(b"\n", k + 1)
    return len(data) if nl < 0 else nl + 1


def _def_name(line: bytes) -> str:
    parts = line.split(maxsplit=1)
    return parts[1].rstrip(b"\r\n").decode("utf-8", errors="replace") if len(parts) > 1 else ""


def _index_field(line: bytes, position: int, what: str, line_no: int, source: str) -> int:
    parts = line.split()
    try:
        return int(parts[position])
    except (IndexError, ValueError):
        raise _corrupted(
            f"{what} line without an integer at field {position}", line_no, source
        ) from None


def filter_dsf_bytes(
    data: bytes, exclusions: OverlayExclusions, *, source: str = "<bytes>"
) -> tuple[Iterator[bytes], FilterStats]:
    """Filter the DSFTool text ``data``; yields the output pieces, fills ``stats`` as it goes.

    ``stats`` is complete only once the iterator is exhausted.
    """
    if b"\r" in data:
        data = data.replace(b"\r\n", b"\n")
    stats = FilterStats(bytes_in=len(data))
    return _filter(data, exclusions, stats, source), stats


def _filter(
    data: bytes, exclusions: OverlayExclusions, stats: FilterStats, source: str
) -> Iterator[bytes]:
    yield OVERLAY_PROPERTY
    stats.bytes_out += len(OVERLAY_PROPERTY)
    net_all = networks_all_excluded(exclusions.ovl_exclude_net)
    net_types = resolve_network_exclusions(exclusions.ovl_exclude_net)
    excluded_pol: frozenset[int] | None = None
    keep_objects = exclusions.keep_objects
    n = len(data)
    pos = 0
    line_no = 0
    while pos < n:
        nl = data.find(b"\n", pos)
        end = n if nl < 0 else nl + 1
        line = data[pos:end]
        line_no += 1
        stats.lines_in += 1
        if line.startswith(b"BEGIN_PRIMITIVE"):
            # One DSF command: only PATCH_VERTEX lines until END_PRIMITIVE. A patch, on the
            # other hand, stays open until the next one begins, so polygons and segments can
            # sit between BEGIN_PATCH and END_PATCH: patches are never skipped as a block.
            stop = _block_end(data, pos, _END_PRIMITIVE)
            if stop < 0:
                raise _corrupted("BEGIN_PRIMITIVE without END_PRIMITIVE", line_no, source)
            stats.lines_in += data.count(b"\n", pos, stop) - 1
            stats.primitives_dropped += 1
            pos = stop
        elif line.startswith(b"BEGIN_POLYGON"):
            if excluded_pol is None:
                excluded_pol = resolve_polygon_exclusions(
                    stats.polygon_defs, exclusions.ovl_exclude_pol
                )
                stats.excluded_polygon_indices = tuple(sorted(excluded_pol))
            idx = _index_field(line, 1, "BEGIN_POLYGON", line_no, source)
            stop = _block_end(data, pos, _END_POLYGON)
            if stop < 0:
                raise _corrupted("BEGIN_POLYGON without END_POLYGON", line_no, source)
            stats.lines_in += data.count(b"\n", pos, stop) - 1
            if idx in excluded_pol:
                stats.polygons_dropped += 1
            else:
                stats.polygons_kept += 1
                stats.bytes_out += stop - pos
                yield data[pos:stop]
            pos = stop
        elif line.startswith(b"BEGIN_SEGMENT"):
            road_type = _index_field(line, 2, "BEGIN_SEGMENT", line_no, source)
            stop = _block_end(data, pos, _END_SEGMENT)
            if stop < 0:
                raise _corrupted("BEGIN_SEGMENT without END_SEGMENT", line_no, source)
            stats.lines_in += data.count(b"\n", pos, stop) - 1
            if net_all or road_type in net_types:
                stats.segments_dropped += 1
            else:
                stats.segments_kept += 1
                stats.bytes_out += stop - pos
                yield data[pos:stop]
            pos = stop
        else:
            keep = False
            if line.startswith(b"PROPERTY"):
                if not line.startswith(b"PROPERTY sim/overlay"):
                    keep = True
                    stats.properties += 1
            elif line.startswith(b"POLYGON_DEF"):
                stats.polygon_defs.append(_def_name(line))
                keep = True
            elif line.startswith(b"NETWORK_DEF"):
                stats.network_defs.append(_def_name(line))
                keep = True
            elif line.startswith(b"OBJECT_DEF"):
                stats.object_defs.append(_def_name(line))
                keep = keep_objects
            elif line.startswith(b"BEGIN_PATCH"):
                stats.patches_dropped += 1
            elif line.startswith(b"OBJECT"):
                if keep_objects:
                    stats.objects_kept += 1
                    keep = True
                else:
                    stats.objects_dropped += 1
            if keep:
                if not line.endswith(b"\n"):
                    line += b"\n"
                stats.bytes_out += len(line)
                yield line
            pos = end
    if excluded_pol is None:
        stats.excluded_polygon_indices = tuple(
            sorted(resolve_polygon_exclusions(stats.polygon_defs, exclusions.ovl_exclude_pol))
        )


def filter_dsf_text(src: Path, dst: Path, exclusions: OverlayExclusions) -> FilterStats:
    """Write the overlay text of ``src`` (a ``dsf2text`` output) at ``dst``; return the stats.

    ``dst`` is written through a sibling temporary name and renamed at the end.
    """
    src, dst = Path(src), Path(dst)
    data = src.read_bytes()
    pieces, stats = filter_dsf_bytes(data, exclusions, source=str(src))
    tmp = dst.with_name(dst.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            for piece in pieces:
                f.write(piece)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return stats
