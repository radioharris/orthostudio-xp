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
    "ENGINE_ARGS",
    "ENGINE_PORT",
    "LOG_NAME",
    "a_build_runs",
    "engine_here",
    "in_a_window",
    "log_path",
    "main",
    "on_close",
    "open_running",
    "open_while_starting",
    "opening_page",
    "start_engine",
    "time_to_close",
]

APP_NAME = "OrthoStudio XP"
LOG_NAME = "serve.log"
DEFAULT_ARGS = ("serve", "--open", "--quit-when-closed")
ENGINE_ARGS = ("serve", "--no-open", "--quit-when-closed")
"""What the engine of a window is started with: the window shows the page, not the browser."""
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


def engine_here(port: int = ENGINE_PORT, *, timeout_s: float = 5.0) -> bool:
    """Whether the OrthoStudio XP already serving ``port`` is this installation at this version
    (``GET /api/engine``: the same package folder, the same version). ``False`` when another one,
    an older one, or something else holds the port: the usual start then decides (it is asked to
    stop, :func:`orthostudio.api.serve.take_over`).

    Asked before the engine's imports, which take seconds: a user on Windows who closed the
    browser and opened the app again saw nothing come, and ended the engine in the Task Manager
    (2026-09-17).
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
        return (
            doc.get("version") == __version__
            and isinstance(root, str)
            and Path(root).resolve() == Path(__file__).resolve().parent
        )
    except (OSError, ValueError, AttributeError):
        return False


def open_running(
    port: int = ENGINE_PORT,
    *,
    browser: Callable[[str], object] = webbrowser.open,
    timeout_s: float = 5.0,
) -> bool:
    """Open the page of the OrthoStudio XP already serving ``port``, when :func:`engine_here`;
    ``False``, and nothing opened, otherwise."""
    if not engine_here(port, timeout_s=timeout_s):
        return False
    browser(f"http://127.0.0.1:{port}/")
    return True


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


def _note(log: Path, text: str) -> None:
    """One line in the log, in the shape the engine's own starts have."""
    with log.open("a", encoding="utf-8") as out:
        out.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME}: {text}\n")


def start_engine(log: Path) -> None:
    """Start the engine in a process of its own, apart from this one, its output in ``log``.

    The window is held by the app's own process, which macOS knows by the app it came from; a
    window opened by a process started aside is called Python and carries Python's icon (measured,
    2026-09-20). The engine is the one put aside, and outlives the window on purpose: closing the
    window leaves a build running, and the engine stops by itself a while after its last page
    (``--quit-when-closed``).
    """
    import subprocess

    from orthostudio.fsutil import NO_CONSOLE_WINDOW

    with log.open("a", encoding="utf-8") as out:
        out.write(
            f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME}: orthostudio "
            f"{' '.join(ENGINE_ARGS)} (for its own window)\n"
        )
        out.flush()
        subprocess.Popen(
            [sys.executable, "-m", "orthostudio", *ENGINE_ARGS],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
            # pythonw has no console, and a console program it starts flashes a window
            creationflags=NO_CONSOLE_WINDOW,
        )


def time_to_close(port: int = ENGINE_PORT) -> Callable[[], bool]:
    """Whether the window has nothing left to show; asked while it is open.

    Yes once the engine has answered and then stopped, which is *Quit* from the page: the window
    would otherwise stand on a page with nothing behind it. No while the engine is still coming
    up, so that a slow start does not take the window away from under the opening page.

    Nothing else closes it. An app put away is still an app, and it stays in the Dock until the
    person who opened it says otherwise: OrthoStudio XP does not disappear from under them.
    """
    answered = False

    def close_now() -> bool:
        nonlocal answered
        if _listening(port):
            answered = True
            return False
        return answered

    return close_now


def may_quit() -> bool:
    """What Cmd+Q, the Quit of the app's own menu and the Quit of its Dock menu do.

    They must not take the app away from under a build without a word. The page already has the
    question, in the user's language, with what a build and a queue are worth: the window comes
    back in front, put away or not, and its own Quit button is pressed
    (``orthostudio.window.ask_the_page_to_quit``). This answers no meanwhile; what stops the app
    is the engine stopping, which the page does once the user has said yes (:func:`time_to_close`).

    Yes, at once, when no engine answers: there is nothing to ask about, and nothing to stop.
    """
    from orthostudio import window

    if not _listening(ENGINE_PORT):
        return True
    window.to_the_front()
    threading.Thread(
        target=window.ask_the_page_to_quit, name="ask-the-page-to-quit", daemon=True
    ).start()
    return False


BUILDING_ON_QUIT = (
    "A build is running. Quit OrthoStudio XP and stop it?\n\n"
    "What it has already built is kept: starting the build again carries on from there."
)
"""Asked by the close button where closing quits the app, and only when a build runs.

Closing a window on Windows means quitting the app (a user, 2026-09-20), so the engine goes with
it rather than living on behind a window that is gone: an app that keeps working where nothing
shows it is the very thing this window was made to end. A build is the one thing worth asking
about, and the answer says what it costs, which is the time since its last finished step and not
the work before it (the store is keyed by content: a build started again takes what is there)."""


def a_build_runs(port: int = ENGINE_PORT, *, timeout_s: float = 1.5) -> bool:
    """Whether the engine on ``port`` has a build running or waiting (``GET /api/engine``).

    Asked on the thread that draws, when the close button is pressed, so it does not wait long:
    the engine is on the loopback, and a slow answer is one the user would feel.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/engine", timeout=timeout_s
        ) as answer:
            doc = json.load(answer)
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("active_job") is not None


def stop_the_engine(
    port: int = ENGINE_PORT, *, force: bool = False, timeout_s: float = 3.0
) -> bool:
    """Ask the engine on ``port`` to stop (``POST /api/quit``); whether it answered.

    A build running is stopped only with ``force``, which is what the page itself passes once the
    user has said so.
    """
    body = json.dumps({"force": force}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/quit",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as answer:
            return 200 <= int(answer.status) < 300
    except OSError:
        return False


def on_close() -> Callable[[], bool] | None:
    """What the close button does, which is not the same thing everywhere.

    On macOS it puts the window away: closing a window there does not quit its app, which stays in
    the Dock for the click that brings it back. On Windows and Linux it quits, which is what
    closing a window means there, and the engine stops with it: leaving it to work on behind a
    window that is gone, with nothing to show for it, is the very thing the window was made to
    end. A build is worth asking about first (:data:`BUILDING_ON_QUIT`), and only a build; the
    question is asked aside, and this answers no meanwhile, because the box cannot be drawn by the
    thread waiting for this answer.
    """
    from orthostudio import window

    if window.puts_away_on_close():

        def keep() -> bool:
            window.away()
            return False  # the window stays, out of sight, and the icon stays in the Dock

        return keep

    said_yes = [False]

    def quit_the_app() -> bool:
        if said_yes[0]:
            return True  # asked and answered: closing must not ask the same question again
        if not a_build_runs(ENGINE_PORT):
            stop_the_engine(ENGINE_PORT)  # nothing to ask about, and nothing left behind
            return True

        def aside() -> None:
            """Closing the window comes back through here, which is why the answer is kept."""
            if window.ask(APP_NAME, BUILDING_ON_QUIT):
                stop_the_engine(ENGINE_PORT, force=True)
                said_yes[0] = True
                window.close_now()

        threading.Thread(target=aside, name="ask-before-quitting", daemon=True).start()
        return False

    return quit_the_app


def in_a_window(log: Path, *, show: Callable[..., None] | None = None) -> bool:
    """Show the app in a window of its own, and return ``True`` once that window is closed.

    ``False``, and nothing started, when this system has no window to give: the caller opens the
    browser, as every version until 0.1.9 did, and the doctor's ``window`` check says in the page
    what to install for one. The engine starts only once the window is up, behind the page that
    says so, so that a system without one is left as it was found.
    """
    from orthostudio import window

    if show is None:
        if not window.possible():
            _note(log, window.hint() or "no window of its own on this system")
            return False
        show = window.show
    running = engine_here(ENGINE_PORT)
    url = f"http://127.0.0.1:{ENGINE_PORT}/"
    if not running:
        shown: list[str] = []
        if open_while_starting(ENGINE_PORT, log=log, browser=shown.append) and shown:
            url = shown[0]  # the opening page, which goes to the engine's by itself
    try:
        show(
            url,
            title=APP_NAME,
            on_shown=None if running else lambda: start_engine(log),
            closes_when=time_to_close(ENGINE_PORT),
            on_close=on_close(),
            may_quit=may_quit,
            note=lambda text: _note(log, text),
        )
    except Exception:
        _note(log, "its window could not be shown: the browser opens instead")
        return False
    _note(log, "its window closed")
    return True


def main(
    argv: Sequence[str] | None = None,
    *,
    log: Path | None = None,
    opening: Callable[..., bool] | None = None,
    running: Callable[[int], bool] | None = None,
    window: Callable[..., None] | bool | None = None,
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
    if (
        starting
        and window is not False
        and in_a_window(path, show=window if callable(window) else None)
    ):
        return 0
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
