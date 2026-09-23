# Prepared OSM layers: the sources a tile is asked of, in order

Status: written before the code (0.1.15). Companion of `osm-source.md`, which stays the
description of Overpass itself. Origin: the night of 2026-09-22, when two of the three public
Overpass machines a build asks became unusable at once and every build on every continent stopped
on its Data step; and the days after it, when a user who had built almost a whole state read that
downloads had "turned random".

The data behind that step is public and downloadable in bulk: one country extract holds what
thousands of live queries would ask for, and the same four questions are asked of it for every
tile anyone builds. A tile's layers can therefore be **prepared once and read as files**, in 60 ms
instead of 8 to 30 s, with no quota and no bad evenings. What follows is how a build chooses
between the prepared libraries and the live servers, and — the harder half — how it refuses a
library that would quietly give it less than the truth.

## 1. The chain

After the tile's own cache has missed, the layers are asked of these, in this order, until one
answers:

| | Source | Present when | Typical |
|---|---|---|---|
| 1 | the user's own folder | `advanced.osm_folder` names one | instant |
| 2 | our baked library | its address and token are set (they ship with the app) | ~60 ms |
| 3 | xpconnect (OrthoForge) | switched on **and** the tile is on our whitelist | ~300 ms |
| 4 | Overpass | always, last | 8-30 s |

The order follows what is known, not what is fast: ours is the only online library whose coverage
we established ourselves; xpconnect covers what we will not bake, at their expense; Overpass alone
is live and complete.

**Fresh data skips the libraries.** A tile asked for with `refresh` (`OsmParams.refresh`, what a
user presses after correcting their region in OSM) goes straight to Overpass: a prepared library
is weeks behind by design, and that is its only real defect.

## 2. What a source is

One method, three outcomes, nothing else:

```python
class PreparedSource(Protocol):
    name: str
    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot] | None: ...
```

* **the layers** — every one of `specs`, taken as they are, the chain stops;
* **`None`** — this source does not hold the tile, the chain moves on without a word;
* **an exception** — the source is broken; the chain moves on and records the reason for the
  report and the log.

**All or nothing, per tile.** A source that holds three of the four layers gives nothing. Mixing
sources inside one tile is how an incoherent tile is made: a coastline truncated at a national
border under roads that are complete, and nothing to show for it. The libraries bake all layers
of a tile together, so the rule costs nothing real.

## 3. Refusing a library that gives less than the truth

This is the part that decides whether the chain is trustworthy, and it is not symmetric: a library
that is *absent* is harmless, a library that is *short* is not. Measured on xpconnect,
2026-09-19 and again 2026-09-23, both times unchanged:

* 7 132 tiles listed, **1 559 of them (22 %) with both road layers empty**;
* the Geneva tile (`+46+006`) holds 10 110 road ways where a live query returns 24 307: the bake
  is made per country extract and the Swiss half of that tile is simply not in it;
* nothing in the file says so. It downloads, its sha256 matches, the XML is valid, and it holds a
  third of the roads. A build would lay scenery with no roads and no water and report success.

Three defences, in order of cost:

1. **An empty layer is not an answer from whoever cannot prove it.** A layer file under 200 bytes
   holds no element at all, not even the wrapper of an empty layer, and the tile is refused
   before anything is downloaded when the manifest carries sizes, as xpconnect's does. Above
   that, an empty layer is refused from an XML source, which can prove nothing about itself, and
   taken from a library whose manifest announces the file's digest and whose file matches it: a
   square of Atlantic off the Sahara really has no road, no airport and no lake, only a
   coastline, and refusing those sent every empty square of a continent to the public servers to
   be told the same thing. What emptiness must never mean is a bake cut short, and that is the
   coverage polygon's business. A folder is read the same way when a `manifest.json` sits beside
   the files, which is what a copied library is; an Ortho4XP folder has none and keeps the strict
   rule.
2. **A library is used only where it has been verified.** For xpconnect this means a whitelist:
   the tiles we have compared, tile by tile, against a live count (`[out:csv(::count)]`, 13 bytes
   of answer, 13 s of their computation) or against our own bake of the same square. The
   whitelist travels **with our manifest**, so a build that cannot reach our library has no
   whitelist and skips xpconnect entirely. No verification, no use.
3. **Every file is checked at the door**: its digest against the manifest, then the document is
   read. Either failing makes the tile move to the next source, with the reason recorded.

The whitelist is void when the publisher rebakes: xpconnect's manifest carries a `version`
(`2026-08-08`, unchanged since we first looked). A different version means the whitelist must be
built again, and until it is, that library is skipped. Our manifest therefore carries not only
`verified_elsewhere` but `verified_elsewhere_version`, the version we compared against; the two
travel together and the source compares them itself.

**A layer whose question depends on the road level is never read from an XML source.** Their files
are OSM 0.6 XML and carry no selectors: nothing in one says whether `small_roads` was baked for
tertiary roads or for tracks as well, and taking it at a road level it was not baked for gives a
scenery quietly missing every forest track. Our own format carries its selectors and is checked
against them, so only the XML sources (a folder in Ortho4XP's layout, xpconnect) are held to this.

**Our own library needs no whitelist**, since its manifest is written by the same tool that cut
the tiles: a tile is in it when its layers were written, and the coverage is whatever the extract
covered. What it does carry, per file, is the digest and the size, and per bake:

| Field | What it is for |
|---|---|
| `bake` | twelve characters taken from what the library holds. Two bakes of the same extract are the same bake, one file changing makes another. A build says which one it read, a verification is recorded against it, and a rollback names it |
| `extracted` | when the data was cut from the planet, as the extract's own header records it, not when we downloaded it |
| `road_level` | which layers it answers for. Compared with the build's before anything is downloaded, so a library baked for other layers costs one manifest and not one tile per tile |

A library baked at road level 1 answers builds at road level 0 and 1, which is the default and
what almost every build uses, and is skipped entirely above that: level 2 adds tertiary roads,
which the extract was filtered out of and the bake never saw. Such a build downloads every layer
live, as every version before this one. Serving the four layers that do not depend on the road
level from the library and asking the servers for the fifth alone would be sound, since both
sources cover the whole square, but it is not what the all-or-nothing rule says today and it is
not worth breaking that rule without measuring first.

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
5 s to connect, 30 s to read, one attempt, then the next source. And per build: **two failures of
the same library and it is set aside for the rest of the run**, in the manner of the Overpass
breaker but simpler, since a library holds no quota and needs no cooldown. A source that refuses a
tile it announced must say so by raising, or the chain can never count it: xpconnect used to
return `None` for everything, so a service that had stopped answering cost every tile of a batch
two minutes of waiting, twice, in silence.

Not answering is not the same as refusing the key. A 401 or a 403 closes the library for the run,
because the same key will be refused at the next tile; anything else, a 502, a cut connection, a
restart of the server, is waited out for two minutes and asked again. One hiccup used to close the
library for the whole job, so a build begun at the wrong second read no prepared tile at all.

While a library cannot be reached, the copy of its manifest kept on disk stands in for it. It was
written at every build and read back at none. A copy is only ever a list of what to ask for, and
every file it names is still checked against the digest it names, so an old copy costs a refusal,
never a wrong tile.

A source's own timeouts never touch the Overpass politeness rules, which stay as `osm-source.md`
describes them.

## 5. What the user sees, and what the tile remembers

The Data step names where the layers came from: *from your folder*, *prepared, 8 August*,
*downloaded*. The snapshot already carries a `mirror` field, and it holds that name
(`folder`, `library`, `xpconnect`, `overpass:<mirror>`), so a tile built months ago still says
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
| `advanced.osm_folder` | empty | a folder of prepared layers, in our format or Ortho4XP's (bzip2 OSM 0.6 XML, which is also xpconnect's) |
| `advanced.osm_library` | our address | the baked library to read; empty switches it off |
| `advanced.osm_library_token` | ships with the app | sent with every request, manifest included |
| `advanced.osm_prepared_public` | on | whether xpconnect may be used where whitelisted |

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

## 8. Publishing a library, and checking the one that is published

Published with `tools/bake/publish.py`, which is three steps and one rule each:

1. **the files first, and alone.** A client reads the manifest and then asks for what it names; a
   manifest that arrives first announces files that are not there yet, and every tile of every
   build in progress pays four refused requests for it. `rsync -a --delete` does exactly the wrong
   thing, since `manifest.json` sorts before `osm/`.
2. **the manifest it replaces is kept** under the name of its bake. The files are never removed,
   so putting an old manifest back serves the old library again: that is the rollback
   (`publish.py --rollback <bake>`).
3. **the manifest last, and by rename**, so a client reads the old one or the new one and never
   half of either.

Never bake into the served tree: the bake rewrites its manifest after every block, and a client
that reads a half-written one sets the library aside for its whole job.

A library replaced in place keeps whatever the last one left, and what the manifest does not name
is never served but still takes room and still hides what the server holds: `publish.py --prune`
lists it and, with `--yes`, removes it. That is a separate step because it ends the rollback.

Everything else proves a tile in the folder it was baked into, and between that folder and a user
there is an upload that can stop half way, a key that can be revoked, a server that can serve a
stale copy. So `tools/bake/verify_library.py` reads the published library with the same client and
the same key a build uses, takes a sample of tiles and says how many come back whole, and with
`--compare` weighs one of them against the live servers as the map stood when the extract was cut.
The key is read from a file and never printed, logged or put in a URL.
