"""X-Plane's threads on Windows, as Live reads them from outside: each one's name and state, then
for 10 s how many of its background workers run. Live counts the threads named "Pool worker
(background ...)", as X-Plane names them on a Mac and Linux; whether it names them so on Windows
is what this says (2026-10-01)."""

import time
from collections import Counter

from orthostudio.live import machine, scenery

pid = scenery.xplane_pid()
if pid is None:
    raise SystemExit("X-Plane ne tourne pas : lance un vol, puis ce script.")
buffer = machine._windows_snapshot()
for p, name, threads in machine.windows_processes(buffer):
    if p == pid:
        names = Counter(
            machine._windows_thread_name(pid, tid) or "(sans nom)" for tid, _ in threads
        )
        print(f"{name}, processus {pid} : {len(threads)} fils")
        for thread_name, count in sorted(names.items()):
            print(f"  {count:3d} x {thread_name}")
for _ in range(10):
    print("workers (running, all):", machine.xplane_pool(pid), flush=True)
    time.sleep(1)
