# Vectors to PSLG: the planar graph handed to Triangle4XP

Status: shipped (`src/orthostudio/vectors/noding.py`, `src/orthostudio/vectors/triangle_files.py`);
the crossing arithmetic of 2.8 was corrected in wave 2 (chantier `nodes10`). Origin: Ortho4XP
`src/O4_Vector_Utils.py` (class `Vector_Map`, lines 44-615) and `src/O4_Vector_Map.py`
(`build_poly_file`, lines 19-180, and the `include_*` functions). Acceptance: semantic comparison
with Ortho4XP's `.node`/`.poly` of a tile: nodes as a set at 1e-9, edges as unordered pairs with
identical markers, z identical to 1e-9 on every node of an INTERP_ALT-class edge. Since wave 2 the
two sets were *equal* on the reference tile (0 node and 0 edge on either side alone), measured until
decision 0010: `docs/benchmarks/noding.md`. What separates the two `.node` files is their node
**numbering** and three coordinates: see 2.8, "what is left".

## 1. What the stage does

Step 1 of Ortho4XP turns OSM ways and airport geometry into a *planar straight-line graph*
(PSLG): nodes with a vector altitude z, undirected edges with an attribute, plus region seeds.
Triangle4XP triangulates it under the constraint that every edge is a mesh edge, then floods
("plagues") the attributes from the seeds until an edge carrying that bit stops them. The
graph must therefore be planar: any two edges either do not meet or meet at a common end
node; otherwise the plague leaks and the mesh has crossing constraints.

Inputs are in **tile-local coordinates**: `x = lon - tile.lon`, `y = lat - tile.lat`, both in
[0, 1] for the tile, extended to [-2^-5, 1 + 2^-5] for the orthophoto grid lines. The
coordinates are anisotropic (one unit of x is `cos(lat)` times shorter than one unit of y).

## 2. Rules, origin and decision

Each rule: what Ortho4XP does, where, and what OrthoStudio XP does (**keep** / **fix** = wanted
difference / **drop**).

### 2.1 Attribute bits

| bit | name | meaning |
|---|---|---|
| 0 | DUMMY | constraint only (grid, border, airport traverses, flat roads) |
| 1 | WATER | inland water (lakes, rivers) |
| 2 | SEA | coastline |
| 4 | SEA_EQUIV | inland water larger than `max_area`, masked like the sea |
| 8 | INTERP_ALT | z taken from the vector data (roads, patches) |
| 16 / 32 / 64 / 128 | RUNWAY / TAXIWAY / APRON / HANGAR | airport surfaces (all imply INTERP_ALT: `attr >= 8`) |

Origin: `O4_Vector_Utils.py:44-54`; consumption `O4_Mesh_Utils.py:251-264, 297-300`
(triangles with `attr >= INTERP_ALT` take their vertex z from the vector attribute, the
others from the DEM). **Keep** (`MARKERS` in `noding.py`).

### 2.2 Planar-graph invariant

Every inserted edge is tested against all existing edges whose bounding box overlaps its own
(rtree, `O4_Vector_Utils.py:127-129`); each contact splits both edges at the contact point and
the pieces are re-inserted (`insert_edge`, lines 117-226). Result: no two edges cross, no
vertex lies strictly inside an edge, no two edges overlap collinearly.
**Keep.** OrthoStudio XP checks it on its own output (`check_planar`, 0 violations on the reference
tile and on 200 k synthetic segments).

### 2.3 Contact detection (`are_encroached`, lines 252-306)

* A 2x2 solve gives the parameters `alpha` (on the new edge) and `beta` (on the old one).
  `numpy.linalg.solve`, not Cramer, and the difference is observable: see 2.8 and
  `_solve_2x2`. The pair is a contact when both are in [0, 1] **and at least one is inside
  (1e-8, 1 - 1e-8)** (lines 278-287): two edges that merely share an end point are not
  split. **Keep** (`ENCROACH_EPS = 1e-8`). OrthoStudio XP adds a 1e-12 slack on the [0, 1] test to
  absorb the last bits of the solve (**fix**, no observable effect on the reference tile).
* Parallel when `|det| <= 1e-8 * |ab| * |cd|` (line 279); then collinear when
  `|cross(ab, ac)| <= 1e-8 * |ab| * |ac|` (line 293); collinear overlap when the projections
  overlap beyond 1e-8 (lines 296-306). Parallel-but-not-collinear pairs are never split,
  even when they actually cross at a tiny angle. **Keep** for parity; flagged as a candidate
  fix (a robust orientation test) once a real tile shows a leak caused by it.
* Two edges sharing an end point exactly are skipped unless they fold back onto each other
  (lines 273-276, cosine threshold 0.9999). OrthoStudio XP skips every such pair in the transverse
  branch and lets the collinear branch handle fold-backs (**fix**: the near-parallel
  shared-end case produced spurious contacts 1e-11 from the shared node).

### 2.4 The z rule

* A node created at a transverse contact gets its z **interpolated on the pre-existing
  edge** (`(1 - beta) * z[id2] + beta * z[id3]`, lines 152-158, comment "important to rely on
  the old id2 id3"), never on the edge being inserted. Since layers are inserted by priority,
  the higher-priority layer decides z; inside a layer, the earlier way decides.
* The pre-existing edge is the **current piece** of the chain (already split by earlier
  insertions), so the interpolation runs between the two nearest existing nodes, which may
  be earlier crossings or vertices with their own z (for example a grid crossing inside a road
  polygon is interpolated between the two road-edge crossings on the same grid line).
* A node that already exists keeps its z (`insert_node`, lines 78-87): a vertex of a way is
  inserted before its edges are checked (`insert_way`, lines 228-235), so a vertex landing
  on an existing edge keeps the way's z; a vertex landing on an existing node (crossing or
  vertex) takes nothing.
* Collinear overlaps create no node.

**Keep**, exactly, by processing layers one after the other against the accumulated graph
(pieces are current) and, inside a layer, by giving every event an insertion time (vertex m
at 2m, segment (m, m+1) at 2m+3, a crossing at the time of the newer segment) and keeping
the earliest event's z per node. Known approximation: inside one layer a crossing is
interpolated on the *original* segment of the earlier way, not on its current piece; this
differs from Ortho4XP only when a vertex of the same layer with its own z first splits that
segment (0 occurrences among the 107 230 INTERP_ALT nodes of the reference tile).

### 2.5 Markers of coincident edges

Re-inserting an existing edge, in either direction, ORs the marker into it (`update_edge`,
lines 89-104; also in the split pieces, lines 213-226, and in `snap_to_grid`, lines 494-514).
**Keep** (`np.bitwise_or.at` over identical unordered node pairs).

### 2.6 Layer order (= priority)

`build_poly_file` (`O4_Vector_Map.py:52-167`) inserts, in this order:

1. airports (`include_airports`, line 53): per airport, runway polygons (RUNWAY, z from
   the smoothed raster) then their DUMMY traverse lines, taxiways (TAXIWAY), aprons (APRON),
   then hangars (HANGAR) and helipads (INTERP_ALT) — `O4_Airport_Utils.py:1038-1462`;
   patches (`include_patches`, `O4_Vector_Map.py:639-968`) are inserted from
   `include_airports` too;
2. roads (`include_roads`, line 63): buffered banked roads as INTERP_ALT polygons refined
   every 100 m (line 342); the "flat network as DUMMY lines" branch is disabled
   (`if False`, line 347);
3. coastline (`include_sea`, line 74): SEA lines cut strictly inside the tile (line 407);
4. inland water (`include_water`, line 84): WATER polygons then SEA_EQUIV polygons
   (lines 555-587), simplified by `water_simplification`;
5. orthophoto grid as DUMMY (lines 96-130): vertical lines first, then horizontal ones;
6. gluing border as DUMMY (lines 136-152).

**Keep** the order; OrthoStudio XP expresses it as the order of the `layers` list. The bench replays
the 100 consecutive runs of equal marker recorded from Ortho4XP as 100 layers (the last DUMMY run
split into verticals, horizontals, border).

### 2.7 Coordinates and scale

`scalx = cos((lat + 0.5) * pi / 180)`, `scaly = 1` (`O4_Vector_Utils.py:15-19`,
`O4_Vector_Map.py:27`) are used only where a metric is needed (`refine_way`, buffers,
normals, banking); the noder compares raw local coordinates. **Keep**: `node_layers` takes
local coordinates and needs no scale; the metric functions belong to the layer builders.

### 2.8 Rounding

* OSM ways are rounded to **7 decimals** once converted to local coordinates
  (`O4_OSM_Utils.py:608-616, 660-668`), airport ways too (`O4_Airport_Utils.py:1108, 1135,
  1185, 1204, 1262, 1273`), patch anchors too (`O4_Vector_Map.py:914-915`). Grid, border,
  refined points and contact points are not rounded.
* At the end, `snap_to_grid(9)` (`O4_Vector_Map.py:167`, `O4_Vector_Utils.py:469-535`)
  merges nodes whose coordinates agree to 9 decimals (the first inserted keeps its z) and
  drops zero-length edges; the `.node` file is written with `{:.9f}`.

**Keep 9**: OrthoStudio XP uses the 9-decimal rounding as the node identity throughout (packed int64
key) and stores the un-rounded first-seen coordinate until output. The 7-decimal input
rounding is the layer builders' business (P4), not the noder's.

#### The ten nodes Ortho4XP writes 1e-9 apart (chantier `nodes10`, wave 2)

**The observation.** On tile +43+005, Ortho4XP writes ten pairs of nodes 1e-9 apart, five
abscissae on `y = 0` and the same five on `y = 1`, every incident edge DUMMY. Wave 1 merged
them (220 471 nodes against 220 481 without airports, 247 272 against 247 282 with), which
renumbered Triangle's input and made the mesh, the masks and the DSF equivalent but not
byte-identical.

**Why Ortho4XP has two nodes there.** Three facts meet:

1. the orthophoto grid abscissa is `x = til_x * 180 / 2**(zl-1) - lon`; for one grid line in
   eight it comes out as `k / 1024` with `k` odd -- **exactly representable in binary and
   exactly halfway at the 9th decimal**. Tile +43+005 has 11 such lines, five of which matter
   below: `145/1024`, `235/1024`, `415/1024`, `685/1024`, `955/1024`. `round(x, 9)` and
   `"{:.9f}".format(x)` both break that tie **to even**, so the grid line's own vertices go
   one way, deterministically;
2. the gluing border has its vertices at `k / 2048` (`O4_Vector_Map.py:137`), so each of
   those grid abscissae **is** a border vertex, written with the same to-even tie break;
3. the crossing of the horizontal grid line `y = 0` with the vertical one is computed by
   `insert_edge` as `(1 - alpha) * a + alpha * b` **on the horizontal line**
   (`O4_Vector_Utils.py:149-156`), i.e. on `[-2**-5, 1 + 2**-5]`, and the last bits of that
   expression put it one or two ulps off the tie. When the noise falls on the side the tie
   break did *not* choose, `snap_to_grid(9)` gets two different keys and keeps two nodes; the
   border polyline, inserted afterwards, then meets the crossing node strictly inside one of
   its segments (collinear branch, `alpha0 > 0` with no eps: lines 200-205) and splits it, so
   the two nodes are joined by a 1e-9 DUMMY edge.

  Of the 11 ties of the tile, 5 land on the "wrong" side and give a visible pair; the other 6
  land on the same side as the tie break and Ortho4XP merges them itself. The five are exactly
  the five observed -- predicted, not fitted (`tests/test_nodes10_solve.py`).

**Rule (fix of wave 2).** The crossing coordinate is `(1 - alpha) * a + alpha * b` on the edge
being inserted, with `alpha` from the **same 2x2 solve Ortho4XP runs, down to the last bit**:
`numpy.linalg.solve(column_stack((ab, dc)), ac)`, i.e. LU with partial pivoting, every
division done as a multiplication by the reciprocal (`_solve_2x2`, `noding.py`). That is
plain IEEE-754 arithmetic -- deterministic on any machine -- and it reproduces
`numpy.linalg.solve` bit for bit on this one (400 000 random pairs, `test_nodes10_solve.py`).
Cramer's rule is algebraically the same and numerically is not: it agrees with LAPACK on only
40 % of pairs, and four of the ten fall on the wrong side of the tie with it.

**Dropped (wave 2).** The axis-aligned substitution of wave 1 -- a crossing with a segment
whose `x` (or `y`) is constant took that exact coordinate instead of the computed one. It was
the deliberate cause of the merge: it erases exactly the noise that decides the tie. Removing
it alone recovers 6 of the 10 on the airports replay (8 of the 10 on the airport-free build,
which is what review 5 measured); removing it **and** matching LAPACK's arithmetic recovers
10 of 10 on both.

**What it costs and what it buys** (replay of the 6 197 recorded `insert_way` calls of
+43+005, airports included, `tests/test_nodes10_oracle.py`):

| | wave 1 | wave 2 | Ortho4XP |
|---|---|---|---|
| nodes | 247 272 | **247 282** | 247 282 |
| edges | 271 310 | **271 320** | 271 320 |
| nodes only ours / only Ortho4XP's | 0 / 10 | **0 / 0** | |
| edges only ours / only Ortho4XP's | 30 / 40 | **0 / 0** | |
| markers on the common edges | 0 mismatch | 0 mismatch | |
| z, maximum difference | 984 m (on 9 merged nodes) | **< 1e-9** | |
| planarity (`check_planar`) | 0 | **0** | |
| `node_layers` on the tile | 1.053 s | **1.052 s** | 23.1 s (`insert_way`) |

Without airports, against the fabricated Ortho4XP reference of arbitration A3: 220 481 nodes and
239 234 edges on both sides, 0 only ours, 0 only Ortho4XP's, 0 marker mismatch, z within 5.0e-10,
`edges_by_marker` identical to Ortho4XP's (DUMMY 38 808, WATER 79 947, SEA 37 327, WATER|SEA
1 609, INTERP_ALT 81 543).

#### Geometry within a millimetre of a grid line (known, 2026-09-25)

The contact tests above are relative to the edges' lengths, as Ortho4XP's are, so geometry a few
nanodegrees from a line of the orthophoto grid is neither merged with it nor kept clear of it.
Two cases, measured on real tiles with OSM from the planet library and X-Plane's relief:

* **a vertex beside a grid line.** On +34-118 a road vertex lies 0.065 mm east of the line
  `x = 547/1024`; the line cuts the road's previous edge 0.2 mm before it, a second node is made
  2e-9 away, and Triangle fills the gap with 872 323 points, 59 % of the tile's mesh. The DSF's
  quadtree then refused to split a bucket that is one position (`dsf-encoding.md` 3.1), which is
  what users saw as `OverflowError: Python integer -2 out of bounds for uint64`; the cap on its
  depth lets the tile build, its piled points sharing one DSF entry, but the mesh keeps the pile;
* **an edge along a grid line.** On +63-112 a lake's edge runs along the line for a hundred
  metres, 6e-9 from it: two parallel constraints 0.3 mm apart, a strip Triangle fills with
  17 391 points in one hundredth of a degree, where a cell holds 100 to 200.

A rule that reused the vertex a crossing lands within a millimetre of fixed the first and made the
second worse (159 975 points in the cell: the grid line, pulled onto the lake's vertices, met the
lake's edge at a vanishing angle), and was taken back. The fix is a noder that treats anything
within a millimetre as touching, a vertex near an edge, a crossing near a vertex and an edge near a
parallel edge alike, and it ships only once a bench of a few hundred real tiles, noded and meshed
before and after, shows no cell denser than before anywhere. Of 199 random configurations, main's
code gives no pile above 27 points on one DSF position, so the cap touches no DSF but a failed one.

#### What is left, and what it is not

Two node sets being equal is not two files being equal. Measured on the same replay:

* **three nodes of 247 282** round to a 9th decimal one unit away from Ortho4XP's (they are still
  matched at 1e-9; the set comparison above is unaffected). All three are grid crossings at a tie
  abscissa. Cause: inside **one** layer, Ortho4XP splits the pre-existing chain as it goes, so the
  `(c, d)` it hands the solve is the sub-piece bounded by the crossings already made, while the
  vectorised noder uses the whole pre-layer edge; the two `alpha` differ by a few ulps. Proof:
  replaying with each horizontal grid line as its own layer -- which *is* Ortho4XP's sequence --
  gives **247 282 / 247 282 nodes with the exact same 9-decimal key**, at 11.36 s instead of 1.05 s.
  The fix is therefore known and costed (x10.8 on the noding step, x1.6 since the incremental
  insertion of 2.13); it is not taken.
* the **node and edge numbering** differ: 226 910 of the 247 282 nodes are at the same index,
  the rest in 508 locally permuted runs. Ortho4XP numbers a crossing when it is created, and
  `insert_edge` walks the candidates in the order the **libspatialindex R-tree** returns them
  (`O4_Vector_Utils.py:127-134`, `insert_node` at line 159), not in order along the edge.
  That order is a property of the index's node splits, not of the geometry: it cannot be
  reproduced without reimplementing libspatialindex, and this spec does not propose to.

  Triangle4XP is sensitive to all three of these, separately (each variant below built from
  Ortho4XP's own PSLG with exactly one ingredient replaced, same binary, same raster, same
  options; the unmodified reference rebuilds `Data+43+005.mesh` **byte-identically**, which is
  what makes the measurement trustworthy):

  | input | mesh |
  |---|---|
  | Ortho4XP's `.node` + `.poly`, verbatim | byte-identical to the fixture |
  | Ortho4XP's coordinates and edges, **our node order** | differs (-18 B) |
  | Ortho4XP's coordinates and node order, **our edge order** | differs (+1 052 B) |
  | **our coordinates** (the three nodes), Ortho4XP's orders | differs (-2 B) |
  | everything ours | differs (+1 055 B), see below |

  With everything ours, 602 281 of the 603 649 vertices are at the same horizontal position,
  their altitudes agree within 4.23 m (282 above 1 m) and the altitude range is identical.

  Sorting both PSLGs into a canonical order (nodes lexicographic, edges sorted) brings the two
  `.node` files to **176 differing lines of 247 284** and the `.poly` files to **458 differing
  lines**; the meshes are then **+91 bytes in size** and the DSFs **+26 bytes in size** --
  but these are *size* deltas: the three nodes shift every later record, so 60 406 287 bytes
  of the mesh and 16 500 661 bytes of the DSF differ in place (review 6). The residue is small
  in geometry, not in bytes. With `exact_grid_order` (below) the canonical `.node`, `.poly`,
  mesh **and DSF** are byte-identical (`tests/test_p4v2_oracle.py`).

**Precision of the claims above (review 6).**

* "0 edges only ours / 0 only Ortho4XP's" is a **set** equality at a tolerance of 1.5e-9. At the
  exact 9-decimal key the PSLG of `+43+005` differs by the **three** nodes above (one unit of
  the 9th decimal) and by the **11** edges incident to them; z, markers, seeds and headers
  are identical.
* The number of such nodes **depends on the input**, it is not a structural 3: on the
  synthetic aeroway layer of `tests/test_review6_fidelite_synthetique.py` (placed on the same
  tile) it is **17** (the same 3 plus 14), on three vertical grid lines. `exact_grid_order`
  closes it in both cases (0 / 0 nodes, identical edges and seeds).
* What `nodes10` did to the airport-free **mesh**: -4 vertices / +2 triangles against Ortho4XP
  before, **+46 / +92** after, with a PSLG that became set-equal to Ortho4XP's. Triangle4XP's
  refinement is order-sensitive and the ten recovered nodes renumber everything after them;
  the numbers are frozen in `tests/test_p4_integration.py` and published in
  `docs/benchmarks/p4-vectors.md`.

### 2.9 Orthophoto grid and gluing border

* Grid: one vertical line per 16-tile column and one horizontal line per 16-tile row of the
  web-mercator grid at `mesh_zl`, plus x = 0, 1 and y = 0, 1, each extended by
  `eps = 2^-5` beyond the tile (lines 96-130), marker DUMMY, z from the DEM. Purpose: the
  mesh must have edges along texture boundaries.
* Gluing: the four tile sides as polylines of **2048 segments** (`segs = 2048`, line 137),
  marker DUMMY, so that neighbouring tiles share identical border vertices.

**Keep** both as ordinary layers (the bench reproduces them).

### 2.10 Seeds

One seed per polygon at `representative_point()` (`O4_Vector_Utils.py:409-424`,
`O4_Vector_Map.py:441-451` for the sea after `coastline_to_MultiPolygon`,
`O4_Airport_Utils.py:1229-1330` for airport surfaces), keyed by marker name. If no seed at all:
one SEA seed at (1000, 1000) — outside the tile, so nothing is flooded — when the DEM
maximum is >= 1 m, else at (0.5, 0.5): a tile entirely at sea level is all sea
(`O4_Vector_Map.py:162-166`). **Keep**; seeds are not the noder's concern
(`write_poly_file` takes them as an argument).

### 2.11 Triangle `.node` / `.poly` contract

* `.node`: `N 2 1 0` then `i x y z` (1-based, 9 decimals): N vertices, 2-D, one attribute (z),
  no boundary marker (`O4_Vector_Utils.py:537-559`).
* `.poly`: `0 2 1 0` (vertices in the `.node`), blank, `M 1`, `i n0 n1 marker`, blank, `H`
  holes (always 0), blank, `S` seeds `i x y marker` with 15 decimals sorted by marker value
  (lines 561-615). Triangle4XP reads the seeds as regional attributes with four fields
  (x, y, attribute, area), the area being absent (`Triangle4XP.c`, `readholes`, text reader).
* Triangle4XP's output `.1.node` has 5 attributes per vertex: DEM z, normal u, normal v, then
  the interpolated vector z (`Triangle4XP.c`, `writenodes`); Ortho4XP keeps the vector z for
  vertices of `attr >= 8` triangles only (`O4_Mesh_Utils.py:297-300`).

**Keep** the text layout (`triangle_files.py`, round-trip tested); the binary variant of
ADR 0004 will carry the same content.

### 2.12 Dropped

* Isolated vertices: a way whose consecutive points coincide gives a node without edge in Ortho4XP
  (`insert_node` runs before the zero-length check); OrthoStudio XP keeps only nodes of non-empty
  segments. **Drop** (no effect on the mesh, none observed on the reference tile).
* `check=False` in `insert_edge` (no intersection resolution): never used with `False` in
  Ortho4XP (6 197 recorded calls, all `True`). **Drop**.

### 2.13 Incremental insertion (review 6)

**Rule.** Inserting a layer gives the graph `_insert_full` gives -- the same nodes with the
same coordinates and z, the same edges and markers, **in the same order** -- but touches only
the new segments and the existing edges the prefilter keeps (`_bbox_prefilter`: bounding box
of the layer widened by `2 * BBOX_TOL`).

**Origin.** Review 6 measured the cost of the full re-derivation on a tile dense in
aerodromes: each aerodrome adds about five passes (RUNWAY, DUMMY traverses, RUNWAY, DUMMY,
TAXIWAY) and every pass re-sorted the whole graph -- 324 synthetic aerodromes spent 53.2 s in
the noding (1624 passes, 197 531 nodes), against 1.21 s for the 247 282 nodes of `+43+005`.
Ortho4XP inserts edge by edge into an R-tree and has no such term.

**Why it is the same graph.** (1) An edge the prefilter drops has no event: its chain is its
two end nodes and it comes out unchanged at its place. (2) Existing nodes carry insertion
times below every new one, so they keep index and coordinate; an event is matched against
all of them through a sorted key index, and a new node is numbered after them by its first
event -- the local event sequence is a subsequence of the full one, in the same order, so the
ranks agree. (3) The sub-edges of the candidates and of the layer are de-duplicated among
themselves by first occurrence; a candidate's sub-edges replace it in place, the layer's are
appended. (4) A node left without edge (a segment shorter than the key) is dropped at the
start of the next pass, which is when re-deriving from the edges forgot it. (5) One case is
not handled locally -- a new sub-edge equal to an existing edge *outside* the candidates,
geometrically impossible since both its ends lie in the layer's box -- and it falls back to
`_insert_full` for that pass.

**Decision.** Keep `_insert_full` as the reference and the fallback; `_insert` is the
incremental one. The candidate test runs x first, y on the survivors (`_edge_prefilter`), with
the very comparisons of `_bbox_prefilter`.

**Acceptance.** `tests/test_p4v2fix_noding.py`: byte equality of `NodedGraph` against the full
insertion on Ortho4XP's recorded layers of `+43+005` (100 passes) and on 800 random layer sets
(3 000 checked once while writing it) with near-coincident vertices, collinear overlaps and sub-key
segments; the fallback path forced gives the same bytes; the existing oracles
(`test_vectors_noding_oracle.py`, `test_p4v2_oracle.py`, 247 282 / 271 320) unchanged. Measured
(nice -n 10, load 1.2): `+43+005` 1.02 s -> 1.01 s; 324 synthetic aerodromes **53.2 s -> 3.8 s**
of noding.

**Cancellation.** `node_layers(..., cancel=)` reads the token before every pass and raises
`SYS_CANCELLED`; `orthostudio.vectors.assemble` passes `AssemblyParams.cancel`.

## 3. OrthoStudio XP contract

```python
node_layers(layers: Sequence[tuple[geometry, marker: int, z: ndarray | None]]) -> NodedGraph
NodedGraph(nodes: (N, 3) float64, edges: (M, 2) int32, markers: (M,) uint8)
```

* `layers` in decreasing priority; `geometry` is any shapely linework (polygons contribute
  their rings); `z` has one value per coordinate of `shapely.get_coordinates(geometry)`, or
  None for the Z of 3-D coordinates (0 when absent).
* Nodes are rounded to 9 decimals and unique; each unordered edge appears once; markers are
  the OR of all covering segments; the graph is planar (`check_planar(nodes, edges) == 0`).
* Node order: existing nodes first, then creation order; edge order: first appearance. Both
  deterministic for a given input.
* Cost: O((segments + contacts) log) per layer, no Python loop per edge; a layer pass costs a
  few ms on a small graph and ~0.2 s for 80 k segments against 150 k edges.

## 4. Not covered here

Coastline closure (`coastline_to_MultiPolygon`), `cut_to_tile`, `refine_way`,
`improved_buffer`, airport reconstruction, patches, the 7-decimal rounding of inputs and the
curvature weight map are the layer builders' specs (P4). The DEM sampling that gives z to
grid and border vertices (`alt_vec`) is the DEM spec.
