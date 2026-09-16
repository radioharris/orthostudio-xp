# Triangle4XP (OrthoStudio XP build)

`Triangle4XP.c` is **Triangle 1.6** by Jonathan Richard Shewchuk, as adapted by Oscar Pilote for
Ortho4XP (curvature-driven refinement of X-Plane terrain meshes), plus the **OrthoStudio XP
binary I/O modifications** described in [`CHANGES.md`](CHANGES.md). It is built as a separate
executable and driven as a sidecar process by `orthostudio.mesh` (see ADR 0004 and
`docs/specs/mesh-triangle-io.md`).

## Provenance

| | |
|---|---|
| Upstream file | `Utils/src/Triangle4XP.c` of Ortho4XP, git commit `d3bf078` (2024-02-23), header "Last modified : Feb 21st 2024" |
| Pristine sha256 | `67bbe5e4fab03d236274b0a12e181a79255c78f471e11c4ec18dfdf2972724af` |
| Original program | Triangle 1.6 (July 28, 2005), <http://www.cs.cmu.edu/~quake/triangle.html> |
| `CMakeLists.txt` | adapted from Ortho4XP `Utils/CMakeLists.txt` |

The OrthoStudio XP modifications are confined to file reading and writing (switch `-b`, one
`fread` per raster). The meshing algorithm, the refinement criterion (`triunsuitable`, curvature
map, "no wall" rule), the attribute plague and the text formats are untouched; the text mode of
this build reproduces the official Ortho4XP macOS binary bit for bit (see "Verification").

## Licence

Triangle's licence, reproduced from the file header:

> This program may be freely redistributed under the condition that the copyright notices
> (including this entire header and the copyright notice printed when the `-h' switch is
> selected) are not removed, and no compensation is received. Private, research, and
> institutional use is free. You may distribute modified versions of this code UNDER THE
> CONDITION THAT THIS CODE AND ANY MODIFICATIONS MADE TO IT IN THE SAME FILE REMAIN UNDER
> COPYRIGHT OF THE ORIGINAL AUTHOR, BOTH SOURCE AND OBJECT CODE ARE MADE FREELY AVAILABLE
> WITHOUT CHARGE, AND CLEAR NOTICE IS GIVEN OF THE MODIFICATIONS. Distribution of this code
> as part of a commercial system is permissible ONLY BY DIRECT ARRANGEMENT WITH THE AUTHOR.
> (If you are not directly supplying this code to a customer, and you are instead telling
> them how they can obtain it for free, then you are not required to make any arrangement
> with me.)
>
> Copyright 1993, 1995, 1997, 1998, 2002, 2005 Jonathan Richard Shewchuk

Oscar Pilote's header states that "the full copyright of this file is left to Jonathan Richard
Shewchuk, with GPL v3 rules for the extension only". The OrthoStudio XP modifications follow the
same terms: copyright of the file stays with the original author, the modifications are made
available free of charge with their sources, and `CHANGES.md` is the clear notice of
modifications required by the licence. `Triangle4XP` is never linked into OrthoStudio XP; it
runs as a separate process, which also isolates OrthoStudio XP from its `exit()` calls.

## Build

Requires CMake >= 3.13 and a C compiler (clang on macOS). From this directory:

```bash
cmake -S . -B build && cmake --build build
./build/Triangle4XP -h
```

`build/` is git-ignored. On macOS the default configuration produces a universal
`arm64 + x86_64` binary in `Release` mode. Floating-point contraction is left at the compiler
default on purpose: the official Ortho4XP binary was built that way and only that setting
reproduces its output exactly on Apple Silicon (with `-ffp-contract=off` the +43+005 mesh gets
603 221 instead of 603 209 vertices). Fast-math is disabled, Triangle's exact predicates need
IEEE doubles.

## Usage

Same command line as Ortho4XP, with one new switch:

```
Triangle4XP <switches> scalx scaly nxdem nydem X0 Y0 X1 Y1 nodata curv_tol \
            file.alt file.weight file.poly
```

* `<switches>` as in Ortho4XP, e.g. `-pq10AuYBQPS1331665.3684210528`; add `b` (e.g.
  `-pq10AuYBQPbS...`) for **binary I/O**: `file.poly` and `file.node` are read in the
  `OSXPPOL1` / `OSXPNOD1` layouts and `file.1.node` / `file.1.ele` are written in the
  `OSXPNOD1` / `OSXPELE1` layouts (`docs/specs/mesh-triangle-io.md`). `-b` cannot be combined
  with `-r` (refinement of an existing mesh).
* `file.alt`: `nxdem * nydem` float32, `file.weight`: `1001 * 1001` float32, both raw and
  unchanged from Ortho4XP; a short file is now an error instead of undefined behaviour.

## Verification

`tests/test_mesh_triangle_io.py` (OrthoStudio XP repository) runs a synthetic square through both
modes, which must give identical vertices, altitudes, normals, attributes and triangles. Until
decision 0010 the same test also ran the exact Ortho4XP command on tile +43+005 (Marseille): the
official `Utils/mac/Triangle4XP` of Ortho4XP, this build in text mode and this build in binary mode
gave identical results.
