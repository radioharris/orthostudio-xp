# 0008. The native vector stage by default

Date: 2026-09-13. Status: accepted. Supersedes arbitration A7 (`vectors-assembly.md`).

## Context
`orthostudio.vectors@1` rebuilds Ortho4XP's PSLG, airports included, about five times faster, and it
is the only path that uses OrthoStudio XP's own OSM client and relief (decision 0007). It stayed
opt-in (`--vectors native`) for two reasons written in `docs/benchmarks/p4-airports.md` 5: every
proof was made on one tile, `+43+005`; and a rebuild changes the bytes of the mesh and the DSF, so
the size of that difference had to be confirmed on a second tile. Meanwhile the default path, the
web page's included, still ran Ortho4XP's stage 1: its relief, the one that built a flat Jura on
`+46+006`, and its Overpass client, three of whose four servers are gone.

## Decision
Two tiles were added as frozen Ortho4XP references (`fixtures/large/oracle`):
`+50+008` Frankfurt, the densest PSLG of the three with EDDF, and `+39+003` eastern
Mallorca, coastal. On both, the native PSLG is Ortho4XP's as a set, and the seed blocks and the
smoothed `Data<tile>.alt` are byte-identical. The mesh moves by a dozen vertices in several
hundred thousand, the airport terrains keep exactly their triangle counts, and the `.ter`
files are byte-identical. Numbers: `docs/benchmarks/p4-airports.md` 7.

`--vectors` gains `auto` and it becomes the default (`BuildSpec.vectors`, the CLI, the API):
- under `--stages native` and `auto`, `auto` means the native stage;
- under `--stages legacy`, it means Ortho4XP's stage 1;
- a native stage that cannot run is downgraded, with its reason recorded.

`native` and `legacy` still force one. `$OSXP_VECTORS` changes the default, and the test suite
pins `legacy`, the recipe its references were built with.

## Consequences
- A default build no longer runs any Ortho4XP code for the vectors. It uses X-Plane's
  relief and refuses a flat tile, from the command line and from the web page alike.
- Rebuilding a tile first built with Ortho4XP's stage 1 gives an equivalent mesh and DSF, not
  the same bytes.
- Not covered by the three tiles: the APRON marker, which no tile exercises because no apron
  carries the `include` tag, and a coastal tile without any aerodrome, since Mallorca has
  small ones. Unit tests remain the only proof for those rules.
