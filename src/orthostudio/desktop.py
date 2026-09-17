"""What the installed app starts: ``osxp serve --open``, its output in a log file.

The launchers of the installers run ``python -m orthostudio.desktop`` (``docs/specs/packaging.md``).
Started from the Finder, the Start menu or a desktop menu, the engine has no terminal to write to
(``pythonw`` on Windows gives it none at all), so its output goes to ``serve.log`` in the
platform's log folder: ``~/Library/Logs/OrthoStudio XP`` on macOS,
``%LOCALAPPDATA%\\OrthoStudio XP\\Logs`` on Windows, ``~/.local/state/OrthoStudio XP/log`` on
Linux. Opening the app while OrthoStudio XP runs opens the running one's page, at once when it is
this same installation (:func:`open_running`). The app stops by itself a while after its last page
closed, unless a build runs (``--quit-when-closed``, :mod:`orthostudio.api.presence`): nothing else
shows it once the page is gone.

Until the engine answers, the browser shows a page saying OrthoStudio XP is opening
(:func:`open_while_starting`): the engine takes a few seconds to start, much longer on a first
launch or in a virtual machine, and the app showed nothing meanwhile (a user asked, 2026-09-15).
"""

from __future__ import annotations

import http.server
import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

from platformdirs import user_log_dir

from orthostudio import __version__

__all__ = [
    "APP_NAME",
    "DEFAULT_ARGS",
    "ENGINE_PORT",
    "LOG_NAME",
    "log_path",
    "main",
    "open_running",
    "open_while_starting",
    "opening_page",
]

APP_NAME = "OrthoStudio XP"
LOG_NAME = "serve.log"
DEFAULT_ARGS = ("serve", "--open", "--quit-when-closed")
ENGINE_PORT = 8641
"""The port the app's engine listens on (``orthostudio.api.serve.DEFAULT_PORT``; a test keeps the
two equal)."""
OPENING_SERVED_S = 120.0
"""How long the opening page stays served: once loaded, it waits for the engine on its own."""

_OPENING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OrthoStudio XP</title>
<style>
:root { color-scheme: light dark; --bg: #f6f7f9; --fg: #1d2330; --muted: #5b6475; --ring: #2f6fde; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #14171d; --fg: #e7eaf0; --muted: #9aa3b2; --ring: #6c9cff; }
}
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
  color: var(--fg); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { text-align: center; padding: 24px; max-width: 36em; }
.spinner { width: 34px; height: 34px; margin: 0 auto 18px; border-radius: 50%;
  border: 3px solid rgba(128, 128, 128, 0.25); border-top-color: var(--ring);
  animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
h1 { font-size: 20px; margin: 0 0 6px; }
p { margin: 0; color: var(--muted); }
#slow { margin-top: 14px; overflow-wrap: anywhere; }
</style>
</head>
<body>
<main>
<div class="spinner" aria-hidden="true"></div>
<h1 id="title">Opening OrthoStudio XP…</h1>
<p id="text">Its page shows as soon as it is ready.</p>
<p id="slow" hidden></p>
</main>
<script>
const ENGINE = "http://127.0.0.1:%PORT%/";
const LOG = %LOG%;
const fr = (navigator.language || "").toLowerCase().startsWith("fr");
const $ = (id) => document.getElementById(id);
if (fr) {
  document.documentElement.lang = "fr";
  $("title").textContent = "Ouverture d'OrthoStudio XP…";
  $("text").textContent = "Sa page s'affiche dès qu'elle est prête.";
}
const started = Date.now();
async function poll() {
  try {
    // any answer of the engine's port means it listens (no-cors: the answer itself is not read)
    await fetch(ENGINE + "api/status", { mode: "no-cors", cache: "no-store" });
    location.replace(ENGINE);
    return;
  } catch (_err) {
    // not listening yet
  }
  if (Date.now() - started > 45000 && $("slow").hidden) {
    $("slow").hidden = false;
    $("slow").textContent = fr
      ? "Le démarrage est long. S'il n'aboutit pas, le journal " + LOG + " dit pourquoi."
      : "Starting takes long. If it does not end, the log " + LOG + " says why.";
  }
  setTimeout(poll, 400);
}
poll();
</script>
</body>
</html>
"""


def log_path() -> Path:
    """``serve.log`` in the platform's log folder for OrthoStudio XP (not created)."""
    return Path(user_log_dir(APP_NAME, appauthor=False)) / LOG_NAME


def opening_page(port: int, log: Path) -> bytes:
    """The page the browser shows while the engine starts: it asks the engine's ``port`` again
    every 0.4 s and goes to its page once it answers; after 45 s it names the ``log``."""
    literal = json.dumps(str(log)).replace("</", "<\\/")
    return _OPENING_PAGE.replace("%PORT%", str(port)).replace("%LOG%", literal).encode("utf-8")


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def open_running(
    port: int = ENGINE_PORT,
    *,
    browser: Callable[[str], object] = webbrowser.open,
    timeout_s: float = 5.0,
) -> bool:
    """Open the page of the OrthoStudio XP already serving ``port`` when it is this installation at
    this version (``GET /api/engine``: the same package folder, the same version); ``False``, and
    nothing opened, otherwise: the usual start then decides (an older one or another installation
    is asked to stop).

    Before the engine's imports, which take seconds: a user on Windows who closed the browser and
    opened the app again saw nothing come, and ended the engine in the Task Manager (2026-09-17).
    """
    if not _listening(port):
        return False
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/engine", timeout=timeout_s
        ) as answer:
            doc = json.load(answer)
        engine = doc.get("engine") if isinstance(doc, dict) else None
        root = engine.get("root") if isinstance(engine, dict) else None
        same = (
            doc.get("version") == __version__
            and isinstance(root, str)
            and Path(root).resolve() == Path(__file__).resolve().parent
        )
    except (OSError, ValueError, AttributeError):
        return False
    if same:
        browser(f"http://127.0.0.1:{port}/")
    return same


def open_while_starting(
    port: int = ENGINE_PORT,
    *,
    log: Path,
    browser: Callable[[str], object] = webbrowser.open,
) -> bool:
    """Show the opening page (:func:`opening_page`) at once, before the engine's long imports:
    served from a free port of the loopback for :data:`OPENING_SERVED_S`, opened in ``browser``.

    ``False``, and nothing shown, when something already listens on the engine's ``port``: the
    usual start opens the running OrthoStudio XP, or asks an older one to stop first. The page is
    on another port than the engine's, so that the engine takes its port without waiting for it.
    """
    if _listening(port):
        return False
    body = opening_page(port, log)

    class Opening(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return  # the engine's log is for the engine

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Opening)
    except OSError:
        return False
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="opening-page", daemon=True).start()
    stop = threading.Timer(OPENING_SERVED_S, server.shutdown)
    stop.daemon = True
    stop.start()
    browser(f"http://127.0.0.1:{server.server_address[1]}/")
    return True


def main(
    argv: Sequence[str] | None = None,
    *,
    log: Path | None = None,
    opening: Callable[..., bool] | None = None,
    running: Callable[[int], bool] | None = None,
) -> int:
    """Run the ``orthostudio`` command with ``argv`` (default :data:`DEFAULT_ARGS`), its output
    appended to ``log`` (default :func:`log_path`); returns its exit status.

    Without ``argv``, the app's own start: ``running`` (default :func:`open_running`) opens the
    page of this OrthoStudio XP when it runs already, and nothing starts; else ``opening``
    (default :func:`open_while_starting`) shows the opening page first, and the engine then does
    not open the page again."""
    path = log_path() if log is None else Path(log)
    path.parent.mkdir(parents=True, exist_ok=True)
    starting = not argv
    if starting and (open_running if running is None else running)(ENGINE_PORT):
        with path.open("a", encoding="utf-8") as out:
            out.write(
                f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME}: already running, its page "
                "opened\n"
            )
        return 0
    args = list(argv) if argv else list(DEFAULT_ARGS)
    if starting and (open_while_starting if opening is None else opening)(ENGINE_PORT, log=path):
        # the opening page goes to the engine's page by itself
        args = ["serve", "--no-open", "--quit-when-closed"]

    from orthostudio.cli import app  # after the opening page: the imports are the long part

    saved = sys.stdout, sys.stderr
    with path.open("a", encoding="utf-8", buffering=1) as out:
        sys.stdout = sys.stderr = out
        try:
            print(
                f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME}: orthostudio {' '.join(args)}"
            )
            try:
                app(args=args, prog_name="orthostudio")
            except SystemExit as exc:
                return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            return 0
        finally:
            sys.stdout, sys.stderr = saved


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
