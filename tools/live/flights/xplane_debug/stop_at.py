"""End the test pilot's path once it passes STOP_S seconds (its stop file)."""

import os
import sys
import time
from pathlib import Path

box = Path(os.environ.get("XPLANE", str(Path.home() / "X-Plane 12"))) / "Output" / "osxp_test_pilot"
stop_s = float(sys.argv[1])
started = time.time()
while True:
    try:
        frames = box / "frames.csv"
        if frames.stat().st_mtime >= started:
            with frames.open("rb") as f:
                f.seek(max(0, frames.stat().st_size - 400))
                text = f.read().decode(errors="replace")
                last = [row for row in text.splitlines() if row.count(",") > 2]
            if last and float(last[-1].split(",")[1]) >= stop_s:
                (box / "stop").write_text("stop\n")
                print(
                    f"{time.strftime('%H:%M:%S')} path past {stop_s:.0f} s: stop asked", flush=True
                )
                break
    except (OSError, ValueError):
        pass
    time.sleep(2)
