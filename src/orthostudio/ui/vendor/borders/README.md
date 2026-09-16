# Country borders (Natural Earth, vendored)

Source: Natural Earth 1:10m Cultural Vectors, *Admin 0 - Boundary Lines* (land), version 5.1.2, as
GeoJSON in the official repository:
`https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_boundary_lines_land.geojson`
(2,284,669 bytes, sha256 `74d9c16229c095fde65943a9919e337682f044bcebccb120764f38edf3b70f4a`,
downloaded 2026-09-13).

Licence: public domain. Natural Earth puts all its data in the public domain
(`https://www.naturalearthdata.com/about/terms-of-use/`); the map still credits it in its
attribution while the borders are shown.

`borders.json` is not the download: `tools/borders/build_borders.py` wrote it, in the format
`osxp-borders-1` that the tool describes. International boundaries, plus the disputed, indefinite
and line-of-control ones as a second class; lease and overlay limits dropped; pieces joined end
to end; points rounded to 0.0001° and simplified at 0.0005°. Result: 402 lines, 62,662 points,
511,743 bytes, sha256 `469c3351c038181738f12664a6c5463427fb7b0cd7123c234cfc10d79922b41e`.

Accuracy: a few hundred metres (a 1:10 million source). The page hides the borders when the map
is zoomed in past level 10, where that error would show against the imagery.

To update: download the new GeoJSON, check it, run
`python tools/borders/build_borders.py <file>` from the repository root, and update this note.
