# OSM layers: the typed replacement of `OSM_layer` and `update_dicosm`

Status: P4 wave 1, written before `src/orthostudio/vectors/osmdata.py` and
`src/orthostudio/vectors/tags.py` (tests `tests/test_vectors_osmdata.py`). Origin: Ortho4XP `src/O4_OSM_Utils.py:22-283` (`OSM_layer`,
`update_dicosm`), `:392-464` (`OSM_queries_to_OSM_layer`, where `input_tags` / `target_tags` are
built), `:587-641` (`OSM_to_MultiLineString`), `:643-772` (`OSM_to_MultiPolygon`), and the call
sites of `src/O4_Vector_Map.py` (`:184-193` airports, `:256-306` roads, `:365-399` coastline,
`:504-539` water, `:658-667` patches). Upstream: `docs/specs/osm-source.md` (the client, the
snapshot, the Ortho4XP `.osm.bz2` writer). Downstream: `docs/specs/vectors-pslg.md` (what the noder
expects), the layer builders of wave 1 (coastline, water, roads, patches) and, in wave 2, the
airports (arbitration A1).

Acceptance: **structural identity** with `update_dicosm`, proven out of process by running
the Ortho4XP parser itself on the same file and comparing every structure *id for id* — the
internal negative ids included, since they are assigned in reading order and decide the
iteration order of everything downstream. Measured in section 8.

## 1. The rule in plain language

`OSM_layer` is Ortho4XP's in-memory OSM store. It is filled by a hand-written line splitter,
`update_dicosm`, which never parses XML: it splits each line on the quote character found in
the `<osm …>` line and picks fields by position. It does four things at once:

1. **renumbers**: every OSM id it reads is replaced by an internal id, negative and
   decreasing, assigned in reading order (nodes `-1, -2, …`, ways and relations likewise);
2. **deduplicates nodes by coordinate**: two nodes with the same `(lon, lat)` pair become one
   internal node, so a way can reference a node it never declared;
3. **filters tags**: only the tags the caller asked for are kept, and the elements that carry
   a tag of the *query* are marked as "first catch" (directly queried) as opposed to children
   pulled in by the `(._;>>;)` recursion;
4. **rebuilds multipolygons**: the `outer` / `inner` way members of a relation are stitched
   end to end into closed rings of node ids, and an ill-formed relation is dropped whole.

OrthoStudio XP keeps all four behaviours exactly. What changes is the shape of the code (a typed
reader with a named structure instead of a 230-line loop over a dict of dicts), the input (an
OrthoStudio XP snapshot; a `.osm` text file for patches) and the API offered to the layer builders.

## 2. Inputs

`OsmData.load(source, *, layer=None, tile=None)` accepts:

| Source | Reading order | Note |
|---|---|---|
| `Path` to a `.osm` text file | file order | a `*.patch.osm` file (until decision 0010 also Ortho4XP's `.osm.bz2` cache and the user's custom coastline and water files) |
| `OsmSnapshot` (`orthostudio.sources.osm`) | **sorted** by `(node, way, relation)` then by OSM id | section 2.1 |
| `Path` to a `.osm.json.zst` snapshot | idem, after `SnapshotStore`-style decompression | |

`layer` is a `LayerTags` (section 3) or a layer name; `None` means "keep every tag and mark
every way and relation as first catch", which is what Ortho4XP does for custom files and patches
(`update_dicosm(path, input_tags=None, target_tags=None)`, `O4_Vector_Map.py:371, 509, 660`).
`tile` is needed only by the geometry helpers of section 5.

`OsmData.update(source, layer=None)` adds a second source to an existing store, as Ortho4XP does
for a directory of custom coastline files (`O4_Vector_Map.py:379-388`): the node
deduplication and the id counters carry over between calls, the per-file id maps do not
(`O4_OSM_Utils.py:58-59`).

### 2.1 Why a snapshot is read in id order

`docs/specs/osm-source.md` section 6 already fixed the order of the Ortho4XP cache file: all
nodes, then all ways, then all relations, each group sorted by OSM id, because the reader
numbers its internal ids in reading order. Reading a snapshot in that same order is what makes
the two inputs give **the same internal ids**, hence the same structures and the same
iteration orders. A snapshot keeps the mirror's `qt` order on disk (it helps the spatial
passes); the ingestion sorts. **Decision: keep** (consistency with the writer already
accepted in P3).

Coordinates are rounded to **7 decimals** on ingestion of a snapshot, because that is what
the cache file spells (``"{:.7f}"``, ``O4_OSM_Utils.py:302-303``) and because the exact pair
of floats is the node deduplication key (R1). Overpass already delivers 7 decimals, so this
changes no real value; it makes the two sources provably identical instead of identical in
practice.

The two element-level rules of the writer are part of the ingestion too, for the same reason:

* a way with an empty node list, or referencing a node absent from the source, is **skipped**
  (`osm-source.md` 6.4; in the file path the Ortho4XP reader deletes the empty way at
  `O4_OSM_Utils.py:182-189` and would raise `KeyError` on the unknown node);
* a relation member that is not `type="way"` with role `outer` or `inner` is ignored
  (`:128-142`), and a member naming a way that was skipped is ignored (`:143-146`).

## 3. Tag sets (`tags.py`)

`OSM_queries_to_OSM_layer:395-419` derives two dictionaries from the query selectors and from
`tags_of_interest`, both keyed by OSM type (`n`, `w`, `r`):

* `input_tags[t]`: one `(key, value)` pair per selector of type `t`, `value` being `""` when
  the selector names a key only (`way["aeroway"]`). An element carrying such a tag is a
  **first catch**.
* `target_tags[t]`: `input_tags[t]` plus, **for the types that appear in the selectors only**,
  one `(tag, "")` per entry of `tags_of_interest`. A tag is kept when it is in
  `target_tags[t]`, or when `("all", "")` is.

The "for the types that appear in the selectors only" is not a simplification: the
`tags_of_interest` loop is nested inside the loop over selectors and appends to the current
`osm_type` (`:409-419`). Consequences on the real layers, kept verbatim:

| Layer | `input_tags` | `target_tags` | Origin |
|---|---|---|---|
| `airports` | n/w/r: `("aeroway", "")` | n/w/r: `("aeroway", "")`, `("all", "")` | `O4_Vector_Map.py:185-193` |
| `big_roads` | w: `("highway","motorway")`, `("highway","trunk")`, `("highway","primary")`, `("highway","secondary")`, `("railway","rail")`, `("railway","narrow_gauge")` | w: idem + `("bridge","")`, `("tunnel","")` | `:261-275` |
| `small_roads` | w: `("highway","tertiary")` (+ `unclassified`, `residential` at `road_level>=3`, `service` at `>=4`, `track` at `>=5`) | w: idem + `("bridge","")`, `("tunnel","")` | `:288-306` |
| `coastline` | w: `("natural","coastline")` | w: idem (`tags_of_interest` empty) | `:391-399` |
| `water` | r: `("natural","water")`, `("waterway","riverbank")`; w: `("natural","water")`, `("waterway","riverbank")`, `("waterway","dock")` | r and w: idem + `("name","")`; **n: empty** | `:525-539` |

So a node tagged `name=…` in the water layer keeps no tag at all, and `("all","")` in the
airports layer keeps every tag of every node, way and relation. **Keep**, both.

The selectors themselves are not re-typed here: `tags.py` derives the tag sets from
`orthostudio.sources.osm.LAYERS` (`osm-source.md` section 3), which already holds them verbatim with
their origin. `tags_from_selectors(selectors, tags_of_interest)` is the transcription of
`:395-419`, including its `except IndexError` for a key-only selector.

`tags.py` also holds the two tag sets the layer builders use on the result, with their
origin: `ROAD_EXCLUSION_TAGS = {"bridge", "tunnel"}` (`O4_Vector_Map.py:258`, a way carrying
either is dropped from the road network) and `LAKE_NAME_TAG = "name"` (`:459-482`, the lake
whose name is in `good_imagery_list` escapes the sea treatment). They are data for wave 1's
builders, not behaviour of this module.

## 4. The structures

```python
@dataclass
class OsmData:
    nodes: dict[int, tuple[float, float]]  # dicosmn: (lon, lat)
    ways: dict[int, list[int]]  # dicosmw
    relations: dict[int, dict[str, list[list[int]]]]  # dicosmr: closed rings of node ids
    relations_orig: dict[int, dict[str, list[int]]]  # dicosmrorig: way ids, unordered
    first: dict[str, set[int]]  # dicosmfirst
    tags: dict[str, dict[int, dict[str, str]]]  # dicosmtags
```

Same names, same contents, same types as the six dictionaries of `OSM_layer.__init__`
(`:24-48`), one rename apart (`relations_orig` for `dicosmrorig`). Keys are the internal
negative ids. Rules, one per behaviour of the reader:

**R1 — node renumbering and deduplication** (`:86-104`). `(lon, lat)` read with `float()`
from the attribute text is the deduplication key, compared as an exact pair of floats, and
`nodes[id] = (lon, lat)` keeps the *first* spelling met. The per-file map from the OSM id
(a **string**, not an int: Ortho4XP never converts it) to the internal id is what `<nd ref=>`
resolves. **Keep**, including the string keying (two ids that differ as text but not as
integers cannot occur in OSM, so this is invisible; it is kept because it costs nothing).

**R2 — way renumbering, empty ways** (`:105-114, 182-189`). A way takes the next negative id
at its opening; at `</way>`, a way with no node is deleted, **its id is given back**
(`next_way_id += 1`, so the next way reuses it) and it is removed from `first["w"]` and from
`tags["w"]`. **Keep**, id recycling included: it shifts every following id and is therefore
observable.

**R3 — first catch** (`:111-113, 122-124, 163-181`). With no `input_tags` every way and every
relation is a first catch at its opening. With `input_tags`, only a tag matching
`input_tags[t]` (by key when the selector has no value, by key and value otherwise) adds the
element to `first[t]`. **Keep.** `first` is a real `set[int]` filled by the same sequence of
`add` / `remove` as Ortho4XP's, so its iteration order — which decides the order of the
geometries handed to the noder, hence which layer wins a shared z (`vectors-pslg.md` 2.4) —
is identical on the same interpreter. **Keep, and tested** (section 8, `first_ways` in file
order).

**R4 — tag filter** (`:163-181`). A tag is stored when there is no filter, or when `("all","")`,
`(k,"")` or `(k,v)` is in `target_tags[t]`. Tag values are stored raw, exactly as the source spells
them: the Ortho4XP reader never unescapes XML entities, so a value read from a `.osm.bz2` keeps its
`&amp;` and its `&#x0a;`. OrthoStudio XP does the same when it reads a `.osm` file, **and
unescapes nothing when it reads a snapshot either**: a snapshot holds the strings Overpass sent.
**Wanted difference, none.**

**R5 — relation members** (`:128-162`). Members are read in file order. A member that is not
a way, or whose role is neither `outer` nor `inner`, is dropped (a `node` member silently, the
rest with a level-2 log). A member naming an unknown way is dropped. For each kept member the
way id is appended to `relations_orig[id][role]`; then, if the way is already closed
(first node == last node) its node list is appended to `relations[id][role]` **as is**;
otherwise its two end nodes are indexed in a per-role open-ends table. **Keep.**

**R6 — ill-formed relation** (`:190-215`). At `</relation>`, if any open end of either role is not
shared by exactly two ways, the whole relation is dropped: `relations`, `relations_orig`, the id
(given back), `first["r"]` and `tags["r"]`. **Keep**; OrthoStudio XP reports it as
`OSM_RELATION_INVALID` (info, continue) instead of a level-2 print.

**R7 — ring stitching** (`:216-243`). While the open-ends table of a role is not empty: take
its *first* key in insertion order, start from the first way recorded there, walk from end to
end always taking the other way of the pair, appending each way's nodes minus its last one
(reversed when the way is met backwards), delete each consumed end, and close the ring with
the starting node. **Keep**, insertion order included — it decides where the ring starts and
in which direction it runs, and the ring's node order reaches the mesh.

**R8 — children of a relation** (`:244-252`). *Only when there is no tag filter*
(`target_tags is None`), the member ways of a relation are removed from `first["w"]`, so a
custom water file does not draw its multipolygon rings twice. With a filter (the queried
layers) they stay, and are drawn again as ways if they carry a first-catch tag of their own.
**Keep** — it looks like an oversight, it is observable, and wave 1 reproduces Ortho4XP.

**R9 — relation with no outer ring** (`:253-260`). Dropped like R6 (id given back, `first`
and `tags` cleaned). **Keep.**

**R10 — truncated file** (`:262, 265-277`). The document must contain `</osm>`; otherwise Ortho4XP
prints an error and returns 0, and the caller treats the layer as missing. OrthoStudio XP raises
`OSM_CACHE_UNREADABLE` with the path and the remedy (delete and refetch). **Fix**: Ortho4XP returns
a *silently empty* layer, which is exactly how a tile ends up with no sea (`errors.md`). A snapshot
cannot be truncated (it carries its digest).

**R11 — what is *not* kept.** `dicosmn_reverse` (an implementation detail of R1) is private;
`next_node_id` / `next_way_id` / `next_rel_id` are private counters. The `UI.vprint` progress
counts of `:278-282` are replaced by the `counts` property.

## 5. Geometry (the API the layer builders use)

Three methods, all in **tile-local coordinates**: `x = lon - tile.lon`, `y = lat - tile.lat`,
rounded to **7 decimals**, exactly as `OSM_to_MultiLineString:604-615` and
`OSM_to_MultiPolygon:660-671, 690-706` do (`vectors-pslg.md` 2.8 records the 7 decimals as
the layer builders' business; here it is).

```python
ways_with(tags=None, *, exclude=()) -> list[WayGeometry]        # WayGeometry(id, coords, tags)
multipolygons_with(tags=None, *, exclude=()) -> list[PolygonGeometry]
node_coords(ids) -> ndarray
```

* **Selection.** `tags=None` selects `first["w"]` (resp. `first["w"] + first["r"]`), which is
  what Ortho4XP iterates. A non-empty `tags` (keys or `(key, value)` pairs) narrows that selection
  to the elements carrying one of them — what the airport module of wave 2 needs
  (`aeroway=runway`, `aeroway=taxiway`, …) and what lets a builder split one layer in two.
  `exclude` drops the elements carrying one of its keys: `OSM_to_MultiLineString:596-603`
  with `tags_for_exclusion = {"bridge", "tunnel"}`.
* **Order.** The iteration order of `first[…]`, then ways before relations, as Ortho4XP does.
* **`ways_with`** returns the coordinates as an `(n, 2)` float64 array (lon-like first), never
  a `LineString`: `refine_way`, `improved_buffer` and the banking test of the road builder all
  work on arrays. A way of fewer than two points is dropped (Ortho4XP drops it through the
  `except` around `LineString`, `:616-624`).
* **`multipolygons_with`** returns shapely geometry, one entry per *polygon*:
  - a first-catch **way** must be closed (`:648-657`; an open way is skipped and reported
    `OSM_WAY_NOT_CLOSED`), gives `Polygon(coords)`, and is skipped when its area is zero or
    when it is invalid (`OSM_WAY_INVALID`);
  - a first-catch **relation** gives `unary_union(valid outer rings).difference(unary_union(
    valid inner rings))`, then that result is exploded into polygons, each dropped when its
    area is zero or when it is invalid (`OSM_RELATION_INVALID`). Invalid *rings* are dropped
    before the union (`:707-710, 718-721`), an exception anywhere drops the whole relation
    (`:727-730`). **Keep**, all of it, including the fact that the difference is computed
    before the explosion, so an inner ring of one outer ring cuts every outer ring of the
    relation.
* Orientation is whatever shapely's constructor produces from the ring as stored; Ortho4XP never
  normalises it and the noder does not care (it consumes rings as linework). **Keep.**
* `on_skip(code, context)` is called for each dropped element instead of Ortho4XP's log line;
  the integrator turns it into an `OsxpError` record. No exception is raised for data faults.

The filter callbacks of Ortho4XP (`road_is_too_much_banked`, `filter_large_lakes`) are **not**
here: they are the builders' business, and they need the DEM or the configuration. This module
gives geometry and tags; the builder decides.

## 6. What this module does not do

The coastline closure (`coastline_to_MultiPolygon`), `cut_to_tile`, `refine_way`,
`improved_buffer`, the water simplification, the patches (`include_patches` reads its
`.patch.osm` through this module but computes altitudes itself) and the whole airport chain
(wave 2, arbitration A1) are other specs. Nothing here touches the network: the source is a
snapshot or a warm cache file, both already on disk.

## 7. Wanted differences, in full

| # | Ortho4XP | OrthoStudio XP | Why |
|---|---|---|---|
| 1 | a file without `</osm>` gives an empty layer and a printed error | `OSM_CACHE_UNREADABLE` | R10; the silent empty layer is a known cause of tiles without sea |
| 2 | ill-formed relation / open way / invalid polygon printed at level 2 | `on_skip` with a registry code (`OSM_RELATION_INVALID`, `OSM_WAY_NOT_CLOSED`, `OSM_WAY_INVALID`), counted | the decision report of `errors.md` |
| 3 | `OSM_to_MultiLineString` returns a `MultiLineString` | `ways_with` returns arrays | every consumer needs the array; building the shapely object and taking it apart again costs a copy per way |
| 4 | one class holding the store *and* the download | `OsmData` holds the store only | the client is `orthostudio.sources.osm` (ADR 0003) |
| 5 | `tags_of_interest` is re-derived at every call site | `LAYER_TAGS` computed once from `LAYERS` | one origin for the selectors (`osm-source.md` 3) |

| 6 | `x = lon - tile.lon`, whatever the longitude | the abscissa is folded by one exact turn of the globe when it leaves `[-180, 180]` (`geom.wrap_local_x`) | a way crossing the antimeridian was *mirrored into* the tile, not merely lost: a fabricated coastline on tiles `±179` (review 5). The fold is a no-op, bit for bit, everywhere else |

Nothing else. In particular the internal negative ids, the coordinate deduplication, the id
recycling of R2, the `first["w"]` set and its iteration order, the R8 asymmetry and the ring
stitching of R7 are reproduced as they are.

**Fidelity is conditioned on CPython.** The insertion order of the ways — hence which layer wins a
shared vertex's altitude, hence the tile's altitudes — is the iteration order of `first["w"]`, a
`set` of small negative integers. That is Ortho4XP's own accident and OrthoStudio XP reproduces it
(`coast.way_set_order`), but it is an interpreter property, not a specification: it was verified
on **CPython 3.14** and a different implementation (or a change to CPython's small-int hashing)
would move altitudes silently.
`tests/test_review5_robustesse_architecture.py::test_the_way_order_of_the_pslg_depends_on_cpython_set_iteration`
is the guard rail: it pins the observed order against `way_set_order`.

## 8. Acceptance and measurements

**Oracle test** (`tests/test_vectors_osmdata_oracle.py`, marker `oracle`). For each of the four warm
caches of `+43+005` (`airports`, `big_roads`, `coastline`, `water`), the Ortho4XP
`OSM_layer.update_dicosm` runs in a sub-process on the same file with the same `input_tags` and
`target_tags`, and prints a fingerprint: the counts, then the **raw** structures with their internal
ids (`dicosmn` in id order, `dicosmw`, `dicosmtags`, `dicosmr`, `dicosmrorig`, and
`list(dicosmfirst["w"])` in *iteration* order), each hashed with sha256 after a canonical
`json.dumps`. The same sub-process then runs `OSM_to_MultiLineString` and, on the layers that have
closed ways, `OSM_to_MultiPolygon` on that store and hashes their output. OrthoStudio XP reads the
same file and must produce the same counts, the same structure hashes and the same geometry hashes.

Result, 2026-09-12, tile `+43+005`, the four warm caches
(`tests/test_vectors_osmdata_oracle.py`, 14 tests, 15.0 s):

| Layer | nodes | ways | rels | first n/w/r | structures | `ways_with` | `multipolygons_with` |
|---|---|---|---|---|---|---|---|
| airports | 9 544 | 1 041 | 3 | 804 / 1 031 / 3 | 8/8 hashes equal | 1 031 lines, equal | 546 polygons, equal |
| big_roads | 163 297 | 25 750 | 0 | 0 / 25 750 / 0 | 8/8 hashes equal | 25 750 lines, equal | not tested (open ways) |
| coastline | 39 545 | 424 | 0 | 0 / 424 / 0 | 8/8 hashes equal | 424 lines, equal | not tested (open ways) |
| water | 117 298 | 4 734 | 113 | 0 / 4 144 / 113 | 8/8 hashes equal | 4 144 lines, equal | 4 271 polygons, equal |

The eight hashes are `dicosmn`, `dicosmw`, `dicosmr`, `dicosmrorig`, `dicosmtags`, and the
three `dicosmfirst` sets **in iteration order**, each over the internal negative ids. The
`ways_with` hash is the list of coordinate arrays of `OSM_to_MultiLineString`, in order; the
`multipolygons_with` hash is the list of polygon WKTs of `OSM_to_MultiPolygon`, in order. So
the equality is not "the same data up to a canonical form": it is the same objects, in the
same order, under the same ids.

The same test rebuilds a snapshot out of each cache file (elements renumbered 1..N in file
order) and checks that reading it gives the same store as reading the file, structure by
structure and `first["w"]` order included: the promise of section 2.1, on real data.

**Timing and memory** (M4 Pro, `nice -n 10`, load average 1.8-2.5, best of three runs, whole
file, no tag filter, 2026-09-12):

| Layer | Ortho4XP `update_dicosm` | OrthoStudio XP from the `.osm.bz2` | OrthoStudio XP from a snapshot (read + ingest) | Ortho4XP RAM | OrthoStudio XP RAM |
|---|---|---|---|---|---|
| airports | 0.029 s | 0.029 s | 0.019 s | 3.4 MB | 3.0 MB |
| big_roads | 0.861 s | 0.873 s | 0.666 s | 91.0 MB | 81.2 MB |
| coastline | 0.114 s | 0.107 s | 0.074 s | 12.3 MB | 10.0 MB |
| water | 0.358 s | 0.343 s | 0.232 s | 37.5 MB | 30.4 MB |
| **total** | **1.362 s** | **1.352 s** (x1.01) | **0.992 s** (x1.37) | 144 MB | 125 MB (-13 %) |

Honest reading of these numbers: on the `.osm.bz2` path OrthoStudio XP is at **parity**, not faster,
because it runs the same line splitter and because bz2 decompression alone is 0.42 s of the
0.87 s of `big_roads`. The gain comes from the snapshot path (x1.37, and no bz2 at all) and
from the memory. This reader is not where a tile spends its time (1.4 s against the 109 s of
an Ortho4XP build): it is ported for *fidelity*, so that everything downstream can be rewritten
against a known-identical input. A vectorised ingestion (numpy `unique` over the coordinate
pairs for R1) would cut the remaining time, at the price of having to prove the id assignment
order again; it is not worth it before the layer builders are measured.
