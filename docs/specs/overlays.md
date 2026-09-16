# Overlays: the `yOrthoStudio_Overlays` pack extracted from the Global Scenery DSF

Status: P2, written before the code of `src/orthostudio/overlays/` (tests
`tests/test_overlays_*.py`). Origin:
Ortho4XP `src/O4_Overlay_Utils.py:1-252` (`build_overlay`, "Step 4"), called from
`O4_Tile_Utils.py:270-271`, parameters `O4_Config_Utils.py:93-115`. Decision: **keep the rule, fix
the mechanics** (section 6).

## 1. The rule in plain language

An ortho tile replaces the base mesh DSF of X-Plane, and with it everything the base DSF drew on top
of its mesh: forests, autogen blocks, facades, roads, power lines, beaches. Step 4 of Ortho4XP gives
them back as an *overlay* scenery pack: the Global Scenery DSF of the tile is decompressed,
converted to text with DSFTool, stripped of its mesh (terrain patches and rasters), stripped of the
polygon and road types the user excludes, marked `sim/overlay 1`, converted back to binary and
written as `yOrtho4XP_Overlays/Earth nav data/<10x10>/<tile>.dsf`. One overlay DSF per tile; the
pack is shared by every tile. OrthoStudio XP writes the same pack under its own name,
`yOrthoStudio_Overlays` (decision 0011).

## 2. Origin in Ortho4XP, line by line (`O4_Overlay_Utils.py`)

| Lines | What Ortho4XP does | OrthoStudio XP |
|---|---|---|
| 11-12 | module globals `ovl_exclude_pol = [0]`, `ovl_exclude_net = []` (cfg `O4_Config_Utils.py:93-108`) | `OverlayExclusions` (section 4) |
| 15, 40-52 | source = `<custom_overlay_src>/Earth nav data/<10x10>/<tile>.dsf`; absent -> message, `return 0` | `overlay_source_path`; absent -> `DSF_OVERLAY_SOURCE_MISSING` |
| 17-25 | `7z` from `PATH` (macOS, Linux) or `Utils/win/7z.exe`; `Utils/<os>/DSFTool` | py7zr in-process; DSFTool path is an explicit argument (`find_dsftool` helps) |
| 53-64 | copy of the source into `tmp/<tile>.dsf` | no copy: a plain DSF is read in place, a 7z one is extracted into the work directory |
| 65-79 | first two bytes `7z` -> rename `.7z`, `os.system("7z e -o<tmp> ...")`, return code ignored | py7zr `extract`; exactly one member expected; failures -> `DSF_SOURCE_DECOMPRESS_FAILED`; magic `XPLNEDSF` checked -> `DSF_SOURCE_CORRUPTED` |
| 80-100 | `DSFTool -dsf2text tmp/<tile>.dsf tmp/<tile>_tmp_dsf.txt`, stdout echoed line by line, then `if fingers_crossed.returncode` **without `wait()`**: `returncode` is `None`, a crash is never seen | `subprocess.run` with `returncode` checked **and** the trailer `# Result code: N` of the text checked (DSFTool 2.3 exits 0 with `Result code: 1` on an undecompressed 7z) -> `DSF_OVERLAY_TOOL_FAILED` |
| 101-116 | output text starts with `PROPERTY sim/overlay 1` | same, first line |
| 121-131 | kept verbatim: lines containing `PROPERTY`, `POLYGON_DEF`, `NETWORK_DEF` | same three, matched at line start (section 3) |
| 124-129 | `POLYGON_DEF` lines are numbered in order of appearance (`pol_dict[index] = name`), which is the DSF definition index | `polygon_defs` list |
| 132-158 | `BEGIN_POLYGON <idx> ...`: the exclusion set is resolved once, at the first polygon; a polygon whose `idx` is excluded is skipped up to and including `END_POLYGON`, otherwise the block is copied verbatim | same block rule, same grammar (section 4) |
| 159-172 | `BEGIN_SEGMENT <net> <road_type> ...`: the block is skipped when `road_type in ovl_exclude_net`, or when `""` or `"*"` is in the list | same |
| everything else | dropped by falling through: header (`A`, `800`, `DSF2TEXT`), comments (`# pool ...`), `TERRAIN_DEF`, `DIVISIONS`, `HEIGHTS`, `RASTER_DEF`, `RASTER_DATA`, `BEGIN_PATCH ... END_PATCH`, **`OBJECT_DEF` and `OBJECT` commands** | same, except objects are kept (section 6, D3) |
| 176-197 | `DSFTool -text2dsf` on the filtered text, stdout printed, **return code never read** | `subprocess.run`, `returncode` checked, output magic checked |
| 198-217 | `yOrtho4XP_Overlays/Earth nav data/<10x10>/` created, final DSF copied there (`shutil.copy`, non-atomic) | `.tmp` + `os.replace` (`fsutil.atomic_link_or_copy`) |
| 218-250 | temporary files removed one by one; the raster sidecars other than `elevation` and `sea_level` (`soundscape`, `fal1/2`, `spr1/2`, `sum1/2`, `win1/2`) are **left behind** in `tmp/` | one work directory per build, removed whole |
| 29-31, 251 | `UI.is_working` gate, timer | `OverlayResult.stats` timings; no global state |

What the source looks like (X-Plane 12 Global Scenery, `+43+005`, DSFTool 2.3.0-b2): 11.5 MB
7z (LZMA, one member `+43+005.dsf`, 24.7 MB), text 169.7 MB / 2 801 507 lines: 7 `PROPERTY`,
190 `TERRAIN_DEF`, 175 `POLYGON_DEF` (index 0 = `lib/g12/beaches.bch`, then `.for` forests,
`.lin` airport borders, `.ags` autogen, `.fac` facades), 1 `NETWORK_DEF` (`lib/g10/roads_EU.net`,
`NETWORK_DEF` index 0, 12 road subtypes in use), 0 `OBJECT_DEF`, 11 `RASTER_DEF`, 12 395
patches (1 087 468 vertices), 38 576 polygons (363 of type 0), 156 258 road segments. Five
other Global Scenery tiles sampled (`+43+004`, `+48+002`, `+51-001`, `+40-074`, `+34-119`)
have the same 7 properties, 1 network definition and 0 objects.

## 3. What is kept, what is dropped (text filter)

The DSFTool text is processed as one stream of lines. Each line is classified by its first
token; the classes and their fate:

| First token | Fate | Note |
|---|---|---|
| `PROPERTY` | kept | the whole line, source order, after the injected `PROPERTY sim/overlay 1`; a source `sim/overlay` line is dropped (D4) |
| `POLYGON_DEF` | kept | its index is its rank among `POLYGON_DEF` lines (0-based), which is the DSF index used by `BEGIN_POLYGON` |
| `NETWORK_DEF` | kept | always, even when every segment is excluded (Ortho4XP does the same) |
| `OBJECT_DEF` | kept (D3) | Ortho4XP drops it |
| `BEGIN_POLYGON idx ...` up to and including `END_POLYGON` | kept verbatim unless `idx` is excluded | `BEGIN_WINDING`, `POLYGON_POINT`, `END_WINDING` inside are copied without inspection |
| `BEGIN_SEGMENT net road_type node lon lat z` up to and including `END_SEGMENT ...` | kept verbatim unless `road_type` is excluded or networks are excluded as a whole | `SHAPE_POINT` inside copied without inspection |
| `OBJECT ...`, `OBJECT_MSL ...` | kept (D3) | Ortho4XP drops them |
| `BEGIN_PATCH`, `END_PATCH` | dropped | the mesh; not a block, see below |
| `BEGIN_PRIMITIVE` up to and including `END_PRIMITIVE` | dropped | one strip/fan command, `PATCH_VERTEX` lines inside skipped without inspection |
| `TERRAIN_DEF`, `RASTER_DEF`, `RASTER_DATA`, `DIVISIONS`, `HEIGHTS` | dropped | mesh definitions and rasters |
| `A`, `800 ...`, `DSF2TEXT`, `#` comments, blank | dropped | header and DSFTool annotations |
| anything else | dropped | unknown commands stay out, as in Ortho4XP |

The filter works on bytes: definition names are matched as UTF-8 bytes, nothing is decoded
or re-encoded, so a kept line is byte-identical to its source line. Blocks that are one DSF
command (`BEGIN_POLYGON`...`END_POLYGON`, `BEGIN_SEGMENT`...`END_SEGMENT`,
`BEGIN_PRIMITIVE`...`END_PRIMITIVE`) are located with `bytes.find` and copied or skipped as a
whole. A **patch is not a block**: DSFTool closes a patch only when the next one begins or at
the end of the file, so on `+43+005` all 156 258 road segments sit between the last
`BEGIN_PATCH` and the final `END_PATCH`; `BEGIN_PATCH`/`END_PATCH` lines are dropped one by
one and whatever stands between them is classified normally (Ortho4XP's line loop has the same
property by construction). `\r\n` endings (a Windows
DSFTool) are normalised to `\n` before filtering. A block that reaches the end of the file
without its terminator, or a `BEGIN_POLYGON`/`BEGIN_SEGMENT` whose index is not an integer,
raises `DSF_SOURCE_CORRUPTED` (Ortho4XP writes the truncated block and lets DSFTool fail later).

## 4. Exclusions (`OverlayExclusions`)

```
class OverlayExclusions(RuleParams):        # frozen pydantic, extra="forbid"
    ovl_exclude_pol: list[str | int] = ["lib/g12/beaches.bch", "lib/g8/beaches.bch"]
    ovl_exclude_net: list[str | int] = []
    keep_objects: bool = True
```

Grammar of `ovl_exclude_pol`, ported unchanged from `O4_Overlay_Utils.py:133-149` and the hint
of `O4_Config_Utils.py:97`:

* an `int` is a `POLYGON_DEF` index (0-based, order of appearance);
* a `str` is a **substring** of the definition name: every definition containing it is
  excluded (`".for"` excludes every forest, `"lib/g12/beaches.bch"` the XP12 beaches);
* a `str` starting with `!` inverts the match: `"!.for"` excludes everything **but** the
  forests (`!` alone excludes nothing);
* items are united; the empty string `""` matches every name (kept as is: it is the grammar,
  not a bug, and `[""]` is the documented way to drop every polygon).

`resolve_polygon_exclusions(defs, items) -> frozenset[int]` is that function.

Grammar of `ovl_exclude_net` (`O4_Overlay_Utils.py:159-165`): an `int` is a road type (the
second number of `BEGIN_SEGMENT`, the subtype index in the `.net` file: 22001 is the XP11
power line); `""` or `"*"` excludes every segment. Road types have no name inside the DSF (the
names live in `roads.net`), so there is no by-name form; any other string is rejected at
validation (`ValueError`), where Ortho4XP ignored it silently.

**`[0]`, the Ortho4XP default, is the beaches.** Verified on `+43+005` (and on the five sampled
tiles) `POLYGON_DEF` 0 is `lib/g12/beaches.bch`, exported by
`Resources/default scenery/1200 beaches/library.txt` (`coastlines.bch`); the XP10/11 Global Scenery
equivalent is `lib/g8/beaches.bch` (`900 beaches/library.txt`, `beach.bch`). Beaches are excluded
because the orthophoto already shows the shore and the `.bch` sand strip would draw over it. An
index depends on the order the scenery creator wrote the definitions, a name does not, so the
**OrthoStudio XP default is by name**: `["lib/g12/beaches.bch", "lib/g8/beaches.bch"]` (both
spellings, so that XP11 Global Scenery and HD Mesh sources keep the same behaviour). Indices stay
accepted for compatibility; `exclusions_by_name(exclusions, defs)` rewrites the indices of an
Ortho4XP configuration into the exact names found in a given source.

## 5. Inputs and outputs (`src/orthostudio/overlays/`)

```
def overlay_source_path(global_scenery_dir, tile) -> Path
    # <dir>/Earth nav data/<10x10>/<tile>.dsf ; <dir> is the folder above "Earth nav data"
    # (Ortho4XP's custom_overlay_src); an X-Plane root is accepted and resolved to
    # Global Scenery/X-Plane 12 Global Scenery
def materialize_source(src, workdir, *, tile) -> Path     # plain DSF in place, 7z extracted
def run_dsftool(dsftool, mode, src, dst, *, timeout_s=600.0) -> DsfToolRun(returncode, seconds, stdout)
def find_dsftool(*, own_dir=None) -> Path | None          # native/dsftool/{mac,win,lin}/DSFTool[.exe]
def filter_dsf_text(src, dst, exclusions) -> FilterStats
def build_overlay_detailed(global_scenery_dir, tile, exclusions, *, dsftool, out_root, workdir,
                           timeout_s=600.0, keep_workdir=False) -> OverlayResult(path, stats)
def build_overlay(global_scenery_dir, tile, exclusions, *, dsftool, out_root, workdir) -> Path
    # writes <out_root>/yOrthoStudio_Overlays/Earth nav data/<10x10>/<tile>.dsf
def overlay_dsf_path(out_root, tile) -> Path
```

`tile` is `orthostudio.model.TileRef` when that module exists (P2 `sched` work), else the identical
`NamedTuple(lat, lon)` defined in `orthostudio.overlays._tileref` (`.name` -> `+43+005`, `.folder`
-> `+40+000`); the code only reads `lat` and `lon`.

`FilterStats`: `polygon_defs`, `network_defs`, `object_defs` (names, in order),
`excluded_polygon_indices`, `polygons_kept/dropped`, `segments_kept/dropped`,
`objects_kept`, `properties`, `bytes_in/out`, `lines_in`. `OverlayResult.stats` adds the
source path and size, whether it was 7z, the extracted size, the text size, the output size,
and the seconds of each of the four steps (`extract`, `dsf2text`, `filter`, `text2dsf`).

Work files live in a fresh subdirectory of `workdir` (`<tile>-<pid>-<rand>/`) removed at the
end, success or failure, unless `keep_workdir=True`; DSFTool's stdout is kept in the stats and
in the error context of `DSF_OVERLAY_TOOL_FAILED`.

## 6. Decisions

Kept as is: the source location, the three kept line classes, the block copy rule, the
exclusion grammar for polygons and networks, the injected `sim/overlay 1`, the source
`sim/creation_agent` (X-Plane's, not Ortho4XP's), the pack name and layout, one DSF per tile.

Fixed (wanted differences):

* **D1** py7zr replaces the `7z` binary and `os.system`: no `PATH` dependency on macOS/Linux,
  a decompression error is an `OsxpError` instead of a silent `getsize` crash later
  (`docs/specs/errors.md`, `DSF_SOURCE_DECOMPRESS_FAILED`).
* **D2** DSFTool's return code is checked on both conversions (Ortho4XP read `returncode` without
  `wait()`, so it was always `None`), and the `# Result code:` trailer of `dsf2text` is
  checked too; the outputs are checked for existence and DSF magic. A failure is
  `DSF_OVERLAY_TOOL_FAILED` with the command, the exit code and the tail of DSFTool's output.
* **D3** `OBJECT_DEF` and `OBJECT`/`OBJECT_MSL` lines are kept (`keep_objects=True`). Ortho4XP
  drops them by omission; an object in a base DSF is exactly the kind of content the overlay is
  meant to preserve. XP12 Global Scenery has no objects (0 in the six tiles sampled), so the
  oracle is unaffected; `keep_objects=False` gives the Ortho4XP behaviour, statement for
  statement (`test_review2_fidelite_overlay.py` transcribes `O4_Overlay_Utils.py:101-172`
  and compares the two for six exclusion grammars). Since the P2a correction round the
  setting is configurable per build (`osxp build --set keep_objects=False`,
  `BuildSpec.keep_objects`) and shows in the overlay node's recipe (`osxp why`); the default
  stays True. The Ortho4XP `ovl_exclude_pol` / `ovl_exclude_net` reach the build the same way
  (`--set`, `config`, spec fields) and default to OrthoStudio XP's values (`pipeline-build.md` 1,
  7); an `Ortho4XP.cfg` is not read for them (decision 0010).
* **D4** a `PROPERTY sim/overlay` line present in the source is not copied (Ortho4XP would emit
  the property twice); `sim/overlay 1` is always the first line.
* **D5** lines are classified by their first token, not by substring (`"PROPERTY" in line`
  also matched comment lines and would have copied a `# ... PROPERTY ...` annotation).
* **D6** temporary files: one work directory, removed whole; nothing is left behind (Ortho4XP
  leaves nine `.raw` sidecars per tile in `tmp/`). Output written by `.tmp` + rename.
* **D7** the default exclusion is by name, not by index (section 4).
* **D8** no copy of a plain DSF source: DSFTool reads it where it is (read-only).

Not carried over: the `UI.is_working` gate, the verbosity-driven listing of definitions
(`FilterStats.polygon_defs` gives the same information), the `tmp/` naming.

## 7. Acceptance

* **Oracle, byte-identical** (`tests/test_overlays_oracle.py`, removed with decision 0010): the Ortho4XP
  `OVL.build_overlay(43, 5)` is run in a subprocess with the legacy venv (`OSXP_LEGACY_DIR`,
  `FNAMES.Tmp_dir`/`Overlay_dir` redirected into `tmp_path`, `custom_overlay_src` = the X-Plane
  12 Global Scenery, `ovl_exclude_pol=[0]`), then `build_overlay` with
  `OverlayExclusions(ovl_exclude_pol=[0])` and with the by-name default; both OrthoStudio XP DSFs
  must be byte-identical to the Ortho4XP DSF. On a mismatch the test converts both files with
  `dsf2text` and reports the first differing line. Skipped when the Global Scenery tile or the
  legacy venv is absent.
* **Determinism**: two OrthoStudio XP builds of the same source give the same bytes (DSFTool
  `text2dsf` is deterministic: three Ortho4XP runs gave one MD5, `8116be88...`).
* **Filter, synthetic** (`tests/test_overlays_filter.py`): a hand-written DSFTool text with
  every line class checks the kept/dropped table of section 3, the exclusion grammar of
  section 4 (index, substring, `!`, `""`, `"*"`, union, road type), `keep_objects`, `\r\n`
  input, the `sim/overlay` de-duplication, the unterminated-block error, and that kept lines
  are byte-identical to their source.
* **Mechanics** (`tests/test_overlays_build.py`): 7z member count and magic checks, missing
  source, DSFTool failure (a fake tool script exiting 1, a fake tool writing `Result code: 1`),
  timeout, work directory removal, atomic output, X-Plane root resolution, `find_dsftool`.

## 8. Measurements (M4 Pro, `nice -n 10`, load average 2.6-3.1, warm file cache)

| | Ortho4XP (`build_overlay`, 3 runs) | OrthoStudio XP |
|---|---|---|
| 7z decompression | `7z e` subprocess (plus a 25 MB copy), inside the 3.0 s | py7zr `extract` 0.36 s |
| `dsf2text` | inside the 3.0 s (1.28 s measured alone) | 1.30-1.42 s |
| text filter | Python text-mode line loop, 2.8 M iterations | 0.24-0.27 s (bytes, block copies) |
| `text2dsf` | inside the 3.0 s | 1.04-1.05 s |
| total | **3.04 / 3.07 / 3.10 s** (3.19 s inside the oracle test) | **2.94 / 2.96 / 2.97 s** (index) and 2.98 / 2.98 / 2.98 s (by name), 3.01-3.07 s inside the oracle test |
| output | 10 325 765 bytes, MD5 `8116be88...` | identical bytes, 3 + 3 + 3 runs |

Both write the same DSF; OrthoStudio XP is 3-5 % faster, which is the copy and the `7z` process that
disappear. The floor is DSFTool itself: 2.35 s of the 2.96 s are the two conversions, so the 1.2 s
the plan of the rewrite hoped for is not reachable with DSFTool in the loop. The gains that remain
are elsewhere: the scheduler runs the overlay of a tile in its subprocess slot while the tile's own
DSF and textures are built, so its wall time hides behind them; and a binary filter (no text round
trip, ~0.5 s expected) is P6 work ("filtre overlays binaire"). Memory: the filter reads the 170 MB
text at once and writes 60 MB of slices (peak about 250 MB per tile).
