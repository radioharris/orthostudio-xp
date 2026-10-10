# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One measured build, the way the page starts it, for ``docs/benchmarks/build-times-1.0.md``.

The tile goes through the app's own path (``make_specs`` with the saved settings, then
``build_tiles``), so every step and every setting is the one a user gets, and each step's time
comes from the build's own report. Nothing is installed in X-Plane.

Run it with ``OSXP_HOME`` set to a folder of its own, never your real one: the settings, the
cache and the tiles of the measurement go there.

    build_times.py --library-from ~/.orthostudio/config.toml   # a new user's settings, plus
                                                                # the map library the app carries
    build_times.py --set essential.photo_look=softer             # change a saved setting
    build_times.py --label NAME --out FILE [--tile +46+006] [--zl 16] [--stop-at 0.5]

``--stop-at F`` sends the build the Ctrl+C a Stop amounts to once its imagery has done F of its
work. The library's address and key are copied, never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any


def _kb(path: Path) -> int:
    if not path.exists():
        return 0
    out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out else 0


def _change_settings(args: argparse.Namespace) -> None:
    from orthostudio import config

    s = config.load_settings(None, [])
    levels: dict[str, Any] = {"essential": s.essential, "advanced": s.advanced, "expert": s.expert}
    if args.library_from:
        mine = tomllib.loads(Path(args.library_from).expanduser().read_text())["expert"]
        levels["expert"] = levels["expert"].model_copy(
            update={
                "osm_library": mine["osm_library"],
                "osm_library_token": mine["osm_library_token"],
            }
        )
    for item in args.set:
        key, raw = item.split("=", 1)
        value: Any = {"true": True, "false": False}.get(raw, raw)
        level, name = key.split(".", 1)
        if "." in name:  # essential.relief.source=copernicus
            field, sub = name.split(".", 1)
            inner = getattr(levels[level], field)
            levels[level] = levels[level].model_copy(
                update={field: inner.model_copy(update={sub: value})}
            )
        else:
            levels[level] = levels[level].model_copy(update={name: value})
    config.save_settings(s.model_copy(update=levels))
    print("settings saved:", ", ".join(args.set) or "(defaults)", flush=True)


def _measure(args: argparse.Namespace, home: Path) -> dict[str, Any]:
    from orthostudio import config
    from orthostudio.api.models import PlanRequest
    from orthostudio.api.specs import make_specs
    from orthostudio.pipeline.build import build_tiles

    settings = config.load_settings(None, [])
    specs = make_specs(
        PlanRequest(tiles=[args.tile], zoom_level=args.zl),
        settings=settings,
        install=False,
        config_module=config,
    )
    chunks_before = _kb(home / "chunks")
    t0 = time.perf_counter()
    started: dict[str, float] = {}
    stopped: dict[str, float] = {}

    def on_event(ev: Any) -> None:
        node = getattr(ev, "node_id", None)
        now = time.perf_counter() - t0
        if node and type(ev).__name__ == "Started":
            started.setdefault(node, now)
        if (
            args.stop_at is not None
            and not stopped
            and node
            and node.endswith("/textures")
            and type(ev).__name__ == "Progress"
            and ev.fraction >= args.stop_at
        ):
            stopped.update(after_s=round(now, 1), imagery_fraction=round(ev.fraction, 3))
            os.kill(os.getpid(), signal.SIGINT)

    report = build_tiles(specs, on_event=on_event).to_dict()
    total = time.perf_counter() - t0
    steps = [
        {
            "id": n["id"],
            "role": n.get("role"),
            "status": n.get("status"),
            "wall_s": n.get("wall_s"),
            "started_s": round(started[n["id"]], 1) if n["id"] in started else None,
        }
        for t in report["tiles"]
        for n in t["nodes"]
    ]
    return {
        "label": args.label,
        "tile": args.tile,
        "zl": args.zl,
        "total_s": round(total, 1),
        "ok": report["ok"],
        "cancelled": report["cancelled"],
        "built": report["built"],
        "hits": report["hits"],
        "failed": report["failed"],
        "stopped": stopped or None,
        "downloaded_mb": round((_kb(home / "chunks") - chunks_before) / 1024, 1),
        "disk_mb": {
            "chunks": round(_kb(home / "chunks") / 1024, 1),
            "store": round(_kb(home / "store") / 1024, 1),
        },
        "steps": steps,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--label")
    ap.add_argument("--out")
    ap.add_argument("--tile", default="+46+006")
    ap.add_argument("--zl", type=int, default=16)
    ap.add_argument("--stop-at", type=float, default=None)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--library-from")
    args = ap.parse_args()
    home_s = os.environ.get("OSXP_HOME", "")
    real = (Path.home() / ".orthostudio").resolve()
    if not home_s or Path(home_s).expanduser().resolve() == real:
        sys.exit("Set OSXP_HOME to a folder of its own: a measurement must not touch your data.")
    home = Path(home_s).expanduser()
    if args.set or args.library_from:
        _change_settings(args)
    if not args.label:
        return
    result = _measure(args, home)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    print(
        f"{result['label']}: {result['total_s']} s, ok={result['ok']}, built {result['built']}, "
        f"hits {result['hits']}, failed {result['failed']}, "
        f"downloaded {result['downloaded_mb']} MB",
        flush=True,
    )
    for s in result["steps"]:
        if s["status"] != "hit":
            line = f"   {s['role']!s:<10} {s['status']:<8} {s['wall_s']:>8} s  {s['id']}"
            print(line, flush=True)


if __name__ == "__main__":
    main()
