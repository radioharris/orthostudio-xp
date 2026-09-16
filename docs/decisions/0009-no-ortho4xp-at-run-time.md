# 0009. Ortho4XP no longer runs inside OrthoStudio XP

Date: 2026-09-14. Status: accepted. Supersedes the stage selection of decisions 0006 and 0008
(`--stages`, `--legacy-stage`, `--vectors`).

## Context
Since decision 0008 every stage of a default build is OrthoStudio XP's own, proven against
Ortho4XP on three tiles. Ortho4XP still ran when a build asked for it (`--stages legacy`,
`--legacy-stage`, `--vectors legacy`), and that path kept a large part of the code alive for no
user benefit:
- a runner and a driver that start Ortho4XP's interpreter, their timeouts, their error domain
  (`LEGACY_STAGE_FAILED`, `LEGACY_STAGE_TIMEOUT`) and a gate serialising its Overpass client, most
  of whose servers are gone;
- two bridges only Ortho4XP read: the `Data<tile>.apt` pickle the vector stage wrote, and the
  `.osm.bz2` files phase 0 copied into the Ortho4XP checkout;
- three switches whose combinations had to be refused one by one, a `downgraded` state per tile,
  and an `osm_snapshot` label consumed by Ortho4XP's stages alone.

Removing it also exposed a bug the dual path hid: without an Ortho4XP folder, phase 0 took the
missing cache for a complete one and downloaded nothing.

## Decision
OrthoStudio XP builds every stage with its own rules and runs no Ortho4XP code.
- Removed: the `orthostudio.legacy` package and its three rules, `--stages`, `--legacy-stage`,
  `--vectors` and `$OSXP_VECTORS`, the stage timeouts, the `LEGACY` error domain, the pickle
  bridge (the airport cover of the DSF reads `airports.npz`), `osm_snapshot` and
  `--osm-snapshot`, and `StageChoice.downgraded`.
- A setting no stage supports (`iterate` other than 0, `masks_use_DEM_too`,
  `masks_custom_extent`) or a missing Triangle4XP stops the build before it downloads anything
  (`CFG_VALUE_INVALID`, `SYS_TOOL_MISSING`).
- An Ortho4XP folder named with `--legacy-dir` or `$OSXP_LEGACY_DIR` is read and never written:
  its OSM cache, curated coastlines, `Patches/`, `Elevation_data/` and the overlay settings of its
  `Ortho4XP.cfg`. The elevation cells a build downloads go to `$OSXP_HOME/elevation`.
- The coastline of the mesh reads the OSM data the vector stage reads. A tile always has one, and
  a tile whose OSM layers cannot be had is reported as not built instead of meshed without its
  coast.
- Ortho4XP stays the reference of the oracle tests. The runner that drives it lives in
  `tools/oracle`, and the tests keep the reader of its pickle (`tests/ortho4xp_apt.py`) and the
  writer of its OSM cache (`tests/ortho4xp_osm.py`).

## Consequences
- Artefacts of the `legacy.*` rules are neither produced nor reused any more; `osxp clean` gives
  their space back once no pack on disk was built from them.
- Build reports and pack manifests name `orthostudio.*` rules only; `stages` holds `dem` alone,
  and the OSM report no longer lists the files written into an Ortho4XP folder.
- Only the oracle tests need an Ortho4XP checkout.
- The pack folder names `zOrtho4XP_<tile>` and `yOrtho4XP_Overlays` stay, for the installs and
  tools that expect them.
