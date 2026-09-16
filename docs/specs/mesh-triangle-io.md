# Triangle4XP input/output (binary sidecar exchange)

Status: implemented (P0). Code: `native/triangle4xp/` (C, switch `-b`),
`src/orthostudio/mesh/triangle_io.py` (Python readers/writers),
tests `tests/test_mesh_triangle_io.py`.

## Rule

OrthoStudio XP runs Triangle4XP exactly as Ortho4XP does (same switches, same 13 positional
parameters, same `.alt` and `.weight` rasters) but exchanges vertices, segments and triangles
as binary tables instead of Triangle's text files. The mesh must be the same: every vertex
coordinate, altitude, normal component, attribute and triangle corner produced in binary mode
is the double / int32 that the text mode would have printed with `%.17g` / `%d`.

## Origin in Ortho4XP

| What | Where |
|---|---|
| Command line, switches `-pq{min_angle}AuYB{Q|V}{P}S{steiner}`, 13 positional parameters | `src/O4_Mesh_Utils.py:636-683` |
| `.weight` map (1001 x 1001 float32 ones, airports and coastline patches) | `src/O4_Mesh_Utils.py:132-228, 655-657` |
| `.alt` raster (`nxdem * nydem` float32, DEM with 36 px margin) | `src/O4_DEM_Utils.py` (`write_to_file`), size check `O4_Mesh_Utils.py:584-596` |
| Text `.node` (`n 2 1 0`, rows `i x y alt` at `%.9f`) and `.poly` (`0 2 1 0`; segments `i a b marker`; holes; regions `i x y attr` at `%.15f`, no area column) | `src/O4_Vector_Utils.py:537-620` |
| Reading `.1.node` (6 columns: x y z u v alt) and `.1.ele` (3 corners + 1 attribute) | `src/O4_Mesh_Utils.py:230-324` (`post_process_nodes_altitudes`) |
| Triangle4XP readers | `Utils/src/Triangle4XP.c`: `readnodes()` 14663, `formskeleton()` 12986, `readholes()` 14946, `main()` 16531-16556 (`.alt`/`.weight`, one `fread` per sample) |
| Triangle4XP writers | `writenodes()` 15098-15265 (`%.17g`), `writeelements()` 15312-15420 |

## Decision

**Keep** the algorithm and the command line; **change the transport** (ADR 0004): a `-b` switch
makes Triangle4XP read `OSXPPOL1`/`OSXPNOD1` inputs and write `OSXPNOD1`/`OSXPELE1` outputs. Text
mode stays intact. The `.alt` and `.weight` rasters keep their raw float32 layout, they are read
with a single `fread`.

## Binary layouts

All integers are `int32`, all reals are `float64` (IEEE binary64, the `double` Triangle computes
with), in **native byte order** (little-endian on every platform OrthoStudio XP supports; the files
are per-run intermediates, never exchanged between machines). No padding, no alignment, no trailer.
Vertex numbers inside segments and triangles start at `first_number` (1 in Ortho4XP), exactly as
Triangle numbers them.

### `OSXPNOD1` (`.node` input, `.1.node` output)

```
bytes  0-7   magic "OSXPNOD1"
int32[5]     n_vertices, dim (=2), n_attrs, n_markers (0|1), first_number (0|1)
float64      table[n_vertices][2 + n_attrs]   x, y, attr_0 .. attr_{n_attrs-1}
int32        markers[n_vertices]              only if n_markers == 1
```

Input from `orthostudio.vectors`: `n_attrs = 1` (the vector altitude, `INTERP_ALT` targets), no
markers. Output of Triangle4XP (`-A`, not `-r`): `n_attrs = n_attrs_in + 3`, columns
`x y z u v attr_in...` with `x, y` clamped to `[0, 1]`, `z = altitude(x, y)` and `(u, v)` the
normal from the DEM, both evaluated at `(x, y)` clamped to the DEM window; `n_markers = 0`
because Ortho4XP passes `-B`.

### `OSXPPOL1` (`.poly` input)

```
bytes  0-7   magic "OSXPPOL1"
             OSXPNOD1 header and table (without magic); n_vertices == 0 means the vertices
             are in the companion .node file, first_number is then taken from that file
int32[2]     n_segments, seg_markers (0|1)
int32        segments[n_segments][2]          vertex numbers
int32        seg_markers[n_segments]          only if seg_markers == 1 (Ortho4XP: attribute bits)
int32        n_holes
float64      holes[n_holes][2]
int32        n_regions
float64      regions[n_regions][4]            x, y, attribute, max_area
```

`max_area` is not used by Ortho4XP (no `-a`); Triangle's text reader copies the attribute into
it when the column is missing, `triangle_io.read_poly_text` does the same. Regions are read
by Triangle4XP only under the same condition as in text mode (`-A` or `-a`, not `-r`).

### `OSXPELE1` (`.1.ele` output)

```
bytes  0-7   magic "OSXPELE1"
int32[4]     n_triangles, n_corners (3, or 6 with -o2), n_attrs, first_number
int32        triangles[n_triangles][n_corners] vertex numbers
float64      attributes[n_triangles][n_attrs]  Ortho4XP: one attribute, the plagued bit mask
```

### Rasters (unchanged)

`.alt`: `nydem` rows of `nxdem` float32, row 0 = north (`Y1`); `.weight`: 1001 rows of 1001
float32, row 0 = north. Read in one `fread`; a short file is an error.

## Python side (`orthostudio.mesh.triangle_io`)

`NodeTable` (points `(n, 2 + n_attrs)` float64, optional markers, `first_number`), `Pslg`
(segments, optional markers, holes, regions, optional embedded nodes) and `Elements`
(triangles, attributes). `write_node_binary`, `write_poly_binary`, `write_ele_binary`,
`read_*_binary`, the text readers `read_node_text`, `read_poly_text`, `read_ele_text`, and
`read_node` / `read_poly` / `read_ele` which sniff the magic and dispatch. Malformed files raise
`TriangleFormatError`.

## Acceptance tests

1. **Text mode identity** (oracle, +43+005, exact Ortho4XP command): the osxp build in text mode
   and the official `Utils/mac/Triangle4XP` of Ortho4XP produce byte-identical `.1.node` and
   `.1.ele` up to the `# Generated by <argv>` trailer. Passed: 603 649 vertices
   (603 209 after undead removal), 1 197 758 triangles.
2. **Binary mode equality** (oracle, same inputs converted by `triangle_io`): every value of
   `.1.node` (x, y, z, u, v, alt) and `.1.ele` (corners, attribute) parsed from the official
   text output equals the binary output bit for bit (`numpy.array_equal`). `%.17g` round-trips
   a double exactly, so no rounding tolerance is needed and none is applied.
3. **Synthetic** (no oracle): round trips of the three layouts through numpy, text readers
   against the binary layouts, `-b` refused with `-r`, and a 54-point square meshed by the
   sidecar in both modes with identical results.

## Measurements (M4 Pro, `nice -n 10`, load average 2.5-5, other work running)

| | official Ortho4XP (text) | osxp build, text | osxp build, `-b` |
|---|---|---|---|
| Triangle4XP wall time, 3 runs | 2.68 / 2.73 / 2.72 s | 2.55 / 2.45 / 2.48 s | 1.25 / 1.24 / 1.19 s |
| `.1.node` + `.1.ele` size | 76.6 + 42.6 MB | same | 29.0 + 24.0 MB |
| Reading the outputs in Python | 0.99 + 0.77 s (`triangle_io` text readers; Ortho4XP's line loop: ~2.5 s) | same | 0.002 + 0.002 s |
| Converting Ortho4XP text inputs to binary (`triangle_io`) | | 0.16 + 0.15 s read, < 0.01 s write | |

The text output alone costs about 1.3 s of the sidecar (`printf("%.17g")` on 4.2 M doubles);
with `-b` the sidecar spends its time in the mesh (Delaunay + quality, ~1.1 s) and the whole
"Triangle4XP + parse" step goes from ~4.4 s to ~1.25 s. The 7.4 s "build_mesh" stage of Ortho4XP
also includes the weight map, the Python post-processing and the `.mesh` text writer, which
belong to later work in `orthostudio.mesh`.

## Candidate differences (documented, behaviour not changed here)

* **`scaly2` bug**, `Triangle4XP.c:3338`: `scaly2 = scalx * scalx;` instead of `scaly * scaly`.
  Harmless today: `scaly2` is never read (only `scalx2` is, in `triunsuitable()`, line 7317).
  Fixing it changes nothing; do not "fix" it into a use without a spec.
* **Retry with `mesh_cmd[-5]`**, `O4_Mesh_Utils.py:711`: when Triangle4XP fails, Ortho4XP intends to
  retry with `min_angle = 0` but overwrites `mesh_cmd[-5]`, which is the `nodata` value, not the
  switch string (`mesh_cmd[1]`). The retry therefore runs with the same `-q10` and `nodata = 0`,
  which also changes the `no_data` test in `altitude()`. OrthoStudio XP will retry with the switch
  string changed (wanted difference, to be specified with the mesh runner).
* **Attribute parsing `line[-2] == "0"`**, `O4_Mesh_Utils.py:245`: Ortho4XP skips triangles whose
  attribute ends with the digit 0, i.e. `0` (dummy) but also `10` (WATER|INTERP_ALT) and
  `160`; the Marseille `.1.ele` holds attributes {0, 1, 2, 3, 8, 9, 10, 16, 32, 128}.
  Binary attributes are floats, so OrthoStudio XP's post-processing will decide by value (wanted
  difference, already listed in the plan).
* **Compiler floating-point contraction**: the mesh depends on fused multiply-add being
  enabled (clang default on arm64). The official Ortho4XP macOS binary behaves that way on Apple
  Silicon; OrthoStudio XP builds with the same default so both agree bit for bit. Builds with
  `-ffp-contract=off`, and the x86_64 slice run under Rosetta (no FMA at the x86-64 baseline,
  verified: identical to the `-ffp-contract=off` arm64 build), give a slightly different but
  equally valid mesh (+43+005: 603 221 vs 603 209 vertices, 1 197 782 vs 1 197 758 triangles).
  So Ortho4XP itself produces different meshes on Intel and Apple Silicon Macs. Parity tests must
  run the official binary and the osxp build on the same architecture.
* **Short `.alt` / `.weight` files**: Ortho4XP silently meshes with uninitialised memory;
  OrthoStudio XP's build exits with an error (wanted difference, coded error to come from
  the runner).
