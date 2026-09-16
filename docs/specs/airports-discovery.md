# Airport discovery and surface attachment

Status: P4 wave 2, written before `src/orthostudio/airports_vec/model.py` and
`src/orthostudio/airports_vec/discover.py` (tests `tests/test_aptdata_discover.py`; the oracle
test of section 9 left with decision 0010).
Origin: Ortho4XP `src/O4_Airport_Utils.py:19-177` (`discover_airport_names`),
`:177-329` (`attach_surfaces_to_airports`), `:682-700` (`discard_unwanted_airports`),
`:805-847` (`update_airport_boundaries`), `:847-910` (`list_airports_and_runways`),
`:910-924` (`build_airport_array`), and the call order of
`src/O4_Vector_Map.py:196-223` (`include_airports`).
Upstream: `docs/specs/vectors-osm-layers.md` (the `airports` store this reads).
Downstream: the runway reconstruction, the hangar / apron / taxiway area builders and the
airport smoothing of the same wave; `docs/specs/vectors-assembly.md` section 7
(`VectorLayers.airports`, `airport_bounds`); `docs/specs/dsf-terrain-assignment.md`
(`cover_airports_with_highres` reads the footprints, arbitration B4).

> **Integration (P4 wave 2).** The questions this spec leaves to the integrator are
> answered in `docs/specs/airports-integration.md` section 6, and the end-to-end numbers
> are in `docs/benchmarks/p4-airports.md`.

Acceptance: **structural identity** with the `dico_airports` of Ortho4XP on `+43+005`, proven out
of process by running the Ortho4XP functions themselves on the same warm cache and comparing the
keys *in insertion order*, the key types, the names, the representative nodes, the boundary
WKTs and the attached way / relation ids *in order*, category by category. Measured in
section 9.

## 1. What this module is

`dico_airports` is Ortho4XP's only airport record. It is a plain `dict` built in three passes —
discover the aerodromes, attach the loose surfaces to them, drop the ones that are too small —
and then completed by the surface builders and re-shaped by `update_airport_boundaries`. Its
**insertion order is load-bearing**: `attach_surfaces_to_airports` walks it and stops at the
*first* airport whose boundary a surface meets (`:194-201`), and the nearest-airport fallback
keeps the *first* minimum (`:219-223`). Two airports that overlap therefore split the
surfaces by the order OSM spelled them, and any reordering moves runways from one aerodrome to
another, hence moves the flattening, hence moves altitudes.

OrthoStudio XP keeps that order exactly. What changes is the shape: a typed `Airport` / `AirportSet`
instead of a dict of dicts with positional tuples, and a spatial index instead of the double
Python loop (section 5). Nothing geometric changes.

This module owns the **beginning** of the chain only: identity, boundary, attachment, the size
filter, the final boundary union, the neighbourhood raster and the listing. The runway
reconstruction (`sort_and_reconstruct_runways`), the hangar / apron / taxiway area builders
and `smooth_raster_over_airports` are other modules of the same wave; they fill
`Airport.areas` and this module reads it back (sections 6 and 7).

## 2. The structures (`model.py`)

```python
AirportKey = str | tuple[float, float]
Category = Literal["runway", "taxiway", "apron", "hangar"]


@dataclass(slots=True)
class Airport:
    key: AirportKey
    key_type: Literal["icao", "iata", "local_ref", "name", "repr_node"]
    name: str
    repr_node: tuple[float, float]  # absolute (lon, lat) degrees
    boundary: BaseGeometry | None  # see R7 and section 6
    ways: dict[Category, list[int]]  # internal way ids, attachment order
    runway_rels: list[int]  # `runway_as_rel`
    areas: SurfaceAreas  # filled by the surface builders
    smoothing_pix: int | None = None
    elevation: float | None = None  # additive, section 8 difference 5


@dataclass(slots=True)
class SurfaceAreas:
    runway: BaseGeometry | None = None  # `runway[0]`
    runway_as_area: tuple[RunwayPart, ...] = ()  # `runway[1]`
    runway_as_line: tuple[RunwayPart, ...] = ()  # `runway[2]`
    taxiway: BaseGeometry | None = None  # `taxiway[0]`
    apron: BaseGeometry | None = None  # `apron[0]`
    hangar: BaseGeometry | None = None  # `hangar`


@dataclass(frozen=True, slots=True)
class RunwayPart:  # one entry of `runways_as_area` / `_as_line`
    polygon: geometry.Polygon
    start: NDArray[np.float64]  # tile-local (x, y)
    end: NDArray[np.float64]
    width: float  # metres
```

`SurfaceAreas` is the exact content of the four positional slots Ortho4XP overwrites in place
(`runway` becomes a 3-tuple at `:676-680`, `apron` and `taxiway` 2-tuples at `:767` and
`:800`, `hangar` a bare `MultiPolygon` at `:731`). Keeping the *way ids* in `Airport.ways`
and the *geometry* in `Airport.areas` splits what Ortho4XP packs into one slot; the second element
of Ortho4XP's `apron` and `taxiway` tuples is exactly `Airport.ways["apron"]` and
`Airport.ways["taxiway"]`, unchanged.

`AirportSet` is an ordered mapping over `AirportKey`: `__iter__` yields the airports in
insertion order, `keys()` the keys in insertion order, `add`, `pop`, `get`, `__contains__`,
`__len__`. It is a thin wrapper on a `dict`, for the reason of section 1 — the order *is* the
specification — and because Ortho4XP keys by an ICAO string **or** by a `(lon, lat)` tuple of
`numpy.float64` (`:110`, `:243`), which a list cannot index.

`model.py` also carries the two geodetic constants the airport rules use, transcribed from
`O4_Geo_Utils.py:4-28`: `EARTH_RADIUS = 6378137`, `LAT_TO_M`, `M_TO_LAT`, `m_to_lon(lat)` and
`great_circle_m(a, b)` — the Ortho4XP `GEO.dist`, formula for formula.

### 2.1 The seam with the geometry modules of the same wave

The runway and surface modules of this wave name the record structurally, with Ortho4XP's own flat
field names (`areas.SurfaceRecord`, `areas.SurfaceSet`, and the same shape in `runways`).
`Airport` therefore exposes `runway`, `taxiway`, `apron`, `hangar` and `runway_as_rel` as
read-only views of `ways` and `runway_rels`, and `AirportSet` exposes `store` (the `OsmData`
the record was read from) and `airports` (the record as a read-only `Mapping`). Nothing is
copied and neither side imports the other. The fit is pinned as a *contract* in
`tests/test_aptdata_discover.py::test_the_record_exposes_ortho4xps_flat_field_names`, not as an
annotation against the sibling module's `Protocol` classes, which are still moving as that
chantier lands.

An `AirportSet` built by hand carries no store, and asking it for one is
`OSM_AIRPORT_INFO_UNAVAILABLE` rather than an `AttributeError` three frames down.

## 3. Discovery (`discover_airport_names`, `:19-177`)

**R1 — which elements are aerodromes** (`:20-27`). For `osmtype` in `("r", "w", "n")`, in that
order, walk `tags[osmtype]` **in insertion order** and keep the elements one of whose tag
*values* is `"aerodrome"` or `"airstrip"`. The test is on values, not on the `aeroway` key, so
`landuse=aerodrome` catches too. **Keep**, order included: it fixes the order of the whole
record, hence the attachment (section 1). Note that this walks the *tag* dictionary, not the
first-catch set, so an element pulled in by the `(._;>>;)` recursion counts as well.

**R2 — the key** (`:28-42`). The first of `icao` (truncated to 4 characters), `iata`
(3 characters), `local_ref` (whole value) that the element carries. **If that key is already
in the record the element is skipped entirely** — a second `LFML` element adds nothing, not
even a surface. **Keep.**

**R3 — the name** (`:43-63`). The first of `name:en`, `name:alt`, `name`, with `&quot;` and
`&apos;` replaced by `"` and `'` — and nothing else, so `&amp;` survives as text. Missing, the
name is `"****"`. A name of 60 characters or more becomes `name[:57] + "..."`. **Keep**,
including the asymmetry of the unescaping (the store keeps XML entities raw,
`vectors-osm-layers.md` R4) and the `>= 60` boundary.

**R4 — the representative node** (`:64-88`). A node gives its own `(lon, lat)`; a way gives
`tuple(numpy.mean(coords, axis=0))` over **all** its nodes, the closing node of a closed way
counted twice; a relation gives the same mean over `relations[id]["outer"][0]`, its first
outer ring only. Absolute degrees, unrounded. **Keep**, `numpy.mean` included — it is a
pairwise summation and the value reaches a `< 3500` comparison in R11 and, when it becomes a
key, a dict.

**R5 — the fallback key** (`:89-99`). With no `icao` / `iata` / `local_ref`: if the *name* is
already a key, skip the element; otherwise the key is the name (`key_type="name"`) unless the
name is `"****"`, in which case it is the representative node itself (`key_type="repr_node"`),
skipped in turn if that tuple is already a key. **Keep**, the `name in dico_airports` test
before the `"****"` test included.

**R6 — `smoothing_pix`** (`:105-113`). An integer tag, kept when `int()` accepts it, dropped
silently otherwise. It overrides the tile's `apt_smoothing_pix` for this airport in the
smoothing module. **Keep.**

**R7 — the boundary** (`:114-177`). A way gives `Polygon(absolute coords)`, a relation the
`unary_union` of one `Polygon` per outer ring, a node `None`. **Absolute degrees**, not
tile-local, and no rounding: that is what `attach_surfaces_to_airports` and
`discard_unwanted_airports` consume, and `update_airport_boundaries` translates it later
(section 6). An invalid polygon is reported and replaced by `None` (`:160-168`,
`OSM_AIRPORT_BOUNDARY_INVALID`); any exception raised while building it **removes the airport
from the record altogether** (`:169-177`, `OSM_AIRPORT_TAG_INVALID`). **Keep**, both.

The truthiness test Ortho4XP writes as `if apt["boundary"]` is shapely's: `None` and an *empty*
geometry are both false. OrthoStudio XP spells it `model.has_boundary(airport)`, and
`discover.live_boundary(airport)` returns the geometry itself so the call sites narrow the
type without an `assert` that `python -O` would delete (review 5).

## 4. Attachment, the ways (`attach_surfaces_to_airports`, `:177-280`)

**R8 — which ways, in which order** (`:179-186`). For `surface_type` in
`("runway", "taxiway", "apron", "hangar")`, in that order, walk **`ways` in insertion order**
(every way of the store, not only the first catches) and keep those whose kept tags carry
`aeroway == surface_type`. **Keep**: the order decides which airport the fallback of R12
creates first, and the outer loop over the four categories decides the order of those
creations.

**R9 — the test geometry** (`:187-193`). `LineString(absolute coords of the way)` — a
*line*, even for a closed apron or hangar outline, so a surface strictly inside a boundary
with no shared edge still counts (`intersects` covers containment).

**R10 — the owning airport** (`:194-201`). The **first** airport of the record, in insertion
order, that has a boundary and whose boundary the linestring `intersects`. Airports created by
R12 during this pass have no boundary and never own anything. **Keep** — see section 5 for how
OrthoStudio XP finds that first one without the double loop.

**R11 — the nearest-airport fallback** (`:202-225`). Failing that, the mean of the way's nodes
(`numpy.mean`, as R4) is compared to the `repr_node` of **every** airport of the record — the
ones without a boundary and the ones R12 just created included — with `GEO.dist`, the great
circle distance in metres. The first strict minimum wins; the surface is attached when that
minimum is below **3500 m**. **Keep**, including the fact that the record grows while the
categories are walked, so the same way can be attached to an airport that did not exist when
the previous category was walked.

**R12 — the orphan** (`:226-252`). Otherwise a new airport is created *on the spot*, keyed by
the way's `name` tag (`key_type="name"`) or, when there is none, by the mean point
(`key_type="repr_node"`), with `boundary=None` and the surface attached to it. Ortho4XP writes
this with a bare `try/except KeyError` on the tag and **overwrites an existing entry of the
same name** (`:230`): a hangar tagged `name=voisin` next to an airport already keyed
`"voisin"` resets that airport's surfaces to empty. **Keep**; reproduced literally, and
reported as `OSM_AIRPORT_SURFACE_INVALID` is *not* — this is not an error, it is Ortho4XP's
record-keeping, and it is observable on `+43+005` (key `"voisin"`).

## 5. The spatial index: what replaces the double loop

R10 is the hot point: Ortho4XP calls `linestring.intersects(boundary)` in a Python double loop,
`O(surfaces x aerodromes)` GEOS calls. OrthoStudio XP builds **one `shapely.STRtree`** over the
boundaries of the airports that have one, in insertion order, and asks it
`tree.query(linestring, predicate="intersects")`; the owner is the **smallest index** the
query returns, which is by construction the first airport in insertion order. The tree is
built once because no boundary changes during the pass and the airports created by R12 have
none (section 4).

This is an exact substitution, not an approximation: `STRtree.query(..., predicate=...)`
evaluates the very GEOS predicate `BaseGeometry.intersects` evaluates, on the same pair of
geometries; the tree only decides *which pairs* are worth evaluating. The proof is the oracle
test: same ids, same order, same categories (section 9).

The nearest-airport search of R11 stays a scalar loop over `great_circle_m`. It runs only for
the surfaces R10 rejected, the record holds tens of airports, and the search **must** see the
entries R12 appends as it goes; a vectorised distance would have to be rebuilt after each
append and would risk moving a `< 3500` boundary case by an ulp.

## 6. The size filter and the final boundary

**R13 — `discard_unwanted_airports` (`:682-700`).** In insertion order: an airport *with* a
boundary is dropped when `boundary.area < 5000 * M_TO_LAT * m_to_lon(tile.lat)` — an area in
square degrees against a threshold built from 5000 m2 — and is **never** checked again;
an airport without a boundary is dropped when its **runway area** is below the same product
built from 2500 m2. Reported `OSM_AIRPORT_TOO_SMALL`. **Keep.**

The runway area is `SurfaceAreas.runway`, produced by the runway module of this wave. When it
is `None` — the module has not run — `discard_unwanted` raises `OSM_AIRPORT_INFO_UNAVAILABLE`
instead of treating the airport as empty: Ortho4XP would raise `AttributeError` there, and
silently discarding every boundary-less airport is exactly the kind of empty-tile failure
`errors.md` exists to stop.

**R14 — `update_airport_boundaries` (`:805-838`).** For every airport, in order:
`boundary = union(taxiway, apron, hangar, runway)` (all four **tile-local**, as the surface
builders produce them); when the airport had an OSM boundary, that boundary is translated by
`(-tile.lon, -tile.lat)` and unioned in. The result is `.buffer(0).simplify(0.00001)`, then
`ensure_MultiPolygon`. After this call `Airport.boundary` is a tile-local `MultiPolygon`, and
every later consumer (the smoothing, the raster of R15, the DSF cover) reads it as such.
**Keep**, including the order of the union arguments and the `simplify` tolerance.

`affinity.translate(geom, -lon, -lat)` is a coordinate subtraction, not a rounding: the
translated boundary keeps the full precision of the absolute degrees, unlike every other
tile-local geometry of the port, which is rounded to 7 decimals. **Keep** — the difference is
below 1e-9 degrees but it reaches `simplify`.

**R15 — `build_airport_array` (`:910-924`).** A `1001 x 1001` boolean raster of the tile:
for each airport the bounding box of its (tile-local) boundary, grown by 1500 m in each
direction, is set to `True`; rows count from the north. `colmin = max(round((xmin - dx) *
1000), 0)`, `rowmax = min(round(((1 - ymin) + dy) * 1000), 1000)`, and the slice is
`[rowmin:rowmax + 1, colmin:colmax + 1]` — inclusive on both ends, one pixel wider than the
rounding suggests. **Keep**, the inclusive slice included: `roads.AirportAreas.contains`
already reads this raster with Ortho4XP's own indexing.

**R16 — `list_airports_and_runways` (`:847-910`).** Presentation only: the airports sorted by
key inside each `key_type` group, the groups in the order `icao`, `iata`, `local_ref`, `name`,
`repr_node`; the runway count is `len(runway_as_area) + len(runway_as_line)`, and an airport
with none is listed as `boundary`. OrthoStudio XP returns the rows as data (`listing()`), formats
nothing, and leaves the printing to the UI. Keys that are not ICAO-like are shown as `****`,
as Ortho4XP does.

## 7. What this module does not do

`sort_and_reconstruct_runways`, `build_hangar_areas`, `build_apron_areas`,
`build_taxiway_areas`, `smooth_raster_over_airports`, `encode_runways_taxiways_and_aprons`,
`encode_hangars`, `flatten_helipads` and `cover_airports_with_highres` are other modules.

**Overlap to resolve at integration.** The geometry chantier of the same wave landed
`runways.wanted_airports`, `areas.update_boundary` and `areas.airport_array` in this very
package: R13, R14 and R15 exist twice, each proven against the oracle by its own test. They
are the same rules, not different ones; the integrator keeps one pair of call sites and the
other becomes a thin alias. This module keeps its three because the chantier brief lists them
and because they are what makes `discover` usable end to end on its own.
Nothing here reads `apt.dat` (arbitration B5): the geometry starts from the OSM `aeroway`
layer and from nothing else. Nothing here writes a file: the rule module of this wave serialises
the `AirportSet` this module returns (`airports-integration.md` 4).

## 8. Wanted differences, in full

| # | Ortho4XP | OrthoStudio XP | Why |
|---|---|---|---|
| 1 | a bare `except:` around the boundary construction prints a warning | `OSM_AIRPORT_TAG_INVALID` through `on_event`, the airport still removed | `errors.md`; the behaviour is unchanged, the report is not a print |
| 2 | a one-node `aeroway=runway` way raises inside `LineString` and kills the step | the way is skipped and reported `OSM_AIRPORT_SURFACE_INVALID` | a crash is not a behaviour worth porting; no such way exists in `+43+005`, so the difference is unobservable there |
| 3 | `discard_unwanted_airports` raises `AttributeError` when the runways were not reconstructed | `OSM_AIRPORT_INFO_UNAVAILABLE` | section 6 |
| 4 | `list_airports_and_runways` prints | `listing()` returns rows | the UI decides |
| 5 | nothing carries the aerodrome elevation | `Airport.elevation`, the OSM `ele` tag parsed as a float when it parses | additive and **consumed by nothing in this port**: the page of `docs/specs/ui.md` wants it. It cannot change any geometry |
| 6 | the record is a `dict` of `dict`s with positional tuples | `AirportSet` of `Airport` with `SurfaceAreas` | section 2; the insertion order, the keys and the contents are identical |
| 7 | a way's or relation's `repr_node` is a tuple of `numpy.float64` | a tuple of Python `float` | `float(numpy.float64)` is exact, and the two hash and compare equal, so a key written by one is found by the other; the pickle bridge of B3 therefore stays compatible |

Nothing else. In particular R2's "skip the whole element", R5's `name in dico_airports` test,
R12's overwrite of a same-named airport, the double counting of a closed way's closing node in
R4, the 3500 m threshold and the inclusive slice of R15 are reproduced as they are.

## 9. Acceptance and measurements

**Oracle test** (`tests/test_aptdata_oracle.py`, marker `oracle`). A sub-process runs the Ortho4XP
chain itself on the warm `+43+005_airports.osm.bz2` — `discover_airport_names`,
`attach_surfaces_to_airports`, `sort_and_reconstruct_runways`, `discard_unwanted_airports`,
the three area builders, `update_airport_boundaries`, `build_airport_array` — and dumps, at
each stage, the keys **in insertion order**, the key types, the names, the representative
nodes, the boundary WKTs, the attached way and relation ids **in order**, and the row sums of
the raster. OrthoStudio XP must match, stage by stage:

1. after `discover`: the key list, and for each airport the key type, the name, the
   representative node (exactly), the boundary WKT and `smoothing_pix`;
2. after the attachment: the key list again (R12 grew it) and the four id lists plus
   `runway_as_rel`, in order;
3. after `discard_unwanted`, `update_boundaries` and `build_airport_array`: the surviving
   keys, the boundary WKTs and the raster — computed from the **Ortho4XP areas**, injected as WKB,
   so this chantier is measured without the runway and surface modules it does not own.

The frozen `fixtures/large/oracle/+43+005_zl14_BI/build/Data+43+005.apt` is the same data
recorded: the test loads it (in the test only, never in `src`) and checks the keys, the key
types, the names, the representative nodes, the final boundary WKTs and the `apron` /
`taxiway` id lists it still carries.

Result, 2026-09-12, tile `+43+005`, warm cache (8 oracle tests, 0.5 s):

| Stage | Ortho4XP | OrthoStudio XP | Comparison |
|---|---|---|---|
| discovery | 22 airports | 22 | key list, key types, names, repr nodes, boundary WKTs, `smoothing_pix`: equal |
| attachment | 37 airports, 606 surfaces | 37, 606 | key list and the five id lists of every airport, in order: equal |
| discard | 18 kept | 18 | key list: equal |
| boundaries | 18 MultiPolygons | 18 | WKT: equal, character for character |
| raster | 35 188 true pixels | 35 188 | every one of the 1001 row sums: equal |
| listing | 18 rows | 18 | group order, sort inside each group, runway counts: equal |

**On the frozen `Data+43+005.apt`.** Keys, key types, names, representative points, the
`apron` / `taxiway` / `runway_as_rel` id lists **and** the final boundaries are identical:
18 of 18 in `equals_exact(0)`, and so are the `runway`, `taxiway`, `apron` and `hangar`
areas. (Corrected by review 6. This paragraph used to report 7 of 18 boundaries and blame a
GEOS drift; both interpreters run shapely 2.1.2 / GEOS 3.13.1, and the disagreement was the
test's own Ortho4XP driver, which never set `O4_Vector_Utils.scalx`
(`O4_Vector_Map.py:27`) before calling the area builders -- `improved_buffer` and
`buffer_simple_way` then buffered with `scalx = 1`. The frozen pickle is a valid reference for
the whole record, geometry included, and must not be regenerated.)

**Timing** (M4 Pro, `nice -n 10`, load average 2.99, best of five, same store in memory):

| Step | Ortho4XP | OrthoStudio XP | Ratio |
|---|---|---|---|
| `discover_airport_names` | 1.07 ms | 0.99 ms | x1.08 |
| `attach_surfaces_to_airports` | 13.93 ms | 3.73 ms | **x3.73** |

x3.7, not the x50 the index promises, and the reason is in the tile: `+43+005` has **14**
aerodrome boundaries, so Ortho4XP's double loop is at most 14 predicate calls per surface and the
constant costs — building 606 linestrings, walking 1 041 ways four times, 2 106 great-circle
distances for the 80 orphan surfaces — dominate. The index earns its keep as the record grows,
which `tests/test_aptdata_discover.py::test_the_index_answers_what_the_double_loop_answers`
measures on synthetic records where half the surfaces sit on an aerodrome and half sit nowhere
(600 surfaces, same answers as the double loop, every time):

| Boundaries | double loop | index | Ratio |
|---|---|---|---|
| 8 | 4.66 ms | 0.38 ms | x12 |
| 64 | 33.48 ms | 0.38 ms | x88 |
| 512 | 235.50 ms | 0.66 ms | **x356** |

The double loop is linear in the number of aerodromes, the index is flat until the tree itself
costs something. A dense tile (a metropolitan area, or a whole-country batch reusing one
record) is where the x50 appears; on `+43+005` the honest figure is x3.7.
