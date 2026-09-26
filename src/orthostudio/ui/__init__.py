# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The web page of OrthoStudio XP: static files served
by ``orthostudio.api.create_app(ui_dir=ui_dir())``.

Spec: ``docs/specs/ui.md`` and ``docs/specs/map-zones.md`` section 7. Nothing here runs Python
at request time; the page is plain HTML, CSS and native ES modules talking to ``/api``, plus
the vendored Leaflet 1.9.4 under ``vendor/leaflet/`` and the country borders of Natural Earth under
``vendor/borders/``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["INDEX_FILE", "STATIC_FILES", "ui_dir"]

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
