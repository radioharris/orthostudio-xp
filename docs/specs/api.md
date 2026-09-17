# Local API and job manager (`osxp serve`, `orthostudio.api`)

Status: P2b, written before the code of `src/orthostudio/api/`. Tests: `tests/test_api_*.py`
(no network, no Ortho4XP: `httpx.AsyncClient(transport=ASGITransport(app))`, a fake `build_tiles`
that plays synthetic scheduler events, a temporary `OSXP_HOME`, a copy of `Custom Scenery`).
Origin in Ortho4XP: none (the Tk GUI, `O4_GUI_Utils.py`, calls the engine in-process
from a worker thread). Decision: **new**. The web page (`src/orthostudio/ui/`, its own work package)
is a static client of this API; the CLI keeps working without it.

## 1. The rule in plain language

`osxp serve` starts a small HTTP server bound to `127.0.0.1` only and opens the page. The page
asks the engine what this machine looks like (`/api/status`), what it would cost to build a
set of tiles (`/api/plan`: two lines, *network: your line* and *compute: your Mac*), starts
**one** build at a time (`/api/jobs`), follows it live (`/api/jobs/{id}/events`, Server-Sent
Events, resumable), stops it, retries the same build after a failure (the artefacts already
in the store are hits: "Retry the missing ones"), and manages the library of built packs
(install into X-Plane, uninstall, delete for good, import the tiles of an Ortho4XP directory).
Settings and the airport index come from the `config` and `airports` packages.

Every failure is an `OsxpError` rendered as JSON (`docs/specs/errors.md`) plus an `action`
field for the page (`retry` / `settings` / `none`, section 6).

## 2. Contract

```python
create_app(*, env_factory=BuildEnv.create, jobs: JobManager | None = None,
           ui_dir: Path | None = None, airports=None, settings_path: Path | None = None,
           allowed_hosts: Iterable[str] = ("127.0.0.1", "localhost", "[::1]")) -> FastAPI

JobManager(*, jobs_dir: Path | None = None, build=build_tiles, env_factory=BuildEnv.create)
    start(specs, *, install=False, request=None, queue=False) -> Job
        # JobBusyError when a job is active; queue=True appends to the FIFO instead
    get(job_id) -> Job | None ; list() -> list[Job] ; active() -> Job | None
    cancel(job_id) -> bool ; retry(job_id, *, queue=False) -> Job   # same specs, new id
    close(timeout=10.0)                                              # cancel + join

Job.state() -> dict   # section 5 ; Job.events(after_seq) -> list[dict] ; Job.wait(timeout)
```

`env_factory(specs) -> BuildEnv` builds the environment of a plan or a job (the default is
`BuildEnv.create`; a zero-argument factory, the literal form of the P2b contract, is accepted
too). `build(specs, *, on_event, env) -> BuildReport` is `orthostudio.pipeline.build.build_tiles`
by default; tests inject a generator of synthetic events.

### 2.1 Endpoints

| Method, path | Body / query | Answer |
|---|---|---|
| `GET /api/status` | – | `{version, api_level, xplane: {path, detected, running}, doctor: [Check...], home, data_dir: {path, chosen, present}, store_bytes, chunks_bytes, library_count, language, active_job, platform: "mac"\|"win"\|"lin", can_quit, engine: {root, pid}}`; `data_dir` is where the tiles and the downloads go (`orthostudio.home.data_root`: `home` unless `chosen` in Settings), `present` false while its disk is unplugged; `can_quit` is true when `osxp serve` started the engine; `engine.root` is the folder of the `orthostudio` package it runs, which tells an installation from another (section 6) |
| `GET /api/engine` | – | `{version, api_level, can_quit, active_job, engine: {root, pid}}`, answered at once, without the measures of `/api/status`: a second launch recognises the running OrthoStudio XP with it (section 6) |
| `POST /api/presence` | `{}` | a page is open (the page says so every 30 s, and when it shows again): `{ok: true}`; `osxp serve --quit-when-closed` stops five minutes after the last one (section 6) |
| `POST /api/quit` | `{force?}` | stops OrthoStudio XP (`osxp serve`'s `uvicorn.Server.should_exit`, 0.3 s after the answer): `{stopping: true, cancelled: <job id> \| null, queued_cancelled: [job id...]}`; 409 `SYS_BUSY` while a job runs unless `force` (the queued jobs are then cancelled, then the running one: `JobManager.cancel_all`, so that none starts in between); 409 `SYS_NOT_STOPPABLE` when the engine was not started by `osxp serve` |
| `POST /api/choose-folder` | `{prompt, start?}` | asks for a folder in the platform's own dialog, on the computer the engine runs on (a user asked for a button instead of typing the X-Plane folder, 2026-09-15; `fsutil.choose_folder_command`): the Finder's on macOS (`osascript -l JavaScript`, `chooseFolder`, in front of the browser: AppleScript's `activate` held it back 2 s), the File Explorer's on Windows (PowerShell, `FolderBrowserDialog` over a topmost owner, brought in front by the engine once it shows, UTF-8 output), zenity's or kdialog's on Linux. `start` opens it in that folder when it exists. `{path}`, `null` when the user cancelled; 409 `SYS_BUSY` while a dialog is already open (the page asks only once: a click while its dialog opens says so); 501 `SYS_NO_FOLDER_DIALOG` when none can open (a Linux without zenity or kdialog). Waits up to 15 minutes |
| `POST /api/reveal` | `{path}` | shows the path in the file manager (`open`, `open -R` on macOS; `explorer`, `explorer /select,` on Windows, whose window the engine then brings in front of the browser, since Windows opened it behind: `winfront.py`; `xdg-open` of the folder on Linux; `fsutil.reveal_command`): `{revealed}`; 403 `SYS_FORBIDDEN_PATH` unless the absolute path lies in OrthoStudio XP's home, its data folder, the X-Plane folder or the folder of a library row; 404 when it does not exist |
| `GET /api/providers` | – | `[{code, name, max_zl, attribution, terms_url, alive, extent, extent_bounds, same_as, custom}]` in the registry's order (Bing Maps, then Esri), the user's sources last with their `url_template`; `extent` is the country in English, `null` for the whole world (`alive` is `null`: no probe offline) |
| `POST /api/sources` | `{name, url_template, max_zl?}` | a source of the user's, added to `$OSXP_HOME/sources.toml` (`imagery-providers.md` 4.1): the provider as above (201); 422 `CFG_VALUE_INVALID` when the address is not http(s) or does not give the tile's place (`{x}` `{y}` `{zoom}`, or `{quadkey}`) |
| `POST /api/sources/test` | `{url_template, max_zl?, lat?, lon?}` | one tile asked for through the address, at zoom 15 (or `max_zl`) at `lat`/`lon`: `{ok, status, image, bytes, url, error}`, `ok` when a JPEG, PNG or WebP image came back |
| `DELETE /api/sources/{code}` | – | `{removed, settings_provider}`: `settings_provider` is `"BI"` when step 1's saved source was this one and went back to Bing Maps, else `null`; 404 `CFG_PROVIDER_UNKNOWN` for a source the user did not add; 409 `SYS_BUSY` while a build under way or waiting uses it; 409 `SYS_SOURCE_IN_USE` while a zone does (`context.zones`: their names) |
| `GET /api/settings` | – | `Settings` JSON (`orthostudio.config`) |
| `PUT /api/settings` | `Settings` JSON | the saved settings (422 on a bad value, `XP_DIR_NOT_FOUND` for a changed `xplane_dir` that is not an X-Plane 12 folder, `context.why` `missing` or `not_xplane` with the subfolders it `lacks`; an unchanged one is not checked again); a changed `xplane_dir` lets the airport index that could not be built be tried again. A changed `data_dir` is refused with 409 `SYS_BUSY` while a build runs or waits, and with 422 `CFG_DATA_DIR_MISSING` (not found) or `CFG_DATA_DIR_INVALID` (`context.why`: `relative`, `file`, `xplane` inside Custom Scenery, `unwritable`, `links` for a disk that cannot hard-link files, exFAT or FAT32) by `orthostudio.home.check_data_dir`; it is saved resolved, and `null` for an empty field or OrthoStudio XP's own folder. Nothing is moved. An unchanged `data_dir` is not checked: the other settings save while its disk is unplugged |
| `GET /api/settings/schema` | – | `settings_schema()` (unit, hint, level, ortho4xp, enum per field) |
| `GET /api/airports?q=&limit=` | `q` 1-64 chars | `[{icao, name, lat, lon}]` |
| `GET /api/airports/{icao}` | – | one airport or 404 |
| `POST /api/plan` | `PlanRequest` (2.2) | `PlanAnswer` (section 4); 422 `CFG_DATA_DIR_MISSING` while the data folder's disk is unplugged |
| `POST /api/jobs` | `JobRequest` = `PlanRequest` + `install: bool` + `queue: bool` | `{job_id, status, queue_position}` (201): `queue_position` 0 when it runs now, 1 for the next to start, 2 after it...; 409 `SYS_BUSY` when a job is active and `queue` is false, or a tile is being deleted; 409 `SYS_TILE_IN_BUILD` when a tile asked for is in the running job or a queued one (`context: {tiles, jobs}`); 422 `CFG_DATA_DIR_MISSING` as for a plan. The saved `request` leaves `queue` out |
| `GET /api/jobs` | – | `[JobSummary...]`, newest first (past jobs of this home included) |
| `POST /api/jobs/clear` | – | `{removed: [job id...]}`, newest first: the finished jobs leave the list and their `<id>.json` and `<id>.jsonl` files are deleted, so a restart does not list them again; the running and queued jobs stay, and the tiles are not touched |
| `GET /api/jobs/{id}` | – | `JobState` (section 5) or 404 |
| `GET /api/jobs/{id}/events` | header `Last-Event-ID` or `?after=` | SSE stream (section 5.3) |
| `POST /api/jobs/{id}/cancel` | – | `{job_id, status}`; 409 when already finished |
| `POST /api/jobs/{id}/retry` | `{queue?}` | `{job_id, status, retry_of, queue_position}` (201): the same specs as a new job, queued like `POST /api/jobs`; 409 `SYS_BUSY` when a job is active and `queue` is false, or a tile is being deleted; 409 `SYS_TILE_IN_BUILD` as above |
| `GET /api/library` | `?xplane_dir=` | `[{tile, kind, provider, zl, path, name, built_by, installed, keys, registered_at, updated_at, size_bytes, present, overlay}]` (section 2.3) |
| `POST /api/library/overlays` | `{use: "others" \| "own", tiles, xplane_dir?}` | leaves the roads, forests and buildings of the squares to the other active overlay packs, or draws the tiles' own again (`install.md` 4.3): `{changed: [tile...], states: {tile: {state, others}}}`; 409 `XP_RUNNING`, 409 `SYS_TILE_IN_BUILD` for a tile in a build under way or waiting, 422 for a name that is not a tile |
| `POST /api/library/import-ortho4xp` | `{folder}` | the imported rows |
| `POST /api/library/{name}/install` | `{xplane_dir?, link?, path?}` | the install receipt (section 2.3); 409 `SYS_TILE_IN_BUILD` for a pack OrthoStudio XP built whose tile is in the running or a queued job: the end of that build decides what X-Plane shows of the tile (an Ortho4XP pack of the tile stays free) |
| `POST /api/library/{name}/uninstall` | `{xplane_dir?, path?}` | the uninstall receipt `{removed, pack, overlay_parked, overlay_pack_removed, pack_deleted, ...}` (`install.md` 4.1); 409 `XP_PACK_CONFLICT` when what Custom Scenery holds under that name is not the pack of the row at `path`; 409 `SYS_TILE_IN_BUILD` as for install |
| `POST /api/library/{name}/delete` | `{xplane_dir?, path?}` | the delete receipt `{format: "osxp-delete-1", name, tile, removed_from_xplane, pack_deleted, freed_bytes, custom_scenery, warning}` (section 2.3, `install.md` 4.2); 409 `SYS_BUSY` while a job is active, 409 `SYS_PACK_NOT_OSXP` for a tile OrthoStudio XP did not build, 409 `XP_RUNNING` while X-Plane runs |
| `GET /api/disk` | – | what the Library's *Free space* would give back, measured with a dry run: `{store_bytes, unused_bytes, images_bytes, mapcache_bytes, tiles, building}`; `unused_bytes` is the tile data no pack on disk needs, whatever its age |
| `POST /api/clean` | `{images?}` | frees it: `{format: "osxp-clean-1", freed_bytes, images_freed_bytes, removed}`; with `images`, the downloaded image pieces and the map background go too. No grace period, so 409 `SYS_BUSY` while a job runs here or another process builds into the store (`Store.building_pids`); no build starts meanwhile |
| `GET /api/zones` | – | `{format, revision, zones, problems}` and `ETag: "<revision>"` (`map-zones.md` 3): the saved zones read one by one, a problem listed per bad zone, never a refusal of the whole file (200 unless the file cannot be read at all); `revision` `""` and no zone when none was saved |
| `PUT /api/zones` | `osxp-zones-1` document; header `If-Match: "<revision>"` (optional) | the normalised document with the new `revision`, `problems: []` and `ETag`; 422 `ZONE_INVALID` refuses the whole document; 409 `ZONE_CONFLICT` when `If-Match` is not the file's current revision (nothing written) |
| `GET /api/map/{provider}/{z}/{x}/{y}` | – | a base map tile from the engine's cache or the provider (`map-zones.md` 6); 204 no imagery, 404 unknown provider, 422 out of range, 502 upstream failure |
| `GET /` , `GET /static/*` | – | the page from `ui_dir` (503 `SYS_RESOURCE_MISSING` when there is none) |

`{name}` of the library routes is a tile (`+43+005`) or a pack directory name
(`zOrthoStudio_+43+005`, or `zOrtho4XP_+43+005` for a tile imported from Ortho4XP).

`/api/status` counts and measures what the user sees, not what the indexes add up: `library_count`
is the number of tiles (distinct tiles among the `ortho` rows: an installed OrthoStudio XP tile also
has an `overlay` row, and the page said "12 tiles in the library" for 6); `store_bytes` and
`chunks_bytes` are the bytes of the files on the disk, each file once
(`orthostudio.clean.disk_bytes`). The store's index counted a DDS once per artefact that hard-links
it (`texture.dds` and `tile.textures`): 50.3 GB for a store `du` measured at 24 GB. The index's sum
remains the answer when the store folder cannot be read. The store holds some 12 000 files; the walk
takes well under a second, and the page calls the route at load and after a library action, never on
a timer.

### 2.2 Request schemas (pydantic, `extra='forbid'`)

```
PlanRequest {
  tiles: ["+43+005", ...]            # 1-64 names sLLsLLL, |lat| <= 89, |lon| <= 179
  | airport: {icao: "LFML", radius_km: 0-300}   # exactly one of tiles / airport
  provider: str = settings.essential.provider   # a registry code
  zoom_level: int = settings.essential.zoom_level  # 10..19 and <= provider.max_zl
  overrides: {name: value} = {}      # Ortho4XP tile variables / overlay settings, typed like --set
  xplane_dir: str | null             # overrides Settings.essential.xplane_dir (validated)
  overlay: bool = true ; xp12_rasters: bool = true ; online: bool = false (plan: probe)
  zones: [Zone...] | null = null    # null: the saved document's zones touching the tiles
                                     # (map-zones.md 5); a list: exactly those
}
JobRequest = PlanRequest + { install: bool = false }
InstallRequest { xplane_dir: str | null ; link: bool = true ; path: str | null }
UninstallRequest { xplane_dir: str | null ; path: str | null }
DeleteRequest { xplane_dir: str | null ; path: str | null }
  # xplane_dir: null = the settings, then detection
  # path: the `path` of the library row the button belongs to (up to 4096 characters);
  #       null = the newest row of the name (delete: the newest osxp build)
```

A bad tile name is a 422 whose body is the `CFG_LATLON_INVALID` error (not the pydantic list): the
page shows code + remedy like any other error. Other schema violations are 422 `CFG_VALUE_INVALID`
with the offending field in `context`. Unknown provider: `CFG_PROVIDER_UNKNOWN` (422). Zoom above
the provider's `max_zl`: `CFG_VALUE_INVALID`. Unknown override name: `CFG_VALUE_INVALID` (raised by
`BuildSpec.tile_config`). Bad `xplane_dir`: `XP_DIR_NOT_FOUND` (422). The import's `folder` must
hold `Ortho4XP.py`, else `SYS_WORKING_DIR_INVALID` (422).

The build specs come from `Settings` (`to_build_overrides`) then the request's `overrides`
(request wins); the packs go to `<OSXP_HOME>/tiles`. The X-Plane folder is the request's, else
the settings', else detection's (`install.md` 2). `install`, and the overlays or the X-Plane 12
rasters (both on by default, as the page sends them), need it: without any, a plan or a job is
422 `XP_DIR_NOT_FOUND` in plain words (`specs.xplane_not_found`: "X-Plane 12 was not found on
this computer.", or "The X-Plane 12 folder saved in Settings, <path>, was not found." for a folder
saved and gone, then what OrthoStudio XP takes from it; remedy "Choose the X-Plane 12 folder in
Settings."); with a folder whose Global Scenery is missing, 422 `XP_GLOBAL_SCENERY_NOT_FOUND`,
`context.path` that folder. A user of the Windows app in a virtual machine, X-Plane on the Mac
around it, had read "Global Scenery is not installed in <not detected>" (2026-09-14).

### 2.3 The library: sizes, presence, the row clicked, delete

Each row of `GET /api/library` carries, besides the fields of the library (`install.md` 5):

* `installed`: the Custom Scenery entry of the row's name is this row's pack. A link counts when it
  leads to the row's directory (or to where it was, once deleted by hand: the real paths are
  compared, `pack.links_to`); two builds of a tile in two output folders share the name, and
  only the linked one is installed. A real folder of that name
  counts for every row of the name;
* `present`: the row's directory exists. A tile whose folder was deleted by hand stays listed
  with `present: false` until it is deleted from the library;
* `overlay`: for the `ortho` row of an OrthoStudio XP tile X-Plane shows, whose roads, forests and
  buildings X-Plane draws on its square, `{state, others}` (`install.md` 4.3: `own`, `double`,
  `left`, `missing`; `others` the other active overlay packs holding the square, such as
  `yAutoOrtho_Overlays`); `null` for any other row;
* `size_bytes`: for an `ortho` row whose directory exists, the bytes of its files on the disk,
  each file once however many times it is hard-linked in the folder, links not followed; `null`
  for an `overlay` row (the overlay pack is shared by the tiles) or a directory that is gone or
  cannot be read. A DDS hard-linked from the store counts in full: deleting the pack alone
  does not give it back, the store clean of the delete does. A pack holds 400 to 720 files:
  the library of 50 such packs is listed in 0.1 s on the reference Mac (warm cache).

The row a button means: the page sends the row's `path` to `install`, `uninstall` and `delete`
(`pack.library_pack`). A `path` that is not a pack of that name is 422
`SYS_WORKING_DIR_INVALID`, like an unknown tile; a bad name is 422 `CFG_LATLON_INVALID`; an extra
field, 422 `CFG_VALUE_INVALID`. Without `path`, install and uninstall take the newest row of the
name, as the command line does. An uninstall with a `path` is 409 `XP_PACK_CONFLICT`, and
changes nothing, when Custom Scenery holds under that name something else than the row's pack
(`pack.is_installed`, the rule of the delete, `install.md` 4.2): a link leading to another pack
(the other row of the tile), or a real folder that is another row's pack built straight into
Custom Scenery or a copy of another build. The button of that row takes it out; a folder no row
owns is the user's to remove by hand. Without the check, the Uninstall of one row took out the
other row's link, or deleted its pack when that was built straight into Custom Scenery (an
uninstall deletes a real folder holding `orthostudio.toml`).

`POST /api/library/{name}/delete` deletes a tile OrthoStudio XP built, for good: `delete_receipt`
(`pipeline/pack.py`, `install.md` 4.2), the same function as `osxp uninstall --delete`.

* The row: the one at `path`; without `path`, the newest OrthoStudio XP row of the name, else its
  newest row (then refused).
* One delete at a time: a second one waits for the first. 409 `SYS_BUSY` while a job is queued or
  running, and a build asked for while a tile is being deleted is 409 `SYS_BUSY` too: the store
  clean that ends a delete could take an artefact the build is about to reuse.
* 409 `SYS_PACK_NOT_OSXP`, nothing deleted: a row built by Ortho4XP, or a directory that is not an
  OrthoStudio XP pack of that tile (no `orthostudio.toml`: it may hold the user's own files; a link;
  the pack of another tile). The remedy says that Uninstall takes it out of X-Plane without deleting
  anything, and to delete the folder by hand.
* 409 `XP_RUNNING`, nothing deleted, whenever X-Plane is known (`xplane_dir`, the settings,
  detection) and runs, the tile installed or not: its overlay DSF may sit in an overlay pack
  X-Plane reads. Without a known X-Plane there is no such check, and nothing is taken out of it.
* Steps: out of X-Plane when the pack is installed there; the tile's overlay DSF and the pack
  directory; the library rows; the store clean of the tile's own cache (ten minutes of grace).
* Answer: `removed_from_xplane`, `pack_deleted` (`false` when the directory was already gone:
  the rows are forgotten all the same), `freed_bytes` (what the disk got back: the files of the
  pack that nothing else links to plus the tile's cache the store gave back), `custom_scenery`
  (`null` when X-Plane is not known), `warning`: always present, `null`, or a plain sentence when
  the tile is deleted but its cache could not be freed (the answer is still 200: the tile is
  gone; `freed_bytes` counts what was freed before). An artefact of the tile used in the last
  ten minutes stays in the store (`orthostudio.clean.DELETE_GRACE_S`: a build in another process may
  share it), and so do superseded builds: `osxp clean` frees them.

## 3. Security

* The server binds `127.0.0.1` (`osxp serve --port 8641`, never `0.0.0.0`); `--host` does
  not exist.
* No CORS middleware: the page is served from the same origin. A `Host` header outside
  `allowed_hosts` is refused (400 `SYS_FORBIDDEN_HOST`): protection against DNS rebinding, the
  classic attack on local servers.
* Another website cannot use the API through the user's browser (an `<img>` pointing at
  `/api/map/...`, a form posting to `/api/jobs`, a script writing `/api/zones`): a request under
  `/api/` whose `Sec-Fetch-Site` header is present and is neither `same-origin` (the page) nor
  `none` (a URL the user typed) is refused (403 `SYS_FORBIDDEN_ORIGIN`). A request without the
  header (curl, a script) is not; `/` and `/static/` are not checked (review of 2026-09-13).
* A form on another website posts to the API without any script, and some browsers send no
  `Sec-Fetch-Site`. A form cannot send JSON: a `POST`, `PUT`, `PATCH` or `DELETE` under `/api/`
  whose `Content-Type` is present and is not `application/json` (parameters such as
  `; charset=utf-8` allowed) is refused (415 `SYS_BAD_CONTENT_TYPE`). A request without a
  body and without `Content-Type` is not: the page's library buttons send none (review of
  2026-09-13: an empty cross-site form reached the delete route).
* Request bodies above 4 MB are refused (413; `MAX_BODY_BYTES`, `map-zones.md` 10). `tiles` is
  capped at 64 names, `q` at 64 characters, `overrides` at 64 entries.
* Every path received (`xplane_dir`, the import's `folder`) is expanded, resolved and checked for
  what it must contain before use; the API never lists arbitrary directories.
* Nothing leaves the machine except the imagery / Overpass requests of a build and the
  optional 20-request probe of `/api/plan` (`online: true`, off by default).
* Static files: only regular files below `ui_dir` (no `..`), served by Starlette.

## 4. `/api/plan`: the two lines

`estimate(specs, online=request.online, env=env_factory(specs))` (`orthostudio.estimate`) gives the
per-tile counts; the answer regroups them for the page:

```json
{
 "network": {"requests": 4352, "mb": 56.6, "seconds_low": 10.9, "seconds_high": 16.3,
             "mbps_measured": null, "req_per_s": 400.0, "probed": false},
 "compute": {"seconds": 48.2, "textures": 128, "cached": 3, "workers": 12},
 "disk": {"free_gb": 56.1, "dds_gb": 1.43, "needed_gb": 1.74, "ok": true},
 "tiles": [ ...TileEstimate.to_dict()... ],
 "warnings": ["SYS_DISK_FULL"],
 "estimate": { ...Estimate.to_dict()... }
}
```

`seconds_low` is `Estimate.network_s`; `seconds_high` is 1.5x (the fetcher's measured
spread between a warm and a throttled Bing line). `mbps_measured` is the probe's `mb_per_s`
(x8 for Mb/s) when one ran, else `null`. `cached` counts the nodes reported `hit`.
`warnings` lists error codes: `SYS_DISK_FULL` when `disk_ok` is false; `TEX_MISSING` is never
predicted. Section 5 of `pipeline-build.md` fixes the estimate itself.

## 5. Jobs

### 5.1 Life cycle

```
POST /api/jobs ─► queued ─► running ─┬─► done        (BuildReport.ok)
                                     ├─► failed      (a tile failed, or the build raised)
                                     └─► cancelled   (POST cancel, or the process stops)
```

One job runs at a time; `start` refuses (`JobBusyError`, HTTP 409 `SYS_BUSY`) while one is
`queued` or `running`; `start(..., queue=True)` appends to a FIFO instead, and the next job
starts when the active one ends. The page always queues (user request, 2026-09-14: start builds
while one runs); `queue` in the request, false by default, keeps the 409 for any other client.

A tile is in one job at most (the same request): `start` refuses a spec whose tile is in the
running job or a queued one (`TileInBuildError`, HTTP 409 `SYS_TILE_IN_BUILD`, naming the tiles
and their jobs), since a second job would build it again once the first ended and install it a
second time. `building_tiles()` maps those tiles to their jobs; the Library's install and
uninstall of a pack OrthoStudio XP built refuse such a tile too. A cancelled queued job never
starts: `cancel` takes it out of the queue, and it ends `cancelled` with no `started_at`.

`SYS_BUSY` (also the answer to a library delete while a job is active, and to a build while a
tile is being deleted), `SYS_TILE_IN_BUILD`, `SYS_SOURCE_IN_USE`, `SYS_NO_FOLDER_DIALOG`, `SYS_FORBIDDEN_HOST`,
`SYS_FORBIDDEN_ORIGIN` and `SYS_BAD_CONTENT_TYPE` are API-only codes rendered in the same JSON shape; they are not in the `errors.py` registry
(candidates for `errors.md`). A job runs `build(specs, on_event=..., env=...)` in **one dedicated
thread** (`osxp-job-<id>`); `build_tiles` opens its own asyncio loop in that thread
(`asyncio.run`), so the server's loop never blocks. `handle_sigint=False` (not the main
thread).

Cancel is cooperative: `cancel()` sets the job's flag; the next scheduler event (at most
`stats_interval_s`, 1 s) raises `CancelRequested` (a `BaseException`) from `on_event`, which
`Scheduler.run` treats like a keyboard interrupt: `cancel()` then a grace period for running
nodes, then `SYS_CANCELLED` on what is left; the exception then leaves `build_tiles` and the
job thread marks the job `cancelled` with the state it has (there is no `BuildReport` in that
case; the aggregated state is complete because every node event was seen). A retry after a
cancel resumes at the node (and, inside the textures node, at the chunk) that was not
committed.

Retry is a **new job with the same specs**: the scheduler's keys make every committed node a hit and
the textures node re-fetches only the `ERROR` chunks (spec `pipeline-build.md` 2.2). The page labels
it "Retry the missing ones" when the failure was `TEX_MISSING`. The specs are copied as saved; the
`legacy_dir` an older job may have saved is ignored (decision 0010).

### 5.2 Events (journal)

Every scheduler event is normalised to one JSON object, given a sequence number `seq`
(1-based, dense), appended to the in-memory journal and to `<OSXP_HOME>/jobs/<id>.jsonl`
(one line per event, flushed; the file is the replay source after a restart):

```
{seq, ts, event, tile, stage, node, role, fraction, message, key, hit, wall_s, error, skipped,
 cause, stats}
```

`ts` is seconds since the job was created, on the job's own clock. `weight_s` on a node event
is the row's weight in its stage's fraction after the event (section 5.6: its expected
seconds, 0 for a hit and for a row skipped or cancelled before it started), the same number as
the row's `weight_s` in the state: a page that draws a step from its rows weighs them with it
(averaging them instead, it drew a bar the next refresh pulled back).

| `event` | From | Fields set |
|---|---|---|
| `started` | `Started` | tile, stage, node, role, key, weight_s |
| `progress` | `Progress` | tile, stage, node, role, fraction (0-1), message, weight_s |
| `log` | `Progress` of the textures node (`req/s` lines) | message, tile, stage=`imagery`; at most one per second per node |
| `done` | `Done`; also a row that will not run because what it produces is there (`Phase.reused`, section 5.6) | tile, stage, node, role, key (`null` when nothing is stored), hit, wall_s, weight_s |
| `failed` | `Failed` | tile, stage, node, role, error `{code, message, remedy, severity, action, context, cause}`; `skipped: true` with `cause` = the upstream node when the node fell because of it; weight_s |
| `stats` | the job itself (section 5.6), prompted by `Stats`, `Phase` and a one-second ticker | `stats: {running, pending, done, failed, hits, elapsed_s, progress, eta_low_s, eta_high_s, phase}` |
| `finished` | job end | `status`, `report` (section 5.4), `decisions`, `error` (when the build itself raised) |

The first entry of every journal is a `log` line (`job <id> started`); a build that raises
(no X-Plane, no Global Scenery...) journals one `failed` entry without a node and the job is
`failed` with that error in `errors`.

Node ids are `<tile>/<role>` or `<tile>/<provider><zl>/<role>` (`#n` suffix possible,
`pipeline-build.md` 2). `tile` is the first segment; `role` the last one without `#n`;
`stage` is one of the six user stages:

| role (rule) | stage |
|---|---|
| `osm` (`orthostudio.osm`), `coastline` (`orthostudio.coastline`), `dem` (`orthostudio.dem`), `vectors` (`orthostudio.vectors`) | `data` |
| `mesh` (`orthostudio.mesh`) | `terrain` |
| `masks` (`orthostudio.masks`) | `coast` |
| `textures` (`tile.textures`) | `imagery` |
| `xp12`, `dsf`, `overlay`, `pack` (`xp12.rasters`, `tile.dsf`, `tile.overlay`, `tile.pack`) | `assembly` |
| `install` (`tile.install`) | `install` |

The three P3 data roles were missing from `ROLE_STAGE` until review 4: their events carried
`stage: null` and the page showed no progress for a node that takes 24 s on a cold tile.

**Rows before the graph exists.** The page needs every row from the first second, but the graph
is declared a moment after the job starts. `_expected_nodes(spec)` predicts them from the spec:
`osm` with `--osm-fetch`; then `dem`, `vectors`, `coastline`, `mesh`, `masks`, and `xp12`,
`dsf`, `textures`, `overlay`, `pack`, `install` as the spec asks. The `Phase("build")` event that
carries the declared nodes (`pipeline-build.md` 4.1) then replaces the prediction tile by tile:
declared rows the prediction missed are added (a `#2` node), predicted rows still `pending` that
were not declared are dropped (the rows of a tile left out of the graph because its OSM layers
could not be had), except an `osm` row, which becomes a hit: the graph declares the downloads it
runs, so a pending `osm` row it does not declare had its data already; and a row of a second pass
that had been `skipped` is `pending` again.

**The scheduler's `Stats` are not journaled as they are.** Each phase runs its own scheduler,
whose `Stats` count only that phase's nodes and restart their clock: during the phase 0 of an
older engine, six tiles said "2 of 4 done", which the page showed as 50 %, and `elapsed_s` went
back to zero with the main graph. A `stats` line is the job's own (section 5.6).

### 5.3 SSE stream

`GET /api/jobs/{id}/events` answers `text/event-stream` (a Starlette `StreamingResponse`;
`sse-starlette` is not needed) and writes one SSE message per journal entry: `id: <seq>`,
`event: <event>`, `data: <json>`. `Last-Event-ID: n` (or `?after=n`) replays from `seq = n + 1`;
without it the whole journal is replayed first, then live events follow; the stream ends after the
`finished` message. A `: keepalive` comment is written every 15 s of silence; `retry: 2000` opens
the stream. Past jobs (a `.jsonl` on disk, no thread) replay the file the same way.

### 5.4 State (`GET /api/jobs/{id}`)

```
{
 id, status, created_at, started_at, finished_at, install, request,
 tiles: [{
   tile, provider, zl, status: pending|running|done|failed|cancelled,
   stages: {data: {status, fraction, wall_s,
                   nodes: [{node, role, status, key, hit, wall_s, fraction, weight_s}]},
            terrain: ..., coast: ..., imagery: ..., assembly: ..., install: ...},
   errors: [{code, message, remedy, severity, action, node, stage, context}]
 }],
 errors: [...same, every tile...],
 stats: {...last stats event...} | null,
 eta: {low_s, high_s} | null,
 last_seq, report: BuildReport.to_dict() | null,
 decisions: [...]  (section 5.5)
}
```

Stage status from its nodes (`jobs.stage_status`), first match:

| status | when |
|---|---|
| `failed` | a node failed on its own |
| `cancelled` | a node was stopped by a cancel (`SYS_CANCELLED` without a cause; not listed as an error) |
| `skipped` | a node was skipped (upstream failure) |
| `done` / `hit` | every node ended; `hit` when every one was a hit |
| `pending` | no node runs and none did real work: nothing started, or only hits with others still to run |
| `running` | a node runs |
| `waiting` | no node runs, some did real work (`done`), others have not started |

A hit alone does not start a stage: the data stage of a tile whose OSM data was there read
`running` from the first second, with nothing of it under way. `running` means a node runs:
the assembly of a tile read `running` for 220 s while its rasters and overlay were built and
its pack waited for the textures, which is `waiting` now (`install` stage is `skipped` when the
job does not install). Once the job is over nothing runs or waits: a stage that would read
`running` or `waiting` reads `cancelled` in a cancelled job, else `skipped` (its other rows
never ran). A job read back from disk keeps the rows it saved, and an `osm` row saved `pending`
in a tile that went past phase 0 is a hit (journals written before phase 0 reported its reused
rows).

`fraction` is the stage's weighted fraction: Σ `weight_s` x part / Σ `weight_s` over its rows,
the part being 1 for an ended row, the live `fraction` of a running one and 0 for a pending
one; by count when every weight is 0 (every row a hit). `wall_s` is the sum. A tile is `done`
when its target node ended without failure.

`stats` is the last `stats` line (section 5.6); `eta` is `{low_s, high_s}` from it while the
job runs and a range is known, else `null`.

Error `action` (for the page's button): `retry` for `TEX_MISSING`, `IMG_*`, `NET_*`;
`settings` for `CFG_*`, `XP_DIR_*`, `XP_GLOBAL_SCENERY_*`, `DSF_GLOBAL_SCENERY_*` (the X-Plane
folder is chosen in Settings); `none` otherwise. `skipped` nodes (`SYS_CANCELLED`
with a cause) are not listed as errors; the root cause is.

### 5.5 Final report and decisions

`report` is `BuildReport.to_dict()` (`pipeline-build.md` 4). `decisions` lists what OrthoStudio XP
did on its own, per tile, for the page's end-of-build panel:

| `kind` | Source | Fields |
|---|---|---|
| `nodes` | the events | `hit`, `built`, `failed`, `skipped` counts |
| `stage_time` | the events | `seconds` per stage (`{data: 12.3, ...}`) |
| `textures` | `<workdir>/logs/textures-<tile>-<level>-<key12>.json` when found | `total`, `built`, `hits`, `missing`, `parent_fallback`, `placeholders`, `second_pass` (chunks fetched again after a transient failure), `recovered` (of those, obtained) |
| `degraded` | `failed` events with severity `degraded` / `info` | `code`, `count`, `message` |
| `pack` | `TileOutcome.pack_dir` | `bytes`, `path`, `installed` |

### 5.6 Progress and time remaining (`api/progress.py`)

`stats` describes the **whole job**, every phase together:

| Field | Meaning |
|---|---|
| `elapsed_s` | seconds since the job thread started the build, across phases (monotonic) |
| `progress` | 0-1, weighted by expected cost over every expected node, never decreasing during a run; 1 in the last line of a job that is `done` |
| `eta_low_s`, `eta_high_s` | seconds left for the whole job; `null` only when the job has no row to estimate; 0 in the last line |
| `running`, `pending`, `done`, `failed`, `hits` | counts over every row of the job (`done` includes the hits, `failed` the skipped and cancelled rows) |
| `phase` | `build`; `data` only while a build that downloads its OSM data before its graph (an older engine's phase 0) says so, for a plain-words hint |

A line is journaled at every `Phase`, after a scheduler `Stats` when the last line is at least
0.5 s old (a burst of sixty hits was sixty lines), at least once a second while the job runs --
a ticker thread covers the seconds when no scheduler runs, such as the declaration of the main
graph -- and once at the end, just before `finished`.

**Rows that will not run.** `build_tiles` knows before the graph runs which tiles already have
their OSM data (a stored snapshot) and says so in the declaring `Phase("build").reused`
(`Phase("data").reused` from `run_osm_phase`); the job turns each such row into a `hit` (fraction 1)
and journals a `done` event with `hit: true`. When the declared nodes arrive, an `osm` row still
`pending` that the graph does not declare becomes a hit the same way. No expected row stays
`pending` past the point where it would have run.

**Weights.** A node weighs the seconds it is expected to take, on the reference Mac (`ROLE_SECONDS`: three `osxp build` runs of one tile; `BATCH_SECONDS`: the
six-tile job of 2026-09-13, which the costs the scheduler learnt over fifteen builds agree
with):

| Role | alone (s) | batch of 3+ tiles (s) |
|---|---:|---:|
| `osm` (Overpass, one tile at a time on its lane) | 26 | 26 |
| `vectors` | 6.3 | 11.1 |
| `mesh` | 2.6 | 8.4 |
| `dem` | 2.5 | 6.2 |
| `dsf` | 1.8 | 5.5 |
| `overlay` | 3.2 | 2.8 |
| `masks` | 0.85 | 1.7 |
| `pack` | 0.3 | 0.8 |
| `xp12` | 0.4 | 0.4 |
| `install` | 0.15 | 0.15 |
| `coastline` | 0.05 | 0.05 |

Nodes share the machine in a batch (a mesh takes 1.4-3.6 s alone, 5-12 s next to five others), so
the weight moves linearly from the first column at one tile to the second at three. The textures node weighs its textures -- the
tile's at its level plus those its `zone_list` adds, as `osxp plan` counts them -- at **0.30 s**
each when their chunks must be downloaded from a provider that takes Bing's 128 requests at once
(about 1 000 req/s; measured 0.17-0.55 s on this line) and **0.05 s** when they are on disk
(encoding only: a warm tile, 234 textures in 11 s). The download part (0.25 s) scales with
`128 / max_in_flight` -- a line's throughput is the requests in flight over its latency, the model
of `osxp plan`'s probe -- so a texture from a provider that takes 16 at once weighs 2.05 s; and it
never counts a rate above the provider's `server_req_per_s`, where the server itself was the limit
(`progress.texture_download_s`): Esri Clarity's 192 requests at once would give 0.22 s a texture,
a user's builds took 0.59 s, and 256 chunks at its 522 req/s weigh 0.54 s (2026-09-15). Whether the chunks are on disk is read from the chunk store before the build
starts, a stat for at most 256 sampled textures per tile. The textures node belongs to the speed
group `textures` when downloading is most of its weight, else `textures_cached`. A node that does
not run -- a hit, a node skipped or cancelled before it started -- counts as ended and **weighs
nothing**: with its full weight, a retry whose first second is sixty hits read 70 % at once, and a
tile whose OSM data was a hit read 48 % after one second of thirty.

**Progress** = Σ weight x fraction / Σ weight over every row (1 for an ended row, the live
fraction of a running one, 0 pending), held at its highest value (rows added by the
declaration, or a second pass, could lower it), 1 when the job is done.

**Time remaining.** The same weights, corrected by what the job has shown:

1. *Speed per group* -- OSM downloads, textures to download, textures from the cache, everything
   else: Σ wall / Σ weight of the rows that ran (done, or failed after reporting 90 % done: a
   textures node fails at the very end), each ended row's say decaying with a 180 s memory (the line
   slowed from 1 230 to 450 req/s during one batch), plus one pseudo-observation of 30 s
   (downloads), 10 s (cached), 20 s (OSM) or 15 s (the rest) at speed 1, bounded to 0.1-10. **The
   downloads start from the line's recent speed** instead of speed 1 (user report, 2026-09-13: a
   six-tile batch on a fast evening was predicted a third too long for its first half): `prepare`
   reads the latest texture reports under the environment's logs
   (`textures-<tile>-<level>-<key>.json`, 24 at most, newest first) and `line_factor` takes, for
   each provider of the job, those that downloaded ten textures' worth of chunks at least and were
   not cancelled: seconds per downloaded texture (the whole time less 0.05 s per texture of cached
   chunks, over the chunks downloaded / 256) over the weight of such a texture for the provider's
   `max_in_flight`; the median of the latest six, each report's say halving every 12 h (the same
   line gave 0.19-0.27 s a texture at 21:00 and 0.23-0.47 s at 21:13). The providers' factors are
   averaged by their download seconds; fewer than two reports keep speed 1. On the six-tile batch,
   its own reports as history make the first estimate 315 s instead of 416 s, for a real end at
   322 s. Downloads and cached textures are apart because the line limits one and the processor the
   other: a warm tile's encoding, read as the speed of the four cold tiles behind it, moved the
   estimate by 85 s in a second. The bounds are wide because a group's factor multiplies every
   textures node still waiting: 0.33-3 hid a provider several times slower than its weight for the
   whole queue.
2. *A running row that reports fine progress* (the textures, from 2 % gained) is extrapolated
   from its **recent rate**: an exponential average of its progress between reports (10 s time
   constant), counted from the moment it started moving -- until its fraction first rises, its
   offset follows it, since a warm tile sits at 50 % for six seconds (its download half being
   free) and then encodes. The rate slows by itself when reports stop for more than 2 s. The
   extrapolation replaces the weight-based time left linearly as it gains fraction (full at 15 %)
   **and** time (full after 12 s of movement): a warm tile gained 17 % in its first half-second,
   which the gain alone trusted fully, and a cold one downloads at 400 req/s while the fetcher
   opens its connections and at 1 200 req/s fifteen seconds later. A running row speaks for its
   group's queue (the textures still waiting) only from 15 s of movement, fully at 45 s: that
   slow start, read as the speed of the queue, added two minutes to the estimate for ten
   seconds. A row without progress reports has its expected seconds minus its elapsed time (at
   least 15 % of them) left.
3. *Queueing*: the main graph ends with the slowest of its lanes -- per scheduler kind, the
   seconds left over the kind's slots (one `net` slot, so the textures nodes of a batch run one
   after the other, and they cannot start before the first tile's OSM data is in; a downloaded
   relief queues there too, X-Plane's is a `subprocess` row; `subprocess` =
   `default_subprocess_slots()`; `io` = 2; `cpu` = the workers), the OSM rows on their lane of one
   (`progress.OSM_LANE`) -- and of its tiles' chains (elevation, vectors, mesh, masks, dsf,
   textures, pack, install in a row), each starting once the OSM downloads queued before its
   tile's and its own are in.
4. *Phases*: an older engine's phase 0 ran the OSM rows one after the other before the main graph
   was declared (0.3 s); a job that says `data` is estimated that way.

The range is `[eta (1 - b/2), eta (1 + 3b/2)]`, with `b` from 0.45 down to 0.20 as the share of
the remaining work whose group speed was observed grows: asymmetric, because a build ends late
by more than it ends early.

**What `stats` publishes moves at a bounded pace** (`progress.EtaSmoother`): the predicted end
(`now + eta`) moves toward the fresh estimate by at most `max(4 % of the time left, 2 s)` per
second, and `b` by 0.01 per second; the end never lies in the past, the first estimate is
published as it is and the last line of a job says 0. Four percent is a dozen seconds on a
job of eight minutes, a change of a minute shows within a dozen seconds, and the page's range
does not run ahead of what the next estimates undo (it moved +85 s then -27 s within a second
when a warm tile started encoding). The floor of 2 s per second keeps the last half-minute
following the job, where 4 % would be under a second.

**Replay of the six-tile job** (`tests/test_api_progress.py`, fixture
`tests/data/jobs/six-tiles-bi16-20260913.jsonl.gz`: its journal trimmed without changing its
timing): every node event and progress line at its recorded time, a progress line every half
second, the scheduler's ticks, the job's own ticker, and the `stats` lines the job publishes,
against the real end at 538 s. From 20 % progress (reached at 118 s) the predicted end stays
within 27 % (p90 21 %), and within 12 % over the last 30 % of the job; the range holds the real
end 68 % of the time. Between two consecutive lines (at most 1.2 s apart) the predicted end
moves by 8.7 s at most, the middle of the range by 10.9 s, its high side by 15.3 s; the same
stream moved the middle by 85 s before the smoothing, the recent rate and the separate groups.
The old per-phase statistics, on the same window: 57 % off (p90 53 %), a range that never held
the end. The first 250 s are 20 % early because the line slowed after them: the textures of
the first three tiles took their expected time within 5 %, the last three 1.4-1.8 times it,
which nothing could tell before they ran. Three `osxp build` logs of one tile (progress
interpolated to the journal's cadence, a `stats` line a second) replay within 20 %, 35 % and
24 % (p90 18 %, 12 %, 13 %): on those short jobs over a fast line the estimate starts high and
comes down at the smoothing's pace.

## 6. `osxp serve`

`osxp serve [--port 8641] [--open/--no-open] [--ui-dir PATH] [--home PATH] [--quit-when-closed]`:
`serve.main()` builds the app (`ui_dir` default `src/orthostudio/ui`), starts uvicorn on
`127.0.0.1:<port>` and opens `http://127.0.0.1:<port>/` in the browser (`webbrowser`) unless
`--no-open`. The command is wired in `cli.py` (`orthostudio.api.serve:main` is the entry point).

`--quit-when-closed` (what the app runs, `desktop.py`): the engine stops by itself
(`presence.quit_when_closed`, the same stop as `POST /api/quit`) once no page has said it is open
(`POST /api/presence`) for 5 min and no build runs or waits. The watch checks every 15 s; a build
under way, or a check more than three ticks late (the computer slept), starts the wait again. The
page says it is open every 30 s and when it shows again (`visibilitychange`); a background tab's
timers slow down to about once a minute. When it gets no answer twice, 3 s apart, it shows the
stopped screen, saying that OrthoStudio XP stops by itself after its last page closed. A plain
`osxp serve` in a terminal never stops by itself.

Port already taken: the launch asks `GET /api/engine` (5 s at most), and an engine older than that
route (a 404) its `/api/status` (20 s at most). It used to ask `/api/status` alone and wait 1.5 s:
on Windows the running engine took longer (it lists the processes with `tasklist` and measures the
store first), the launch took it for another program, and nothing showed until the engine was ended
in the Task Manager (2026-09-17). Then, when the port is taken:

- by the same installation as recent as this one (its `/api/status` gives an `api_level` at least
  this one's and `engine.root` this one's package folder): its page opens, nothing starts. Opening
  the app twice shows the running OrthoStudio XP;
- by another installation (another `engine.root`, or none given: the app while a checkout is
  started, or the other way round): it is asked to quit like an older one, below, whatever its
  level. A user who ran a checkout's `osxp serve --open` while the installed app ran got the app's
  page and code, told only "OrthoStudio XP is already running" (2026-09-14);
- by an older OrthoStudio XP: it is asked to quit, `POST /api/quit` without `force`
  (`serve.take_over`), and this one starts once the port is free. Left running, it would serve
  the new page next to its old routes (a checkout's page is read from disk), and the page could only
  ask to restart it: the user opened the updated app on the engine started before the update
  (2026-09-14). An older engine that does not stop (a build runs in it: 409) or that predates
  `POST /api/quit` (`api_level` below 8) stays, and its page opens, with the banner asking to quit it
  and open OrthoStudio XP again;
- by another program, or anything that does not say it is OrthoStudio XP in time:
  `SYS_RESOURCE_MISSING` with the port in context, exit 1; with `--open`, the port's address still
  opens, so that the app shows what is there rather than nothing.

## 7. Acceptance tests (`tests/test_api_*.py`)

* `test_api_app.py`: status (temporary home, no X-Plane), providers, settings GET / PUT round trip,
  schema, airports with a fake index, 422 with `CFG_LATLON_INVALID` on `+430+005`,
  422 `CFG_PROVIDER_UNKNOWN`, `Host` rejection, body-size limit, plan offline on a fake Global
  Scenery (two lines, `probed: false`), library on an empty home, `import-ortho4xp` on a synthetic
  Ortho4XP tree, install / uninstall on a copied `Custom Scenery`; delete of an installed tile
  (link, `scenery_packs.ini` lines, folder and rows gone, `warning` null), without an X-Plane, of a
  folder already gone, the refusals (`SYS_PACK_NOT_OSXP` for a Ortho4XP row and a folder without
  `orthostudio.toml`, `SYS_BUSY` while a job runs), `path` choosing between an Ortho4XP and an
  OrthoStudio XP row of one tile for delete, install and uninstall (409 `XP_PACK_CONFLICT` when the
  link leads to the other row's pack); `installed` true only for the linked pack; no build while a
  tile is deleted; 415 `SYS_BAD_CONTENT_TYPE` for a form or plain-text body; `size_bytes`,
  `present`, `library_count` counting tiles and `store_bytes` counting a hard-linked file once.
  `test_delete.py` covers `delete_receipt` itself on a real store and `osxp uninstall --delete`;
  `test_uninstall.py` the overlays of Ortho4XP left alone.
* `test_api_jobs.py`: a fake `build` that plays `Started` / `Progress` / `Done` / `Failed` /
  `Stats` for one or two tiles: job runs to `done` with 6 stages aggregated; a
  `TEX_MISSING` failure gives `status: failed`, one error with `action: retry`, `retry`
  creates a new job whose nodes are hits; cancel while running gives `cancelled`; 409 when a
  second job is posted while one runs; SSE stream read to `finished`, replay with
  `Last-Event-ID`; the `.jsonl` journal exists and replays after a new `JobManager` on the
  same home.
* `test_api_progress.py` (section 5.6): the replay of the six-tile job at its real cadence
  (accuracy bounds above, the published end moving at most at the smoothing's pace between
  consecutive `stats` lines, progress never decreasing, `elapsed_s` spanning both phases,
  `phase`, counts over the whole job, the reused OSM rows hits); stage status rules, `waiting`
  and a hit that does not start a stage; `weight_s` on node events and rows, the stage fraction
  from them; the smoother; a warm tile's pace not speaking for the downloads; a provider that
  takes fewer requests weighing more; the extrapolation waiting for movement and time; a reused
  OSM row journaled as a hit, and a pending one turned into a hit when the build phase starts;
  the declared graph replacing the predicted rows; a second pass; the line's recent speed read
  from texture reports (provider, cancelled, warm and short reports skipped, in-flight scaling,
  recency) and the first estimate of the six-tile batch started from it; the `stats` shape in the
  event and the state, a line at least every (patched) second across a gap without events; the
  weights (batch interpolation, texture counts, cached chunks, zones); a past job read back with the
  rows it saved; the second pass in plain words; `run_osm_phase`
  and `build_tiles` announcing their phases before any node event, and a real failing batch
  through the manager.

## 8. Implementation notes

* Cross-thread hand-off: the job thread appends to a list under a lock; the SSE generator
  polls it (50-250 ms) rather than waking a loop-bound primitive, so the manager has no
  dependency on the server's loop (the CLI could drive a `JobManager` too).
* `/api/status` runs `run_doctor(offline=True)` (no Bing probe) and caches it for 60 s;
  `active_job` is the id of the queued or running job, else `null`.
* `/api/airports` with the real index: `default_index` builds `~/.orthostudio/airports.sqlite` from
  `apt.dat` on first use (a few seconds, in a worker thread; its progress callback is not
  relayed to the page in P2b). Without an X-Plane folder it answers 422 `XP_DIR_NOT_FOUND`.
* A cancelled job has no `BuildReport` (`report: null`): the `CancelRequested` exception
  leaves `build_tiles` before it assembles one; the per-node state is complete.
* `retry` records `retry_of` in the new job's `request`.
* Measured on the reference Mac (empty home): `/api/status` 60 ms (doctor
  offline), `/api/plan` for +43+005 at ZL14 10 ms; the 18 tests run in 1.4 s.
* `store_bytes` walks the store folder (`orthostudio.clean.disk_bytes`); the index's sum is the
  fallback when the folder cannot be read, and is opened only when the directory exists (opening
  creates it).
