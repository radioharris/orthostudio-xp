# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The test flights' window: what to fly, chosen with the mouse, the flight under way, and the
results (``docs/specs/live/flights.md``). It runs ``fly.py`` as its command line does, with the
options chosen, and reads what it leaves in ``build/flights``.

    uv run python tools/live/flights/bench.py

Built as Live's own window is (``orthostudio.live.web``): OrthoStudio XP's window, the guard of its
API and its style sheet, on a port of its own (8661). A window apart from Live's: Live's is for its
users, this one is the developer's, and speaks of AutoOrtho. A flight goes on if the window is
closed: it is ``fly.py``'s, in a process of its own, found again when the window opens.
"""

from __future__ import annotations

import contextlib
import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import fly  # noqa: E402
import scenario as scenarios  # noqa: E402

PAGE_DIR = HERE / "bench"
PORT = 8661  # the preview app's engine takes 8651, Live's window 8642
TITLE = "Vols de test — OrthoStudio XP Live"
SIZE = (1240, 900)
CURRENT = "bench-current.json"  # in the results: the flight under way, for a window opened again
LOGS = "bench-logs"
START_S = 60.0  # X-Plane's start and the runner's own steps, for the time a choice will take
RUN_EXTRA_S = (
    8 * 60
)  # a campaign's run beyond its flights: a cold start (AutoOrtho's took 4 min), the quits
STREAMERS = ("none", "live", "autoortho")
ZOOMS = ("", "16/18", "17/19")


FRENCH = (  # the runner's words for a setup (fly.label), as the window says them
    ("X-Plane alone", "X-Plane seul"),
    ("its defaults", "ses réglages par défaut"),
    ("your settings", "tes réglages"),
    ("cold", "à froid"),
)


def in_french(label: str) -> str:
    for en, fr in FRENCH:
        label = label.replace(en, fr)
    return label


class BenchError(ValueError):
    """A choice the runner would refuse; the message says why, in the window's words."""


# ---- what there is to fly ---------------------------------------------------------------------


def _lead(path: Path) -> str:
    """A file's first comment lines, as one text: what it is for."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    return " ".join(ln for ln in lines if ln)


def options() -> dict[str, Any]:
    """The scenarios and campaigns there are, and whether AutoOrtho can be started."""
    scs = []
    for path in sorted(scenarios.SCENARIOS.glob("*.txt")):
        try:
            sc = scenarios.load(path)
        except scenarios.ScenarioError as e:
            scs.append({"name": path.stem, "error": str(e)})
            continue
        scs.append(
            {
                "name": sc.name,
                "title": sc.title or sc.name,
                "minutes": round(sc.duration / 60, 1),
                "about": _lead(path),
            }
        )
    camps = []
    for path in sorted(fly.CAMPAIGNS.glob("*.txt")):
        try:
            camp = fly.read_campaign(str(path))
            flight = sum(scenarios.load(n).duration for n in camp.scenarios) / 60
        except (fly.RefusedError, scenarios.ScenarioError) as e:
            camps.append({"name": path.stem, "error": str(e)})
            continue
        parser = parser_for_labels()
        args = {i: parser.parse_args([*opts, *camp.scenarios]) for i, opts in camp.runs}
        all_cold = all(a.cold for a in args.values())
        labels = {i: in_french(fly.label(a)) for i, a in args.items()}
        short = {i: lb.removesuffix(", à froid") if all_cold else lb for i, lb in labels.items()}
        camps.append(
            {
                "name": camp.name,
                "title": camp.title or camp.name,
                "about": _lead(path),
                "scenarios": camp.scenarios,
                "runs": [{"id": i, "label": labels[i]} for i, _ in camp.runs],
                "comparisons": [
                    {"index": n, "runs": comp, "title": " contre ".join(short[i] for i in comp)}
                    for n, comp in enumerate(camp.comparisons, 1)
                ],
                "run_minutes": round(flight + RUN_EXTRA_S / 60, 1),
            }
        )
    ao = fly.AO_PYTHON.is_file() and (fly.AO_SOURCES / "__main__.py").is_file()
    return {"scenarios": scs, "campaigns": camps, "autoortho": ao}


def parser_for_labels() -> Any:
    """fly.py's options, to name a campaign's runs as the runner names them."""
    import argparse

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("scenarios", nargs="*")
    p.add_argument("--streamer", choices=STREAMERS, default="none")
    p.add_argument("--cold", action="store_true")
    p.add_argument("--ao-settings", choices=("mine", "default"), default="mine")
    p.add_argument("--zooms", type=fly.zooms_of)
    p.add_argument("--keep-open", action="store_true")
    return p


def fly_args(choice: dict[str, Any]) -> list[str]:
    """``fly.py``'s arguments for what the window chose; BenchError when they make no sense."""
    campaign = choice.get("campaign") or ""
    if campaign:
        known = {p.stem for p in fly.CAMPAIGNS.glob("*.txt")}
        if campaign not in known:
            raise BenchError(f"pas de campagne « {campaign} »")
        camp = fly.read_campaign(campaign)
        count = len(camp.comparisons) or len(camp.runs)
        pick = sorted({int(k) for k in choice.get("pick") or []})
        if "pick" in choice and not pick:
            raise BenchError("coche au moins une comparaison")
        if any(not 1 <= k <= count for k in pick):
            raise BenchError(f"comparaisons de 1 à {count}")
        if pick and len(pick) < count:
            return ["--campaign", campaign, "--pick", ",".join(map(str, pick))]
        return ["--campaign", campaign]
    names = [str(n) for n in choice.get("scenarios") or []]
    if not names:
        raise BenchError("choisis au moins un scénario")
    known = {p.stem for p in scenarios.SCENARIOS.glob("*.txt")}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise BenchError(f"pas de scénario « {unknown[0]} »")
    streamer = choice.get("streamer", "none")
    if streamer not in STREAMERS:
        raise BenchError(f"streamer inconnu : {streamer}")
    args = ["--streamer", streamer]
    if streamer == "autoortho":
        settings = choice.get("ao_settings", "mine")
        if settings not in ("mine", "default"):
            raise BenchError(f"réglages d'AutoOrtho inconnus : {settings}")
        args += ["--ao-settings", settings]
    zooms = choice.get("zooms") or ""
    if zooms:
        if streamer == "none":
            raise BenchError("les zooms sont ceux d'un streamer : choisis Live ou AutoOrtho")
        try:
            fly.zooms_of(zooms)
        except Exception as e:  # argparse's own error
            raise BenchError(f"zooms : {e}") from None
        args += ["--zooms", zooms]
    if choice.get("cold"):
        if streamer == "none":
            raise BenchError("« à froid » est pour un streamer : choisis Live ou AutoOrtho")
        args.append("--cold")
    if choice.get("keep_open"):
        args.append("--keep-open")
    return [*args, *names]


# ---- the flight under way ---------------------------------------------------------------------

_SERIES = re.compile(r"^\S+ (\d+)/(\d+) (\S+): ([\d.]+) min of path")
_FLYING = re.compile(r"^\S+ +flying: (\d+) of (\d+) s")
_CAMPAIGN = re.compile(r"^\S+ campaign (\S+), run (\d+) of (\d+): (.+)$")


def progress(lines: list[str]) -> dict[str, Any]:
    """Where the runner is, from its output: the campaign's run, the scenario, the path's time."""
    out: dict[str, Any] = {}
    for line in lines:
        m = _CAMPAIGN.match(line)
        if m:
            out.update(campaign=m[1], run=int(m[2]), runs=int(m[3]), setup=m[4])
            continue
        m = _SERIES.match(line)
        if m:
            out.update(index=int(m[1]), count=int(m[2]), scenario=m[3], path_s=0.0)
            out["duration_s"] = float(m[4]) * 60
            continue
        m = _FLYING.match(line)
        if m:
            out.update(path_s=float(m[1]), duration_s=float(m[2]))
    return out


class Runner:
    """``fly.py`` run for the window, one at a time. The flight under way is written down in the
    results (``CURRENT``), so that a window opened again finds it."""

    def __init__(self, results: Path = fly.RESULTS, command: list[str] | None = None):
        self.results = results
        self.command = command or [sys.executable, str(HERE / "fly.py")]
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def _current(self) -> dict[str, Any] | None:
        try:
            return json.loads((self.results / CURRENT).read_text())
        except (OSError, ValueError):
            return None

    def running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        cur = self._current()
        if not cur:
            return False
        try:
            os.kill(int(cur["pid"]), 0)
        except (OSError, KeyError, ValueError):
            return False
        return True

    def start(self, args: list[str]) -> dict[str, Any]:
        with self.lock:
            if self.running():
                raise BenchError("un vol est déjà en cours")
            logs = self.results / LOGS
            logs.mkdir(parents=True, exist_ok=True)
            log = logs / f"{datetime.now():%Y-%m-%d %H%M%S}.log"
            with log.open("ab") as f:
                self.proc = subprocess.Popen(
                    [*self.command, *args],
                    cwd=fly.REPO,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # it flies on if the window is closed
                )
            cur = {"pid": self.proc.pid, "log": str(log), "args": args, "started": time.time()}
            (self.results / CURRENT).write_text(json.dumps(cur))
            return cur

    def stop(self) -> bool:
        """Ctrl-C, as in its terminal: the runner writes what it has, and quits X-Plane."""
        cur = self._current()
        pid = self.proc.pid if self.proc is not None else (cur or {}).get("pid")
        if not pid or not self.running():
            return False
        with contextlib.suppress(OSError):
            os.kill(int(pid), signal.SIGINT)
        return True

    def status(self, since: int = 0) -> dict[str, Any]:
        cur = self._current() or {}
        lines: list[str] = []
        if cur.get("log"):
            with contextlib.suppress(OSError):
                lines = Path(cur["log"]).read_text(errors="replace").splitlines()
        return {
            "running": self.running(),
            "args": cur.get("args", []),
            "started": cur.get("started"),
            "lines": lines[since:],
            "next": len(lines),
            "progress": {
                k: in_french(v) if k == "setup" else v for k, v in progress(lines).items()
            },
        }


# ---- the results ------------------------------------------------------------------------------


def _inside(results: Path, rel: str) -> Path:
    path = (results / rel).resolve()
    if results.resolve() not in path.parents and path != results.resolve():
        raise BenchError("hors des résultats")
    return path


def results_list(results: Path = fly.RESULTS) -> list[dict[str, Any]]:
    """The series and campaigns flown, newest first."""
    items = []
    if not results.is_dir():
        return items
    for path in results.iterdir():
        if path.is_dir() and re.match(r"^\d{4}-\d\d-\d\d \d{6}$", path.name):
            flights = sorted(p for p in path.iterdir() if p.is_dir() and re.match(r"^\d+-", p.name))
            runs = []
            for f in flights:
                with contextlib.suppress(OSError, ValueError):
                    runs.append(json.loads((f / "run.json").read_text()))
            what = ", ".join(r.get("scenario", "?") for r in runs) or "(rien de volé)"
            first = runs[0] if runs else {}
            outcomes = {r.get("outcome") for r in runs}
            items.append(
                {
                    "path": path.name,
                    "kind": "series",
                    "when": path.name,
                    "what": what,
                    "with": first.get("streamer", ""),
                    "outcome": "done"
                    if outcomes == {"done"}
                    else ", ".join(sorted(map(str, outcomes))),
                }
            )
        elif (
            path.is_file()
            and path.suffix == ".md"
            and re.match(r"^\d{4}-\d\d-\d\d \d{6} ", path.name)
        ):
            items.append(
                {
                    "path": path.name,
                    "kind": "campaign",
                    "when": path.name[:17],
                    "what": f"campagne {path.stem[18:]}",
                    "with": "",
                    "outcome": "",
                }
            )
    return sorted(items, key=lambda i: i["when"], reverse=True)


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def markdown_html(text: str) -> str:
    """The reports' own markdown, as HTML: headings, lists, tables, code blocks, bold, code. Every
    character of the text is escaped first."""
    out: list[str] = []
    lines = html.escape(text, quote=False).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre>" + "\n".join(block) + "</pre>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"-+", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            i -= 1
            head, body = rows[0], rows[1:]
            out.append(
                '<div class="table-wrap"><table class="table"><thead><tr>'
                + "".join(f"<th>{_inline(c)}</th>" for c in head)
                + "</tr></thead><tbody>"
                + "".join(
                    "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body
                )
                + "</tbody></table></div>"
            )
        elif m := re.match(r"^(#{1,3}) (.*)$", line):
            level = len(m[1]) + 1
            out.append(f"<h{level}>{_inline(m[2])}</h{level}>")
        elif m := re.match(r"^( *)- (.*)$", line):
            items = []
            while i < len(lines) and (m := re.match(r"^( *)- (.*)$", lines[i])):
                cls = ' class="sub"' if m[1] else ""
                items.append(f"<li{cls}>{_inline(m[2])}</li>")
                i += 1
            i -= 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(out)


def result_view(rel: str, results: Path = fly.RESULTS) -> dict[str, Any]:
    """A series (its summary, then each flight's report and screenshots) or a campaign's table."""
    path = _inside(results, rel)
    parts: list[dict[str, Any]] = []
    if path.is_file():
        parts.append({"title": path.stem, "html": markdown_html(path.read_text(errors="replace"))})
        return {"parts": parts}
    if not path.is_dir():
        raise BenchError("introuvable")
    summary = path / "summary.md"
    if summary.is_file():
        parts.append({"title": "Bilan", "html": markdown_html(summary.read_text(errors="replace"))})
    for flight in sorted(p for p in path.iterdir() if p.is_dir() and re.match(r"^\d+-", p.name)):
        report = flight / "report.md"
        shots = (
            sorted((flight / "screenshots").glob("*.png"))
            if (flight / "screenshots").is_dir()
            else []
        )
        parts.append(
            {
                "title": flight.name,
                "html": markdown_html(report.read_text(errors="replace"))
                if report.is_file()
                else "",
                "screenshots": [f"results/{rel}/{flight.name}/screenshots/{s.name}" for s in shots],
            }
        )
    return {"parts": parts}


# ---- the window ---------------------------------------------------------------------------------


def create_app(runner: Runner, results: Path = fly.RESULTS) -> Any:
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import FileResponse

    from orthostudio.api.app import (
        DEFAULT_ALLOWED_HOSTS,
        PAGE_CACHE_CONTROL,
        _GuardMiddleware,
        _RevalidatedStaticFiles,
    )
    from orthostudio.ui import ui_dir

    app = FastAPI(title=TITLE, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_GuardMiddleware, allowed_hosts=DEFAULT_ALLOWED_HOSTS)
    app.mount("/static", _RevalidatedStaticFiles(directory=ui_dir()), name="static")
    app.mount("/bench", _RevalidatedStaticFiles(directory=PAGE_DIR), name="bench")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(PAGE_DIR / "index.html", headers={"Cache-Control": PAGE_CACHE_CONTROL})

    @app.get("/api/bench/options")
    def get_options() -> dict[str, Any]:
        return options()

    @app.get("/api/bench/machine")
    def machine() -> dict[str, bool]:
        return {
            "xplane": fly.xplane_up(),
            "live": fly.live_runs(),
            "autoortho": fly.autoortho_runs(),
        }

    @app.get("/api/bench/status")
    def status(since: int = 0) -> dict[str, Any]:
        return runner.status(max(0, since))

    @app.post("/api/bench/start")
    def start(choice: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        try:
            return runner.start(fly_args(choice))
        except BenchError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/bench/stop")
    def stop() -> dict[str, bool]:
        return {"stopped": runner.stop()}

    @app.get("/api/bench/results")
    def get_results() -> list[dict[str, Any]]:
        return results_list(results)

    @app.get("/api/bench/result")
    def get_result(path: str) -> dict[str, Any]:
        try:
            return result_view(path, results)
        except BenchError as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/results/{rel:path}", include_in_schema=False)
    def result_file(rel: str) -> FileResponse:
        try:
            path = _inside(results, rel)
        except BenchError:
            raise HTTPException(404, "not found") from None
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".csv", ".txt", ".md"):
            raise HTTPException(404, "not found")
        return FileResponse(path)

    return app


def main(argv: list[str] | None = None) -> int:
    """The window; ``--serve``: the page and its API only, until Ctrl-C (a browser of one's own);
    ``--port N``: another port than PORT."""
    import uvicorn

    from orthostudio import window

    args = sys.argv[1:] if argv is None else argv
    serve_only = "--serve" in args
    port = int(args[args.index("--port") + 1]) if "--port" in args else PORT
    app = create_app(Runner())
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, name="bench-web", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            print(f"nothing could listen on 127.0.0.1:{port} (the window open already?)")
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return 1
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/"
    try:
        if serve_only:
            print(f"{TITLE}: {url} (Ctrl-C here to stop)", flush=True)
            while True:
                time.sleep(1)
        elif window.possible():
            window.show(url, title=TITLE, size=SIZE)
        else:
            webbrowser.open(url)
            print(f"{TITLE}: {url} (Ctrl-C here to close)")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
