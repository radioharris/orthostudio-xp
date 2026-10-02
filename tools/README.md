# tools

`bench/network/`: one-off network benchmarks (tile hosts, Overpass mirrors); their results are
copied into `docs/benchmarks/`. `borders/`: writes the Plan map's country borders from Natural
Earth (`src/orthostudio/ui/vendor/borders/`). `screenshots/`: turns a capture of the whole screen
into a picture of the page alone, for the README and the store listing. `package/`: builds the
installers, Python inside (`docs/specs/packaging.md`), and the macOS app that runs a checkout.
`relief/`: says how far apart two relief sources are over one square, from the files a build
keeps (ANADEM against Copernicus, a folder of one's own against either). `live/`: writes
OrthoStudio XP Live's map of which of AutoOrtho's regions has the mesh of each square
(`src/orthostudio/live/aoregions_map.json`), from the regions' zips' lists of files. `live/flights/`:
OrthoStudio XP Live's replayable test flights, flown by X-Plane itself through the test pilot
plugin (`native/xplane_test_pilot`), each with its report (`docs/specs/live/flights.md`).
