"""lldb command osxp_heap_dump: at malloc's abort, the history macOS kept (MallocStackLogging=1)
of every heap block the crashing frame's registers point into: who allocated it, who freed it."""

import subprocess


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f heapdump.dump osxp_heap_dump")


def dump(debugger, command, result, internal_dict):
    process = debugger.GetSelectedTarget().GetProcess()
    pid = process.GetProcessID()
    seen = []
    for thread in [process.GetSelectedThread()]:
        for i in range(min(3, thread.GetNumFrames())):
            for regset in thread.GetFrameAtIndex(i).GetRegisters():
                for reg in regset:
                    v = reg.GetValueAsUnsigned(0)
                    if 0x100000000 <= v < 0x7FFFFFFFFFFF and v not in seen:
                        seen.append(v)
    print(f"pid {pid}, {len(seen)} candidate pointers: {', '.join(hex(v) for v in seen)}")
    for v in seen:
        try:
            out = subprocess.run(
                ["malloc_history", str(pid), hex(v)], capture_output=True, text=True, timeout=180
            )
            text = (out.stdout or "") + (out.stderr or "")
        except subprocess.TimeoutExpired:
            text = "timeout"
        if "ALLOC" in text or "FREE" in text:
            print(f"===== {hex(v)} =====")
            print(text[-6000:])
        else:
            last = text.strip().splitlines()[-1][:120] if text.strip() else "empty"
            print(f"----- {hex(v)}: no history ({last})")
