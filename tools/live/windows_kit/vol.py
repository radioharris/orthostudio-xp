"""A replayable test flight on this Windows (the kit's fourth step): tools/live/flights/fly.py's
series, as on the developer's Mac, then what they left zipped beside the kit's scripts, to send
back. Usage: vol.py 1|2|3 (Live; X-Plane alone; both, one after the other) and the kit's folder.

Each flight in the same conditions as the other: the kit's folder kept out of Windows Search (its
Python, its results, a cold Live's folder), and each flight begun once Windows' own background
work is done (Windows Search, Windows Update, the antivirus): on the user's Shadow PC, Windows
Update ran through the flight without Live and Windows Search through both (2026-10-02)."""

import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tools" / "live" / "flights"))
import fly
import sampler_windows

LIVE = ["--streamer", "live", "--cold", "--zooms", "16/18", "toulouse-low"]
ALONE = ["toulouse-low"]
RUNS = {"1": [LIVE], "2": [ALONE], "3": [ALONE, LIVE]}
BIGGEST = 300 * 2**20  # a file larger is left out of the zip (none should be)
# Windows' own background work: its search's indexer, its updates, its antivirus
BACKGROUND = (
    "SearchIndexer.exe", "SearchProtocolHost.exe", "SearchFilterHost.exe", "TiWorker.exe",
    "TrustedInstaller.exe", "MoUsoCoreWorker.exe", "MsMpEng.exe",
)  # fmt: skip
QUIET_CORES = 0.15  # that much between them, at most
QUIET_WINDOW_S = 30.0
QUIET_MAX_S = 15 * 60  # then the flight goes all the same, and its processes.csv says why


def background_cores(window_s: float = QUIET_WINDOW_S) -> dict[str, float]:
    """The cores each of Windows' background workers used over ``window_s``."""

    def times() -> dict[int, tuple[str, float]]:
        return {
            p.pid: (p.name, p.cpu_s) for p in sampler_windows.snapshot() if p.name in BACKGROUND
        }

    before = times()
    time.sleep(window_s)
    used: dict[str, float] = {}
    for pid, (name, cpu_s) in times().items():
        if pid in before:
            used[name] = used.get(name, 0.0) + (cpu_s - before[pid][1]) / window_s
    return used


def wait_quiet(max_s: float = QUIET_MAX_S) -> bool:
    """Until Windows' background work uses less than QUIET_CORES; False after ``max_s``."""
    began = time.time()
    while time.time() - began < max_s:
        used = background_cores()
        if sum(used.values()) < QUIET_CORES:
            return True
        busy = ", ".join(f"{n} {c:.2f}" for n, c in sorted(used.items(), key=lambda kv: -kv[1]))
        print(f"Windows travaille en arriere-plan ({busy} coeur) : le vol attend.", flush=True)
    print("Windows travaille encore apres 15 min : le vol part quand meme.", flush=True)
    return False


def main(choice: str, kit: Path) -> int:
    from orthostudio.live import winsearch
    from orthostudio.live.pack import live_root

    # before the first flight, X-Plane alone: the kit's folder and the user's Live folder (its
    # region's hundreds of thousands of files) out of Windows Search, which then has done
    for folder in (kit.resolve(), live_root()):
        winsearch.keep_out(folder, print)
    before = set(fly.RESULTS.iterdir()) if fly.RESULTS.is_dir() else set()
    codes = []
    for args in RUNS[choice]:
        print(f"\n=== fly.py {' '.join(args)}\n", flush=True)
        wait_quiet()
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
