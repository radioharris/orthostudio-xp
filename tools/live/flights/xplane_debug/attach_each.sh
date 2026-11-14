#!/bin/bash
# attach_each.sh: lldb attached to each X-Plane that starts within the hour (no malloc checks, which
# slow it 4 times); on a crash, the stacks, then X-Plane killed, so that nothing stays frozen.
# Captures in build/flights/xplane-debug/lldb-<pid>.txt.
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../../../.." && pwd)
out="$repo/build/flights/xplane-debug"; mkdir -p "$out"
done_pids=""; end=$(( $(date +%s) + 3600 ))
while [ "$(date +%s)" -lt $end ]; do
  pid=$(pgrep -x X-Plane | head -1)
  if [ -n "$pid" ] && ! echo " $done_pids " | grep -q " $pid "; then
    done_pids="$done_pids $pid"; sleep 3
    echo "$(date +%H:%M:%S) attaching to X-Plane $pid"
    xcrun lldb -p "$pid" --batch \
      -o "process handle SIGABRT -s true -n true -p false" \
      -o "process handle SIGUSR1 SIGUSR2 SIGPIPE SIGALRM -s false -n false -p true" \
      -o "continue" -k "thread backtrace" -k "thread backtrace all" -k "process kill" \
      > "$out/lldb-$pid.txt" 2>&1
    if grep -q "stop reason = \(EXC_BREAKPOINT\|EXC_BAD_ACCESS\|signal SIGABRT\|signal SIGSEGV\|signal SIGBUS\)" "$out/lldb-$pid.txt"; then
      echo "$(date +%H:%M:%S) X-Plane $pid CRASHED: see $out/lldb-$pid.txt"
    else
      echo "$(date +%H:%M:%S) X-Plane $pid ended without a crash"
    fi
  fi
  sleep 2
done
