# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The mark of the code an OrthoStudio XP runs (``orthostudio.codemark``).

A checkout opened again after a change showed the engine started before it, the old code behind
the new page (2026-09-25): a launch compares the mark the engine started with and the files.
"""

from __future__ import annotations

import os
from pathlib import Path

from orthostudio.codemark import PACKAGE, code_mark


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "orthostudio"
    (root / "api").mkdir(parents=True)
    (root / "__init__.py").write_text('__version__ = "0.1.18"\n', encoding="utf-8")
    (root / "api" / "serve.py").write_text("PORT = 8641\n", encoding="utf-8")
    (root / "ui").mkdir()
    (root / "ui" / "app.js").write_text("const A = 1;\n", encoding="utf-8")
    return root


def test_the_mark_changes_with_the_code_and_only_with_it(tmp_path: Path) -> None:
    root = _package(tmp_path)
    first = code_mark(root)
    assert code_mark(root) == first  # nothing changed
    # the page is read from the disk at each load: its files are not the engine's code
    (root / "ui" / "app.js").write_text("const A = 2;\n", encoding="utf-8")
    assert code_mark(root) == first
    serve = root / "api" / "serve.py"
    serve.write_text("PORT = 8642\n", encoding="utf-8")  # same size: the time of change tells
    stat = serve.stat()
    os.utime(serve, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    edited = code_mark(root)
    assert edited != first
    (root / "api" / "presence.py").write_text("", encoding="utf-8")  # a file added
    added = code_mark(root)
    assert added != edited
    (root / "api" / "presence.py").unlink()  # and taken away again
    assert code_mark(root) == edited


def test_the_mark_is_of_this_package_by_default() -> None:
    import orthostudio

    assert Path(orthostudio.__file__).resolve().parent == PACKAGE
    assert code_mark() == code_mark(PACKAGE) and len(code_mark()) == 16
