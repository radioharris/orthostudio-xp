# Benchmarks

Measurements that justify a design choice. Each file names the machine, the date, the exact
command, and the raw numbers. A benchmark that cannot be re-run is not a benchmark.

The comparisons with Ortho4XP were run with tools that decision 0010 removed (`tools/oracle`, the
noding, DDS and P1 benchmarks) once OrthoStudio XP stopped depending on Ortho4XP. Their figures
stand as measured on the dates given; their commands only run from a checkout of the repository
older than that decision. Some records cite `PLAN.md` and `DIAGNOSTIC.md`, the plan of the rewrite
and the brief it was built from, which the repository kept in `docs/plan/` until 2026-09-14 (they
remain in its history), and show the pack names used before decision 0011.
