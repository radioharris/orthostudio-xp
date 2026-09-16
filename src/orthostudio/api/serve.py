"""``osxp serve``: the API and the page on ``127.0.0.1`` (spec ``api.md`` section 6).

``main`` is what ``cli.py`` wires; it never binds another address than the loopback.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import webbrowser
from pathlib import Path

from orthostudio.errors import OsxpError

__all__ = [
    "DEFAULT_PORT",
    "HOST",
    "QUIT_API_LEVEL",
    "check",
    "default_ui_dir",
    "main",
    "package_root",
    "running_osxp",
    "serve",
    "take_over",
]

HOST = "127.0.0.1"
DEFAULT_PORT = 8641
QUIT_API_LEVEL = 8
"""The first engine that can be stopped from outside (``POST /api/quit``)."""


def default_ui_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ui"


def package_root() -> Path:
    """The folder of the ``orthostudio`` package this engine runs: tells an installation from
    another (an app, a checkout) in ``/api/status``'s ``engine``."""
    return Path(__file__).resolve().parent.parent


def _check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            # Windows: SO_REUSEADDR lets a socket bind a port another one is listening on, so the
            # check passed and uvicorn then failed on the busy port. Exclusive use refuses it.
            s.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
        except OSError as exc:
            raise OsxpError(
                "SYS_RESOURCE_MISSING",
                context={"path": f"{HOST}:{port}"},
                message=f"Port {port} on {HOST} is already in use ({exc.strerror or exc}).",
                remedy="Close the other osxp serve, or give --port.",
            ) from None


def running_osxp(port: int, timeout_s: float = 1.5) -> dict | None:
    """The status of an OrthoStudio XP already serving on ``port``, or ``None`` (nothing, or not
    OrthoStudio XP)."""
    import httpx

    try:
        r = httpx.get(f"http://{HOST}:{port}/api/status", timeout=timeout_s)
        doc = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return doc if isinstance(doc, dict) and "api_level" in doc else None


def running_root(status: dict) -> Path | None:
    """The package folder of the OrthoStudio XP whose ``/api/status`` this is, when it says."""
    engine = status.get("engine")
    root = engine.get("root") if isinstance(engine, dict) else None
    return Path(root) if isinstance(root, str) and root else None


def take_over(port: int, status: dict, *, timeout_s: float = 15.0) -> bool:
    """Whether the OrthoStudio XP running on ``port`` stopped when asked, leaving the port free.

    It is asked to quit, without stopping a build, when it is older than this one, or when it runs
    another installation (another package folder, or one that does not say: the app and a
    checkout). An older engine serves this version's page next to its own routes, and the page can
    only ask to restart it; another installation made a user who ran a checkout's
    ``osxp serve --open`` get the app's page and code, told only that it was already running
    (2026-09-14). The same installation as recent as this one stays: opening the app twice shows
    the running one. It stays too, and ``False`` is returned, when a build runs in it, when it is
    older than ``POST /api/quit``, or when the port is not free in ``timeout_s``.
    """
    import httpx

    from orthostudio.api.app import API_LEVEL

    try:
        level = int(status.get("api_level", 1))
    except (TypeError, ValueError):
        level = 1
    root = running_root(status)
    other = root is None or root.resolve() != package_root()
    if level >= API_LEVEL and not other:
        return False
    which = "An older OrthoStudio XP" if level < API_LEVEL else "Another OrthoStudio XP"
    where = f" ({root})" if other and root is not None else ""
    if level < QUIT_API_LEVEL:
        print(f"{which} is running{where}: quit it, then open OrthoStudio XP again.")
        return False
    try:
        answer = httpx.post(f"http://{HOST}:{port}/api/quit", json={}, timeout=3.0)
    except httpx.HTTPError:
        return False
    if answer.status_code != 200:
        print(
            f"{which} is running{where} and did not stop (a build may be running in it): quit it "
            "once it is done, then open OrthoStudio XP again."
        )
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _check_port(port)
        except OsxpError:
            time.sleep(0.2)
            continue
        print(f"{which} was running{where}: it stopped, this one starts.")
        return True
    return False


def serve(
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    ui_dir: Path | None = None,
    log_level: str = "info",
) -> None:
    """Run uvicorn on ``127.0.0.1:port`` until interrupted or quit from the page.

    When an OrthoStudio XP already serves the port, its page is opened instead: launching the app
    twice shows the running OrthoStudio XP rather than an error. An older one, or another
    installation, is asked to stop first (:func:`take_over`), so that the OrthoStudio XP opened is
    the one that runs.
    """
    import uvicorn

    from orthostudio.api.app import create_app

    url = f"http://{HOST}:{port}/"
    try:
        _check_port(port)
    except OsxpError:
        status = running_osxp(port)
        if status is None:
            raise
        if not take_over(port, status):
            print(f"OrthoStudio XP is already running: {url}")
            if open_browser:
                webbrowser.open(url)
            return
    ui = Path(ui_dir) if ui_dir is not None else default_ui_dir()
    holder: dict[str, uvicorn.Server] = {}

    def shutdown() -> None:
        server = holder.get("server")
        if server is not None:
            server.should_exit = True

    app = create_app(ui_dir=ui if ui.is_dir() else None, shutdown=shutdown)
    if open_browser:
        timer = threading.Timer(0.8, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level=log_level))
        holder["server"] = server
        server.run()
    finally:
        manager = app.state.orthostudio["jobs"]
        with contextlib.suppress(Exception):
            manager.close(timeout=5.0)


def check(*, port: int = DEFAULT_PORT, ui_dir: Path | None = None, timeout_s: float = 20.0) -> dict:
    """Start the real server, read ``/api/status`` over HTTP, stop it. Returns the status.

    This is what CI runs: it proves the port binds, uvicorn serves, the page directory is
    mounted and the job manager shuts down, without leaving a process behind.
    """
    import time

    import httpx
    import uvicorn

    from orthostudio.api.app import create_app

    _check_port(port)
    ui = Path(ui_dir) if ui_dir is not None else default_ui_dir()
    app = create_app(ui_dir=ui if ui.is_dir() else None)
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="osxp-serve-check", daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    try:
        while time.monotonic() < deadline:
            if not thread.is_alive():
                raise OsxpError(
                    "SYS_INTERNAL_ERROR",
                    message="the server thread stopped before answering",
                    remedy="Run osxp serve to see the traceback.",
                )
            try:
                r = httpx.get(f"http://{HOST}:{port}/api/status", timeout=2.0)
                r.raise_for_status()
                payload = r.json()
                page = httpx.get(f"http://{HOST}:{port}/", timeout=2.0)
                payload["page_status"] = page.status_code
                return payload
            except Exception as exc:  # not up yet
                last = exc
                time.sleep(0.1)
        raise OsxpError(
            "NET_TIMEOUT",
            message=f"the server did not answer on {HOST}:{port} within {timeout_s:g} s ({last})",
            remedy="Check that no firewall blocks the loopback interface.",
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        manager = app.state.orthostudio["jobs"]
        with contextlib.suppress(Exception):
            manager.close(timeout=5.0)


def main(
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    ui_dir: Path | None = None,
) -> int:
    """Entry point for ``cli.py``; returns the exit status."""
    try:
        serve(port=port, open_browser=open_browser, ui_dir=ui_dir)
    except OsxpError as exc:
        import sys

        print(f"error {exc.code}: {exc.message}", file=sys.stderr)
        if exc.remedy:
            print(f"  remedy: {exc.remedy}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
