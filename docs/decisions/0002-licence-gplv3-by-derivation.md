# 0002. GPL v3, as a derivative of Ortho4XP

Date: 2026-09-12. Status: accepted.

## Context
Two paths were weighed. Clean-room (permissive licence, rules re-derived from public
specifications and black-box observation of Ortho4XP) versus derivation (rules ported by reading
Ortho4XP, which makes OrthoStudio XP a derivative work under the GPL v3).

The X-Plane facts (DSF, .ter, DDS, web-mercator grid) are public either way. The value of
Ortho4XP lies in ten years of edge-case handling in coastlines, water, airports, masks and the
curvature weight map. A clean-room would rediscover those cases one user report at a time.
A clean-room by the same people who read the code is also a promise, not a proof.

## Decision
GPL v3, derivation. Rules may be ported by reading Ortho4XP. Each ported rule gets a spec in
`docs/specs/` stating the rule in plain language, its origin (file:line in Ortho4XP), and whether
OrthoStudio XP keeps it, fixes it (a "wanted difference") or drops it.

## Consequences
Binaries ship with sources. Closed forks are not possible; the Mac App Store is not a target. Any
host program that wants to embed OrthoStudio XP as a Python library must be GPL-compatible; invoking
`orthostudio` as a separate command-line program is fine for any licence. Revisit only if a
concrete commercial plan appears, and then before the porting of the sensitive rules begins.
