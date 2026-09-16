# Pipeline: textures of one tile (download, assemble, imprint, encode, `.ter`)

Status: P1, written before the code of `src/orthostudio/pipeline/` (tests
`tests/test_pipeline_*.py`, `tests/test_cli_doctor.py`). Measurements: `docs/benchmarks/p1-imagery.md`.
The P1 command `osxp textures --from-legacy` and the validation against Ortho4XP (sections 9 and 11)
were removed by decision 0010: the textures are built by `osxp build` (`pipeline-build.md` 2.2).
Amended after the
two P1 reviews (fidelity, robustness): the amended rules are marked **(review)** and tested by
`tests/test_review_*.py` and `tests/test_fixes_p1.py`. Amended again after a user build lost three
tiles to five timed-out requests out of 212 000: section 4.1 (the second pass), marked **(review
2026-09-13)** and tested by `tests/test_pipeline_second_pass.py`.

Origin in Ortho4XP: `O4_DSF_Utils.py:640-760` (which textures and terrain kinds a tile needs, the
mask decision per texture, the rebuild heuristics), `O4_Tile_Utils.py:21-40` and
`O4_Imagery_Utils.py:1329-1368, 1572-1582` (download queue, 16 threads per texture, JPEG cache),
`O4_Imagery_Utils.py:2291-2453` (`convert_texture`: imprint, PNG round trip, nvcompress). Decision:
**replaced** by a graph-driven pipeline over the four P1 modules (`orthostudio.imagery`,
`orthostudio.net`, `orthostudio.textures`, `orthostudio.tilefiles`); the domain rules stay in
those modules and their specs, this document only fixes how they are composed.

## 1. The rule in plain language

Given the list of textures a tile needs (each with its terrain kinds) the pipeline produces,
in an output directory laid out like an Ortho4XP tile, `textures/<name>.dds` and
`terrain/<name>[_kind].ter` for every texture, downloading only the tiles it does not have,
encoding only the textures whose recipe is not already in the artefact store, and using every
core for the encoding while the network is busy.

```
plan ──► fetch missing tiles (Fetcher, AIMD, hedging) ──► container complete ──► parents? ──►
  key = blake3(rule, params, digest(chunks), digest(mask), digest(parents))
  ├─ in store  → hit: hard-link the DDS into textures/, write the .ter files
  └─ else      → worker (ProcessPoolExecutor): assemble → mask → imprint → mips → BC1/BC3
                 → DDS committed to the store → hard-link → .ter files
```

## 2. Inputs and outputs

`build_textures(spec: TexturesSpec) -> TexturesReport` (`src/orthostudio/pipeline/textures.py`).

| Field of `TexturesSpec` | Meaning |
|---|---|
| `lat`, `lon` | tile (integers, south-west corner) |
| `provider` | an `orthostudio.imagery.Provider` (URL grammar, placeholder rule, `max_in_flight`) |
| `zl` | zoom level of the textures |
| `jobs` | `TextureJob(texture: TextureId, kinds: tuple[TerKind, ...])`, from `tilefiles.list_textures` in P1, from the OrthoStudio XP mesh stage later. **(review)** A texture listed several times is one job, its kinds merged in first-seen order (`merge_jobs`): one download, one encode, one outcome |
| `chunks_root` | root of the `ChunkStore` (`<root>/<provider>/<zl>/<y>_<x>.chunks`) and of the parent cache (section 5); default `<data folder>/chunks` |
| `store_root` | root of the artefact `Store` (`docs/specs/graph-keys.md`); default `<data folder>/store` |
| `out_dir` | tile directory: `textures/` and `terrain/` are created inside |
| `mask_lookup`, `mask_zl` | `(m_til_x, m_til_y) -> Path | None` over the masks at `mask_zl` (`tilefiles.masks_index` in P1). **(review)** `mask_zl` is the single source: the pipeline copies it into `ter_params.mask_zl`, so the crop window and the `LOAD_CENTER_BORDER` of the `.ter` cannot disagree |
| `ter_params`, `sea_texture_blur`, `clean_halo` | `.ter` and imprint parameters (`textures-ter.md`, `textures-imprint.md`); `ter_params.mask_zl` is overridden by `mask_zl` (above) |
| `workers` | encoding processes, default `cpu_count() - 2`; `0` runs the encoding inline (tests) |
| `encoder`, `mip_mode`, `refine_passes` | `encode_dds` arguments (`textures-dds.md`); `auto` resolves to the first available encoder **before** the run, so that the key names the real encoder |
| `max_in_flight`, `start_in_flight`, `hedge_after_s`, `timeout_s`, `max_attempts` | `Fetcher` tuning; `max_in_flight` defaults to the provider's (128 for BI), `start_in_flight` to that same ceiling (from 64, one more per round, Esri Clarity took 90 s and half of a tile's pieces to reach its 192, 2026-09-15; every ceiling of the registry was measured starting at it), hedge 1.0 s (about 5 x p90 on the measured line; `net-download.md` R3) |
| `second_pass_pauses_s` | **(review 2026-09-13)** pause before each round of the second pass, one round per value, default `(5, 15, 45)` s; `()` turns the second pass off (section 4.1) |
| `second_pass_max_s` | **(second review, 2026-09-13)** wall time of the whole second pass, pauses, probes and rounds included, default 180 s (section 4.1) |
| `parent_levels` | parent fallback depth, 5 (`textures-assemble.md` section 3) |
| `link` | hard-link the store artefact into `textures/` (copy when the file systems differ or `link=False`) |
| `progress`, `cancel` | progress callback (default: stderr) and a `threading.Event`; `build_textures` also turns the first `SIGINT` into that event (section 8) |

**The data folder** (2026-09-15, a user asked to keep the downloads on an external disk). The
default roots are those of `orthostudio.home`: `<data folder>/chunks`, `/store`, `/tiles`,
`/work`, `/mapcache`, `/osm`, `/elevation` and `/dem`, where the data folder is `$OSXP_DATA_DIR`,
else the setting `essential.data_dir`, else `$OSXP_HOME`. The settings, the library, the jobs, the
zones, the user's sources and the airport index stay in `$OSXP_HOME`. The store and the tiles share
their textures through hard links (`link` above), so a folder is accepted only on a disk that makes
them (`check_data_dir`: exFAT and FAT32 would write each texture three times) and all these roots
live on the same disk; nothing is moved when the setting changes. A data folder that is not there
(its disk unplugged) is never created: `require_data_root` refuses a plan, a job and `BuildEnv`'s
default work folder with `CFG_DATA_DIR_MISSING`, and the map proxy serves without its cache.

`TexturesReport`: one `TextureOutcome` per job (`status` in `built | hit | incomplete |
failed | cancelled`, `fmt`, key, digest, fetched / cached / placeholder / 404 / error /
retried tile counts, fallback, unfilled and corrupted chunk counts, seconds, the coded
`error` that ended it when it is not `built` or `hit`), counters (the same per run, plus
`tiles_retried`, `chunks_corrupted`), network totals (requests, bytes, hedges, retries,
req/s), stage timings (plan, fetch, encode wall, total), the list of coded errors and free
`notes` (the CLI adds what else the Ortho4XP build references). The CLI writes it as
`osxp_textures.json` in the output directory. **(review 2026-09-13)** Also: per outcome
`second_pass`, `recovered` and `failures`; counters `chunks_second_pass`, `chunks_recovered`,
`second_pass_rounds`, `second_pass_capped`; timing `second_pass_s` (section 4.1). `tiles_error`
counts the chunks left `ERROR` at the end of the run, and `req_per_s_mean` is computed over the
fetch time without `second_pass_s`.

## 3. Planning (per texture, before any request)

1. `ChunkStore.read(t)`: absent → new container, all 256 tiles to fetch; present and
   `complete()` → nothing to fetch (`MISSING` entries of a complete container are 404
   answers, not holes); present with `ERROR` entries → only those are fetched again
   (the "retry missing textures" of the plan: no flag, it is the default behaviour).
2. Mask decision. Ortho4XP checks the mask only for textures that carry sea triangles
   (`O4_DSF_Utils.py:669-690`: `MASK.needs_mask` is called for `tri_type == 2`, and a texture
   passing it gets a `_sea[_overlay]` terrain). The P1 pipeline therefore imprints a mask
   **iff** the texture has a sea kind (`SEA` or `SEA_OVERLAY`) **and** the raw crop of the
   `mask_zl` mask covering it has a maximum above 30 (`needs_mask(mask_crop_raw(...))`, the
   one rule of `textures-imprint.md` section 4). **(review)** A sea kind **without a usable
   mask** (no lookup, `zl < mask_zl`, no mask file for the cell, or a crop at most 30) is
   **not built**: a `_sea[_overlay]` terrain is `WET` and, over a DXT1 texture without alpha,
   an opaque overlay that hides X-Plane's water, a situation Ortho4XP never produces (there,
   `needs_mask` False sends the triangles to `terrain_Water` and no `.ter` exists). The
   texture is `failed` with `MASK_STALE` (remedy: rebuild the masks of the tile), nothing is
   downloaded for it, its `.ter` files are still written because the DSF references them, no DDS
   is published, and the run ends with `TEX_MISSING`. With `imprint_masks_to_dds = False` the
   **raw** crop (`4096 // 2**(zl - mask_zl)` px, exactly what Ortho4XP saves from `needs_mask`'s
   `small_img`, `O4_DSF_Utils.py:737-742`, and the size the `.ter` declares in
   `LOAD_CENTER_BORDER`) is kept for publication as `textures/<y>_<x>_ZL<zl>.png` (the
   `BORDER_TEX` of the `.ter`) and the DDS stays DXT1. **(review)** The PNG is never
   resampled and is written atomically together with the `.ter` files when the texture is
   published, so a cancelled run leaves no orphan.
3. Recipe inputs (section 6) are prepared: the mask file digest (once per mask file) and
   the crop window `(x0, y0, side)` (`imprint.mask_cell`).

Textures with nothing to fetch and no missing chunk are keyed at once: a store hit is
published without touching the pool or the network. **(review)** The containers are read in
a thread by batches of 32 while the progress ticker and the cancellation watcher already run,
so a long plan (ZL17: 800 containers of about 4 MB) shows progress and can be cancelled.

## 4. Fetching

- One `Fetcher` per run (one HTTP/2 session for all textures), `host_group` = provider code,
  requests in texture order (the 256 tiles of a texture, then the next) so that containers
  complete early and the encoding overlaps the download.
- `on_result` classifies each answer into a `ChunkEntry` (`imagery-chunks.md` section 2):

  | Answer | Entry |
  |---|---|
  | `error` set (transport, 5xx, 429 after the fetcher's attempts, cancelled) | `ERROR`, reason code in `content_type`; asked again by the second pass of the run when the failure says "try later" (section 4.1), else at the next run |
  | 200 recognised by `is_placeholder` (header, hash, size) | `PLACEHOLDER` (empty body kept out) |
  | 200 with an image content type and a **structurally complete** image body | `OK`, body as received |
  | 200 with anything else (HTML login page, wrong type, garbage, truncated image) | `ERROR` (`IMG_BAD_CONTENT_TYPE` / `IMG_TILE_CORRUPTED`) |
  | 404 | `MISSING` (no such tile; parent fallback) |
  | other status (403, 3xx not followed, ...) | `ERROR` (`NET_UNEXPECTED_STATUS`) |

- **(review)** Corrupted bodies. Ortho4XP decoded every body on receipt and asked again up to
  `max_baddata_retries = 10` times (`O4_Imagery_Utils.py:1035-1046, 1083-1088`). Decoding
  1 400 bodies per second on the event loop is too costly, so OrthoStudio XP applies two layers:
  1. `image_body_complete` (`textures/assemble.py`) checks the signature **and** the trailer
     the format requires (JPEG `FFD9`, PNG `IEND` chunk, WebP/BMP declared size, GIF `0x3B`)
     before a body is stored `OK`; a body failing it is `IMG_TILE_CORRUPTED` and is asked
     again in the run, `CORRUPT_RETRIES = 2` more times, in the rounds of section 5 (counted
     in `tiles_retried`); still corrupted, it stays `ERROR` and the texture is `incomplete`
     (the next run fetches it again, never the whole texture).
  2. A body that passes the structural check but does not decode at assembly is reported by
     the worker (`BuildInfo.corrupted`); the pipeline flips those entries to `ERROR` in the
     container on disk so the next run fetches them, and reports `IMG_TILE_CORRUPTED`
     (degraded). The texture is still published with the parent or neighbourhood fill (its
     recipe holds the corrupted body, so the refetched texture is a rebuild, not a hit).
  A cache is therefore never poisoned by a body that is not an image.
- When the last pending tile of a container arrives, the container is written to the
  `ChunkStore` (atomic, in a thread) whatever its completeness, so that a crash or a
  cancellation never loses received tiles. A container with `ERROR` entries is **not**
  encoded: when every one of them is a "try later" failure it waits for the second pass
  (section 4.1); otherwise, or when the second pass could not obtain them, the texture is
  reported `incomplete` (`IMG_TILE_MISSING`, degraded) and the run ends with `TEX_MISSING`
  (exit code 2); its `.ter` files are still written because the Ortho4XP DSF references them. A
  complete container goes to the parent resolution. **(review)** Once
  written and digested, the container's bodies are dropped from the parent process (only the
  statuses are kept for the parent resolution; the worker re-reads the file): the parent
  holds about 4 kB per finished texture instead of 4 MB (13 GB at ZL18 otherwise), and the
  fetcher is run with `keep_results=False` for the same reason.

### 4.1 Second pass: a transient failure costs time, not the tile (review 2026-09-13)

**Evidence.** The first six-tile build of a user (Bing ZL16, job `20260913-183959-fe0d`, logs
`textures-+46+00{8,9}-BI16-*.json`, `textures-+46+010-BI16-*.json`) sent 212 000 requests. Five
failed, four textures were `incomplete`, and three tiles were not installed. The reason code
kept in the chunk containers (`imagery-chunks.md` section 3) was `NET_TIMEOUT` for all five.
Each time, the 255 other chunks of the texture arrived within the same second and the failed
chunk was delivered 43 s after them: two dispatches, each a transfer and its hedge timing out at
20 s, with about 1 s of back-off between them (`net-download.md` R4, which explains why more
attempts in the fetcher would not help). The provider served 460-850 requests per second
meanwhile, so this is a stall of one URL lasting more than 40 s, not an outage. Ortho4XP would have
kept the tile: with its default `check_tms_response = False` a failed request is not retried,
and the chunk is pasted white into the saved texture.

**Rule.**

1. *Which failures.* The chunks whose failure says "try later" wait for the second pass instead
   of failing their texture at once: `NET_TIMEOUT`, `NET_CONNECTION_FAILED` (refused, reset,
   empty reply, HTTP/2 stream error), `NET_RATE_LIMITED` (429 beyond the fetcher's pushback
   budget) and `NET_SERVER_ERROR` with status 502, 503 or 504 (a gateway or its upstream failed,
   or the server is unavailable for now). A 500 does not: it is the server's own answer for that
   URL, already asked `max_attempts` times, and in the 270 000 requests of P0's sustained runs
   Bing never sent one (100 % HTTP 200, `docs/benchmarks/network.md` s. 2). Nor do a 403 or any
   other `NET_UNEXPECTED_STATUS`, `IMG_BAD_CONTENT_TYPE`, and `IMG_TILE_CORRUPTED` (it has
   `CORRUPT_RETRIES`, section 4). A 404 or a placeholder is an answer, not an error: it goes to
   the parent fallback of section 5, and **the parent fallback is not applied to a transient
   failure**. A chunk that timed out may well have imagery, and filling it from its parent would
   publish a blurred square that a retry can avoid. A texture whose `ERROR` entries are not all
   "try later" is reported `incomplete` at once, as before: it cannot be completed in this run,
   so waiting for its other chunks would only cost time.
2. *Nothing held meanwhile.* The container is written with its `ERROR` entries when its
   first-pass answers are in, as before (a crash or a cancellation loses nothing), and its
   bodies are dropped from the parent process. The other textures go on to the pool.
3. *When.* Once the first pass and its further rounds (parents, corrupted bodies) are over, one
   round per value of `second_pass_pauses_s` = (5, 15, 45) s, each after its pause; the pool
   keeps encoding during the pauses. A few stuck chunks are thus asked again about 5 s, 21-41 s
   and 66-107 s after the end of the first pass (the upper bounds when a round stalls for its
   whole 20 s timeout: a transfer and its hedge, 21 s). Why x3 and three rounds: the stall seen
   lasted more than 43 s, and three rounds reach two minutes after the failure; a fourth round
   (135 s) would push that schedule past five minutes for a tile whose next run fetches only the
   chunks left.

   *How long, at most.* **(second review, 2026-09-13)** The whole second pass, pauses, probes
   and rounds included, stops at `second_pass_max_s` = 180 s per run of `build_textures` (one
   provider and zoom level). A pause that would end past the limit is not taken, and a probe or
   a round still running at the limit is cancelled (within a second; what arrived is kept). The
   chunks left stay `ERROR`, the texture is `incomplete` as after the last round, and the log
   says the limit ended the second pass (item 9). This limit is the bound, not the schedule
   above: a round of N stuck chunks lasts N / (its width) waves of up to 21 s each, which is
   minutes when an outage failed thousands of chunks, and the textures node holds the build's
   single network slot all along, so the other tiles' downloads wait. Why 180 s: three rounds
   that stall need about 130 s (65 s of pauses and 3 x 21 s) plus probes and container writes,
   which 180 s keeps whole; and it is about twice the first pass of a ZL16 tile on the reference
   line (65-110 s in the job of 2026-09-13), so retries that do not converge delay the other
   tiles by no more than that. The parent rounds that follow a round (for chunks it recovered as
   a 404 or a placeholder) are not cut: they complete textures that are otherwise done.

4. *Probe first.* Each round starts with a probe: two tiles the provider already answered in
   this run or that the chunk store holds, from the waiting textures first, an image before a
   404 or a placeholder. When neither is answered, the provider or the line is down: the second
   pass stops there, the chunks stay `ERROR`, and the `IMG_TILE_MISSING` message says why ("the
   retries stopped: ..."). A dead provider thus costs the first pause and the probe (under a
   second when connections are refused, 21 s when packets are dropped), not 65 s and a sweep of
   failing requests (the dying-provider test of R2 still ends in under 30 s). The probe is two
   requests whatever the number of chunks waiting, so a line that is down never gets a wide
   round. When nothing at all was answered in the run there is no probe, hence no second pass.
5. *Polite rounds.* A round sends each waiting chunk once through the `Fetcher` of the run: same
   session, AIMD window and 429 pause state, so a `Retry-After` received in the first pass is
   still obeyed, and a 429 in a round pauses the group and costs no attempt (`net-download.md`
   R2). **(second review)** Its width follows the number of chunks waiting:
   `limit_in_flight = min(max(8, pending // 4), ceiling // 2)`, where `ceiling` is the run's
   `max_in_flight` (the provider's, 128 for BI). That is at least the AIMD floor, a round of
   about four waves whatever the count, and never more than half the provider's ceiling (64 for
   Bing, which ran 867 req/s without push-back in P0, `network.md` s. 2; 8 for the HTTP/1.1
   providers at 16); the fetcher still admits no more than the group's AIMD window. One or two
   stuck chunks keep 8, 400 chunks run 64 at a time: at a fixed 8, 20 000 chunks failed by an
   outage would take 2 500 waves, about 200 s at Bing's 80 ms p50, and 25 s at 64. The round
   uses `max_attempts = 2` (a transfer and its hedge, or one quick retry: the rounds are the
   spaced retries). A round in which 8 requests failed without a single usable answer is
   abandoned (its fetch is cancelled within a second, whatever its width), and the chunks it did
   not ask go first in the next round.
6. *Another host.* When the URL template has `{switch:}` alternatives, round `r` asks
   `switch = x + y + r` instead of `x + y`. For Bing (`ecn.t0..t3`, same bytes, `network.md`
   s. 1) that is another hostname, hence another HTTP/2 connection, whereas a hedge or an
   in-fetcher retry rides the connection of the failed transfer (`net-download.md` R3). Ortho4XP
   drew the host at random for every request.
7. *Recovered.* An answer (image, 404, placeholder) is written into the container on disk (read,
   set, atomic write, in a thread), the digest is recomputed, and the texture goes on as any
   complete container (parents, key, hit or encode). What a round received for a texture still
   waiting on other chunks is written at the end of the round, so no body is held between
   rounds. After the last round the chunks still waiting keep `ERROR` with their last reason
   code, and the texture is `incomplete` (`IMG_TILE_MISSING`, then `TEX_MISSING`): the next run
   fetches only them.
8. *Cancellation.* A cancel during a pause returns within a second (the pause waits on the run's
   cancel event, which the watcher sets within 0.2 s); during a probe or a round, the fetcher's
   R5 applies. What arrived is written, and the waiting textures are `cancelled`.
9. *What the log says.* Counters `chunks_second_pass` (chunks that needed the second pass),
   `chunks_recovered` (chunks recovered on a second pass), `second_pass_rounds` (rounds whose
   requests were sent) and `second_pass_capped` (1 when the time limit of item 3 ended the
   second pass, else 0; the `IMG_TILE_MISSING` of the textures left then ends with "the retries
   stopped: the second pass reached its time limit (180 s)"); timing `second_pass_s` (pauses,
   probes and rounds); per outcome
   `second_pass`, `recovered` and `failures`. `failures` holds, for the first eight chunks of
   the texture that failed in the run, the chunk index and tile, the reason code, the HTTP
   status (0 without an answer), the transfers of all passes, whether a hedge was issued, the
   elapsed time of the last pass, curl's error line (`net-download.md` section 1, `detail`), the
   passes and whether the chunk was recovered. `IMG_TILE_MISSING` carries the same `failures`
   for its chunks and names the codes and the rounds in its message, e.g. "1 chunk of texture
   23280_34352_BI16 could not be obtained from BI (NET_TIMEOUT), still failing after 3 retry
   rounds." The progress snapshot carries `second_pass_chunks`, `second_pass_round`,
   `second_pass_rounds` and `second_pass_wait_s` (the wait is added to `eta_s`), and the stderr
   line shows `retrying N chunk(s), round r/R in Ns`.

Parents are not part of the second pass. A parent request that fails still ends its chain
(section 5): the chunk is filled from its neighbours and the texture is built, degraded, so it
never costs the tile.

## 5. Parent fallback and the parent cache

For every `MISSING` or `PLACEHOLDER` tile `(x, y)` the chain `(x >> d, y >> d, zl - d)`,
`d = 1..parent_levels`, is walked until a tile with a body is found (`textures-assemble.md`
section 3). Parent tiles are looked up, in order, in the run's memo, in a complete container
of the `ChunkStore` at that zoom level, then in the **parent cache**
`<chunks_root>/<provider>/_parents/<zl>/<y>_<x>.tile` (body as received; a zero-length file
is a tombstone: placeholder or 404). Unknown parents are fetched in a further `fetch_many`
round once the first round is over; a parent that comes back as a placeholder or 404 is a
tombstone and the chain continues one level up in the next round. A parent that fails
(`ERROR`) is not cached; its chain ends and the chunk is left to the neighbourhood mean
(`unfilled`, `IMG_TILE_MISSING`, texture still built). **(review)** Between rounds only the
I/O tasks (container writes, parent resolutions, hit publications) are awaited, never the
encode tasks: the pool keeps encoding the complete textures of the first round while the
parents (and the corrupted-body retries of section 4) are fetched. Test: a 2 s encode of the
first texture does not delay the parent request of the second by more than 1 s.

Why a separate cache and not the `ChunkStore` at `zl - d`: a container's `MISSING` status
means "404" once the container is complete, so a partially filled parent container would be
misread as "these tiles do not exist" by a later build at that level. The flat parent cache
has no such ambiguity. It is a P1 device; P2 may add a `NOT_FETCHED` status to the container
and retire it.

The chain walk is `parents.resolve_parents(t, container, lookup, levels)`.

The resolved chain of every fallback chunk is serialised into a **parents blob** (magic
`OSXPPRT1`, the texture's `til_x, til_y, zl`, then `(zl, x, y, has_body, length, body)` per
consulted parent, tombstones included) which is an input of the recipe: a texture whose
parents change is re-encoded, one whose parents did not is a hit.

## 6. The graph rule `texture.dds` (`src/orthostudio/pipeline/rule.py`)

```
Rule(name="texture.dds", version=1, kind="file", ram_mb=250)
params  TextureDdsParams: provider, zl, encoder, encoder_version, mip_mode, refine_passes,
                          mask_zl, mask_crop (x0, y0, side) | None, sea_texture_blur, clean_halo
inputs  chunks   digest = ChunkContainer.digest() (statuses + bodies, independent of fetch times)
        mask     blake3 of the mask PNG, or None when the texture is not masked
        parents  blake3 of the parents blob, or None when every chunk came from the container
```

The artefact is the DDS file image (`encode_dds`, `textures-dds.md`). The texture position is
**not** a parameter: the DDS is a pure function of the chunk bodies, the mask crop and the
parents, so two textures with identical inputs share one artefact (open sea). Parameters a
texture does not consume are neutralised before keying (`graph-keys.md` K2): an unmasked
texture carries `mask_zl = 0`, `mask_crop = None`, `sea_texture_blur = 0`, `clean_halo =
False`; a texture without fallback chunks carries `parent_levels = 0`. Changing
`sea_texture_blur` therefore re-encodes the masked textures only (test P1). The output file
`textures/<name>.dds` is a hard link to the artefact (`os.link`, falling back to a copy across
file systems), published through a temporary name and `os.replace`. Nothing ever edits the
output, so sharing the inode is safe; `Store.gc` unlinking an artefact leaves the published
copy intact.

Per-process `Executor(Store(store_root))` in every worker: key from the input digests, hit
(touch) or build (write protocol of `graph-keys.md` section 6). The parent process computes
the same key before submitting, so hits never reach the pool. `Rule.version` is bumped when
`assemble`, `imprint`, `mip_chain` or `encode_dds` change their output for equal inputs.

## 7. Worker (one texture, in one process, nothing large crosses a pickle)

`assemble_texture_detailed(container, fallback)` (parents from the blob) → mask crop
resampled (`imprint.mask_crop`) → `imprint(rgb, mask, sea_texture_blur, zl, clean_halo)`
when masked → `encode_dds(rgba, "bc3" | "bc1", mips=True, mip_mode, encoder, refine_passes)`
→ `ctx.out`. Peak memory about 250 MB per worker (RGBA 64 MB, float32 mip chain, DDS).
Default `workers = cpu_count() - 2` (12 on the reference machine); the pool is a `spawn`
context. The worker also publishes the DDS (link/copy); the `.ter` files are written by the
parent process (`ter_text`, `ter_filename`, one file per kind, temporary name + `os.replace`).
`water_transition.png` (`src/orthostudio/pipeline/data/`, the Ortho4XP `Utils/` file, sha256
`050485c6…bd44`, GPL like the rest) is copied once into `textures/` when a `WATER_OVERLAY`
kind exists (Ortho4XP copies it when it writes such a `.ter`, `O4_DSF_Utils.py:308-317`).

## 8. Progress and errors

Progress on stderr (or a callback): tiles done/total, req/s, MB/s, in flight, hedges,
textures built/hit/failed/total, simple ETA = remaining tiles / current req/s + remaining
textures x mean encode time / workers. Errors are `OsxpError`s with existing codes:
`TEX_ENCODER_UNAVAILABLE` (blocking, before the run), `IMG_TILE_MISSING` (incomplete texture
or unfilled chunks; for an incomplete texture it lists what each chunk met, section 4.1),
`IMG_TILE_CORRUPTED` (bodies that did not decode, section 4),
`IMG_TILE_PARENT_FALLBACK` (info, counted), `IMG_TILE_PLACEHOLDER` (counted), `MASK_STALE`
(sea kind without a usable mask: texture failed, section 3), `TEX_ENCODE_FAILED` (worker
exception), `TEX_MISSING` (end of run, blocking, exit code 2), `SYS_CANCELLED` (added to the
report of a cancelled run).

**(review)** Nothing is swallowed. Every task the pipeline spawns runs under a guard: an
exception leaking from a container write, a hit publication, a `.ter` or border PNG write or
a worker follow-up fails **that texture** with a coded error and never a mute `failed`:
`SYS_WRITE_FAILED` (with the path and the OS reason) for any `OSError`, the `OsxpError`
itself when it is one, `SYS_INTERNAL_ERROR` (type and detail) otherwise. A texture that
would be left `pending` at the end of a run that was not cancelled is failed with
`SYS_INTERNAL_ERROR` naming the state it was stuck in. Test: a read-only `terrain/` gives
`SYS_WRITE_FAILED` on the texture, a `RuntimeError` planted in `write_ter_files` gives
`SYS_INTERNAL_ERROR`.

**(review)** Ctrl-C. `build_textures` installs a `SIGINT` handler on the loop (Unix, main
thread; elsewhere Ctrl-C keeps its default behaviour). The first Ctrl-C takes the cooperative
path of `spec.cancel`: no new request, in-flight transfers cancelled within a second, the
tiles received so far written to their containers with the rest `ERROR` (`SYS_CANCELLED`),
the pool shut down (workers ignore `SIGINT` themselves, `_worker_init`, so a terminal
Ctrl-C that reaches the whole process group does not kill them mid-encode), textures marked
`cancelled` and a report returned with `cancelled=True`. A second Ctrl-C cancels the loop
task: the partial containers are still written before the report is returned. The CLI writes
the report and exits **130**; it never shows a traceback for an interruption. Test: a driver
process interrupted after 100 requests leaves a container with at least 90 `OK` tiles, no
traceback, exit code 0 from `build_textures` (report `cancelled`).

## 9. CLI

```
osxp doctor [--json] [--online] [--xplane DIR] [--store DIR] [--chunks DIR]
```

The P1 command `osxp textures --from-legacy <Ortho4XP build>` rebuilt the textures of a tile Ortho4XP
had meshed, from its `terrain/*.ter` and `Ortho4XP_<tile>.cfg`; decision 0010 removed it with every
other reading of an Ortho4XP folder. Two of its review rules live on in `osxp build`: the cfg's
`sea_texture_blur` is not an Ortho4XP blur (in Ortho4XP it only blurs the `mask` layer of combined
providers, `O4_Imagery_Utils.py:2182-2189`), and a DSF that names other `{provider}{zl}` textures
(`zone_list`, airport covers) gets them built (`pipeline-build.md` 2.2).

`doctor`: Python version, active DDS encoder and version, curl_cffi + libcurl + HTTP/2, free disk at
the store root (warn below 20 GB), the X-Plane 12 folder (`--xplane`, the page's folder, else
the folders `detect_xplane` tries, in its order: `$OSXP_XPLANE_DIR`, the X-Plane installer's list,
the usual folders, `install.md` 2; a warning naming them when none is one), Triangle4XP binary (`$OSXP_TRIANGLE4XP`, the installed copy, PATH, the repo's
`native/triangle4xp/build`), on a Mac with Apple Silicon whether the programs it starts run natively
(`architecture`: `sysctl.proc_translated` of a child, `packaging.md` 2), on Windows whether the
app runs without RedirectionGuard, which would keep it from following the junctions it installs
tiles with (`junctions`, a fail: `install.md` 4), store and chunk roots, and
**(review)** one Bing tile fetched only with `--online` (a local diagnostic sends nothing by
default; `--offline` is still accepted). Human output and `--json`; exit 1 when a check fails.

## 10. Acceptance tests

| # | Test | Where |
|---|---|---|
| P1 | local tile server (PNG tiles, 404 / placeholder / 500 / flaky routes), 2 textures at ZL6, `workers=0` and `workers=2`: cold run downloads exactly the tiles asked once, writes both containers, both DDS (sizes 11 184 952 / 22 369 776, DXT1 / DXT5 by the mask), every `.ter` byte-identical to `ter_text`; a second run issues **no request** and encodes **nothing** (all `hit`); changing `sea_texture_blur` re-encodes only the masked texture | `test_pipeline_textures.py` |
| P2 | a 404 tile is replaced by the parent quadrant (pixels compared against the server's parent image); the parent is fetched once for the four children and cached; a placeholder parent walks one level higher | idem |
| P3 | a tile that always fails leaves an `ERROR` entry, the texture is `incomplete`, its `.ter` exist, the DDS does not, the report carries `IMG_TILE_MISSING` and `TEX_MISSING`; a following run with the route fixed fetches **only** that tile and completes the texture | idem |
| P4 | key stability: the recipe of a texture is identical across processes; `fetched_at` of the container does not change it; a changed mask crop window or parents blob does | idem |
| P5 | `osxp doctor --json --offline` lists the checks | `test_cli_doctor.py` |
| R1 (review) | border PNG = raw crop of the size the `.ter` declares; DXT1/DXT5 decision on the raw crop when bicubic overshoots; sea kind without mask is failed, no DDS; truncated body is `ERROR`, retried `CORRUPT_RETRIES` times then refetched by the next run only for that tile; chain of 404 parents to the fifth level; `water_transition.png` identical to Ortho4XP | `test_review_fidelite_pipeline.py`, `test_review_fidelite_grid_ter.py` |
| R2 (review) | provider dies mid-run: containers persisted, resume fetches only the `ERROR` tiles, no child process left; 429 storm: no dispatch during a `Retry-After`, no tile lost when the storm outlasts `max_attempts`; cancel mid-download keeps received tiles and resumes exactly; read-only `terrain/` gives `SYS_WRITE_FAILED`; parent round starts within 1 s of the last child tile while an encode takes 2 s; no body held after a texture is built; Ctrl-C in a driver process keeps the tiles and shows no traceback; duplicate jobs fetched once | `test_review_robustesse_pipeline.py` |
| R3 (review) | atomic writes (`fsutil`), `image_body_complete` per format, raw-crop rule in the module API, `_` in provider codes, `merge_jobs`, single `mask_zl`, leaked `RuntimeError` -> `SYS_INTERNAL_ERROR`, undecodable body with a valid trailer flipped to `ERROR` on disk and refetched, `doctor` offline by default | `test_fixes_p1.py` |
| R4 (review 2026-09-13) | with a scripted fetcher: a chunk timing out in the first pass and in round 1 is recovered in round 2, the texture is built, the container complete, each pass asks another `{switch:}` host, the rounds run at 8 in flight and 2 attempts after a probe, and the report and its JSON carry `chunks_second_pass = 1`, `chunks_recovered = 1`, `second_pass_rounds = 2`, the failure record; a chunk failing for good ends `incomplete` after exactly three rounds (four requests), with `IMG_TILE_MISSING` naming the code and the rounds, `TEX_MISSING`, the reason code on disk; a probe that is not answered stops the second pass without taking the 30 s pause of round 2; a run where nothing was answered sends no probe; the classification of every reason code and status; a 404, a placeholder and a 500 (and a timeout in the same texture as the 500) take no second pass and no pause; a cancel during a 30 s pause returns within 1 s with the chunk still `ERROR`; a round with 8 failures and no answer is abandoned, and the next round starts with the chunks it did not ask; a chunk recovered as a 404 gets its parent after the round; what round 1 obtained is on disk when round 2 starts. **(second review)** The width of a round for 1, 40 and 400 chunks waiting with ceilings 128, 16, 1 and 100; 400 chunks answered in 20 ms each finish within a 0.6 s limit at 64 in flight (a fixed 8 would need 1 s); chunks stalling past a 0.5 s limit are cancelled in flight, `second_pass_capped = 1`, the textures `incomplete` with the reason on disk and the limit in the message; a pause that would end past the limit is not waited; the default limit holds three stalled rounds; a line down gets two probe requests and no round even with 400 chunks waiting; a cancel with 64 requests in flight returns within 1 s. With the real fetcher and a local server: a 429 with `Retry-After: 1` in a round is obeyed (>= 0.95 s), costs neither an attempt nor a round, and the chunk is recovered; a real timeout is recorded as `NET_TIMEOUT`, status 0, `curl: (28)`; 160 chunks answered 502, then 0.15 s each, never have more than 32 requests in flight on the server (min(max(8, 40), 64 // 2)), and more than 8 | `test_pipeline_second_pass.py` |

## 11. Validation against Ortho4XP (removed)

Until decision 0010, `pipeline/validate.py` (then `tools/oracle`) compared a tile's `.ter` and DDS
with an Ortho4XP build: `.ter` as text and byte for byte, each DDS against **its own source** mip
chain (OrthoStudio XP: the container re-assembled with the parent cache, plus the mask; Ortho4XP:
its cached q75 JPEG plus the same mask), with the gate of ADR 0005 applied to the difference of the
two PSNR/SSIM series. Result on +43+005 (`docs/benchmarks/p1-imagery.md`): ZL14 39/39 `.ter`
identical, colour gate 17/17, alpha gate as written (-1 dB on every level 0-6) 14/17; ZL16 356/356
`.ter`, colour gate 178/179 (one 128² level at -0.51 dB), alpha gate 174/179, the misses by 0.01 to
0.44 dB at 50-78 dB absolute, hence the alpha tolerance of 1.5 dB of the amended ADR 0005.

## 12. Changes made to the P1 modules by the integration

- `orthostudio.net.fetch`: the per-group slot (`in_flight`) is released in a task done-callback
  instead of the worker coroutine's `finally`: a worker task cancelled before its first step
  never runs its `finally`, and a cancel arriving right after dispatch left `fetch_many`
  waiting forever (found by the pipeline's cancellation test; regression test
  `test_fetcher_cancelled_before_workers_start_returns`).
- `orthostudio.textures.imprint.mask_crop(full, x0, y0, side)`: the crop-and-resample step of
  `mask_for_texture` as a function of the window, so the rule can apply the window stored in
  its recipe without recomputing it from the texture position.

Changes made by the **P1 review fixes**:

- `orthostudio.fsutil` (new): one implementation of "temporary name with pid and random token,
  `os.replace`, optional fsync of file and directory" (`atomic_write_bytes`,
  `atomic_write_text`, `atomic_link_or_copy`, `fsync_dir`, Windows-safe). It replaced the six
  copies of the pattern (`write_report`, `publish_file`, `.ter` writes,
  `ensure_water_transition`, `ParentCache.put`, `ChunkStore.write`) and the two `_fsync_dir`
  (`chunks.py`, `graph/store.py`).
- `orthostudio.net.fetch`: a 429 obeyed with its pause no longer costs an attempt and is re-queued
  at the **tail** of the group's queue; bounded by `max_pushbacks` (12) and `pushback_budget_s`
  (120 s) per request (`net-download.md` R2, R4). `fetch_many(..., keep_results=False)` hands the
  bodies to `on_result` only.
- `orthostudio.imagery.chunks`: the reason code of an `ERROR` entry survives the file format (codes
  100-107 of the index byte, `imagery-chunks.md` section 3).
- `orthostudio.textures.imprint`: `mask_for_texture` applies the Ortho4XP threshold on the raw crop
  (returns `None` below it); `mask_crop_raw` and `needs_mask_for_texture` added
  (`textures-imprint.md` section 4).
- `orthostudio.textures.assemble`: `image_body_complete`; `AssembledTexture.corrupted`.
- `orthostudio.pipeline.rule`: `BuildInfo.corrupted`.
- `orthostudio.tilefiles.terrain`: provider codes may contain `_` (`tile-files.md` 3.1).

Changes made by the **review of 2026-09-13** (section 4.1):

- `orthostudio.net.fetch`: `FetchResult.detail` (curl's error line of a request given up) and
  `fetch_many(..., limit_in_flight=, max_attempts=)`, per-run overrides for the rounds of the
  second pass (`net-download.md` section 1, R4, 5.6).

## 13. Wanted differences from Ortho4XP and what remains for P2

- No JPEG cache, no white fill, no mtime/size rebuild heuristics: the key decides.
- Download of the whole tile through one session at 64-128 in flight instead of 16 threads
  per texture; encoding overlaps the download.
- Incomplete textures are reported and retried at the tile level instead of being saved white.
- **P2**: the list of textures and kinds comes from the OrthoStudio XP mesh (no `terrain/*.ter`
  reading); the DSF is written by OrthoStudio XP; masks are artefacts of the mask rule (the `mask`
  input becomes an artefact digest instead of a file digest); the parent cache becomes a container
  status; the pool and the fetcher move under the scheduler with RAM admission; `import-ortho4xp`
  replaces the `--from-legacy` reading of `.cfg`; per-provider hedge and concurrency settings move
  into the registry; the batch of several tiles shares one fetcher and one pool.
