# The `.mesh` file of Ortho4XP (reader, writer, npz twin)

Status: P2, written before the code of `src/orthostudio/mesh/mesh_file.py`. Tests:
`tests/test_mesh_file.py` (synthetic files, npz round trip); until decision 0010 an oracle part
re-wrote Ortho4XP's `Data+43+005.mesh` byte for byte.

Origin: `src/O4_Mesh_Utils.py:326-372` (`write_mesh_file`) and `src/O4_Mesh_Utils.py:847-894`
(`read_mesh_file`); the same reader is duplicated inline in `O4_Mask_Utils.py:432-460`
(`record_water_tris`) and consumed by `O4_DSF_Utils.py:471-473` (`build_dsf`).

## 1. Rule

The `.mesh` file is the only product of step 2 that steps 2.5 and 3 read (the masks read the tile's
mesh and the meshes of the eight neighbours; the DSF builder reads the tile's mesh). It is a text
file, written once per tile and re-parsed in Python line by line up to ten times per build.
OrthoStudio XP reads it once into typed numpy arrays (`MeshData`), stores that as a versioned `.npz`
next to the text file inside the `orthostudio.mesh` artefact, and can re-emit the text file byte for
byte (debug export).

## 2. File layout (`write_mesh_file`, Ortho4XP)

```
MeshVersionFormatted 2          line 1: literal + version token (float in Ortho4XP's reader)
Dimension 3                     line 2
                                line 3: empty
Vertices                        line 4
603649                          line 5: N
5.117254700000000 43.605783099999996 0.000596498252070 0     N lines: lon lat z/100000 0
                                empty line
Normals
603649                          N again (never differs from Vertices)
0.06 -0.11 0                    N lines: nx ny 0
                                empty line
Triangles
1197758                         M
603600 586596 603648 0          M lines: n1 n2 n3 attr, corners 1-based
```

| Field | Written as | Origin |
|---|---|---|
| lon, lat | `"{:.15f}".format(vertices[6i] + tile.lon)`, same for lat | `O4_Mesh_Utils.py:344-347`; Triangle4XP works in tile-relative degrees, the offset is added here |
| altitude | `"{:.15f}".format(vertices[6i+2] / 100000)`: metres divided by 100 000 | `O4_Mesh_Utils.py:348`; the reader multiplies back (`:864`) |
| vertex reference | literal `0` (fourth column, INRIA `.mesh` "reference") | `O4_Mesh_Utils.py:349` |
| normals | `"{:.2f} {:.2f} 0"`: two components, third column literal `0` | `O4_Mesh_Utils.py:356-361`; `-0.00` occurs (74 430 times in the fixture) |
| triangles | the four tokens after the index of each `.1.ele` line: three 1-based corners and the Triangle4XP region attribute | `O4_Mesh_Utils.py:367-369` |
| attribute | integer bit set, DUMMY 0 … HANGAR 128 (`O4_Vector_Utils.py:44-54`) | in the fixture: 0, 1, 2, 3, 8, 9, 10, 16, 32, 128 |
| line endings | `\n`, file ends with `\n`, no trailing comment line | `open(..., "w")` on POSIX |

Everything the file carries is therefore: a version token, a dimension token, N vertices
(lon, lat, z), N normals (2 components) and M triangles (3 corners + 1 attribute). The two
reference columns are constants.

## 3. In-memory representation (`MeshData`)

The fields mirror what `read_mesh_file` returns, so that the DSF builder ported from
`O4_DSF_Utils.py` consumes the same numbers as Ortho4XP:

| Field | dtype / shape | Meaning |
|---|---|---|
| `vertices` | float64 `(N, 3)` | absolute lon, lat (degrees), **z in metres** (`file_z * 100000`, exactly `read_mesh_file:864`) |
| `normals` | float32 `(N, 2)` | the two written components; the constant third column is not stored |
| `tris` | int32 `(M, 3)` | **0-based** corners (`read_mesh_file:889` does `int(x) - 1`); the file is 1-based |
| `tri_attr` | uint8 `(M,)` | the attribute as written (`read_mesh_file:891-892`: `t + 1` after `int(x) - 1`, i.e. unchanged) |
| `extra` | dict | `version` (the token after `MeshVersionFormatted`, string, `"2"`), `dimension` (`"3"`) |

Decision: keep Ortho4XP's in-memory semantics (metres, 0-based) rather than the file's (scaled,
1-based), because every consumer in OrthoStudio XP is a port of an Ortho4XP consumer. This is the
opposite choice from `triangle_io.py`, which keeps Triangle's 1-based numbering because Triangle4XP
itself reads those files; both are documented at the type.

A file whose vertex reference or third normal column is not `0` is rejected
(`MeshFormatError`): Ortho4XP never writes one, and silently dropping the values would make the
writer lie. Community meshes (`MeshVersionFormatted 1.3` in `O4_Mask_Utils.py:433`) have the
same layout; the version token is kept verbatim in `extra["version"]`.

## 4. Byte-identical rewrite (`write_mesh_text`)

The writer emits exactly the layout of section 2 from a `MeshData`:

* lon, lat: `f"{v:.15f}"` of the stored double. The stored double is the one the parser
  produced from a 15-decimal string; it is the nearest double to that decimal, so formatting
  it again yields the same string (the spacing of doubles below 2^7 is < 1e-15/2 away from
  any rounding boundary; at 43° the spacing is 7.1e-15 and the parsed double is the *only*
  double within half a spacing of the 15-decimal grid point).
* z: `f"{z_m / 100000:.15f}"`. `z_m = file_z * 100000` then `/ 100000` may differ from
  `file_z` by one ulp, but `file_z` was itself printed by Ortho4XP with `.15f`, so it sits on
  (within one ulp of) a 15-decimal grid point, at least 0.5e-15 minus one ulp from any
  rounding boundary: a one-ulp perturbation cannot change the printed string.
* normals: `f"{nx:.2f}"` of the float32 value converted to Python float; a float32 is within
  6e-9 of the parsed double, far from any 0.005 boundary. `-0.0` prints as `-0.00` like Ortho4XP.
* triangles: `f"{n1 + 1} {n2 + 1} {n3 + 1} {attr}"`.

Acceptance (oracle): `write_mesh_text(read_mesh(fixture))` equals the fixture byte for byte
(69 171 409 bytes, blake3 compared). This is the proof that nothing is lost in `MeshData`.

## 5. `.npz` twin (`write_mesh_npz`, `read_mesh_npz`)

`numpy.savez` (uncompressed: the store may compress later, and mmap-friendly loading matters
more than 20 % of size) with keys `format` (`"osxp-mesh-npz-1"`), `vertices`, `normals`,
`tris`, `tri_attr`, `extra_json` (JSON of `extra`). `read_mesh_npz` rejects another format
string. Round trip is exact (same dtypes, same bytes).

## 6. Performance (acceptance: read < 1 s on the 69 MB fixture)

The reader loads the whole file as bytes, finds the three section headers, and hands each
block to `numpy.fromstring(block, sep=" ")` (C parser, whitespace and newlines as
separators) then reshapes to `(N, 4)`, `(N, 3)`, `(M, 4)`; a block that does not parse to
exactly the announced count is a `MeshFormatError`. `read_mesh_file` in Ortho4XP takes ~9 s on the
same file (Python `float()` per token). Measured values are recorded in the test output.

## 7. Wanted differences from Ortho4XP

None in content. Differences in behaviour: a malformed file raises instead of returning
partial arrays; the two constant columns are validated rather than skipped.

## 8. Normals: the float32 contract (dette D5)

`MeshData.normals` is `float32 (N, 2)` while the DSF encoder brings them back to float64 rounded to
2 decimals (`orthostudio.dsf.encode._normals_as_ortho4xp`, `NORMAL_DECIMALS = 2`). This section states
why that is the right contract today, and what changes when OrthoStudio XP computes the normals
itself. **No code change is implied**: `mesh_file.py` and `encode.py` are correct as they stand.

### 8.1 What the numbers actually are

A normal in an Ortho4XP `.mesh` file is written with `"{:.2f}"` (section 2): the file only ever
contains the 201 values `-1.00, -0.99, … 1.00` (198 of them occur in the reference tile), and
`-0.00` is one of them. **Two decimals is all the information there is.** Ortho4XP parses them
back with `float()` into float64; OrthoStudio XP parses them into float32.

Every one of those 201 values survives `float64 -> float32 -> round(·, 2)` exactly
(exhaustively verified over the grid, `tests/test_dette_mesh_normals.py`), and on the
reference tile the float32 value is at most `2.9e-8` from the float64 one. So float32 is
lossless *for this file format*, and halves the array (4.83 MB instead of 9.66 MB for
603 649 vertices), which matters because the masks stage reads nine meshes at once.

### 8.2 Why the encoder rounds anyway

The DSF stores a normal as `u16 = round((1 ± strength·n) / 2 · 65535)`. That maps the
2-decimal grid onto values that sit exactly **on a .5 tie** (for example `n = -0.40` gives
`45874.5`). A difference of `2.9e-8` in the input decides which side of the tie the result
falls on, so using the float32 value directly changes the encoded byte. Measured on
`fixtures/large/oracle/+43+005_zl14_BI/build/Data+43+005.mesh`: **1 493 of the 1 207 298
encoded normal components** (640 on x, 853 on y) differ between the raw float32 value and the
same value rounded to 2 decimals in float64. Rounding first reproduces Ortho4XP bit for bit; this
is why the DSF of the tile is byte-identical.

The contract is therefore:

* the `.mesh` reader/writer keeps the **text** meaning of a normal (a 2-decimal number), in
  the smallest type that holds it exactly: float32;
* any consumer that quantises normals must first take them back to that meaning
  (`round(x.astype(float64), 2)`), because float32 is exact *as a label of the grid point*,
  not as an operand of a tie-breaking multiplication;
* `_normals_as_ortho4xp` passes float64 input through untouched, so a producer that already
  works in float64 is not rounded twice.

### 8.3 What changes when normals are computed natively (P3/P4)

Once OrthoStudio XP computes normals from the mesh geometry instead of re-reading Ortho4XP's text,
the values are no longer on a 2-decimal grid: they are ordinary float64 results of a cross-product.
At that point,

* `_normals_as_ortho4xp` must **not** be applied to them (it would quantise a good normal to
  1/100, i.e. up to 0.005 of error, ~0.3° of tilt, visible as banding on shallow slopes). Its
  float64 pass-through already gives the right behaviour, which is why it is written that way;
* `MeshData.normals` should then carry float64, or float32 with the understanding that it is
  now an approximation of a continuous value (a float32 normal component is within 6e-8, four
  orders of magnitude below the u16 quantum of 3e-5, so float32 stays visually exact — the tie
  problem disappears because a computed normal lands on a tie with probability ~0);
* writing such a mesh back as Ortho4XP text is lossy by construction (2 decimals). The text writer
  stays exact only for meshes that came from Ortho4XP text.

The decision of the type (and whether the byte-identity target survives) belongs to the mesh
chantier of P3; this section exists so the choice is made with the numbers in hand.
