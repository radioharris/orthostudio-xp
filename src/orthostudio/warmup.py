# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The app's code opened once at the end of its Windows install, so that its first start does not
wait for it.

The first time a new file is opened, Windows reads it from the disk and its antivirus may inspect
it: on a cloud PC the first start of 0.1.21 after its install took about 10 s, 7 of them loading
the engine's code, where the next ones took about 3 s, and Live's build waited more than a minute
(a user, 2026-10-02 and 2026-10-03). The setup runs this under its progress bar
(``tools/package/build.py``, ``inno_setup_script``): every module of OrthoStudio XP, which its
window and its engine load, and on Windows the .NET and web view files its window shows its page
through. Nothing is written or started; a part that cannot load is passed over, and the app's own
start says why. The setup never waits more than :data:`LIMIT_S` for it.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import pkgutil
import sys
import threading
from collections.abc import Callable

import orthostudio

LIMIT_S = 300.0
"""The longest the setup waits: past it, what is loaded stays so and the setup goes on."""

WINDOW_PARTS = ("webview", "webview.platforms.winforms")
"""What the window loads beside OrthoStudio XP's code: pywebview and, on Windows, .NET, Windows
Forms and the WebView2 interop, which ``webview.platforms.winforms`` loads as it is imported."""


def modules() -> list[str]:
    """Every module of the app but the ``__main__`` ones, which would run a command."""
    found = pkgutil.walk_packages(orthostudio.__path__, "orthostudio.", onerror=lambda _: None)
    return [m.name for m in found if m.name.rsplit(".", 1)[-1] != "__main__"]


def main(load: Callable[[str], object] = importlib.import_module) -> int:
    """Open every module (``load``), then the window's parts; 0 whatever could not load."""
    stop = threading.Timer(LIMIT_S, os._exit, (0,))
    stop.daemon = True
    stop.start()
    window = WINDOW_PARTS if sys.platform == "win32" else WINDOW_PARTS[:1]
    try:
        for name in (*modules(), *window):
            with contextlib.suppress(Exception):
                load(name)
    finally:
        stop.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
