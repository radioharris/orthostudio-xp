# 0003. Python orchestration, native kernels, no full native rewrite

Date: 2026-09-12. Status: accepted.

## Context
A Rust core scored best on raw performance in the design panel but 4/10 on feasibility for
1-2 developers, and Triangle's licence (no in-process linking into a commercial system without
the author's agreement, 45 `exit()` calls) makes it a poor library. Benchmarks on the
reference machine: httpx HTTP/2 1 023 req/s, ispc_texcomp BC1 53 ms and BC3 64 ms per 4096²
texture, numpy DSF encoding 1.8 s, GEOS noding ~3.5 s on the real Marseille PSLG. About 90 %
of the attainable gain is orchestration (pools, graph, cache, binary formats, in-process
encoding), not language.

## Decision
Python 3.12+ (developed on 3.14) orchestrates. Heavy work runs in native libraries already
shipped as wheels (numpy, scipy, shapely/GEOS, Pillow/libjpeg-turbo, ispc_texcomp, blake3,
zstandard) and in Triangle4XP as a separate process. A native extension is written only for a
hot path that measurably resists, one at a time, behind a Python fallback.

## Consequences
Contributors from the Ortho4XP/AutoOrtho community can read and change the code. Distribution
bundles a Python runtime (PyInstaller or python-build-standalone); users never see pip.
Wheels must exist for macOS arm64/x86_64, Windows and Linux for every dependency (checked in
CI). `rtree` is not a dependency: shapely 2 STRtree replaces it.
