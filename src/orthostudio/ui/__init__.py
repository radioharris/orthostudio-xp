# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The web page of OrthoStudio XP: static files served
by ``orthostudio.api.create_app(ui_dir=ui_dir())``.

Spec: ``docs/specs/ui.md`` and ``docs/specs/map-zones.md`` section 7. The page is plain HTML, CSS
and native ES modules talking to ``/api``, plus the vendored Leaflet 1.9.4 under ``vendor/leaflet/``
and the country borders of Natural Earth under ``vendor/borders/``; a page's server writes what it
starts from into it (:func:`with_json`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["INDEX_FILE", "STATIC_FILES", "ui_dir", "with_json"]

STATIC_FILES: tuple[str, ...] = (
    "app.js",
    "geo.js",
    "i18n.js",
    "map.js",
    "settings.js",
    "styles.css",
    "vendor/borders/borders.json",
    "vendor/leaflet/leaflet.css",
    "vendor/leaflet/leaflet.js",
)
INDEX_FILE = "index.html"


def ui_dir() -> Path:
    """Directory holding ``index.html`` and the files served under ``/static``."""
    return Path(__file__).resolve().parent


def with_json(page: str, mark: str, value: Any) -> str:
    """``page`` with ``value`` written as JSON into the script element ``mark``, as the page carries
    it (``<script type="application/json" id="...">...</script>``). Every ``<`` is written
    ``\\u003c``, which JSON reads back as it was: nothing a value holds can end the script."""
    opening = mark[: mark.index(">") + 1]
    literal = json.dumps(value).replace("<", "\\u003c")
    return page.replace(mark, f"{opening}{literal}</script>", 1)
