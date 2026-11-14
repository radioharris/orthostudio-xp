#!/bin/bash
# attach_history.sh: the deep look, for one flight started with FLY_MALLOC_DEBUG set (fly.py: macOS's
# MallocStackLogging=1 and MallocScribble=1, X-Plane's stderr kept): lldb on the X-Plane that starts,
# and at its abort the stacks, the registers, and malloc_history of every heap block they point to
# (heapdump.py): who allocated and freed the damaged blocks. Then X-Plane killed.
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../../../.." && pwd)
out="$repo/build/flights/xplane-debug"; mkdir -p "$out"
until pid=$(pgrep -x X-Plane | head -1) && [ -n "$pid" ]; do sleep 1; done
sleep 5
echo "$(date +%H:%M:%S) attaching to X-Plane $pid"
xcrun lldb -p "$pid" --batch \
  -o "process handle SIGABRT -s true -n true -p false" \
  -o "process handle SIGUSR1 SIGUSR2 SIGPIPE SIGALRM -s false -n false -p true" \
  -o "continue" -k "thread backtrace" -k "register read" \
  -k "command script import $here/heapdump.py" -k "osxp_heap_dump" -k "thread backtrace all" -k "process kill" \
  > "$out/lldb-history-$pid.txt" 2>&1
echo "$(date +%H:%M:%S) lldb ended: $(grep -c '=====' "$out/lldb-history-$pid.txt") blocks with a history"
