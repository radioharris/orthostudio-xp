# Review of the prepared chain before it ships, 2026-09-23

Six reviews of the chain that reads prepared OSM layers before asking the public servers
(`docs/specs/osm-prepared.md`), run the day it was written, with the failure of 2026-09-22 as the
standard: what went wrong that night was not code but unexamined assumptions.

Status column: **done** (fixed and tested the same day), **open** (still to do).

## Blockers, found by the operational review

| | Finding | Status |
|---|---|---|
| B1 | `release.yml`: `secrets` is not available in a step-level `if`, so a `v*` tag builds **no installers at all**. Never run, so nobody would know until the next release. | done |
| B2 | Hatchling honours `.gitignore`, so `library.json` — written from the secrets at build time — is **dropped from the wheel**. Every release would ship with no library and no complaint. | done |
| B3 | The four `osm_*` settings never reach a build: `to_build_overrides` does not emit them, so the folder source is never built and the public switch is always on. Four controls that do nothing. | done |

## Silent wrongness, found by the chain review

| | Finding | Status |
|---|---|---|
| S1 | The "an empty layer is not an answer" rule was calibrated on bzip2 XML (200 bytes). An empty snapshot of ours weighs 233-281, so the rule was **inert on our own library**. | done |
| S2 | `snapshot_from_xml` writes the **caller's** selectors onto whatever the file holds, so the road-level guard does not protect the XML sources (a folder, xpconnect). A build at road level 5 could silently lose every track. | open |
| S3 | The whitelist of the public library is never invalidated when they rebake; their manifest carries a `version` nobody compares. | open |
| S4 | The folder source did not check that a file holds the tile and layer it is filed under. | done |
| S5 | Our own bake publishes **border tiles of the extract half empty** — the Geneva failure, in our library, with a valid digest. The whitelist waiver assumes coverage equals the extract, which is a rectangle only in its bbox. | open |

## Isolation and cost

| | Finding | Status |
|---|---|---|
| F1 | `PublicSource` never raises, so the chain can never set it aside: a stalling xpconnect costs up to 2x110 s **per tile**. Its timeouts are 120 s and two attempts where the spec says 5/30 and one. | open |
| F2 | A road-level mismatch on our library downloads the whole tile before refusing it, once per tile. `LibraryIndex.road_level` is parsed and never read. | open |
| F3 | An unreadable XML raises `SyntaxError`, which escaped the `except` clause, so one bad file set the whole folder aside. | done |
| F4 | No size cap and unbounded decompression on the library path. | open |
| F5 | The chain is shared by the two network slots of a batch with no lock: doubled manifest fetches, miscounted failures. | open |
| F6 | The kept manifest is written and never read back: every job re-downloads it, and a library briefly unreachable has no copy to fall back on. | open |
| F7 | One 502 or one wifi hiccup latches `_missing` for the whole job, all 40 tiles. | open |

## What the user is told, found by the message review

| | Finding | Status |
|---|---|---|
| M1 | Five remedies promised a "land polygons" coastline fallback **that does not exist anywhere in the code**. | done |
| M2 | No `OSM_` code offered a *Try again* button: the card said "build again" with nothing to click. | done |
| M3 | The provenance line (`4 OSM layers from library (2026-08-08)`) is throttled away and then dropped with the row: **the page never says where a tile's data came from, or how old it is**. | open |
| M4 | A wrong folder, a wrong address, a revoked key and a library set aside mid-build are all **silent**, and indistinguishable from a tile outside coverage. | open |
| M5 | The only way to ask for fresh data is a command-line flag no pilot will ever type. | open |
| M6 | `errors.md` has no entry for any state of the prepared chain; the registry is where messages live, and nobody added rows. | open |

## Publishing

| | Finding | Status |
|---|---|---|
| P1 | Files must go up **before** the manifest, and the manifest must arrive by rename. A plain `rsync -a --delete` does the opposite, since `manifest.json` sorts before `osm/`. | done |
| P2 | Never bake into the served tree: the manifest is rewritten after every block, and a client reading a half-written one disables the library for its whole job. | done |
| P3 | `SnapshotStore` writes a `.meta.json` beside every layer, doubling the published file count for nothing. | done |
| P4 | No bake identity, so a client cannot say which bake it read and a whitelist cannot be tied to one. No rollback: publishing is an in-place rsync. | open |
| P5 | Nothing verifies the **published** library, only a local tile against Overpass. | open |
