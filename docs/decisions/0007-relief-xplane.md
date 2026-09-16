# 0007. X-Plane 12's own relief by default, and never a flat tile

Date: 2026-09-13. Status: accepted.

## Context
Flight test of `+46+006` (Geneva, built with the native stages): the Jura was flat. The
elevation stage reads viewfinderpanoramas, as Ortho4XP does. Its `dem1/` archives, which serve the
34 Alpine cells one by one, now answer 404 (`dem3/` still works). The tile's own cell then
degraded to 0 m exactly like a neighbour, and nothing stopped the build. Ortho4XP does the same.

Measured on the user's machine, X-Plane 12's Global Scenery holds the relief of every tile it
covers (18 197 DSFs), inside each DSF: an `elevation` raster of 1201 x 1201 int16 posts,
3 arc-seconds, sea at 0 m, no hole. That is the geometry of a 3" `.hgt`. It agrees with
AutoOrtho's and XPME's base packages within a few metres. Copernicus GLO-30 (1") is better on
steep ground: 21.6 m median difference on the steepest 12 % of `+46+006`. But it is a 38 GB
download for Europe, and wiring it was judged too long for now.

## Decision
- The native elevation stage takes X-Plane 12's relief when `custom_dem` is empty. OrthoStudio XP
  adds the source `XP12`, and the pipeline writes it into the params (`BuildSpec.relief = "xplane"`,
  `osxp build --relief xplane|view`, env `OSXP_RELIEF`). `view` keeps Ortho4XP's source for
  comparisons with the Ortho4XP references, and the test suite runs with it.
- The nine Global Scenery DSFs of the 3x3 block are inputs of `orthostudio.dem@1`, so their digests
  are in its key. An X-Plane update that changes a DSF rebuilds the relief.
- A tile whose own cell has no data is refused with `DEM_TILE_UNAVAILABLE`, whatever the
  source. This covers a missing file, a refused archive, and an unreadable file or DSF.
  Neighbours still degrade.
- Two adjacent OrthoStudio XP tiles must meet on one line. X-Plane's rasters do not always agree
  there: 186 of the 1201 posts of the `+46+006`/`+46+007` border differ, by up to 31 m. So a border
  post is the mean of the tiles that carry it: two along a side, up to four at a corner. Built from
  either side, the line is the same to the bit.

## Consequences
- The relief costs no download and no dead link. It matches the neighbouring default scenery
  wherever X-Plane's own rasters agree.
- It is 3" (about 90 m). Copernicus can be added later as a third `relief` value without
  touching the rest: same assembly, same inputs pattern.
- The Ortho4XP stages (`--vectors legacy`, the web page's default path) still read Ortho4XP's
  relief. The protection against a flat tile only covers the native elevation stage.
- `Data<tile>.alt` differs from Ortho4XP by design when `relief = xplane`. The byte-identity
  targets of ADR 0006 hold with `relief = view`.
