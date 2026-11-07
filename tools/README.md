# tools

`bench/network/`: one-off network benchmarks (tile hosts, Overpass mirrors); their results are
copied into `docs/benchmarks/`. `bench/build_times.py`: one build of a tile through the app's own
path, each step timed (`docs/benchmarks/build-times-1.0.md`). `borders/`: writes the Plan map's country borders from Natural
Earth (`src/orthostudio/ui/vendor/borders/`). `screenshots/`: turns a capture of the whole screen
into a picture of the page alone, for the README and the store listing. `package/`: builds the
installers, Python inside (`docs/specs/packaging.md`), and the macOS app that runs a checkout.
`relief/`: says how far apart two relief sources are over one square, from the files a build
keeps (ANADEM against Copernicus, a folder of one's own against either).
`witness/`: builds the witness tiles (`witness/tiles.txt`) with a checkout, up to their DSF, and compares two
such builds tile by tile: what a change touches, before it is released (`docs/testing/witness-tiles.md`).
`8k-test/`: copies of built tiles with their 4K photos glued four by four into 8K textures, beside 4K
copies rewritten the same way, to measure what fewer, larger textures change in X-Plane; not part of
the app, it runs with an installed OrthoStudio XP's own Python and DSFTool, a launcher per system
(`8k-test/README.txt`).
`clouds/`: converts OmniCloudMask's networks for OpenCV (`export_detector.py`, published as the
release `clouds-detector-1`) and measures the cloud mode's After against the textures as laid
(`after_vs_built.py`, `docs/benchmarks/clouds.md` B5).
