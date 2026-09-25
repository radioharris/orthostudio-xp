# Review of the prepared chain before 0.1.17, 2026-09-25

The first review (`prepared-chain-2026-09-23.md`) closed thirty findings of thirty-two. Since
then three things changed under the branch: main moved on to 0.1.15 and was merged in, the planet
was baked whole at road level 5 (19 572 squares, 97 860 files, 42 GB, OSM of 2026-09-13), and
xpconnect was dropped. This review asks what each of them breaks.

Status column: **done** (fixed and tested the same day), **open** (still to do).

## What the planet bake broke in the client

| | Finding | Status |
|---|---|---|
| R1 | The library asked for exactly the road level it was baked at. With the planet baked at 5, every build at the default level 1 would have gone to the public servers, the library unread. | done: `narrowed` wired into the library and into a copy of it in a folder; each file is cut down on arrival, bit for bit what a bake at that level writes |
| R2 | A request had 35 s in all. A file of the planet library weighs up to 61 MB (Tokyo's small roads) and its manifest 28 MB: below 14 and 6.5 Mbit/s they could never arrive, and two such tiles set the library aside for the whole build. | done: 30 s without a byte, not 30 s in all; reproduced with a local server first |
| R3 | The manifest travels uncompressed (the site has no `encode`), 28.4 MB where 3.6 would do, and every build that needs map data downloads it again: the copy kept on disk is only read when the server does not answer. Parsing it peaks at 417 MB of memory for 0.3 s. | compression done the same evening (zstd, 3.4 MB on the wire, read in 0.4 s through the client); a conditional request, so that an unchanged manifest is not sent again, and a leaner manifest stay open |
| R4 | The Settings hints said an empty library address switched the library off. A build does the opposite: empty means the address this version carries. | done |

## What was wrong before, and still was

| | Finding | Status |
|---|---|---|
| R5 | The rule that an XML file's small roads are never taken (S2 of the first review) was applied by xpconnect's reader only. A folder in Ortho4XP's shape gave its small roads to a build at any road level, under the build's own selectors. | done: refused before they are read; levels 0 and 1, which never ask for them, are served as before |
| R6 | `publish.py` promises a rollback by putting the old manifest back. A rebake writes its files at the same paths, so every file it changed is refused against the old manifest: the rollback serves almost nothing. | open for the tool (each bake in a directory of its own); the planet library was published beside the served one and put in place by moving folders, which keeps a real rollback; the bake tools moved to a private repository on 2026-09-25, where this is still to do |
| R7 | `publish.py` sent the Finder's `.DS_Store` files to the server. | done |
| R8 | The bake tools' tests never ran: pyosmium is declared in no dependency group, so the whole file was skipped everywhere, and one test had been broken since the planet was cut in stages. | test fixed; the tools and their tests moved to a private repository on 2026-09-25, where they run with pyosmium |
| R9 | Lint errors committed in the bake tools. | done |
| R13 | An address typed in Settings without a key was sent the key this version carries: the chain filled the missing key from the build, whatever the address. | done: our key goes to our library only |
| R14 | The fallback to Overpass was the design, but no test went through the path a build takes: they injected `OsmJob`'s fetch, which answers before the chain is asked, and the test of fresh data passed without the rule it named. Nothing caught a fault in the chain itself. | done: one test per failure through the real path, Overpass faked where a build reaches it; `OsmJob` catches whatever the chain raises |
| R15 | A manifest that could not be read was asked again every two minutes all build long; a silent server cost 35 s each time, some fifty minutes of a three-hour build. | done: set aside at the second failure, as after two failed tiles |

## What the merge of 0.1.15 asked for

| | Finding | Status |
|---|---|---|
| R10 | 0.1.15 rewrote the Overpass retry logic; the branch carried an older fix of the same thing. | done: main's logic kept, `osm.py` identical to main's |
| R11 | Every engine code needs French words since 0.1.15; four of the branch's had none. | done |
| R12 | The progress line of 0.1.15 takes layer names; the branch's readers still passed counts. | done |

## What can be proved against the live servers at road level 5

Nothing, on the public servers. Asked for the map as it stood when the planet was cut
(`[date:"2026-09-13T23:59:59Z"]`), the small roads of level 5 run the servers out of memory
(2 GB) even on the north of Iceland, 4 205 ways in all: forty rounds, no answer. Paris and London
failed the same way the night before. So the proof is in two halves:

* **the bake against itself.** A tile cut alone from the planet, in one pass, is compared with the
  file the two-stage cutting of the whole planet wrote. Same digest means the same elements, tags
  and order;
* **the bake and `narrowed` against the live servers, at road level 2.** The library's level-5
  files, cut down by the client exactly as a build cuts them, are weighed against what Overpass
  returns for level 2 at the extract's date. Level 2 asks one class of small roads, which the
  servers can answer.

Four tiles on four continents, sparse enough for the public servers, each with all five layers:

| Tile | Place | Cut alone, against the library | Level 2, against Overpass |
|---|---|---|---|
| +65-019 | north of Iceland, Akureyri | 5 of 5 files identical | 5 of 5 layers identical |
| -29+016 | mouth of the Orange River, Namibia and South Africa | 5 of 5 files identical | 5 of 5 layers identical |
| +20-156 | north of the Big Island, Hawaii | 5 of 5 files identical | 5 of 5 layers identical |
| -38+178 | East Cape, New Zealand | 5 of 5 files identical | 5 of 5 layers identical |

Identical means the same content digest: the same elements, tags and order, bit for bit. Before
that, 35 tiles (30 at random, and Paris, Geneva, Tokyo, New York and Sydney) were read whole
through the client at every road level from 0 to 5, straight from the baked folder.

## The planet library, published and read back

Sent beside the served folder with `publish.py` (97 860 files, 44.9 GB, 1 h 19), checked there
file by file against its manifest (none missing, none of another size, none extra, the manifest
byte for byte the local one), then swapped in by moving folders, the old library kept aside. Read
back over HTTPS with the client and the key a build uses:

| What was asked | What happened |
|---|---|
| the manifest, 28 MB | read in 1.8 s |
| twelve tiles at random, road levels 1, 3 and 5 | 12 of 12 whole at each level |
| Paris at road level 5, the heaviest in Europe | whole, 47 MB in 6.3 s |
| Tokyo at road level 5, the heaviest of all | whole, 74 MB in 10.9 s |
| the manifest and a tile without the key, or with a wrong one | 403, nothing served |

## Still open from the first review

M3 (the page never says where a tile's map data came from, or how old it is) is still open.

M5 (fresh data only through a command-line flag) is closed by decision, 2026-09-25: no switch on
the page. A switch sends builds back to the public servers and takes away what the library is
for; the library is baked again regularly instead, and the flag stays for the rare case. A user
who corrected their region in OSM gets the correction at the next bake, on the tiles they build
for the first time: a tile already built keeps the snapshot of its first download for as long as
the store keeps it.
