# Prepared OSM layers: the sources a tile is asked of, in order

Status: written before the code; ships in 0.1.17. Companion of `osm-source.md`, which stays the
description of Overpass itself. Origin: the night of 2026-09-22, when two of the three public
Overpass machines a build asks became unusable at once and every build on every continent stopped
on its Data step; and the days after it, when a user who had built almost a whole state read that
downloads had "turned random".

The data behind that step is public and downloadable in bulk: one country extract holds what
thousands of live queries would ask for, and the same four questions are asked of it for every
tile anyone builds. A tile's layers can therefore be **prepared once and read as files**, in 60 ms
instead of 8 to 30 s, with no quota and no bad evenings. What follows is how a build chooses
between the prepared libraries and the live servers, and (the harder half) how it refuses a
library that would quietly give it less than the truth.

## 1. The chain

After the tile's own cache has missed, the layers are asked of these, in this order, until one
answers:

| | Source | Present when | Typical |
|---|---|---|---|
| 1 | the user's own folder | `expert.osm_folder` names one | instant |
| 2 | our baked library | its address and token are set (they ship with the app) | ~60 ms |
| 3 | Overpass | always, last | 8-30 s |

The order follows what is known, not what is fast: the folder is the user's own; ours is the only
online library whose coverage we established ourselves; Overpass alone is live and complete.

A third source stood between the library and Overpass while the chain was written, never in a
release: the library another project publishes in Ortho4XP's format (OrthoForge, served by
xpconnect), read only for the tiles we had compared against a live count, from a whitelist carried
in our manifest. It was dropped once the whole planet was baked here at road level 5 (2026-09-25):
it covered nothing ours does not, and its files cannot say which road level they answer, so it could
never serve a build above level 1.

**Fresh data skips the libraries.** A tile asked for with `refresh` (`OsmParams.refresh`, the
command line's `--osm-refresh`) goes straight to Overpass: a prepared library is weeks behind by
design, and that is its only real defect. The page has no switch for it, by decision
(2026-09-25): a switch sends builds back to the public servers and takes away what the library is
for, so the library is baked again regularly instead. A rebake reaches the tiles a user builds for
the first time; a tile already built keeps the snapshot of its first download for as long as the
store keeps it.

## 2. What a source is

One method, three outcomes, nothing else:

```python
class PreparedSource(Protocol):
    name: str

    def layers(
        self, tile: TileRef, specs: Sequence[LayerSpec]
    ) -> dict[str, OsmSnapshot] | None: ...
```

* **the layers**: every one of `specs`, taken as they are, the chain stops;
* **`None`**: this source does not hold the tile, the chain moves on without a word;
* **an exception**: the source is broken; the chain moves on and records the reason for the
  report and the log.

**All or nothing, per tile.** A source that holds three of the four layers gives nothing. Mixing
sources inside one tile is how an incoherent tile is made: a coastline truncated at a national
border under roads that are complete, and nothing to show for it. The libraries bake all layers
of a tile together, so the rule costs nothing real.

## 3. Refusing a library that gives less than the truth

This is the part that decides whether the chain is trustworthy, and it is not symmetric: a library
that is *absent* is harmless, a library that is *short* is not. Measured on xpconnect, the public
library the chain first read, on 2026-09-19 and again 2026-09-23, both times unchanged:

* 7 132 tiles listed, **1 559 of them (22 %) with both road layers empty**;
* the Geneva tile (`+46+006`) holds 10 110 road ways where a live query returns 24 307: the bake
  is made per country extract and the Swiss half of that tile is simply not in it;
* nothing in the file says so. It downloads, its sha256 matches, the XML is valid, and it holds a
  third of the roads. A build would lay scenery with no roads and no water and report success.

Two defences, in order of cost:

1. **An empty layer is not an answer from whoever cannot prove it.** A layer file under 200 bytes
   holds no element at all, not even the wrapper of an empty layer, and the tile is refused
   before anything is downloaded when the manifest carries sizes, as ours does. Above
   that, an empty layer is refused from an XML source, which can prove nothing about itself, and
   taken from a library whose manifest announces the file's digest and whose file matches it: a
   square of Atlantic off the Sahara really has no road, no airport and no lake, only a
   coastline, and refusing those sent every empty square of a continent to the public servers to
   be told the same thing. What emptiness must never mean is a bake cut short, and that is the
   coverage polygon's business. A folder is read the same way when a `manifest.json` sits beside
   the files, which is what a copied library is; an Ortho4XP folder has none and keeps the strict
   rule.
2. **Every file is checked at the door**: its digest against the manifest, then the document is
   read. Either failing makes the tile move to the next source, with the reason recorded.

**A layer whose question depends on the road level is never read from an XML source.** Such files
are OSM 0.6 XML and carry no selectors: nothing in one says whether `small_roads` was baked for
tertiary roads or for tracks as well, and taking it at a road level it was not baked for gives a
scenery quietly missing every forest track. Our own format carries its selectors and is checked
against them, so only the XML source, a folder in Ortho4XP's layout, is held to this.
A folder in Ortho4XP's shape therefore answers builds at road level 0 and 1, which never ask for
`small_roads`, and leaves the others to the next source; its small roads are refused before they
are read. Until 2026-09-25 only xpconnect's reader applied the rule, and a folder's small roads
were taken at any level under the build's own selectors.

**Our own library is taken for what its manifest lists**, since the manifest is written by the
same tool that cut the tiles: a tile is in it when its layers were written, and the coverage is whatever the extract
covered. What it does carry, per file, is the digest and the size, and per bake:

| Field | What it is for |
|---|---|
| `bake` | twelve characters taken from what the library holds. Two bakes of the same extract are the same bake, one file changing makes another. A build says which one it read, a verification is recorded against it, and a rollback names it |
| `extracted` | when the data was cut from the planet, as the extract's own header records it, not when we downloaded it |
| `road_level` | which layers it answers for: those of every level up to it. Compared with the build's before anything is downloaded, so a library baked for less costs one manifest and not one tile per tile |

**A library answers every road level up to the one it was baked at.** The planet is baked at road
level 5, which holds every road a lower level asks for, and each file is cut down on arrival to
exactly what the build's level asks (`osm.narrowed`): a way is kept when its tags match one of the
level's selectors, a node when a kept way names it, and the digest is taken again over what is
left, so the result is bit for bit what a bake at that level writes. The four layers other than
`small_roads` ask the same question at every level and pass through untouched. A library baked
below the level asked cannot invent the roads it lacks and is skipped entirely: one baked at road
level 1 sends a build at level 2 or above to the live servers. Asking for exactly the baked level,
as the chain first did, sent every build at the default level 1 to the public servers once the
planet was baked at 5 (2026-09-25). A copy of the library in a folder is read the same way.

The coverage is *not* whatever the extract's bounding box covers. Geofabrik clips to a country
outline, so a square on the border of the download holds one side of it and nothing of the other,
which is the Geneva failure produced by our own tool. The bake reads the `.poly` beside the `.pbf`
and publishes only squares wholly inside it; Europe loses 96 squares of 1 723 that way.

Three sizes are refused before they can hurt: a file the manifest announces above 120 MB, a body
that does not weigh what the manifest says, and a frame that unpacks to more than 800 MB. A
kilobyte and a half of zstd unpacks to fifty megabytes, and `max_output_size` does not stop it: a
frame that declares its own size is unpacked to that size whatever the limit says.

## 4. A library must not slow a build down

A prepared source exists to save seconds; one that hangs would cost them. Therefore, per request:
5 s to connect, 30 s without receiving a byte, one attempt, then the next source. The limit is on
silence, not on the whole transfer: a file of the planet library weighs up to 61 MB (Tokyo's small
roads) and its manifest 28 MB, and a limit of 35 s on the whole of it, as the client first had,
failed them below 14 and 6.5 Mbit/s and set the library aside after two tiles (2026-09-25). A body
longer than the largest file the manifest may announce is not read to its end. And per build: **two failures of
the same library and it is set aside for the rest of the run**, in the manner of the Overpass
breaker but simpler, since a library holds no quota and needs no cooldown. A source that refuses a
tile it announced must say so by raising, or the chain can never count it: xpconnect used to
return `None` for everything, so a service that had stopped answering cost every tile of a batch
two minutes of waiting, twice, in silence.

Not answering is not the same as refusing the key. A 401 or a 403 closes the library for the run,
because the same key will be refused at the next tile; anything else, a 502, a cut connection, a
restart of the server, is waited out for two minutes and asked again. One hiccup used to close the
library for the whole job, so a build begun at the wrong second read no prepared tile at all. A
second failure does close it for the build: a library down all build long was asked every two
minutes, and a silent one cost 35 s each time, some fifty minutes of a three-hour build
(2026-09-25).

While a library cannot be reached, the copy of its manifest kept on disk stands in for it. It was
written at every build and read back at none. A copy is only ever a list of what to ask for, and
every file it names is still checked against the digest it names, so an old copy costs a refusal,
never a wrong tile.

**Whatever goes wrong, the tile goes to the public servers, as every tile did in 0.1.15.** Each
case below is a test through the path a build takes (`test_sources_chain.py`), with Overpass faked
where a build reaches it:

| What goes wrong | What the build does | What it costs |
|---|---|---|
| the server is down, or nobody listens at the address | the tile from Overpass; the manifest is asked once more two minutes later, the copy kept on disk standing in meanwhile, then the library is set aside for the build | 5 s at most, twice in a build |
| the server says nothing for 30 s | the same | 35 s at most, twice in a build |
| the manifest answers a 5xx, or is not one of ours | the same; a document that is not ours closes the library at once | nothing more |
| the key is refused (401, 403) | every tile of the build from Overpass; `OSM_LIBRARY_KEY_REFUSED` said once | nothing more |
| a tile the manifest lists is missing, damaged, of the wrong size or digest | that tile from Overpass; after two such tiles the library is set aside for the build, `OSM_PREPARED_SET_ASIDE` said once | one request per tile, two tiles at most |
| a tile the library does not hold | that tile from Overpass | nothing |
| a fault in our own code reading the library | the tile from Overpass: `OsmJob` catches whatever the chain raises | nothing |
| the user asked for fresh data | the library is not asked at all | nothing |

A source's own timeouts never touch the Overpass politeness rules, which stay as `osm-source.md`
describes them.

## 5. What the user sees, and what the tile remembers

The Data step names where the layers came from: *from your folder*, *prepared, 13 September*,
*downloaded*. The snapshot already carries a `mirror` field, and it holds that name
(`folder`, `library`, `overpass:<mirror>`), so a tile built months ago still says
what it was made from, and the Works page and the job's journal say it while it happens.

When every source fails, the error is the one `osm-source.md` describes, and it names what each
tried source answered.

Three things used to happen in the log alone, each of them turning a build that would have read
prepared tiles into one that queues behind the public servers for an hour:

| Code | When |
|---|---|
| `OSM_PREPARED_FOLDER_MISSING` | the folder named in Settings is not there, so the setting does nothing |
| `OSM_LIBRARY_KEY_REFUSED` | the key was refused, or none was given beside an address |
| `OSM_LIBRARY_UNREACHABLE` | the manifest could not be read at all, or a key was given without an address |
| `OSM_LIBRARY_INCOMPLETE` | a tile the manifest lists and the library then refused, or served as a file that is not the one announced |
| `OSM_PREPARED_SET_ASIDE` | a source failed twice and is not asked again for this build |

The settings are checked once, before a build starts; the set-aside is said once, when it happens,
not once per tile.

The doctor asks the question before a build does: `map_library` says how many tiles the library
this build carries holds and when they were cut, or that the build carries none at all. A release
is built with the address and the key written in from the repository's secrets, and if that step
is skipped or the key is rotated, everything still works and every tile is downloaded live, which
is precisely the kind of silence this whole chain exists to end.

## 6. Settings

| Setting | Default | What it does |
|---|---|---|
| `expert.osm_folder` | empty | a folder of prepared layers, in our format or Ortho4XP's (bzip2 OSM 0.6 XML) |
| `expert.osm_library` | empty | another baked library to read; empty means the one this version carries |
| `expert.osm_library_token` | empty | the key of that library, sent with every request, manifest included; the key this version carries goes to its own library only, never to an address typed here |

Neither the address nor the key this version carries is ever shown: an empty field stands for
them, so the address stays out of the page as it stays out of the README and the log. The hints
said until 2026-09-25 that an empty address switched the library off, which is the opposite of
what a build does. There is no switch to turn the library off, and none is needed: a library that
fails is set aside by itself (section 4), and fresh data skips it (section 1).

A user pointing `osm_folder` at what they already downloaded is the request that started this
(a user, 2026-09-23); it is first in the chain because it is local, free, and theirs.

## 7. How this is tested

* one test per source, against a library fixture our own tools bake from a small extract;
* a chain test: first source empty, second broken, third answers, fourth never asked;
* a **poisoned library**: wrong digest, truncated file, empty road layers, a tile listed but
  absent. Every one must fall through and be recorded, never consumed;
* the equality that matters: for a sample of tiles, the snapshot a library gives and the snapshot
  Overpass gives must have the same `digest`, which is what proved the bake right on 2026-09-23
  (identical to 0.1 %, every difference an edit made after the extract's date) and what proved
  xpconnect short at Geneva.

Nothing here reaches the network in the tests: the sources take their transport injected, as
`OverpassClient` already does.

## 8. How the library is made, and what travels

The library is made from OpenStreetMap's planet file (the date is in its manifest). Only the
elements the layers of `orthostudio.sources.osm` ask for at road level 5 are kept, and each
one-degree square gets what lies in it or crosses it, cut with a margin of 0.1 degree, in the
format this document describes. The tools that make and publish it are not part of this
repository (2026-09-25).

**The manifest travels compressed.** The site compresses what it sends (`encode zstd gzip` in its
block of the web server's configuration, 2026-09-25): the manifest, 28.4 MB of JSON, arrives as
3.4 MB, and the client, which asks for it, unpacks it without a word. The layer files are zstd
already and pass as they are, at exactly the size the manifest announces. A build downloads the
manifest once, at its first tile that needs map data.

Every request of a build also names the program and its version (`OrthoStudio-XP/0.1.17 (+...)`,
the user agent the app sends everywhere), which is what the server's log shows: which version asks
what. It is no lock, since anyone may send the same words, but a program that does not bother
stands out (2026-09-25). The server's log never holds the key: the web server writes
`Authorization` as `REDACTED`.
