"""What one relief source changes against another, on a square already on disk.

The elevation files a build keeps live side by side (``<data>/elevation/<block>/``), one per
source: ``S23W044_ANADEM.tif`` beside ``S23W044_COP30.tif``. This reads two of them and says how
far apart they are, which is how a source is judged before a flight.

Usage (from the repository root)::

    uv run python tools/relief/compare.py S23W044
    uv run python tools/relief/compare.py S10W060 --against COP30 --source ANADEM

Over the Amazon (``S10W060``, 13.7 million points) Copernicus stands 12.7 m above ANADEM on
average, 16.3 m at the median, higher on 85 % of the points: that is the forest canopy, which
ANADEM takes out and Copernicus keeps.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from orthostudio.dem.raster import read_elevation_from_file
from orthostudio.dem.sources import default_elevation_dir


def cell_of(name: str) -> tuple[int, int]:
    """``S23W044`` -> ``(-23, -44)``."""
    lat = int(name[1:3]) * (1 if name[0].upper() == "N" else -1)
    lon = int(name[4:7]) * (1 if name[3].upper() == "E" else -1)
    return lat, lon


def find(root: Path, name: str, source: str) -> Path | None:
    """The file of one source for one square, wherever its block folder is."""
    return next(iter(root.glob(f"*/{name}_{source}.*")), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cell", help="the square, as the files name it (S23W044)")
    parser.add_argument("--source", default="ANADEM", help="the relief to judge")
    parser.add_argument("--against", default="COP30", help="the relief to judge it against")
    parser.add_argument("--elevation", type=Path, default=None, help="where the files are")
    args = parser.parse_args(argv)

    root = args.elevation or default_elevation_dir()
    lat, lon = cell_of(args.cell)
    mine, other = find(root, args.cell, args.source), find(root, args.cell, args.against)
    if mine is None or other is None:
        print(f"under {root}: {args.source} = {mine}, {args.against} = {other}")
        print("build that square with each relief first; the files stay on disk.")
        return 2

    a = np.asarray(read_elevation_from_file(mine, lat, lon).alt_dem, np.float32)
    read = read_elevation_from_file(other, lat, lon)
    b = np.asarray(read.alt_dem, np.float32)
    # The two grids rarely share a step: the coarser one is read at the finer one's points.
    rows, cols = a.shape
    r = np.rint(np.linspace(0, b.shape[0] - 1, rows)).astype(int)
    c = np.rint(np.linspace(0, b.shape[1] - 1, cols)).astype(int)
    ref = b[np.ix_(r, c)]
    ok = (a != read.nodata) & (ref != read.nodata)
    if not ok.any():
        print("nothing in common: one of the files is empty over this square")
        return 1
    d = (ref - a)[ok]
    print(f"{args.cell}: {ok.sum():,} points compared".replace(",", " "))
    print(
        f"  {args.against} above {args.source} by {d.mean():+.2f} m on average "
        f"(median {np.median(d):+.2f})"
    )
    print(
        f"  higher on {100 * np.mean(d > 0.5):.0f} % of the points, "
        f"lower on {100 * np.mean(d < -0.5):.0f} %"
    )
    print(
        f"  mean height: {args.against} {ref[ok].mean():.1f} m, {args.source} {a[ok].mean():.1f} m"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
