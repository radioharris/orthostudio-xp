# DSF: binary encoding of the mesh (pools, commands, atoms)

Status: P2, written before the code of `src/orthostudio/dsf/` (modules `quadtree.py`, `encode.py`,
`decode.py`, `write.py`, `params.py`). Tests: `tests/test_dsf_reference.py` (bucket order, recut,
zone map and whole encoder against transcriptions of Ortho4XP), `tests/test_dsf_encode.py`
(synthetic mesh: land, inland water, masked and unmasked sea, both water techs, decoded back;
writing; parameters); until decision 0010, `tests/test_dsf_oracle.py` (byte-identical to the
reference DSF at ZL14 and ZL16, and the < 3 s target). Companion specs: `dsf-terrain-assignment.md`
(what feeds the encoder), `dsf-xp12-rasters.md` (DEMN/DEMS).

Origin in Ortho4XP: `src/O4_DSF_Utils.py:20-107` (quadtree), `:491-578` (pools and
quantisation), `:580-625` (DSF pool layout), `:640-1042` (pool entries and triangle lists),
`:1044-1365` (atoms, commands, MD5). The DSF format itself is public (X-Plane DSF
specification); the choices below are Ortho4XP's.

## 1. The rule in plain language

A DSF stores vertices in **pools** of at most 65 535 entries, each a table of 16-bit
integers with `planes` columns, decoded as `offset + value * scale` per plane (`SCAL`
atom). Triangles are **commands**: select a terrain definition, select a pool, then lists of
vertex indices (all in the selected pool, command 23) or of `(pool, index)` pairs (command
24). Ortho4XP partitions the tile into a quadtree of square cells so that every pool's
longitude/latitude fit in 16 bits at the cell's resolution, quantises altitudes with one of
four fixed scales per pool, writes three pool families (land 7 planes, masked water and
overlays 9 planes, X-Plane water 7 planes), and emits the commands terrain by terrain, pool
by pool, in first-encounter order. The whole file ends with the MD5 of everything before it.

## 2. Contract

```python
class DsfParams(RuleParams):          # frozen pydantic, subset_of(cfg) works
    water_tech: Literal["XP11 + bathy", "XP12"] = "XP11 + bathy"
    ratio_bathy: float = 1.0 ; ratio_water: float = 0.25 ; normal_map_strength: float = 1.0
    terrain_casts_shadows: bool = True ; use_decal_on_terrain: bool = False ; decal_on_sea: bool = False
    # a build gives both decal fields False: the pack writes the decals (pipeline-build.md 2.3)
    imprint_masks_to_dds: bool = True ; use_masks_for_inland: bool = False
    mesh_zl: int = 19 ; mask_zl: int = 14 ; default_zl: int = 16 ; default_website: str = "BI"
    zone_list: list[Zone] = [] ; cover_airports_with_highres: str = "False"
    cover_zl: int = 18 ; cover_extent: float = 1.0 ; sea_texture_blur: float = 0.0
    overlay_lod: float = 25000.0 ; use_test_texture: bool = False

@dataclass TextureJob: texture: TextureId ; kinds: frozenset[TerKind]
@dataclass DsfBuild: data: bytes ; textures: list[TextureJob] ; ter_files: dict[str, str] ; stats: dict
@dataclass Xp12Rasters: demn: bytes ; dems: bytes

def build_dsf(tile: TileRef, mesh: MeshLike, masks: MaskLookup | None, params: DsfParams,
              rasters: Xp12Rasters | None, *, creation_agent: str = "osxp",
              distance_masks: MaskLookup | None = None, airports: Sequence[AirportCover] = (),
              existing_textures: Iterable[TextureId] = (),
              _quad_capacity: int | None = None) -> DsfBuild
def write_dsf(path: Path, build: DsfBuild, *, backup: bool = True) -> None
def decode_dsf(data: bytes) -> DecodedDsf      # pools, scales, terrain names, patches (tests, debugging)
```

`MeshLike` is a protocol over the fields of the contract's `MeshData`
(`orthostudio.mesh.mesh_file`), so the encoder accepts that class and the private fallback reader
alike. `DsfBuild.terrains` (the `DEFN/TERT` entries after `terrain_Water`, with texture and kind) is
an addition to the contract. `_quad_capacity` is a test hook (small meshes must split pools to
exercise 3.1).

**Normals as Ortho4XP holds them.** `MeshData.normals` is float32 by the mesh-file spec; Ortho4XP
computes the normal planes from the float64 value parsed from the `%.2f` text
(`O4_Mesh_Utils.py:355-360, 872-874`). `float64(float32("-0.40"))` is not `float("-0.40")`,
and `(1 - v) / 2 * 65535` then lands on the other side of a rounding tie: 1 630 entries of
the reference tile differ by one unit. The encoder therefore rounds float32 normals back to
2 decimals in float64 (`numpy.round(x.astype(float64), 2)`, exact for every 2-decimal value
in [-1, 1], verified) and takes float64 normals as they are.

`sea_texture_blur` is listed by the P2a contract; the DSF does not consume it (it is an
imagery parameter) and it is documented as such in `params.py`. `use_test_texture` mirrors the
Ortho4XP module global consumed by `create_terrain_file`.

Limits and errors: `DSF_POOL_OVERFLOW` (a pool or the terrain table beyond 65 535 entries,
where Ortho4XP fails in `struct.pack`), `SYS_INTERNAL_ERROR` for a barycentre outside the tile
(`dsf-terrain-assignment.md` 3.4). Commands are written `terrain_Water` first (dict order of
`textured_tris`, `:629`), then the terrains in index order.

## 3. Rules ported

### 3.1 Quadtree of pools (`O4_DSF_Utils.py:20-107`, `:491-509`)

- Every node (after the recut) is quantised to 24 bits per axis: `q = int(16777216 * x)` for
  `x = lon - tile.lon` and `y = lat - tile.lat` in `[0, 1)`, `q = 2**24 - 1` when `x >= 1`
  (`float2qquad`, `:28-31`; `int()` truncates). `bx`/`by` are the 24-character binary strings.
- The tree starts with the 64 buckets of level 3, keys `(bx[:3], by[:3])` in the order
  `for i in range(8): for j in range(8)` (i = x prefix, j = y prefix, `:47-56`); capacity
  `50000` nodes, or `35000` when `use_masks_for_inland` (`:21-22`, `:495-498`).
- Nodes are inserted in node order (`:500-504`). A node descends to the existing bucket whose
  key is a prefix of its strings (`:72-77`). A bucket holding `capacity` nodes is **split**
  when one more arrives (`:78-86`): its four children `(k0+"0", k1+"0")`, `(k0+"0", k1+"1")`,
  `(k0+"1", k1+"0")`, `(k0+"1", k1+"1")` are appended to the dict, its nodes redistributed,
  the bucket deleted (`:59-70`), and the insertion retried one level down (possibly splitting
  again).
- A bucket of level 24 is one quantised position, which no split can divide, and is never split
  (**fix**, 2026-09-25). Ortho4XP splits it all the same and fails on the children it cannot
  key; OrthoStudio XP raised `OverflowError: Python integer -2 out of bounds for uint64`, the
  shift of level 25, when a mesh piled more than a pool's capacity on one spot (+34-118, 872 323
  points within a millimetre). The points of such a bucket share its entries, since the DSF
  cannot tell them apart. A pool holding more than 65 535 entries is `DSF_POOL_OVERFLOW`, whose
  context gives the corner of the fullest pool (`at`, latitude and longitude) for `serve.log`.
- `clean()` deletes empty buckets (`:88-91`).
- The **pool order** is the dict order after cleaning (`:513-518`): the initial 64 keys minus the
  split ones, then the children in split order. Since Python dicts keep insertion order and a split
  appends four children, the order depends only on the *time* (node index) at which each bucket
  receives its `capacity + 1`-th node. OrthoStudio XP computes, for every candidate bucket, the
  index of its `capacity + 1`-th node in node order (numpy: sort by quadkey prefix, stable), and
  replays the splits in increasing time (parent before child at equal time), which reproduces the
  dict order without inserting nodes one by one.

### 3.2 Quantisation of a pool (`:519-578`)

For a bucket of level `L` (prefix length):

- `ix = int(bx[L:L+16], 2)`, `iy = int(by[L:L+16], 2)`: the 16 bits after the prefix (fewer
  when `L > 8`, then the low `24 - L` bits, kept as Ortho4XP does);
- `altmin = floor(min z)`, `altmax = ceil(max z)` over the bucket's nodes (`:534-535`);
  `scale_z, inv_stp = (771, 85)` when `altmax - altmin < 770`, `(1285, 51)` when `< 1284`,
  `(4369, 15)` when `< 4368`, else `(13107, 5)` (`:536-547`; `65535 = scale * inv_stp`);
  `iz = numpy.round((z - altmin) * inv_stp)` (half to even, `:549-551`);
- normals for every node: `inx = numpy.round((1 + strength * u) / 2 * 65535)`,
  `iny = numpy.round((1 - strength * v) / 2 * 65535)` (`:572-577`; X-Plane normals point
  east and **south**, hence `-v`);
- `SCAL` of the pool: `(2**-L, lon + int(k0, 2) * 2**-L, 2**-L, lat + int(k1, 2) * 2**-L,
  scale_z, altmin, 2, -1, 2, -1, 1, 0, 1, 0, 1, 0, 1, 0)` as little-endian float32, the first
  `2 * planes` values (`:552-571`, `:1159-1163`).

### 3.3 DSF pool families (`:580-625`)

With `P` = number of buckets, DSF pool `k` for `k in range(3 * P)` uses the quadtree bucket
`k % P`: `[0, P)` land, 7 planes; `[P, 2P)` masked sea / overlays, 9 planes; `[2P, 3P)`
X-Plane water (`terrain_Water`), 7 planes. Both water techs use the same plane counts
(`:592-609`). Empty pools are not written (`:1092-1095`, `:1137-1139`); the written pools are
renumbered in increasing `k` (`:1175-1180`).

### 3.4 Pool entries (`:770-872`, `:943-1040`)

Triangles are visited in the two passes of `dsf-terrain-assignment.md` (sea, then land and
inland water), corners in the order `(n1, n3, n2)` (`:774, 841, 945, 1010`: orientation flip).

- Terrain entries are deduplicated by `(bucket, ix, iy, terrain_idx)` (`:776-780`): the
  first corner met with that key creates the entry, later ones reuse it, *whatever their z,
  normal or exact position*. Entries take their position in the DSF pool in first-encounter
  order across both passes (`dsf_pool_length` counter, `:819-821`).
- X-Plane water entries (`terrain_Water`) are deduplicated by node index (`:842, 1011`), one
  entry per node in the `[2P, 3P)` pools, first-encounter order across both passes.
- Plane values (`(s, t) = st_coord(lat, lon, tex_x, tex_y, zl)` of the node in the
  triangle's texture, `O4_Geo_Utils.py:137-150`, `round(s * 65535)` half to even):

| Family | Planes | Origin |
|---|---|---|
| land | `ix iy iz inx iny s t` | `:963-969` |
| sea overlay (`XP11 + bathy`, or masks not imprinted) | `ix iy iz inx iny s t s t` | `:796-806` |
| sea masked, `XP12` with imprinted masks | `ix iy iz inx iny 65535 int(65535 * ratio_bathy) s t` | `:808-818` |
| inland water overlay | `ix iy iz 32768 32768 s t 0 round(ratio_water * 65535)` | `:971-984` |
| `terrain_Water` | `ix iy iz 32768 32768 65535 int(65535 * ratio_bathy)` | `:853-864`, `:1022-1033` |

`ratio_fetch` is the constant 1 (`:813, 861, 1029`). `ratio_bathy` per node is
`dsf-terrain-assignment.md` 3.3.

### 3.5 Triangle lists (`:822-838`, `:865-872`, `:990-1006`, `:1034-1040`)

For every triangle drawn with a terrain: the three `(pool, position)` pairs; if two of them
are equal the triangle is dropped (pool snapping, `:825-830`, `:993-998`), and for sea and
inland-water overlays the drop also skips the `terrain_Water` copy (the `continue` covers the
rest of the loop body). If the three pools are equal the triple goes to
`textured_tris[terrain][pool]`, else the six values to `textured_tris[terrain]["cross-pool"]`.
`terrain_Water` triangles are never checked for degeneracy (`:865-872`, `:1034-1040`).

Per terrain, the groups (`pool` or `"cross-pool"`) are ordered by first use (defaultdict
order); triangles inside a group keep their visiting order.

### 3.6 Atoms (`:1044-1130`, `:1327-1330`)

```
"XPLNEDSF" u32(1)
DAEH size=16+len(PROP)          PORP size=8+len   PROP = "sim/west\0<lon>\0sim/east\0<lon+1>\0sim/south\0<lat>\0sim/north\0<lat+1>\0sim/creation_agent\0<agent>\0"
NFED size=48+Σ                  TRET (names "\0"-terminated, terrain_Water first) TJBO "" YLOP "" WTEN "" NMED (copied)
DOEG size=8+Σ(21+planes*(9+2n)) LOOP ... (every non-empty pool, k ascending)   LACS ... (same order)
SDMC size=8+Σ                   commands (3.7)
SMED size=8+len(DEMS)           (only when rasters are given; copied, bathy clamped: dsf-xp12-rasters.md)
MD5 of everything above (16 bytes)
```

`<lon>` etc. are `str(int)`; the atom names are written reversed (`DAEH` for `HEAD`).
`POOL`: `u32 n`, `u8 planes`, then per plane `u8 0` (no run-length encoding) followed by the
`n` values as `u16` (plane-major, `:1140-1157`). `SCAL`: `2 * planes` float32.

### 3.7 Commands (`:1182-1325`)

For every terrain in index order whose triangle dict is not empty: `u8 4, u16 terrain_idx`;
`flag = 1` (physical) or `2` (overlay), `far_lod = -1.0` or `overlay_lod` as float32. For
every group in order: `u8 1, u16 pool` (for `"cross-pool"` the pool of the first pair),
`u8 18, u8 flag, f32 0, f32 far_lod`, then

- same-pool: blocks `u8 23, u8 255, 255 x u16` for each full 255 indices (85 triangles) and
  one `u8 23, u8 r, r x u16` for the remaining `r = len % 255` indices (`:1228-1259`);
- cross-pool: blocks `u8 24, u8 255, 255 x (u16 pool, u16 index)` per 510 values and one
  `u8 24, u8 r` for the remaining `r = (len % 510) // 2` pairs (`:1272-1325`). Pool numbers
  are the renumbered ones (3.3).

The CMDS size is `8 + Σ(3 + 13 + 2 * (len + ceil(len / 255)))` for same-pool groups and
`13 + 2 * (len + ceil(len / 510))` for cross-pool groups (`:1182-1200`), which is what the
blocks above occupy.

### 3.8 Writing (`:1053-1057`, `:1330-1346`)

The file is written to `<path>.tmp` then renamed; an existing `<path>` is moved to `<path>.bak`
first. OrthoStudio XP: `write_dsf` uses `fsutil.atomic_write_bytes` (`.tmp-<pid>-<rand>` then
`os.replace`) and keeps the `.bak` when `backup=True`.

## 4. Vectorisation (OrthoStudio XP)

Everything is array work: barycentres and `wgs84_to_orthogrid` per triangle; the quadtree
by sorting the 48-bit interleaved quadkeys; per-bucket `altmin`/`altmax` and scales by
`numpy.minimum.reduceat`; `(s, t)` for every corner; first-encounter deduplication with
`numpy.unique(return_index, return_inverse)` on a packed `uint64` key `(terrain, bucket, ix,
iy)`, then a stable sort of the unique entries by `(dsf_pool, first index)` for the positions;
degenerate triangles by comparing the three `(pool, position)` pairs; groups by a second
`unique` on `(terrain, pool-or-cross)`; pools serialised with `ndarray.T.tobytes()`; commands
by reshaping the index arrays into 255/510 blocks. Target: < 3 s for the 1.2 M triangles of
+43+005 (Ortho4XP: 13-18 s in `build_dsf` alone).

Limits kept from Ortho4XP: a pool of more than 65 535 entries raises `DSF_POOL_OVERFLOW` (Ortho4XP
would fail in `struct.pack`); terrain indices must fit in `u16` (65 535 terrains).

## 5. Acceptance tests

- Oracle ZL14 and ZL16, until decision 0010 removed it: `build_dsf(TileRef(43, 5),
  read_mesh(Data+43+005.mesh), masks_dir_lookup(masks), DsfParams.subset_of(cfg),
  extract_xp12_rasters(...))` compared to the reference with `compare_dsf` (ignores
  `sim/creation_agent`): identical;
  with `creation_agent="Ortho4XP"` the bytes and the MD5 are identical too. `textures` gives
  17 / 179 jobs and `ter_files` the 39 / 356 reference files verbatim; the `DEFN/TERT` order
  equals the reference's. **Measured** (M4 Pro, `nice -n 10`, load 1.0-1.6, median of 3):
  `build_dsf` 1.34 s at ZL14 and 1.40 s at ZL16 for 1 334 642 triangles after the recut
  (672 093 nodes), plus 0.31 s to parse the `.mesh` text; Ortho4XP's `build_dsf` takes 13-18 s.
  Breakdown at ZL14: recut 0.23 s, quadtree 0.10 s, terrains and masks 0.23 s, pool entries
  0.73 s, serialisation 0.06 s. The test asserts < 6 s (twice the target) to stay robust
  under load.
- Transcription (`tests/test_dsf_reference.py`): `ReferenceQuadTree`, `reference_recut`,
  `reference_zone_dico` and `reference_build_dsf` follow Ortho4XP statement by statement; on random
  synthetic meshes (land / inland water / sea, near-duplicate nodes, pool capacity lowered to
  25-40) the encoder's bytes and `.ter` texts equal the transcription's for the default
  parameters, `XP12`, masks not imprinted, `use_masks_for_inland`, `ratio_water`,
  `overlay_lod`, `ratio_bathy` with distance masks, `normal_map_strength`, a `zone_list` and
  the airport upgrade.
- Quadtree: random node sets around the capacity (capacity lowered to 5-50 for the test)
  give the same bucket order and the same `(bucket, ix, iy)` per node as the transcription of
  Ortho4XP's `QuadTree`.
- Synthetic mesh (a few triangles: land, inland water, sea with a mask, sea without a mask,
  a triangle snapped to nothing): the DSF parses (`orthostudio.dsf.container`), its MD5 is valid,
  `decode.py` returns the expected terrains, plane values and triangle counts, for both water
  techs and with `imprint_masks_to_dds` off; the pool of a degenerate triangle still holds its
  entries.
- `write_dsf`: temporary name then rename, `.bak` of the previous file.

## 6. Wanted differences from Ortho4XP

None in the bytes. `DSF_POOL_OVERFLOW` and a coded error for a barycentre outside the tile
(`dsf-terrain-assignment.md` 3.4) replace Python exceptions. The `download_queue` becomes the
returned `TextureJob` list (no side effect on `textures/`).
