# 0001. A new project, not a fork of Ortho4XP

Date: 2026-09-12. Status: accepted.

## Context
Ortho4XP (16 537 lines, one volunteer maintainer, last substantive commit Feb 2024) works,
but its architecture is the bottleneck: 75 module globals mutated per tile, configuration by
`exec()`, a Tk GUI welded to the engine, text intermediates, threads without locks, no tests,
180 bare `except:`. Measured on +43+005 at ZL14: 109 s on 1.35 of 14 cores; at ZL16 the
single download thread alone costs ~490 s.

## Decision
Start a new repository and package (`orthostudio`). Ortho4XP is kept as a git submodule and used
only as a black-box oracle (same inputs, compared outputs) and, temporarily, as a subprocess
for stages not yet rewritten. No legacy module is imported by OrthoStudio XP code.

## Consequences
Every ported rule is re-implemented in the new architecture with a behaviour spec and a comparison
test. The Tk GUI is not carried over. Users of Ortho4XP keep it until OrthoStudio XP reaches parity;
`osxp import-ortho4xp` will read their existing tiles and caches.
