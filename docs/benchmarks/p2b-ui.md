# P2b: the local API and the page

Machine: Mac M4 Pro (14 cores, 48 GB), macOS 26, Python 3.14.7. Date: 2026-09-12.
Everything below was measured against the running server (`osxp serve`), not a test double.

## A real build driven through the API

`POST /api/jobs {tiles: ["+43+005"], provider: "BI", zoom_level: 14, legacy_dir: ...}`,
followed on `GET /api/jobs/{id}/events` (SSE) until `finished`. OrthoStudio XP store and chunk store
empty, Ortho4XP caches warm.

| Stage | Wall |
|---|---:|
| data (Ortho4XP vectors) | 32.2 s |
| terrain (Ortho4XP mesh) | 8.5 s |
| coast (Ortho4XP masks) | 4.7 s |
| imagery (4 352 tiles downloaded, 17 textures encoded) | 5.8 s |
| assembly (XP12 rasters, DSF, overlay, pack) | 5.0 s |
| **total** | **53 s** |

Pack: 301 MB. Decisions reported: 17 textures, 17 built, 0 from cache, 360 parent fallbacks,
360 provider placeholders, 0 missing. No errors.

| Follow-up | Result |
|---|---|
| Same job again | 0.11 s, six stages `hit`, no subprocess, no request |
| `install: true` on a copy of Custom Scenery | 0.11 s; two symlinks, `scenery_packs.ini` reordered with a `.bak`, two library rows |
| Cancel 6 s into a cold ZL15 job | `cancelled` ~1 s after the request, no orphan Triangle4XP/nvcompress/DSFTool process |
| Second job while one runs | `409` |
| The user's real X-Plane | untouched: `scenery_packs.ini` byte-identical, no OrthoStudio XP pack in Custom Scenery |

## Endpoint latency

| Endpoint | Time |
|---|---:|
| `GET /api/status` (doctor cached 30 s) | ~15 ms |
| `GET /api/providers` | ~5 ms |
| `GET /api/airports?q=LFML` | ~4 ms (index built once: 1.84 s for the 383 MB apt.dat, 5.1 MB sqlite) |
| `POST /api/plan` with the network probe | ~0.6 s (20 probe tiles) |
| `POST /api/plan` offline | ~40 ms |
| `osxp serve --check` (bind, status, page, shutdown) | 0.23 s |

## The page

Vanilla ES modules, no bundler, no CDN: `index.html` + `app.js` (1 655 lines) + `i18n.js` +
`styles.css`, served by the engine. Checked in a real browser at 1280x900 and 800x600:
the four screens render, the estimate shows its two lines (network / compute), a finished job
shows its six stages with times, its journal and its final report, French and English are
complete, and the console is clean.

Four defects found in that pass and fixed:

1. the page read `steps`/`zoom_level` where the API returns `stages`/`zl`, so a reloaded or
   finished job showed every stage as `pending` and no zoom level (`normalizeJob`);
2. a finished job showed `Log (0 lines)` because no stream is opened for it: its journal is
   now replayed once (`loadJournalLog`), and the block is hidden when there is nothing;
3. the final report's three panels overlapped below ~900 px (`.report-grid` had auto-fill
   tracks narrower than the table);
4. the decisions panel counted `{code, count}` records the engine never emits: it now reads
   the real per-tile records (textures, parent fallbacks, placeholders, missing, pack bytes),
   and the three vector-stage decisions that cannot be measured before P4 were removed
   rather than shown as permanent zeros.

## Known gaps

- One job at a time (the queue is FIFO but untested beyond two).
- Settings group titles and parameter hints stay in English: they are quoted verbatim from
  Ortho4XP's author. Only the page's own text is translated.
- The library shows `ZL 0` for an overlay pack, which has no zoom level.
- No map and no custom zoom zones: P5.
