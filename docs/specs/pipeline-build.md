# Pipeline: `osxp build` (one command, one installed tile)

Status: P2a integration, written before the code of `src/orthostudio/pipeline/build.py`,
`src/orthostudio/pipeline/pack.py`, `src/orthostudio/estimate.py` and the `build` / `plan` /
`install` / `import-ortho4xp` / `library` / `why` commands of `src/orthostudio/cli.py`. Tests:
`tests/test_build_*.py` (graph, pack, estimate: no network), `tests/test_cli_build.py`.
Measurements: `docs/benchmarks/p2-build.md`.

Origin in Ortho4XP: `O4_Tile_Utils.py:build_tile` / `build_all` (the four steps of a tile
in one thread), `O4_GUI_Utils.py:1713-1780` (install by hand), `O4_Tile_Utils.py:build_tile_list`
(batch = a `for` loop). Decision: **replaced** by a graph declared per tile and run by
`orthostudio.sched.Scheduler` over the P2a modules; nothing is ported here, this document only fixes
how the modules are composed, what enters each key, and what the user sees.

## 1. The rule in plain language

`osxp build --tile +43+005 --provider BI --zl 14` declares the tile's graph, the download of the OSM
layers the tile does not have yet included (section 8.3), ten nodes more (eleven with `--install`), runs the ones whose
artefact is not in the store, assembles `<out>/zOrthoStudio_+43+005/` (DSF, `terrain/`, `textures/`,
`orthostudio.toml`, `tile_settings.cfg`) and `<out>/yOrthoStudio_Overlays/`, and, with `--install`,
links both packs into `Custom Scenery`, orders `scenery_packs.ini` and records the tile in the
library. `--set name=value` overrides one of the 44 tile variables of Ortho4XP or one of the three
overlay settings (`ovl_exclude_pol`, `ovl_exclude_net`: the Ortho4XP *application* variables;
`keep_objects`, OrthoStudio XP's, `overlays.md` D3). OrthoStudio XP runs no Ortho4XP code (decision
0009) and a build reads no Ortho4XP folder (decision 0010). Running it again with nothing changed
touches nothing and finishes in a few seconds; changing one parameter rebuilds only the nodes whose
consumed subset contains it, and everything below them whose input bytes changed.

## 2. Graph of one tile

```
                 +43+005/osm       (orthostudio.osm, net, lane overpass; when the layers are missing)
                        :
                 +43+005/dem       (orthostudio.dem, subprocess; net for a downloaded relief)
                        |
                 +43+005/vectors   (orthostudio.vectors, subprocess) <- OSM layers
                        |          +43+005/coastline (orthostudio.coastline, io) <- OSM layers
                        |                 |
                 +43+005/mesh      (orthostudio.mesh, subprocess)
                        |
                 +43+005/masks     (orthostudio.masks, subprocess)  <-- neighbours' meshes (nb_*)
                        |            +43+005/xp12 (xp12.rasters, io)  <- Global Scenery 7z
                        |                 |
                 +43+005/BI14/dsf (tile.dsf, cpu) <- vectors (airports only when covered)
                        |
                 +43+005/BI14/textures (tile.textures, net; build_textures of P1) <- masks
                        |                                   +43+005/overlay (tile.overlay,
                 +43+005/BI14/pack (tile.pack, io) <--------  subprocess) <- Global Scenery 7z
                        |
                 +43+005/BI14/install (tile.install, io, --install only)
```

Node ids are `<tile>/<name>` for the parts that do not depend on the imagery
(`osm`, `dem`, `vectors`, `coastline`, `mesh`, `masks`, `xp12`, `overlay`) and
`<tile>/<provider><zl>/<name>` for the rest, so two builds of the same tile at two zoom levels in
one batch share those nodes (one object, one execution: spec `scheduler.md` 2.1). When two specs of
a batch need the same id with different params (the same tile with two `masks_width`), the second
id gets a `#2` suffix.

| Node | Rule (version) | Kind, RAM | Params (consumed subset) | Inputs | Artefact |
|---|---|---|---|---|---|
| dem | `orthostudio.dem@1` | net, 0.6 GB | `DemParams` (spec `dem.md` 9) + `tile` | the Global Scenery DSFs of the 3x3 block when the relief is X-Plane's (decision 0007) | dir `Data<tile>.alt`, `dem.npy`, `meta.json` |
| vectors | `orthostudio.vectors@1` | subprocess, 1.2 GB | `VectorsParams` (spec `vectors-assembly.md`) + `tile` | `osm` (8.5), `dem`, `patches` (absent), `airports` (absent) | dir `Data<tile>.{node,poly,alt}`, `dem.json`, `airports.json`, `airports.npz`, `airports.wkb.json` |
| coastline | `orthostudio.coastline@1` | io, 0.2 GB | `tile` | `osm` (8.4) | file `coastline.npz` |
| mesh | `orthostudio.mesh@1` | subprocess, 0.9 GB | `MeshParams` (spec `mesh-build.md` 9) + `tile` | `vectors`, `dem` (8.2), `coastline` | dir `Data<tile>.mesh`, `mesh.npz` |
| masks | `orthostudio.masks@1` | subprocess, `300 + 800 x workers` MB | `MasksParams` (spec `masks-build.md`) + `tile` | `mesh`, `nb_n`..`nb_nw` | dir `<y>_<x>.png`, `index.json` |
| xp12 | `xp12.rasters@1` | io, 0.2 GB | `tile` | `source` = the Global Scenery DSF (7z) of the tile, by content digest | dir `demn.bin`, `dems.bin` (bathy already clamped) |
| dsf | `tile.dsf@1` | cpu, 3 GB | `TileDsfParams` = `DsfParams` (spec `dsf-encoding.md` 2, minus `sea_texture_blur`, see 7) + `tile` + `creation_agent` | `mesh`, `masks`, `rasters` (None = no DEMS, explicit choice only), `vectors` (None unless `cover_airports_with_highres` is `True` or `ICAO`) | dir `<tile>.dsf`, `terrain/*.ter`, `textures.json`, `stats.json` |
| textures | `tile.textures@1` | net (+ its own encoding pool), `workers x 0.25 GB` | `TileTexturesParams`: encoder and version, `mip_mode`, `refine_passes`, `sea_texture_blur`, `clean_halo`, `parent_levels`, `TerParams` fields | `dsf`, `masks` | dir `textures/*.dds` (hard links to the `texture.dds` artefacts), `textures/water_transition.png` when needed, `terrain/*.ter`, `manifest.json` |
| overlay | `tile.overlay@1` | subprocess, 0.3 GB | `OverlayParams` = `OverlayExclusions` + `tile`; values from `config` / the spec fields, else the OrthoStudio XP defaults (`overlay_settings`) | `source` = the Global Scenery DSF, by digest | file: the overlay DSF |
| pack | `tile.pack@1` | io | `PackParams`: `tile`, `provider`, `zl`, `out_dir`, `link`, `tile_cfg` (the text of `Ortho4XP_<tile>.cfg`: the 44 tile variables the build consumed) | `dsf`, `textures`, `overlay` (None with `--no-overlay`) | file `orthostudio.toml` (the manifest, also written in the pack) |
| install | `tile.install@1` | io | `InstallParams`: `tile`, `custom_scenery`, `link` | `pack` | file `install.json` (receipt) |

### 2.1 Keys and what changes what

Every key is `key_for(rule, params, {input: digest})` (spec `graph-keys.md` 3), so:

* a `masks_width` change (consumed by `orthostudio.masks` only) rebuilds `masks`; the DSF is rebuilt
  when the mask *bytes* changed (they do: the transition width is in the PNG); the textures
  node is rebuilt because its `masks` input digest changed, but inside it only the textures
  whose mask crop changed are re-encoded (`texture.dds` keys on the mask digest, spec
  `pipeline-textures.md` 6): the coastal ones. `dem`, `vectors`, `coastline`, `mesh`, `xp12`,
  `overlay` hit.
  (measured by the former oracle test 4 of section 6);
* a `curvature_tol` change rebuilds `mesh` and everything below; `vectors` hits;
* a `zl` change rebuilds `dsf`, `textures`, `pack`; the stage nodes hit (their params do not
  contain the zoom level: `mesh_zl` is what shapes the mesh, and no stage reads `default_zl`);
* changing `creation_agent` rebuilds the DSF only (1.3 s);
* the OSM data enters the vector and coastline nodes as an **input**, by content: the phase-0
  snapshot (8.5). A refetch of unchanged data
  rebuilds nothing; `--osm-refresh LABEL` changes the OSM node's key to ask for fresh data;
* the Global Scenery file is keyed by its content digest (`ArtifactRef` whose key and digest
  are both the blake3 of the file, recorded as a *source* edge): a new X-Plane version with a
  different DSF rebuilds `xp12` and `overlay`.

Neighbours of `masks`: for each of the eight neighbours, the tile's `mesh` node when the
neighbour is in the batch, else the most recently used `orthostudio.mesh` artefact of the store
that holds `Data<neighbour>.mesh` (an `ArtifactRef`), else `None`. The three cases give three
different keys, so a tile built alone and then again next to its neighbour gets new masks along
the shared edge, as Ortho4XP would if run in that order.

### 2.2 The textures node

`tile.textures` reads `textures.json` of the DSF artefact (the `TextureJob` list of `build_dsf`:
texture id and terrain kinds), groups it by `(provider, zl)` (zone_list and airport covers can
name other levels) and calls `build_textures` (P1) once per group with `out_dir` = the artefact
directory being built, the masks artefact as `mask_lookup`, `quiet=True`, `progress` relayed to
`ctx.progress` (fraction = half the tiles fetched + half the textures finished) and `cancel` =
the node's cancel token. `build_textures` commits one `texture.dds` artefact per texture into
the same store (its own connection) and hard-links it into `textures/`; the node's own artefact
is that directory. Its full report (timings, network counters) is written next to the logs
(`<workdir>/logs/textures-<tile>-<key12>.json`), never inside the artefact (timestamps would
defeat early cutoff for the pack).

The node **fails** (nothing committed) when the report is not `ok`: a texture is `incomplete`
(`IMG_TILE_MISSING`), `failed` (`MASK_STALE`, encoder) or cancelled. A committed textures
artefact is therefore always complete; running `osxp build` again after a network failure
retries only the missing tiles (containers keep their `ERROR` entries, spec
`pipeline-textures.md` 3) and re-encodes only the textures whose key is not in the store.

The chunk containers are fetched by the node, so they are **not** inputs of its key (a
texture's DDS is keyed on its container digest; the node artefact is keyed on the DSF and
masks digests and the params). A provider whose imagery changed is not detected: `osxp cache`
(P6) will offer the refresh; until then delete the containers.

### 2.3 Pack and install: effects, not only artefacts

The pack and install nodes have effects outside the store (the output directory, `Custom
Scenery`, `scenery_packs.ini`, the library). Their artefacts are small receipts keyed on the
inputs and the destination, so that an unchanged build hits and costs nothing. Because a hit
executes nothing, `build_tiles` **verifies the effects after the run**: a pack whose manifest
hit but whose directory lacks a listed file is re-assembled (hard links, under a second), an
install whose link or `scenery_packs.ini` line is gone is redone. Both operations are
idempotent.

`tile.pack` writes, atomically per file (`fsutil`):

```
<out>/zOrthoStudio_+43+005/
  Earth nav data/+40+000/+43+005.dsf      hard link (copy across file systems) of the DSF artefact
  terrain/*.ter                           copies (1-2 kB each)
  textures/*.dds, water_transition.png    hard links of the textures artefact
  orthostudio.toml                        manifest (section 3)
  tile_settings.cfg                       the 44 tile variables the build consumed
<out>/yOrthoStudio_Overlays/Earth nav data/+40+000/+43+005.dsf   hard link of the overlay artefact
```

An existing DSF of a different content becomes `<name>.dsf.bak` (Ortho4XP convention, `write_dsf`;
the overlay DSF likewise); DDS files are **replaced without a backup** (a `.dds.bak` per
re-encoded texture would leave gigabytes in `textures/`; stray `*.bak` in `textures/` and
`terrain/` are swept) and DDS and `.ter` files that are no longer referenced by the new DSF
are removed from the pack (they stay in the store). The pack directory is named by the tile
only, so `declare` **refuses** two specs of a batch that would write different content
(another level, another parameter set) into the same `<out>/zOrthoStudio_<tile>/`
(`CFG_VALUE_INVALID`: one level per output directory). On Windows, `tile.pack`
refuses (`XP_RUNNING`) to rewrite a pack that is installed while X-Plane runs (it holds the
DSF open; `os.replace` would fail), and a `PermissionError` on a file is `XP_PACK_CONFLICT`
with the path. Group mode (several tiles sharing `terrain/` and `textures/`) is not offered
in P2a: one pack per tile, the overlays pack shared.

`tile.install` (with `--install`; serialised by a process-wide lock because `scenery_packs.ini` is
edited): `install_pack(pack_dir, custom_scenery, update_ini=False)` for the tile pack and for
`yOrthoStudio_Overlays`, then **one** `scenery_packs.ini` load / `ensure` / `ensure` / save (spec
`install.md` 3-4: symlink or junction, order `yOrthoStudio_Overlays` > `zOrthoStudio_*` > AutoOrtho
> mesh packs; the `.bak` is the state before OrthoStudio XP touched the file, not a half-updated
list), then `Library.register(tile, provider, zl, pack_dir, "osxp", keys)` with every artefact key
of the manifest. `XP_RUNNING` refuses the install while X-Plane runs; the build itself is
unaffected.

### 2.4 Global Scenery and the XP12 rasters

`--global-scenery DIR` names the X-Plane 12 Global Scenery (or the X-Plane folder); by default
it is `<detect_xplane()>/Global Scenery/X-Plane 12 Global Scenery`. When the tile's DSF is not there
the `xp12` and `overlay` nodes fail (`DSF_GLOBAL_SCENERY_MISSING`) and the DSF is not built:
never an empty artefact. `--no-xp12-rasters` is the explicit choice of a
DSF without `DEMS` (XP11 sea level); `--no-overlay` skips the overlay pack.

## 3. The pack manifest `orthostudio.toml`

```toml
format = "osxp-pack-1"

[tile]
name = "+43+005"
provider = "BI"
zl = 14

[artefacts]                      # key and content digest of every artefact the pack came from
dem = { key = "...", digest = "...", rule = "orthostudio.dem@1" }
vectors = { key = "...", digest = "...", rule = "orthostudio.vectors@1" }
coastline = { ... }
mesh = { ... }
masks = { ... }
xp12 = { ... }
dsf = { ... }
textures = { ... }
overlay = { ... }

[files]
dsf = "Earth nav data/+40+000/+43+005.dsf"
dsf_size = 32442870
textures = 17
terrain = 39
overlay = "../yOrthoStudio_Overlays/Earth nav data/+40+000/+43+005.dsf"
```

Deterministic (no timestamps): the manifest is the pack artefact, and its digest keys the
install receipt. `osxp why <pack dir>` reads it and explains every artefact (`Store.explain`).
The upstream keys (`dem`, `vectors`, `coastline`, `mesh`, `masks`, `xp12`) come from the store's
provenance edges of the DSF artefact (`Store.why`), not from the graph: the manifest can be rebuilt
from the store alone.

## 4. Batches (`build_tiles`)

One `Scheduler` for the batch: `cpu_workers = --workers` (default `cores - 2`),
`subprocess_slots = min(3, max(1, cores - 1))` (the vector, mesh, masks and overlay nodes, 0.3-1.2
GB each plus the masks' own pool, and X-Plane 12's relief, read from its DSFs; a quarter of the cores
until 2026-09-15, which gave a 4-core Windows virtual machine one slot, so that a batch's stages ran one
after the other there while a 14-core Mac ran three), `net_slots = 1` (one
Bing pipeline at a time: the provider's `max_in_flight` is the politeness limit, not the number of
tiles; a downloaded relief queues there too), `io_slots = 2`, `ram_budget_mb = 60 %` of the physical
memory, and the `overpass` lane of one (`OVERPASS_LANE`, `scheduler.md` 2): the OSM downloads (8.3)
run in the graph, one tile at a time, because the public Overpass mirrors already answer "too
busy" to more than the client's two requests a cluster (`docs/benchmarks/p2-build.md`, run F; rule
9, "polite network"), and beside the imagery's slot. Until 2026-09-14 they ran first, in a phase 0
of their own, and every tile waited for the downloads of all of them: ten tiles waited about four
minutes before the first relief, mesh or image; a tile now goes on once its own layers are in, and
its relief does not wait for them at all. Artefacts shared between tiles are deduplicated by key:
neighbours' meshes, the overlays of a tile built at two levels, the `texture.dds` of overlapping
zones.

Failures do not stop the batch (`fail_fast=False`): the tiles that fail are reported with their
coded error and the first upstream cause; the others are packed and installed. A tile whose OSM
download fails is reported like any failed stage: its `osm` row carries the download's error, the
nodes that read OSM data are skipped with it as their cause, and the others (relief, X-Plane
rasters, overlay) still run and stay in the store. A tile that asks for no download and has no
snapshot (`--no-osm-fetch`) is left out of the graph and reported as not built, its `osm` row
carrying `OSM_LAYER_UNAVAILABLE`. A tile whose
masks were skipped only because a *neighbour of the batch* failed its own OSM download, vector or mesh stage is
built again in a **second pass** with that neighbour treated as absent
(`declare(..., unavailable={neighbour})`): the neighbour's failure must not cost the tile, and a
later run with the neighbour present gives the masks their new key anyway (2.1). The first Ctrl-C
cancels cooperatively (the Triangle4XP and DSFTool children are killed, the download stops within a
second and keeps what it received); a second one abandons the run at once (no second grace period).
Where the event loop has no signal handler (Windows: `add_signal_handler` raises
`NotImplementedError`), `build_tiles` installs a plain `signal.signal(SIGINT)` handler doing the
same, restored afterwards; and `Scheduler.run` treats any `BaseException` escaping the loop
(`KeyboardInterrupt` with `handle_sigint=False`, a callback raising) as a cancel: the running nodes
are asked to stop before the pools are shut down, instead of waiting for the running stages to end.

### 4.1 Phases announced to the caller

A batch runs on up to two schedulers: the main graph, its OSM downloads included (section 8.3), and
the second pass (an older engine ran a third before them, phase 0, for the downloads). Each one's
`Stats` only knows its own nodes and restarts its clock, so a caller
that shows the whole job -- the API's jobs, `api.md` 5.6 -- could not tell where one phase ends nor
which nodes the next declared. `build_tiles` therefore hands `on_event`, next to the scheduler's
events, a `Phase` (`BuildEvent = Event | Phase`; the scheduler never emits one):

| When | Event |
|---|---|
| the settings validated, before the graph is declared | `Phase("build")` (`nodes=None`: the graph is being declared) |
| `declare` returned, before the first node event | `Phase("build", nodes=((id, kind, rule name), ...), reused=((<tile>/osm, key), ...))`: every node registered on the scheduler, the OSM downloads included, and the `osm` rows that will not run because the store holds the tile's snapshot |
| `run_osm_phase` (the downloads alone, on a scheduler of their own; `build_tiles` no longer calls it) | `Phase("data", nodes=((<tile>/osm, "net", "orthostudio.osm"), ...), reused=...)` before its downloads |
| the second pass declared | `Phase("build", nodes=...)` of the tiles it runs again |

The CLI ignores them. A callback that raises from a `Phase` (the API's `CancelRequested`)
stops the build there, between schedulers.

## 5. `osxp plan` / `--dry-run` (`src/orthostudio/estimate.py`)

For each spec, without building anything:

| Field | How |
|---|---|
| `nodes` | `Scheduler.plan(targets)`: `hit` / `build` / `unknown` and the EWMA seconds of each node (spec `scheduler.md` 2.8) |
| `textures.total`, `textures.exact` | when the DSF node is a hit, the `textures.json` of that artefact (exact: 100 % sea cells produce no texture); else every texture covering the tile (`textures_covering`), `exact = false` |
| `textures.masked` | from the same list: textures with a sea kind (DXT5, 22.4 MB) versus DXT1 (11.2 MB); unknown without the DSF: all DXT1 plus a note |
| `requests`, `download_mb` | per texture, the container of the chunk store: absent = 256 requests, present = its `ERROR` entries; MB = requests x mean body size (the probe's, else 13 kB, the Bing ZL14 mean) |
| `probe` | `--online` only: 20 chunks of the first incomplete texture through `Fetcher` (20 in flight, one round, polite, not stored); gives the line's latency (`seconds` of the round), `kb_per_request`, and `throughput = min(2000, max(20 / seconds, max_in_flight / seconds))` req/s: the fetcher keeps `max_in_flight` (128 for Bing) transfers open, so a 0.1 s latency sustains ~1 300 req/s (measured 1 300-1 500 on this line, section 6 runs) |
| `network_s` | `requests / throughput` (probe) or `requests / 400` (no probe: a conservative sustained rate, `net-download.md` measured 1 000+) |
| `compute_s` | the sum of the `build` and `unknown` node estimates plus `textures.total x` the per-texture cost (`sched.cost.tile.textures/texture`, learnt by `build_tiles` as `wall / textures`, default 0.5 s) divided by `workers` |
| `dds_gb`, `disk_free_gb`, `disk_ok` | DDS bytes of the plan; `shutil.disk_usage` of the store root; `disk_ok = free > 2 x needed` |

Output: JSON (`--json`) or a two-line summary ("network: your line ..." / "compute: your
Mac ...") per tile. Nothing is written except the probe's 20 chunks, which are **not** stored.

## 6. Acceptance tests

No network, no Ortho4XP: `tests/test_build_graph.py` (ids, sharing across ZL and tiles,
neighbour wiring, `#2` suffix, consumed subsets: `masks_width` in masks only, `zl` in none of
the stage params, `creation_agent` in the DSF params only; a synthetic run of the DSF and
pack nodes through the scheduler in thread mode on a tiny mesh), `tests/test_build_pack.py`
(layout, manifest round trip, `.bak`, stale file removal, `pack_is_intact`),
`tests/test_build_estimate.py` (offline estimate on a fake chunk store and store),
`tests/test_review2_robustesse_build.py` (a batch where nothing can build, and one where a neighbour
fails its vector stage: the second pass builds the tile's masks without it, with stand-in stages),
`tests/test_cli_build.py` (`plan --offline --json`, `install` on a temporary Custom Scenery,
`import-ortho4xp` on a synthetic Ortho4XP folder, `library --json`, `why` on a store artefact).

Measured with the former oracle test of the chain (+43+005, BI, Ortho4XP caches warm, OrthoStudio
XP store and chunks **empty**), removed with decision 0010:

| # | Run | Expected |
|---|---|---|
| 1 | ZL14, first run | the ten nodes built; DSF equivalent to the fixture, not byte-identical (within 2 kB of its size, first difference in the terrain definitions, which follow the triangle order of the mesh: `p4-airports.md` 7), 39 `.ter` identical, 17 DDS of the fixture's sizes, overlay DSF byte-identical (MD5 8116be88...); wall against 63.2 s |
| 2 | ZL14, second run, nothing changed | every node `Done(hit=True)`, no subprocess, no request, wall < 3 s |
| 3 | ZL16 | DSF equivalent to the ZL16 fixture, 356 `.ter` identical, 179 DDS; the stage nodes and `xp12`, `overlay` hit; wall against 250.7 s |
| 4 | ZL14 with `masks_width=200` | hits: dem, vectors, coastline, mesh, xp12, overlay; built: masks, dsf, textures (only the 7 coastal DDS re-encoded, 10 hits inside the report), pack |
| 5 | `--install --xplane <copy of Custom Scenery>` | links present, `scenery_packs.ini` ordered (overlay above ortho, both above AutoOrtho), library row `built_by="osxp"` with the keys; nothing written under the real X-Plane |
| 6 | batch `+43+005` at ZL14 and ZL15 | shared nodes executed once: `dem`/`vectors`/`coastline`/`mesh`/`masks`/`xp12`/`overlay` hit, the ZL14 pack hits and the ZL15 one is built |

## 7. Wanted differences, corrections to P2a modules, limits

* Every stage params model carries `tile: str`: a stage node with no input that names the tile
  (the elevation node of a tile whose relief is not X-Plane's) would otherwise share one key with
  every tile built with equal parameters, and the second tile would *hit the first tile's
  artefact* (found by the batch test of section 6 on the P2a rules). The graph sets it explicitly
  and the elevation rule checks it against its job. The invariant it restores: a key determines the
  artefact's content.
* `DsfParams.sea_texture_blur` is **removed** (listed by the contract, consumed by nothing in
  the DSF: it would rebuild the DSF when the imagery blur changes, against K2). The blur lives
  in `TileTexturesParams`.
* `orthostudio.dsf._mesh_reader` keeps only the `MeshLike` protocol and re-exports
  `orthostudio.mesh.mesh_file.MeshData` / `read_mesh` (the transitional private reader is deleted:
  one mesh object for the whole pipeline; the encoder reads the mesh version from
  `MeshData.version`).
* `orthostudio.dsf.xp12.read_global_scenery_dsf(path, tile)` is made public (the rasters node reads
  the source file it was given, not a directory).
* Ortho4XP wrote `Ortho4XP_<tile>.cfg` in the pack at every step 3 (`O4_Tile_Utils.py:67`);
  OrthoStudio XP writes it too (`PackParams.tile_cfg`, `tile_cfg_text` of the 44 values the build
  consumed, listed in the manifest as `files.cfg`) next to `orthostudio.toml`, so the Ortho4XP GUI
  can reread a tile built by OrthoStudio XP and `import-ortho4xp` reads `default_website` /
  `default_zl` from it instead of voting on the `.ter` names. (P2a review, minor.)
* Overlay: the OrthoStudio XP default excludes the beaches by name (`overlays.md` 4), byte-identical
  to Ortho4XP's `[0]` on XP12 Global Scenery. The Ortho4XP application variables `ovl_exclude_pol` /
  `ovl_exclude_net` are passed verbatim (an index stays an index), set per build (`--set`, `BuildSpec.config`, spec fields); `keep_objects` (D3,
  default True) is exposed the same way: `--set keep_objects=False` gives Ortho4XP's exact overlay
  on sources with objects. (P2a review, major: the settings were unreachable, the overlay node
  always ran with the OrthoStudio XP defaults.)
* P2a correction round (review findings, all fixed unless noted): the store sweeps only the
  `*.tmp-*` directories whose owner pid is gone (or older than 24 h); the same tile at two
  levels into one `--out` is refused; no `.dds.bak`; a `BaseException` in the loop cancels the
  running nodes; the second Ctrl-C abandons at once; the in-flight twins of a `KeyMismatch` node
  fail with a cause; one `scenery_packs.ini` save per install; a dangling link in `Custom Scenery`
  is replaced; a skipped node is blamed on its own tile's failure first; specs of a batch must
  agree on the fields resolved once per batch (`ValueError`); a running X-Plane on Windows makes
  the rewrite of an installed pack `XP_RUNNING`. Kept as they are: the neighbour wrapped at
  the antimeridian (`TileRef.neighbour`) and `creation_agent = "osxp"` (identity with
  Ortho4XP through `--creation-agent Ortho4XP`).
* Not in P2a: group mode, `Existing` cover mode (`CFG_VALUE_INVALID`), imagery refresh,
  `retry-missing` as a separate command (it is the default behaviour of `build`), a web page.
  P2b adds the web UI over these events; P3 and P4 replaced the Ortho4XP stages by OrthoStudio XP's
  without touching the DSF, textures, pack and install nodes, and decision 0009 removed the
  Ortho4XP ones.

## 8. The stages and their data

Status: written for P3 (native elevation, mesh and masks, an OSM client of our own) and P4 (native
vectors), then trimmed by decision 0009, when Ortho4XP stopped running inside OrthoStudio XP. Code:
`src/orthostudio/pipeline/native.py` and `src/orthostudio/pipeline/build.py`. Tests:
`tests/test_p3_native.py`, `tests/test_p3_fixes.py`, `tests/test_build_cold_tile.py` (graph shape,
no network, no Ortho4XP). Measurements:
`docs/benchmarks/p3-native.md`, `p4-vectors.md`, `p4-airports.md`.

### 8.1 What the stages refuse

Every stage is OrthoStudio XP's: `orthostudio.dem@1` (spec `dem.md`), `orthostudio.vectors@1`
(`vectors-assembly.md`), `orthostudio.coastline@1` (8.4), `orthostudio.mesh@1` (`mesh-build.md`) and
`orthostudio.masks@1` (`masks-build.md`). `resolve_stages` checks the settings of every tile
**before** the graph is declared and before an OSM download starts (`build_tiles` calls
`stage_choices` first), never through a failure at run time:

* the Triangle4XP program must exist (`$OSXP_TRIANGLE4XP`, `PATH`, then the repository build
  `native/triangle4xp/build`), else `SYS_TOOL_MISSING` with the two `cmake` commands that build it;
* `iterate` must be 0 (the `-r` refinement has no fixture, `mesh-build.md` 4.2), and
  `masks_use_DEM_too` and `masks_custom_extent` must be off (`masks-build.md` 8: the inputs they
  need do not exist), else `CFG_VALUE_INVALID` naming the setting.

There is no fallback recipe: a tile is built by these rules or refused. The one choice left is
`--dem` (8.2), recorded per tile in the report (`TileOutcome.stages`, `{"dem": ...}`).

### 8.2 Where the mesh reads its elevation (`--dem`)

`Data<tile>.alt` has two producers:

* `orthostudio.vectors@1` publishes the raster **after** `smooth_raster_over_airports`
  (`O4_Airport_Utils.py:1034`), smoothed over the tile's airports as Ortho4XP's step 1 did;
* `orthostudio.dem@1` publishes it **before** that smoothing (`dem.md` 9.2). It is the vector
  stage's elevation input, so it is declared for every tile.

`--dem vectors`, the default, wires the mesh's `dem` input to the vectors artefact, which is what
Ortho4XP meshed; `--dem native` wires the raw raster. On `+43+005` the two differ on 21 923 of
13 490 929 samples (0.163 %, up to 13.57 m), and the whole of it is the airport smoothing: against a
vector stage run with an empty airport layer, the raw raster differs on 204 samples outside the tile
and Triangle4XP returns the same mesh from either (`p4-vectors.md` 5).

### 8.3 The OSM node

```
  +43+005/osm       (orthostudio.osm@1, net, lane overpass)   snapshots of the 4-5 layers
        |
        +--> +43+005/vectors   (orthostudio.vectors@1)     both read the snapshot (8.5)
        +--> +43+005/coastline (orthostudio.coastline@1)
```

`build_tiles` declares the OSM node of each tile whose snapshot the store does not hold
(`declare(..., fetch_osm=True)`), as the `osm` input of its vector stage and coastline node: the
graph waits for the tile's own download only. The nodes queue on the `overpass` lane, one tile at a
time, apart from the imagery's network slot; the Overpass client already spreads a tile's layers
over the mirrors (`osm-source.md` 4) and reports each layer and its download rate
(`+43+005: 2/4 OSM layers (1.4 MB/s)`). A relief that downloads (not X-Plane 12's) reports each file received and its rate the same way
(`+43+005: elevation, 2 file(s) (3.1 MB/s)`, `dem_download_message`). `osm_plan` decides before
anything runs which tiles have a
snapshot already (their `osm` rows are announced as reused, the snapshot is the input) and which
download. `run_osm_phase` runs the same nodes on a scheduler of their own, for a caller that wants
the data alone.

* artefact (`kind=dir`): `osm/<folder>/<tile>/<tile>_<layer>.osm.json.zst` + `.meta.json` (a
  `SnapshotStore` rooted at the artefact) and `snapshot.json`
  `{format, tile, road_level, label, layers: {name: digest}}`; `label` is `snapshot_label(...)`
  (`osm-<12 hex>`, content-based, `osm-source.md` 5), shown in the report;
* params: `tile`, `road_level` (the layer list depends on it, `osm-source.md` 3) and `refresh` (a
  free string; changing it is how a user asks for fresh data). No inputs: it is a graph root, so a
  second build **hits and sends nothing**.

When the node runs: `--osm-fetch` (default) fetches **only what is missing**. A tile whose snapshot
is in the store is not downloaded again: the rule is a root, so its key is a pure function of its
params, and the artefact is looked up by key. `--no-osm-fetch` never declares the node;
`--osm-refresh LABEL` downloads again by changing the key, and the fresh snapshot is what the vector
stage reads.

A failed download is reported with `status = "failed"` in the OSM report (it used to say
`"skipped"`), and on the tile's `osm` row, the root cause of the nodes that read OSM data
(section 4).

The main run's SIGINT handler covers the downloads; `run_osm_phase` installs one of its own
(`_run_phase0`), the same shape: the first Ctrl-C cancels its scheduler and it returns, instead of
letting a `KeyboardInterrupt` escape while the download kept going. The OSM node itself takes the
token (`OsmJob.cancel` -> `OverpassClient.fetch_tile`, polled every 0.1 s) and its `timeout_s`
(whole tile, `NET_TIMEOUT`).

No Ortho4XP folder takes part (decision 0010): its OSM cache is not a source, and the download
writes nothing outside the store.

### 8.4 The coastline input of the mesh

`orthostudio.mesh` refines the weight map within `coast_curv_ext` km of every OSM coastline node
(`mesh-build.md` 3). Without them the map loses its coastal term and the mesh is not Ortho4XP's --
measured on +43+005, same `.node`/`.poly`/`.alt`: 1 164 344 triangles instead of 1 197 758.
`orthostudio.coastline@1` (`kind=file`, `io`) publishes the `coastline.npz` the mesh rule reads from
its one input, `osm`: the `coastline` snapshot of the tile's OSM node or store, the one the vector stage reads
(`(lon, lat)` of every node, first occurrence order, deduplicated, as `O4_OSM_Utils.py:60-103`
reads Ortho4XP's cache).

The mesh and the vector stage therefore never read two different extracts of the coast. There
always is one: the vector stage demands the `coastline` layer at every road level, so a tile
without any fails at its vector stage. An estimate declares the graph without downloading: the
coastline then reads the placeholder of the layers still to fetch, like the vector stage.

Ortho4XP preferred a coastline the user curated by hand in its `OSM_data/` folder
(`O4_Mesh_Utils.py:166-186`). OrthoStudio XP read that file too until decision 0010; a curated
coastline now has no source.

### 8.5 Keys: what changes what

* the `osm` input of the vector node is the phase-0 snapshot, which must hold *every* layer of the
  tile's `road_level`; when it does not, `OSM_LAYER_UNAVAILABLE` names the missing layers and
  points at `--osm-refresh` (or at `--no-osm-fetch` when the build forbade the download).
  `patches` stays absent: Ortho4XP's `Patches/` folder is not read (decision 0010), and the stage
  builds the family only from a folder it is given; `airports` stays absent (the stage builds the
  aerodromes from the `airports` layer, `airports-integration.md` 1);
* `road_level`, `road_banking_limit`, `lane_width`, `max_levelled_segs`, `water_simplification`,
  `min_area`, `max_area`, `clean_bad_geometries`, `mesh_zl`, `apt_smoothing_pix`,
  `exact_grid_order` -> `orthostudio.vectors` and everything below it (`road_level` also changes
  the layers `orthostudio.osm` downloads);
* `custom_dem`, `fill_nodata` -> `orthostudio.dem`, then through its digest the vector stage and
  the mesh. They are nobody else's params (`mesh-build.md` 9);
* `curvature_tol`, `apt_curv_*`, `coast_curv_*`, `limit_tris`, `min_angle`, `sea_smoothing_mode`,
  `water_smoothing`, `iterate`, `skip_multiples_of_ten`, `water_in_set_order` -> `orthostudio.mesh` and
  everything below it; `dem`, `vectors`, `osm` and `coastline` hit;
* `mask_zl`, `masks_width`, `masking_mode`, `use_masks_for_inland`, `ratio_water`,
  `distance_masks_too` -> `orthostudio.masks`, then the DSF and the textures (the coastal ones only,
  inside the node).

The neighbour meshes of the masks node are resolved as in 2.1: the node of the batch, else the most
recently used `orthostudio.mesh` artefact of the store that holds the neighbour.

### 8.6 RAM, pools and the masks workers

`orthostudio.dem` is declared `net` (it may download elevation cells; 0.6 GB), `orthostudio.osm`
`net` (0.3 GB), `orthostudio.coastline` `io` (0.2 GB), `orthostudio.vectors` `subprocess` (1.2 GB),
`orthostudio.mesh` `subprocess` (0.9 GB: the Python side plus the Triangle4XP child) and
`orthostudio.masks` `subprocess` (its own process pool inside). `cpu` is deliberately not used for
them: the `cpu` pool is a `spawn` process pool that receives the node's `run` callable by pickle, and
the stage rules need a bound job (`dem_job`, `masks_job`, the build environment) or spawn their own
pool. The vector node found that the hard way: declared `cpu`, every real build failed with a
`PicklingError` before its first layer was read.

The masks node's `ram_mb` is `300 + 800 x workers` MB, and **the same `workers` is given to the
rule**: `_masks_run` binds `MasksJob(workers=masks_workers(env), cancel=...)`, the rule passes it to
`build_masks`, and `worker_count` honours it, so the declared budget describes the pool that starts.
`masks_workers(env)` is `$OSXP_MASKS_WORKERS` when it is a positive integer, else
`min(8, --workers)`; the variable has one parser (`masks.build.env_workers`, `masks-build.md` 6.5)
and `0`, a negative value or junk mean *unset*. The rule's own decorator declares 1100 MB, the honest
cost of a node built without `declare()` (base + one worker).

Cancellation reaches every stage: `orthostudio.osm` (token and timeout to the Overpass client),
`orthostudio.dem` (`EnsureOptions.cancel`, polled between the nine cells of the block),
`orthostudio.mesh` (the sidecar is polled and killed), `orthostudio.masks` (raises between cells
and cancels the pending ones), `orthostudio.coastline` (a single small read). Every one of them
*raises* `SYS_CANCELLED` rather than returning early, so the store commits nothing.

The elevation cells are read from and downloaded into `$OSXP_ELEVATION_DIR` /
`<data folder>/elevation` (`dem.md` 9.4); the file names are Ortho4XP's.

### 8.7 Acceptance tests

1. every tile declares `dem`, `vectors`, `coastline`, `mesh`, `masks` with the `orthostudio.*`
   rules; the mesh's `dem` input is the vectors node with `--dem vectors`, the `orthostudio.dem`
   node with `--dem native` (`test_p3_native.py`);
2. a missing Triangle4XP is `SYS_TOOL_MISSING`, `iterate = 1`, `masks_use_DEM_too` and
   `masks_custom_extent` are `CFG_VALUE_INVALID`, all before an Overpass request is sent
   (`test_review4_robustesse_p3.py`, `test_review5_robustesse_erreurs.py`);
3. rebuild rules: nothing changed -> every node hits; `masks_width` -> masks, dsf, textures, pack;
   `curvature_tol` -> mesh, masks, dsf, textures, pack, `vectors` hits;
4. a tile without any OSM data builds with `--osm-fetch`, and a tile without data and without
   `--osm-fetch` is refused with `OSM_LAYER_UNAVAILABLE` naming the `coastline` layer among the
   missing ones; an Ortho4XP OSM cache is never a source (`test_p3_native.py`,
   `test_review4_robustesse_p3.py`);
5. the fidelity targets of decision 0008 were held on the three reference tiles by the oracle tests
   of each stage until decision 0010 removed them (`p4-airports.md` 7).

### 8.8 The DSF reads the text mesh, not the npz

`orthostudio.mesh` publishes `mesh.npz` with the **full-precision doubles** Triangle4XP returned;
the text `Data<tile>.mesh` keeps 15 significant digits of `z/100000`. On +43+005 that is 8.9e-16 deg
on 53 225 of 603 649 longitudes and 5.0e-11 m on 451 285 of 603 649 elevations -- enough for
elevations to land on the other side of a u16 rounding boundary. Re-measured byte by byte in review
4: the DSF built from the npz has the **same size** (32 442 870 bytes) and differs from Ortho4XP's
on **23 bytes** spread over several pools -- the first is `GEOD/POOL[5]`, payload offset 38 706,
file offsets 591 194, 2 513 916, 6 588 280, 7 916 314, 7 916 624. (The earlier "exactly one byte"
was `compare_dsf.first_difference`, which names the first difference, not their count.)

`tile.dsf` therefore reads `Data<tile>.mesh` when the mesh artefact has one (what Ortho4XP's step
3 does) and falls back to `mesh.npz` otherwise. Cost: 0.32 s instead of 0.006 s per DSF.
Normals are unaffected: `_normals_as_ortho4xp` rounds both representations to the same value on
all 1 207 298 components.

This is a *transitional* choice, and the decision it hides belongs to the mesh and DSF
chantiers together: either `orthostudio.mesh` writes an npz that is a faithful twin of the text file
(and the npz becomes usable again, saving 0.3 s per DSF), or the project accepts full
precision and gives up byte-identity on those 23 bytes. Until then the text file is authoritative
and the npz is the fast path for consumers that do not need Ortho4XP's exact output (the masks
stage reads it).
