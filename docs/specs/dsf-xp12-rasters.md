# DSF: elevation and bathymetry rasters copied from X-Plane 12 Global Scenery

Status: P2, written before the code of `src/orthostudio/dsf/xp12.py`. Tests:
`tests/test_dsf_xp12.py` (synthetic 7z and plain DSF; `xplane` on the local Global Scenery).

Origin in Ortho4XP: `src/O4_DSF_Utils.py:360-453` (`extract_elevation_and_bathymetry_data`),
called at `:1082`; `src/O4_Overlay_Utils.py:15-25` (`custom_overlay_src`, `unzip_cmd`).

## 1. The rule in plain language

X-Plane 12 renders sea level and water depth from two rasters (`elevation`, `sea_level`)
shipped in the Global Scenery DSF of every tile, together with a soundscape raster and eight
seasonal rasters. An ortho DSF replaces the Global Scenery mesh, so it must carry the same
rasters or XP12 has no sea level. Ortho4XP copies the `DEFN/DEMN` atom (raster names) and the
`DEMS` atom (raster infos and data) verbatim from the Global Scenery DSF, except that the
second large raster (bathymetry) is clamped to `elevation - 2`: XP's bathymetry is partial
for inland water, and a depth above the terrain would draw water in the air.

## 2. Rules ported

| Line | Rule | OrthoStudio XP |
|---|---|---|
| `:363-367` | source `<custom_overlay_src>/Earth nav data/<10x10>/<tile>.dsf` | `global_scenery_dsf`: `global_scenery_dir / tile.dsf_relpath`, else the same path under `X-Plane 12 Demo Areas` beside `global_scenery_dir` (`DEMO_AREAS`), where X-Plane 12's installer keeps the regions of its demo, some only there: Maui to Kauai (`+20-160` of the Global Scenery is an empty folder), the coast of Oregon and Washington, south-east Alaska. Ortho4XP reads one folder, so those tiles need `custom_overlay_src` changed |
| `:368-375` | missing file: error message, empty atoms | `OsxpError("DSF_GLOBAL_SCENERY_MISSING", tile, path)` |
| `:376-388` | copy to a temporary file (copy failure: error) | read in place; `DSF_GLOBAL_SCENERY_COPY_FAILED` only if the read fails |
| `:390-397` | first two bytes `7z` -> `7z e` into the tmp dir, else the file is a plain DSF | `py7zr` when the file starts with `7z\xbc\xaf\x27\x1c` (the archive holds one DSF; the first member is taken); failure -> `DSF_SOURCE_DECOMPRESS_FAILED` |
| `:398-405` | magic `XPLNEDSF` else "Corrupted DSF" | `DSF_SOURCE_CORRUPTED` (also for a truncated atom tree) |
| `:409-446` | walk the top-level atoms; `DEMS` (`SMED`): copy every sub-atom header and payload, counting the sub-atoms longer than 100 bytes: the first is `elevation` (kept), the second is `sea_level` and becomes `numpy.minimum(bathy, elev - 2)` as `int16`; `DEFN` (`NFED`): keep the payload of `DEMN` (`NMED`) | same walk on bytes; `int16` arithmetic (`- 2` wraps like Ortho4XP would, irrelevant for real elevations); a DSF without `DEMS`/`DEMN` -> `DSF_SOURCE_CORRUPTED` (Ortho4XP would raise `UnboundLocalError`) |
| `:447-453` | remove the tmp file, return `(bDEMN, bDEMS)` | `Xp12Rasters(demn=bytes, dems=bytes)`; nothing written on disk |

The `> 100` bytes rule is what Ortho4XP does: it relies on the `DEMI` info atoms being 28 bytes
and every `DEMD` data atom being large. The order of the rasters is the Global Scenery's
(`elevation`, `sea_level`, `soundscape`, seasons), which is why the second large sub-atom is
the bathymetry; OrthoStudio XP checks that the `DEMN` names start with `elevation`, `sea_level` and
raises `DSF_SOURCE_CORRUPTED` otherwise rather than clamping the wrong raster.

**Rasters of two sizes** (issue 17, 2026-10-23). Every Global Scenery DSF of X-Plane 12.4.4 holds
both at 1201 x 1201, posts on the tile's edges (the 18,197 of the owner's). A user's `+32+012`,
an older Global Scenery file by its content (no soundscape or season rasters, beaches from
`lib/g8`, a `DEMN` naming a third raster, `bathymetry`, it does not hold), had its `sea_level`
at 256 x 256 by cell (area-centric, flags `0x1`): the minimum post by post stopped there, as
Ortho4XP's would, and so did every square of his. Each bathymetry value is then clamped by the
ground at its own place (`_clamped`): the elevation read between its four nearest posts, each
raster placed by its own centring (`_place`), and `floor` of it minus 2 m; the raster keeps its
size, and every other byte stays. Rasters of one size give Ortho4XP's bytes, as before. A raster
that cannot be placed (no `DEMI` before it, not 16-bit integers, a size its data does not have)
is left as X-Plane wrote it, which X-Plane draws on its own scenery. That `+32+012` builds whole
with it, its relief from -6 to 876 m, its `DEMN` and both rasters copied as they are.

The result is used as is by `build_dsf` (`dsf-encoding.md` 3.6): `DEMN` becomes the payload of
`DEFN/DEMN`, `DEMS` the payload of the trailing `DEMS` atom. A build without rasters
(`rasters=None`) writes an empty `DEMN` and no `DEMS` atom, as Ortho4XP does when the extraction
fails (`:373-375`) — but OrthoStudio XP never does this silently: the caller decides (plan section
3, "xp12.dsf is a blocking node requiring an explicit choice").

## 3. Decision

Keep the rule and the bytes; replace the `7z` subprocess and the temporary copy with `py7zr`
in memory; typed errors instead of messages and empty bytes.

## 4. Acceptance tests

- Oracle: `extract_xp12_rasters("~/X-Plane 12/Global Scenery/X-Plane 12 Global
  Scenery", TileRef(43, 5))` gives `demn` equal to the `DEFN/DEMN` payload and `dems` equal to
  the `DEMS` payload of the reference DSF (verified once by hand: identical, 7 341 072 bytes
  of DEMS, 11 rasters, 1201² int16 elevation and bathymetry).
- Synthetic: a hand-built DSF with `DEMN = "elevation\0sea_level\0"`, two `DEMI` and two
  `DEMD` of 200 int16 each: the bathymetry is clamped where it exceeds `elev - 2`, the other
  bytes are untouched; the same DSF inside a 7z archive gives the same result; a missing
  file, a non-DSF file and a DSF without `DEMS` raise the three coded errors.
- Two sizes: a 17 x 17 elevation by post and an 8 x 8 bathymetry by cell: each value is the
  minimum of its own and the ground between its four nearest posts minus 2 m, computed apart;
  the raster keeps its size and every other byte; rasters of one size stay post by post; a
  raster of floats is left as it is; the code before stopped on it as on the user's file.

## 5. Wanted differences from Ortho4XP

No temporary files, no `7z` binary, no silent empty rasters.
