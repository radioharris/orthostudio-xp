# A real batch: six ZL16 tiles, every image downloaded

The first batch built by a user from the page after the Works progress and the second pass
landed: what an ordinary evening build costs, measured from its journal and texture reports.

- **Date**: 2026-09-13, 20:58 (job `20260913-205818-3f20`).
- **Machine**: Apple M4 Pro (10 performance and 4 efficiency cores), 48 GB of memory, macOS
  26.6.2, on a home internet connection.
- **OrthoStudio XP**: commit `7a69685` (`osxp serve`, the page).
- **Build**: tiles `+46+005` to `+46+010` (Lake Geneva, the Swiss plateau and the Alps), Bing (BI)
  at ZL16, no zones, overlays and install on, native stages, relief from X-Plane 12.
- **Starting state**: `osxp clean --all` had emptied the store and the downloaded image pieces a
  few minutes before, so every texture was built and every image piece downloaded again. The
  OpenStreetMap data of the six tiles was still cached from an earlier build of the same tiles
  (the six OSM rows were cache hits): a first build of new tiles adds about 25 s per tile, one
  tile after the other (`docs/benchmarks/p2-build.md`, the Overpass rule of
  `docs/specs/pipeline-build.md` section 4).

## Result

**5 min 22 s (322.4 s) for the six tiles, installed, with no error.**

| | |
|---|---:|
| Textures (DDS, 4096 × 4096) | 1,347 (1,258 built, 89 shared with a neighbour built in the same batch) |
| Image pieces downloaded | 321,792 |
| Downloaded | 4.64 GB |
| Errors, retries, second pass | 0 errors, 0 retries, 1 hedged request, no second pass |
| Disk after the build | store 16 GB, image pieces 4.3 GB, tile folders 10 MB (hard links) |

## Where the time went

| From | To | What |
|---:|---:|---|
| 0 s | 55 s | Data, Terrain, Coast and the DSF of all six tiles, in parallel |
| 35 s | 322 s | Imagery, one tile after the other on the network lane |

| Tile | Imagery | Downloaded | Download rate |
|---|---:|---:|---:|
| +46+005 | 63.6 s | 775 MB | 12.3 MB/s (99 Mbit/s), 954 requests/s |
| +46+006 | 48.4 s | 701 MB | 14.8 MB/s (118 Mbit/s), 1,052 requests/s |
| +46+007 | 42.3 s | 790 MB | 19.0 MB/s (152 Mbit/s), 1,333 requests/s |
| +46+008 | 38.6 s | 766 MB | 20.3 MB/s (162 Mbit/s), 1,341 requests/s |
| +46+009 | 44.7 s | 726 MB | 16.6 MB/s (133 Mbit/s), 1,156 requests/s |
| +46+010 | 49.3 s | 886 MB | 18.3 MB/s (146 Mbit/s), 1,141 requests/s |
| **total** | **287 s** | **4.64 GB** | **16.5 MB/s (132 Mbit/s) over 282 s of downloading** |

Download rates are the bytes of each tile's texture report over its fetch time
(`timings.fetch_s`); the imagery time also includes encoding. The connection used about
100-160 Mbit/s: with up to 128 requests in flight, Bing's time per request sets the pace
(`docs/benchmarks/network.md`), so a faster line would not necessarily go faster.

## The estimate shown on the Works screen

| Progress | Time | Predicted end (range) | Real end |
|---:|---:|---:|---:|
| 10 % | 22 s | 348-704 s | 322 s |
| 25 % | 40 s | 355-696 s | 322 s |
| 50 % | 118 s | 345-571 s | 322 s |
| 75 % | 217 s | 311-382 s | 322 s |

The estimate was pessimistic in the first half: Bing served 954-1,341 requests/s that evening,
against the slower line of the journal the weights were fitted on
(`tests/data/jobs/six-tiles-bi16-20260913.jsonl.gz`, 450-1,230 requests/s). From 75 % on, the
range held the real end.

**Since then** the downloads start from the line's speed on the latest builds (`docs/specs/api.
md` section 5.6). This batch's own six texture reports give 0.19-0.27 s per downloaded texture,
0.73 of the fixed weight; started from them, its first estimate is 315 s instead of 416 s, with
a range of 244-527 s that holds the real end (`tests/test_api_progress.py`,
`test_the_first_estimate_starts_from_the_line_of_recent_builds`). A later build on a slower
evening starts from this one's speed and corrects itself as its first tile downloads.

## Against Ortho4XP

No Ortho4XP run of these six tiles was made. For scale only: Ortho4XP took 250.7 s for one ZL16 tile
with its caches warm except the imagery (`docs/benchmarks/p2-build.md`), tiles one after the
other, which is about 25 minutes for six. That figure assumes every elevation and OSM download
works; Ortho4XP also depends on elevation files downloaded from sites that no longer answer, which
OrthoStudio XP does not need (the relief comes from X-Plane 12).

## Reproduce

```bash
osxp clean --all
osxp build --tile +46+005 --tile +46+006 --tile +46+007 --tile +46+008 --tile +46+009 --tile +46+010 --zl 16 --install
```

The journal of the job (`~/.orthostudio/jobs/<job id>.jsonl`) and the texture reports
(`~/.orthostudio/work/logs/textures-<tile>-BI16-<key>.json`) hold every figure above.
