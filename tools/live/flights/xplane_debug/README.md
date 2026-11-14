# X-Plane's heap crashes: reproduce one, then catch it

A crash of X-Plane's that aborts in `libsystem_malloc` (exception 6, malloc finding its heap damaged)
says only where the damage was found, never who did it: the stack of the crash is a witness. These
tools reproduce such a crash on a Mac and catch it with the debugger, to find the module that damaged
the heap: X-Plane itself, a plugin, or a library one of them uses.

What works on a Mac, and what does not:

- **AddressSanitizer and Guard Malloc cannot run inside X-Plane**: X-Plane is signed with the
  hardened runtime and lacks `allow-dyld-environment-variables`, so `DYLD_INSERT_LIBRARIES` is
  dropped. A plugin of ours can be checked with AddressSanitizer outside X-Plane, against
  `tests/data/live/fake_xplm.c`.
- **macOS's own malloc checks do work**: `fly.py` starts X-Plane with `MallocStackLogging=1` and
  `MallocScribble=1` when `FLY_MALLOC_DEBUG` names a folder (`open --env`, X-Plane's stderr kept
  there). They slow X-Plane about four times (a scenery load in 24 s instead of 6).
- **X-Plane may be debugged** (`get-task-allow`): lldb attaches to it without a password, and stops
  it at the abort, before X-Plane's own crash window.

Use, from the repository's root (its `.venv` made with `uv sync`), X-Plane closed:

```
# a crash rate: COUNT flights of X-Plane alone, a crash ending the series
tools/live/flights/xplane_debug/series.sh LABEL 3
# the same, with lldb on each flight: which thread and module were running at the crash
tools/live/flights/xplane_debug/attach_each.sh &   # then series.sh
# the deep look, one flight: who allocated and freed the damaged block
tools/live/flights/xplane_debug/attach_history.sh &
FLY_MALLOC_DEBUG="$PWD/build/flights/xplane-debug" .venv/bin/python tools/live/flights/fly.py --streamer none crash-croisiere
```

Such a crash is random: a clean flight proves little. Three in a row without a crash, where the same
setting crashed three times out of four, is a fair bar. Taking plugins out one by one rarely settles
it for that reason; the debugger does, by showing which thread was running and who made the blocks
next to the damaged one. X-Plane's crash dumps (`Output/crash_reports/completed/*.dmp`) list the
plugins loaded: comparing many of them tells which plugins were there every time.

The scenario `crash-croisiere` flies straight east at 47 N, FL350, 490 kt, the camera 500 m above
the aircraft; `stop_at.py` ends its path at 240 s.
