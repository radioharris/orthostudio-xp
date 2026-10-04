# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Replayable test flights on this Windows (the kit's third step), for the app's one window
(2026-10-04): tools/live/flights/fly.py's runs, as on the developer's Mac, the app itself opened by
the runner, Live alone in it or with OrthoStudio XP open beside it, each cold; then what they left
zipped beside the kit's scripts, the runs side by side in ``comparaison.md``, to send back. Usage:
vol.py 1|2|3|4|5 (the comparison; Live alone in the app; with OrthoStudio XP open; X-Plane alone;
all of them) and the kit's folder.

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
import report
import sampler_windows

APP = ["--streamer", "app", "--cold", "--zooms", "16/18"]
LIVE_ALONE = [*APP, "toulouse-low"]
WITH_OSXP = [*APP, "--with-osxp", "toulouse-low"]
XPLANE_ALONE = ["toulouse-low"]
RUNS = {
    "1": [LIVE_ALONE, WITH_OSXP],  # the comparison: what OrthoStudio XP open costs beside Live
    "2": [LIVE_ALONE],
    "3": [WITH_OSXP],
    "4": [XPLANE_ALONE],
    "5": [LIVE_ALONE, WITH_OSXP, XPLANE_ALONE],
}
BIGGEST = 300 * 2**20  # a file larger is left out of the zip (none should be)
# Windows' own background work: its search's indexer, its updates, its antivirus
BACKGROUND = (
    "SearchIndexer.exe", "SearchProtocolHost.exe", "SearchFilterHost.exe", "TiWorker.exe",
    "TrustedInstaller.exe", "MoUsoCoreWorker.exe", "MsMpEng.exe",
)  # fmt: skip
QUIET_CORES = 0.15  # that much between them, at most
QUIET_WINDOW_S = 30.0
QUIET_MAX_S = 15 * 60  # then the flight goes all the same, and its processes.csv says why


def label_of(args: list[str]) -> str:
    """A run, in the table's words."""
    if "--streamer" not in args:
        return "X-Plane seul"
    return "Live avec OrthoStudio XP ouvert" if "--with-osxp" in args else "Live seul dans l'app"


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


def series_now() -> set[Path]:
    return set(fly.RESULTS.iterdir()) if fly.RESULTS.is_dir() else set()


def main(choice: str, kit: Path) -> int:
    from orthostudio.live import winsearch
    from orthostudio.live.pack import live_root

    # before the first flight: the kit's folder and the user's Live folder (its region's hundreds
    # of thousands of files) out of Windows Search, which then has done
    for folder in (kit.resolve(), live_root()):
        winsearch.keep_out(folder, print)
    codes, flown = [], []
    for k, args in enumerate(RUNS[choice]):
        print(f"\n=== {label_of(args)} : fly.py {' '.join(args)}\n", flush=True)
        if k:  # the run before's X-Plane and app gone first
            try:
                fly.wait_quiet()
            except fly.RefusedError as e:
                print(e, flush=True)
        wait_quiet()
        before = series_now()
        codes.append(fly.main(args))
        flown += [(label_of(args), folder) for folder in sorted(series_now() - before)]
    if not flown:
        print("no flight recorded")
        return 1
    target = kit / f"resultats-vols-{time.strftime('%Y-%m-%d-%H%M')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "comparaison.md",
            f"# {time.strftime('%Y-%m-%d %H:%M')}\n\n" + report.campaign_table(flown),
        )
        for _, folder in flown:
            for f in sorted(folder.rglob("*")):
                if f.is_file() and f.stat().st_size <= BIGGEST:
                    z.write(f, f.relative_to(fly.RESULTS))
    print(f"\nLes vols sont enregistres dans : {target}")
    return max(codes)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2])))
