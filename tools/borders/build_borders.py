"""Country borders for the Plan map, from Natural Earth (public domain).

The page draws ``src/orthostudio/ui/vendor/borders/borders.json``; this tool writes it from Natural
Earth's 1:10m *Admin 0 - Boundary Lines* (land) GeoJSON. It does not download anything: where
the input comes from, and its checksum, are in the README beside the output.

What happens to the lines, and why:

- the international boundaries are kept, and as a second class the disputed, indefinite and
  line-of-control ones (the page draws them fainter); lease and overlay limits are dropped, they
  are not borders anyone flies by;
- pieces that meet end to end are joined: Natural Earth cuts its borders into ~8,000 pieces, and
  each one costs a JSON array;
- points are rounded to 0.0001° and simplified with Douglas-Peucker at 0.0005° (about 50 m),
  well below the data's own accuracy of a few hundred metres;
- each point is written as integers, the difference from the previous point.

Format ``osxp-borders-1``::

    {"format": "osxp-borders-1", "scale": 10000, "classes": ["international", "disputed"],
     "lines": [[class, lon0, lat0, dlon1, dlat1, ...], ...]}

with ``lon = sum(lon_i) / scale``, ``lat = sum(lat_i) / scale``. ``geo.js`` ``decodeBorders``
reads it.

Usage (from the repository root)::

    python tools/borders/build_borders.py ne_10m_admin_0_boundary_lines_land.geojson
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

FORMAT = "osxp-borders-1"
SCALE = 10_000
"""Integer units per degree: 0.0001°, about 11 m."""
TOLERANCE = 5
"""Douglas-Peucker tolerance in ``SCALE`` units: 0.0005°."""
CLASSES = ("international", "disputed")
CLASS_OF_FEATURE = {
    "International boundary (verify)": 0,
    "Disputed (please verify)": 1,
    "Indefinite (please verify)": 1,
    "Line of control (please verify)": 1,
    "Indeterminant frontier": 1,
    "Unrecognized": 1,
}
"""Natural Earth ``FEATURECLA`` to class index; any other value (lease and overlay limits) is
dropped."""
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "src/orthostudio/ui/vendor/borders/borders.json"

Point = tuple[int, int]


def pieces(doc: dict) -> dict[int, list[list[Point]]]:
    """The lines of each class, rounded to ``SCALE``, repeated points removed."""
    if doc.get("type") != "FeatureCollection":
        raise ValueError("not a GeoJSON FeatureCollection")
    out: dict[int, list[list[Point]]] = defaultdict(list)
    for feature in doc["features"]:
        cls = CLASS_OF_FEATURE.get(feature["properties"].get("FEATURECLA"))
        if cls is None:
            continue
        geometry = feature["geometry"]
        if geometry["type"] == "LineString":
            parts = [geometry["coordinates"]]
        elif geometry["type"] == "MultiLineString":
            parts = geometry["coordinates"]
        else:
            raise ValueError(f"unexpected geometry {geometry['type']}")
        for part in parts:
            line: list[Point] = []
            for lon, lat, *_ in part:
                point = (round(lon * SCALE), round(lat * SCALE))
                if not line or point != line[-1]:
                    line.append(point)
            if len(line) >= 2:
                out[cls].append(line)
    return out


def join(lines: list[list[Point]]) -> list[list[Point]]:
    """Chains of the pieces that meet end to end, where exactly two piece ends meet.

    A point where three borders meet (a tripoint) stays a chain end: joining there would be as
    valid to draw, but which two to join would be arbitrary.
    """
    ends: dict[Point, list[int]] = defaultdict(list)
    for i, line in enumerate(lines):
        ends[line[0]].append(i)
        ends[line[-1]].append(i)
    used = [False] * len(lines)

    def partner(point: Point, current: int) -> int | None:
        refs = ends[point]
        if len(refs) != 2:
            return None
        other = refs[1] if refs[0] == current else refs[0]
        return None if other == current or used[other] else other

    chains: list[list[Point]] = []
    for start in range(len(lines)):
        if used[start]:
            continue
        used[start] = True
        chain = list(lines[start])
        last = first = start
        while (nxt := partner(chain[-1], last)) is not None:
            used[nxt] = True
            seg = lines[nxt] if lines[nxt][0] == chain[-1] else lines[nxt][::-1]
            chain.extend(seg[1:])
            last = nxt
        while (prev := partner(chain[0], first)) is not None:
            used[prev] = True
            seg = lines[prev] if lines[prev][-1] == chain[0] else lines[prev][::-1]
            chain[:0] = seg[:-1]
            first = prev
        chains.append(chain)
    return chains


def simplify(line: list[Point], tolerance: float = TOLERANCE) -> list[Point]:
    """Douglas-Peucker (iterative); the two ends always stay."""
    n = len(line)
    if n < 3:
        return list(line)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    limit = tolerance * tolerance
    while stack:
        a, b = stack.pop()
        ax, ay = line[a]
        bx, by = line[b]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        worst, index = -1.0, -1
        for i in range(a + 1, b):
            px, py = line[i]
            if length2 == 0:
                qx, qy = ax, ay
            else:
                s = min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                qx, qy = ax + s * dx, ay + s * dy
            d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 > worst:
                worst, index = d2, i
        if worst > limit:
            keep[index] = True
            stack.extend(((a, index), (index, b)))
    return [p for p, k in zip(line, keep, strict=True) if k]


def encode(doc: dict) -> dict:
    lines: list[list[int]] = []
    for cls, found in sorted(pieces(doc).items()):
        for chain in join(found):
            points = simplify(chain)
            flat = [cls, points[0][0], points[0][1]]
            for (x0, y0), (x1, y1) in pairwise(points):
                flat += (x1 - x0, y1 - y0)
            lines.append(flat)
    return {"format": FORMAT, "scale": SCALE, "classes": list(CLASSES), "lines": lines}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("geojson", type=Path, help="ne_10m_admin_0_boundary_lines_land.geojson")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    doc = json.loads(args.geojson.read_text(encoding="utf-8"))
    result = encode(doc)
    text = json.dumps(result, separators=(",", ":")) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    points = sum((len(line) - 1) // 2 for line in result["lines"])
    print(f"{len(result['lines'])} lines, {points} points, {len(text)} bytes -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
