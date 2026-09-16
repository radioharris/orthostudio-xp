# 0004. Triangle4XP stays a separate process with binary I/O

Date: 2026-09-12. Status: accepted.

## Context
Triangle4XP produces 1.2 M triangles in 2.9 s; the 7.3 s around it in Ortho4XP are text parsing
and formatting. Its licence allows free redistribution of modified versions with sources and a
notice of modifications; it forbids distribution as part of a commercial system without the
author's agreement. In-process use would also expose the host to its `exit()` calls.

## Decision
Keep Triangle4XP as a sidecar executable built from `native/triangle4xp/`, one process per tile,
killable for cancellation. Patch only its readers and writers to accept and emit binary
`.node/.poly/.alt/.weight` and `.1.node/.1.ele`, behind a flag, with the text path intact and
the modifications listed in `native/triangle4xp/CHANGES.md`.

## Consequences
Byte-identical output between text and binary modes is a P0 test. The mesh refinement
criterion (DEM Hessian, "no wall" rule, min angle, attribute interpolation) is not touched.
