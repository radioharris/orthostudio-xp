# 0010. Nothing in OrthoStudio XP depends on Ortho4XP

Date: 2026-09-14. Status: accepted. Supersedes the last two points of decision 0009 (the
Ortho4XP folder a build read, Ortho4XP as the reference of the tests) and the oracle tooling that
decisions 0005 to 0008 relied on.

## Context
After decision 0009 no Ortho4XP code ran inside a build, but OrthoStudio XP still depended on
Ortho4XP in three ways:
- a build read an Ortho4XP folder named with `--legacy-dir` or `$OSXP_LEGACY_DIR`: its OSM cache
  instead of a snapshot, its hand-corrected coastlines, `Patches/`, `Elevation_data/`, the overlay
  settings of its `Ortho4XP.cfg` and the nvcompress of its `Utils/`; `osxp textures --from-legacy`
  rebuilt the textures of an Ortho4XP build;
- 58 test files compared OrthoStudio XP with Ortho4XP: 12 ran its Python in its own virtual
  environment, others read its caches and binaries, the rest 3 GB of its frozen builds
  (`fixtures/large`); `tools/oracle` drove it headless, `tools/audit` read its provider files and
  three benchmarks measured against it;
- the product kept code only those comparisons used: the vector stage's reader of Ortho4XP's
  `.osm.bz2` cache, the DSF comparators, Ortho4XP's airport dictionary.

The two projects had become so intertwined that it was no longer clear which code was whose.

## Decision
OrthoStudio XP does not read, run or compare itself with Ortho4XP, in the product, the tests or
the tools. The one exception is the import of tiles an Ortho4XP folder built (`osxp
import-ortho4xp <folder>`, *Import my Ortho4XP tiles* in the Library): it registers them so they
can be installed, compared and removed like the others, and it writes nothing into that folder.
- Removed from the product: `--legacy-dir`, `$OSXP_LEGACY_DIR`, `osxp textures`, the Ortho4XP
  cache source of the vector stage (it reads the snapshots of `orthostudio.osm@1` only), the
  curated coastline and water files, the overlay defaults read from `Ortho4XP.cfg`, the
  elevation cells and nvcompress of an Ortho4XP folder, `apt_dictionary`, `features` in
  `/api/status` and the stand-ins of `api/_compat.py`.
- The DSF container reader, which the relief and the DSF decoder use, moves from
  `orthostudio.oracle` to `orthostudio.dsf.container`.
- Removed from the repository: the oracle tests, `tools/oracle`, `tools/audit`, the noding, DDS and
  P1 benchmarks, `fixtures/oracle` and the `oracle` test marker. The tests that read the local
  X-Plane 12 install (read only, skipped without it) carry the `xplane` marker.
- To compare a tile with Ortho4XP: build it with the Ortho4XP project itself, import it, and
  compare the two packs.

## Consequences
- A build uses OrthoStudio XP's own data only: its store, its elevation directory, X-Plane's
  Global Scenery and the network.
- Fidelity to Ortho4XP is no longer checked automatically. What the specifications and the
  benchmarks say about byte identity with Ortho4XP was measured before this decision. The tests
  keep the reference implementations written into them (DSF encoding, weight map, mesh
  post-processing, masks, overlays), which need nothing from Ortho4XP.
- Patches have no source in a build: the vector stage still builds the family from a folder it
  is given.
- The coastline rule lost its `legacy_cache` input, so its key changes: a stored tile computes its
  coastline once more, in milliseconds.
- `fixtures/` is git-ignored; the frozen Ortho4XP builds in `fixtures/large` are no longer used and
  stay on the disk they were made on until their owner deletes them.
- The pack names `zOrtho4XP_<tile>` and `yOrtho4XP_Overlays` and the `Ortho4XP_<tile>.cfg` of a
  pack stay: they are file formats that X-Plane users and tools expect, not a dependency.
