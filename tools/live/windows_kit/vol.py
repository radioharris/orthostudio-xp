"""A replayable test flight on this Windows (the kit's fourth step): tools/live/flights/fly.py's
series, as on the developer's Mac, then what they left zipped beside the kit's scripts, to send
back. Usage: vol.py 1|2|3 (Live; X-Plane alone; both, one after the other) and the kit's folder."""

import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tools" / "live" / "flights"))
import fly

LIVE = ["--streamer", "live", "--cold", "--zooms", "16/18", "toulouse-low"]
ALONE = ["toulouse-low"]
RUNS = {"1": [LIVE], "2": [ALONE], "3": [ALONE, LIVE]}
BIGGEST = 300 * 2**20  # a file larger is left out of the zip (none should be)


def main(choice: str, kit: Path) -> int:
    before = set(fly.RESULTS.iterdir()) if fly.RESULTS.is_dir() else set()
    codes = []
    for args in RUNS[choice]:
        print(f"\n=== fly.py {' '.join(args)}\n", flush=True)
        codes.append(fly.main(args))
    made = sorted(set(fly.RESULTS.iterdir()) - before) if fly.RESULTS.is_dir() else []
    if not made:
        print("no flight recorded")
        return 1
    target = kit / f"resultats-vol-{time.strftime('%Y-%m-%d-%H%M')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in made:
            for f in sorted(folder.rglob("*")):
                if f.is_file() and f.stat().st_size <= BIGGEST:
                    z.write(f, f.relative_to(fly.RESULTS))
    print(f"\nLe vol est enregistre dans : {target}")
    return max(codes)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2])))
