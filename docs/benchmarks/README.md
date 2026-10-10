# Benchmarks

Measurements that justify a design choice. Each file names the machine, the date, the exact
command, and the raw numbers. A benchmark that cannot be re-run is not a benchmark.

The comparisons with Ortho4XP were run with tools that decision 0010 removed (`tools/oracle`, the
noding, DDS and P1 benchmarks) once OrthoStudio XP stopped depending on Ortho4XP. Their figures
stand as measured on the dates given. Their commands ran in the history before that decision,
which this repository does not carry: its history starts with version 0.1.0 (2026-09-16), and the
earlier one is in an earlier repository, kept privately. Some records cite `PLAN.md` and
`DIAGNOSTIC.md`, the plan of the rewrite and the brief it was built from, kept in `docs/plan/` of
that earlier history until 2026-09-14, and show the pack names used before decision 0011.
