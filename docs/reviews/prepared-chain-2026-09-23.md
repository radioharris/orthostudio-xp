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
| S2 | `snapshot_from_xml` writes the **caller's** selectors onto whatever the file holds, so the road-level guard does not protect the XML sources (a folder, xpconnect). A build at road level 5 could silently lose every track. | done |
| S3 | The whitelist of the public library is never invalidated when they rebake; their manifest carries a `version` nobody compares. | done |
| S4 | The folder source did not check that a file holds the tile and layer it is filed under. | done |
| S5 | Our own bake publishes **border tiles of the extract half empty** — the Geneva failure, in our library, with a valid digest. The whitelist waiver assumes coverage equals the extract, which is a rectangle only in its bbox. | done |

## Isolation and cost

| | Finding | Status |
|---|---|---|
| F1 | `PublicSource` never raises, so the chain can never set it aside: a stalling xpconnect costs up to 2x110 s **per tile**. Its timeouts are 120 s and two attempts where the spec says 5/30 and one. | done |
| F2 | A road-level mismatch on our library downloads the whole tile before refusing it, once per tile. `LibraryIndex.road_level` is parsed and never read. | done |
| F3 | An unreadable XML raises `SyntaxError`, which escaped the `except` clause, so one bad file set the whole folder aside. | done |
| F4 | No size cap and unbounded decompression on the library path. | done |
| F5 | The chain is shared by the two network slots of a batch with no lock: doubled manifest fetches, miscounted failures. | done |
| F6 | The kept manifest is written and never read back: every job re-downloads it, and a library briefly unreachable has no copy to fall back on. | done |
| F7 | One 502 or one wifi hiccup latches `_missing` for the whole job, all 40 tiles. | done |

## What the user is told, found by the message review

| | Finding | Status |
|---|---|---|
| M1 | Five remedies promised a "land polygons" coastline fallback **that does not exist anywhere in the code**. | done |
| M2 | No `OSM_` code offered a *Try again* button: the card said "build again" with nothing to click. | done |
| M3 | The provenance line (`4 OSM layers from library (2026-08-08)`) is throttled away and then dropped with the row: **the page never says where a tile's data came from, or how old it is**. | open |
| M4 | A wrong folder, a wrong address, a revoked key and a library set aside mid-build are all **silent**, and indistinguishable from a tile outside coverage. | done |
| M5 | The only way to ask for fresh data is a command-line flag no pilot will ever type. | open |
| M6 | `errors.md` has no entry for any state of the prepared chain; the registry is where messages live, and nobody added rows. | done |

## Publishing

| | Finding | Status |
|---|---|---|
| P1 | Files must go up **before** the manifest, and the manifest must arrive by rename. A plain `rsync -a --delete` does the opposite, since `manifest.json` sorts before `osm/`. | done |
| P2 | Never bake into the served tree: the manifest is rewritten after every block, and a client reading a half-written one disables the library for its whole job. | done |
| P3 | `SnapshotStore` writes a `.meta.json` beside every layer, doubling the published file count for nothing. | done |
| P4 | No bake identity, so a client cannot say which bake it read and a whitelist cannot be tied to one. No rollback: publishing is an in-place rsync. | done |
| P5 | Nothing verifies the **published** library, only a local tile against Overpass. | done |

## The bake itself, found by the tools review (the data, not the code around it)

Everything here means the baked files do **not** hold what a live query returns. All open until the
tools are fixed and the bake redone.

| | Finding | Status |
|---|---|---|
| T1 | A square the extract covers only partly is published as if whole. Geofabrik clips to a country **polygon**, so every border square holds one side only -- Geneva, Strasbourg, the Pyrenees -- and the manifest lists them like any other. This is the failure we refuse from xpconnect, produced by our own tool. Fix: read the `.poly` beside the `.pbf` and publish only squares wholly inside it. | done |
| T2 | **Node tags are dropped.** Only `airports` has a node selector, so every other layer emits a way's nodes bare. Overpass returns them with their tags, so a baked digest can never equal a live one, and for `airports` (whose `tags_of_interest` is "all") real data is lost. This, not two days of editing, is why every comparison said "digest different" while the identifiers matched. | done |
| T3 | `--strategy=smart` completes `type=multipolygon` relations only. An aerodrome is `type=site`, so half its taxiways are dropped exactly as the lake was. Needs `-S types=any`, at both cutting levels. | done |
| T4 | A way that **crosses** the square without a node inside it is missing; Overpass returns it. Long coastline segments, causeways, railway tangents. Needs a buffered cut and a segment test instead of a vertex test. | done, and it was real |
| T5 | Relations whose members inside the square are nodes are dropped; a relation's node members are never emitted; member relations are never followed. | done |
| T6 | The verification method cannot catch T2-T5: it compares identifier sets only, and excuses any difference whose element was edited after the cut. The sound method is Overpass **attic data** -- `[date:"&lt;the extract's date&gt;"]` -- after which any difference at all is a bake bug and the digests must match exactly. | done |
| T7 | The manifest can announce what the files do not hold: the filter's road level is not compared with the bake's, `extracted` is the file's mtime rather than the OSM data date, a resumed run rewrites every tile's origin, the manifest is written non-atomically, the `.meta.json` sidecars are not listed, and `verified_elsewhere` is never written. | done |
| T8 | Silent drops: a way whose nodes are missing from the file is discarded with no counter, and a selector the parser does not fully understand yields a layer baked empty without a word. | done |

## What the bake is worth now, measured against the past

The method is T6's: ask the servers for the map as it stood at the extract's own timestamp
(`[date:"2026-09-22T20:22:59Z"]`), so that the two answers describe the same world and any
difference at all is a fault of ours. Six tiles, road level 1, 2026-09-23:

| Tile | Place | Result |
|---|---|---|
| +47+007 | Basel, three borders, a lake in pieces | airports, coastline, water identical; 5 elements differ in big_roads |
| +45+009 | Milan and the Italian lakes | airports, coastline, water identical; 62 elements differ in big_roads |
| +51+000 | London, coast and dense city | **all four layers identical** |
| +59+017 | Stockholm, archipelago | **all four layers identical** |
| +40-075 | New York | coastline, water identical; 30 elements differ in the other two |

"Identical" means the same content digest: the same elements, the same tags, the same order,
bit for bit what a live query returns.

Every remaining difference was traced element by element against the OSM history API, and all of
them are the same thing: an edit made on 22 September that the servers' own copy of the past had
not yet taken in. Node 384839458 gained `crossing=informal` at 15:36, way 37003458 gained a node
at 15:01, node 6846497814 was deleted at 18:48, way 1252913018 stopped being an aerodrome at
15:01. In each case the extract holds the version valid at its timestamp and the attic query
returns the one before it. The bake is right and the reference is late, which is the one direction
of error that costs nothing.

### T4, measured rather than argued

The question was whether Overpass returns a way that crosses a square without a node inside it.
Asked directly, on a 1.3 km box laid across one 5.3 km hop of Interstate 80 in Nevada: it returns
the way, and `osmium extract` does not keep it. So the finding was real, and a tile of empty
country would have been missing the one road in it.

The fix is to cut each square with a margin of 0.1 degrees and then test every way against the
exact square, a node inside it or a segment through it. The margin is one hop's length: a way can
only cross the square without stopping if a single hop spans the crossing, so reaching one hop
outside is enough, and the longest hop measured over our layers is 0.097 degrees (Nevada; Alaska
0.036; Basel none at all). It costs about half again as long to read a square and changes nothing
else: Basel gives the same 49 091 ways with the margin as without.

Measured again afterwards, with the margin, six tiles are **identical in all four layers**, which
is to say the same content digest, bit for bit what a live query returns:

| Tile | Place |
|---|---|
| +40-117 | Nevada, where the crossing way was found |
| +47+007 | Basel, three borders, a lake in pieces |
| +45+009 | Milan and the Italian lakes |
| +51+000 | London, coast and dense city |
| +59+017 | Stockholm, archipelago |
| +47-123 | Seattle, Puget Sound |

Basel had five elements differing in the morning and none in the afternoon, Milan sixty-two and
then none: the same lag showing from the other side, the servers' copy of the past catching up
with the edits of 22 September. Three further tiles (New York, New Orleans, Svalbard) were not
reached, the address having spent its quota on the verification itself.

## What is left, and why it waits

M3 (the page never says where a tile's map data came from, or how old it is) and M5 (the only way
to ask for fresh data is a command-line flag) are the two findings still open, and both are
choices about the interface rather than about the chain: where a provenance line belongs on the
Works page, and what a button that means "download this tile again" should say and sit next to.
The facts they need are already recorded and durable, in the snapshot's `mirror` and `fetched_at`
and in the library's `bake`; what is missing is the decision about showing them.

T4's fix carries one known limit: the margin is one hop's length, 0.1 degrees, measured over our
layers (worst seen 0.097 in Nevada, 0.036 in Alaska, none at all in Basel). A way crossing a square
in a single hop longer than that, which would have to be a coarsely drawn coastline near a pole,
would still be missed. Raising the margin costs about half again as long per square for each extra
0.1 degrees, so it is a number to raise if a measurement ever asks for it, not before.

## The whole chain, against the real server

Run on 2026-09-23 against the published library, with the client and the key a build uses, not a
fixture:

| What was asked | What happened |
|---|---|
| no key | nothing at all, and the manifest not read |
| a wrong key | nothing, and the library closed for the run: asked once, never again |
| a tile the library holds | four layers in 0.23 s |
| a tile announced and not served (the upload that stopped half way) | refused by name: `OSM_LIBRARY_INCOMPLETE`, the tile, the layer and the status |
| the same, three times | counted, and the library set aside after two with the line the user sees |
| a tile outside the coverage (Sydney) | `library: not held`, `xpconnect: not held`, and on to the live servers |
| settings naming nothing | `OSM_PREPARED_FOLDER_MISSING`, `OSM_LIBRARY_UNREACHABLE` |
| the engine's own entry point, `OsmJob` | the tile in 0.80 s, the snapshot carrying `baked:north-america-osxp.osm.pbf` and the date it was cut, and the page line `4 OSM layers from library (2026-09-23)` |
| the same with `refresh` | straight to the live servers, as a user correcting their region expects |

The library it was run against is the first, defective one, which announces 23 828 files and holds
11 268: that is what made the fourth and fifth rows testable rather than hypothetical.

The bake's own two cuttings, blocks and then squares, were weighed against the same tiles cut on
their own, which are the ones proved identical to the live servers: +30-017 (on a block edge, and
holding nothing but a coastline), +33-017 and +39-032 come out the same either way.
