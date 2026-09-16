# 0006. Native DEM, mesh and masks beside the Ortho4XP stages

Date: 2026-09-12. Status: accepted.

## Context
After P2, three of the five build stages still run Ortho4XP in a subprocess: vectors
(31.6 s), mesh (7.5 s) and masks (4.3 s) of the 49.9 s a ZL14 tile costs. Two further facts
forced the order of work: Ortho4XP's Overpass client targets a mirror that no longer answers this
machine, so a tile with no warm OSM cache cannot be built at all; and the masks stage is the
one whose output the DSF and the coastal textures both depend on, so its cache key decides how
much a parameter change rebuilds.

## Decision
P3 ports DEM, mesh and masks to OrthoStudio XP, and gives OrthoStudio XP its own Overpass client.
Four rules are added: `orthostudio.dem@1`, `orthostudio.mesh@1`, `orthostudio.masks@1`,
`orthostudio.osm@1`. The `legacy.*` rules stay callable and stay the oracle.
`osxp build --stages native|legacy|auto` chooses; `auto` prefers the native rules and falls back
per stage.

Vectors stay on Ortho4XP until P4: they are the largest and least mechanical port, and the noding
core (the 28.6 s hot spot) is already written and proven.

Fidelity targets, per artefact:

| Artefact | Target |
|---|---|
| `Data<tile>.alt` | byte-identical to Ortho4XP |
| `Data<tile>.mesh`, `Data<tile>.weight` | byte-identical (same Triangle inputs) |
| masks in `sand` and `rocks` mode | byte-identical |
| masks in `3steps`, distance masks | semantic, with a declared and measured tolerance (Ortho4XP approximates a distance by repeated dilations; the exact transform is closer to the truth than the thing it replaces) |
| the DSF built on native artefacts | byte-identical, which is what the integration proves |

The OSM client writes two things: OrthoStudio XP's own snapshot (zstd JSON, content-keyed) and
Ortho4XP's `.osm.bz2` cache, which its vectors stage recycles without touching the network. That is
what unblocks a cold tile before P4 exists.

## Consequences
Two implementations of three stages coexist for one phase; the legacy ones are how we keep
proving the native ones. Mirror policy is fixed by ADR 0005. A native rule must declare
exactly the parameters it consumes: a forgotten one is a silently wrong cache, and it is the
first thing the review checks.
