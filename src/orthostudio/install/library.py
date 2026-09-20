"""The library of built tiles (``~/.orthostudio/library.sqlite``) and the import of Ortho4XP tiles.

Spec: ``docs/specs/install.md`` section 5. One row per (tile, kind, path): where the pack is, which
provider and ZL it holds, who built it (``osxp`` with the store keys of its artefacts, or
``ortho4xp`` with no keys: installable but not incremental).

``import_ortho4xp`` scans an Ortho4XP folder the way its GUI does (``O4_File_Names.py:20-22,
62-68``: ``Tiles/``, the custom build dir, ``yOrtho4XP_Overlays/``) and reads the tile cfg with
``orthostudio.tilefiles``. It is the one place OrthoStudio XP reads an Ortho4XP folder
(decision 0010), and it only registers what it finds.
"""

from __future__ import annotations

import builtins
import json
import logging
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orthostudio.errors import OsxpError
from orthostudio.install._model import TileRef
from orthostudio.install.scenery_packs import (
    IMPORTED_OVERLAY_PACK,
    IMPORTED_PACK_PREFIX,
    ORTHO_PREFIXES,
)
from orthostudio.pipeline.home import osxp_home
from orthostudio.tilefiles import list_textures, tile_config

__all__ = [
    "LIBRARY_FILENAME",
    "SCHEMA_VERSION",
    "BuiltBy",
    "Library",
    "LibraryEntry",
    "PackKind",
    "default_library_path",
    "pack_tile",
    "tile_from_name",
]

log = logging.getLogger("orthostudio.install.library")

LIBRARY_FILENAME = "library.sqlite"
SCHEMA_VERSION = 1
IMPORT_TILES_DIR = "Tiles"
IMPORT_GUI_PARAMS = ".last_gui_params.txt"
IMPORT_GLOBAL_CFG = "Ortho4XP.cfg"

BuiltBy = Literal["osxp", "ortho4xp"]
PackKind = Literal["ortho", "overlay"]

_TILE_NAME = re.compile(r"^([+-]\d{2})([+-]\d{3})$")

_TILES_COLUMNS = """(
    lat           INTEGER NOT NULL,
    lon           INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('ortho', 'overlay')),
    path          TEXT NOT NULL,
    provider      TEXT NOT NULL,
    zl            INTEGER NOT NULL,
    built_by      TEXT NOT NULL CHECK (built_by IN ('osxp', 'ortho4xp')),
    keys          TEXT,
    registered_at REAL NOT NULL,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (lat, lon, kind, path)
)"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tiles {_TILES_COLUMNS};
"""


def default_library_path() -> Path:
    """``<OrthoStudio XP home>/library.sqlite`` (``orthostudio.pipeline.home``)."""
    return osxp_home() / LIBRARY_FILENAME


def tile_from_name(name: str) -> TileRef | None:
    """``+43+005`` -> ``TileRef(43, 5)``; ``None`` when the text is not a tile name."""
    m = _TILE_NAME.match(name)
    if m is None:
        return None
    return TileRef(int(m.group(1)), int(m.group(2)))


def pack_tile(name: str) -> TileRef | None:
    """The tile a pack name or a tile name designates: ``zOrthoStudio_+43+005``, an imported
    ``zOrtho4XP_+43+005`` or ``+43+005``; ``None`` for anything else."""
    for prefix in ORTHO_PREFIXES:
        if name.startswith(prefix):
            return tile_from_name(name[len(prefix) :])
    return tile_from_name(name)


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One row of the library."""

    tile: TileRef
    kind: PackKind
    path: Path
    provider: str
    zl: int
    built_by: BuiltBy
    keys: dict[str, Any] | None
    registered_at: float
    updated_at: float

    @property
    def pack_name(self) -> str:
        return self.path.name


class Library:
    """sqlite-backed registry of built packs; use as a context manager or call ``close``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_library_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 30000")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Library:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ rows

    @staticmethod
    def _row(row: sqlite3.Row) -> LibraryEntry:
        return LibraryEntry(
            tile=TileRef(int(row["lat"]), int(row["lon"])),
            kind=row["kind"],
            path=Path(row["path"]),
            provider=row["provider"],
            zl=int(row["zl"]),
            built_by=row["built_by"],
            keys=None if row["keys"] is None else json.loads(row["keys"]),
            registered_at=float(row["registered_at"]),
            updated_at=float(row["updated_at"]),
        )

    def register(
        self,
        tile: TileRef,
        provider: str,
        zl: int,
        path: Path,
        built_by: BuiltBy,
        keys: dict[str, Any] | None = None,
        *,
        kind: PackKind = "ortho",
    ) -> LibraryEntry:
        """Insert or update the row of (``tile``, ``kind``, ``path``).

        ``path`` is stored absolute: ``osxp build --out tiles --install`` gave a relative one,
        which ``osxp serve`` then read from its own working directory.

        ``built_by`` is written when the row is created and **never changed afterwards**: who
        built a pack is a fact about the pack, not about what is being done to it now. Installing
        one went through here with ``"osxp"`` whatever the row said, so adding an imported tile to
        X-Plane turned it into a tile OrthoStudio XP claimed to have built -- and Delete, which
        refuses what it did not build, then deleted it where it stood, inside the Ortho4XP folder
        (a user, 2026-09-20).
        """
        now = time.time()
        path_s = str(Path(path).absolute())
        keys_s = None if keys is None else json.dumps(keys, sort_keys=True)
        self._db.execute(
            """
            INSERT INTO tiles (lat, lon, kind, path, provider, zl, built_by, keys,
                               registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (lat, lon, kind, path) DO UPDATE SET
                provider = excluded.provider, zl = excluded.zl,
                keys = excluded.keys, updated_at = excluded.updated_at
            """,
            (tile.lat, tile.lon, kind, path_s, provider, int(zl), built_by, keys_s, now, now),
        )
        row = self._db.execute(
            "SELECT * FROM tiles WHERE lat = ? AND lon = ? AND kind = ? AND path = ?",
            (tile.lat, tile.lon, kind, path_s),
        ).fetchone()
        return self._row(row)

    def list(
        self, *, tile: TileRef | None = None, kind: PackKind | None = None
    ) -> builtins.list[LibraryEntry]:
        """Entries ordered by (lat, lon, kind, path), optionally filtered."""
        clauses: list[str] = []
        args: list[Any] = []
        if tile is not None:
            clauses.append("lat = ? AND lon = ?")
            args.extend((tile.lat, tile.lon))
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM tiles{where} ORDER BY lat, lon, kind, path", args
        ).fetchall()
        return [self._row(r) for r in rows]

    def forget(
        self, tile: TileRef, *, kind: PackKind | None = None, path: Path | None = None
    ) -> int:
        """Delete the rows of ``tile`` (narrowed by ``kind`` and/or ``path``); returns the count."""
        clauses = ["lat = ?", "lon = ?"]
        args: list[Any] = [tile.lat, tile.lon]
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        if path is not None:
            clauses.append("path = ?")
            args.append(str(Path(path)))
        cur = self._db.execute(f"DELETE FROM tiles WHERE {' AND '.join(clauses)}", args)
        return int(cur.rowcount)

    # ------------------------------------------------------------ import Ortho4XP

    def import_ortho4xp(self, folder: Path) -> builtins.list[LibraryEntry]:
        """Register every tile found in an Ortho4XP folder as ``built_by='ortho4xp'``."""
        folder = Path(folder)
        if not folder.is_dir():
            raise OsxpError(
                "SYS_WORKING_DIR_INVALID",
                context={"path": str(folder)},
                message=f"{folder} is not an Ortho4XP directory.",
                remedy="Point at the folder holding Ortho4XP.py (with Tiles/ next to it).",
            )
        out: list[LibraryEntry] = []
        for pack_dir in _ortho4xp_pack_dirs(folder):
            for tile in _dsf_tiles(pack_dir):
                provider, zl = _imported_provider_zl(pack_dir, tile)
                out.append(self.register(tile, provider, zl, pack_dir, "ortho4xp", None))
        overlays = folder / IMPORTED_OVERLAY_PACK
        if overlays.is_dir():
            for tile in _dsf_tiles(overlays):
                out.append(self.register(tile, "", 0, overlays, "ortho4xp", None, kind="overlay"))
        return out


# ---------------------------------------------------------------- Ortho4XP scan helpers


def _ortho4xp_custom_build_dir(folder: Path) -> str:
    """The custom build dir the Ortho4XP GUI remembers (``.last_gui_params.txt`` line 2 or cfg)."""
    params = folder / IMPORT_GUI_PARAMS
    try:
        lines = params.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) >= 2 and lines[1].strip():
            return lines[1].strip()
    except OSError:
        pass
    cfg = folder / IMPORT_GLOBAL_CFG
    try:
        for raw in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = raw.partition("=")
            if sep and key.strip() == "custom_build_dir":
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def _ortho4xp_pack_dirs(folder: Path) -> Iterator[Path]:
    """``zOrtho4XP_*`` directories of ``Tiles/`` and of the custom build dir, deduplicated."""
    roots: list[Path] = [folder / IMPORT_TILES_DIR]
    custom = _ortho4xp_custom_build_dir(folder)
    single: Path | None = None
    if custom:
        if custom.endswith("/"):
            roots.append(Path(custom.rstrip("/")))
        else:
            single = Path(custom)
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name.startswith(IMPORTED_PACK_PREFIX):
                real = child.resolve()
                if real not in seen:
                    seen.add(real)
                    yield child
    if single is not None and single.is_dir() and single.resolve() not in seen:
        yield single


def _dsf_tiles(pack_dir: Path) -> list[TileRef]:
    """Tiles of the DSFs under ``<pack>/Earth nav data/<folder>/<tile>.dsf``."""
    nav = pack_dir / "Earth nav data"
    if not nav.is_dir():
        return []
    tiles: list[TileRef] = []
    for dsf in sorted(nav.glob("*/*.dsf")):
        tile = tile_from_name(dsf.stem)
        if tile is not None:
            tiles.append(tile)
    return tiles


def _imported_provider_zl(pack_dir: Path, tile: TileRef) -> tuple[str, int]:
    """``default_website``/``default_zl`` of the tile cfg, else the majority of the .ter names."""
    try:
        cfg = tile_config(pack_dir, lat=tile.lat, lon=tile.lon)
    except OsxpError:
        cfg = {}
    provider = str(cfg.get("default_website", "") or "")
    zl = int(cfg.get("default_zl", 0) or 0)
    if provider and zl:
        return provider, zl
    try:
        textures = list_textures(pack_dir)
    except OsxpError:
        textures = []
    if textures:
        votes = Counter((t.provider, t.zl) for t, _kinds in textures)
        (best_provider, best_zl), _ = votes.most_common(1)[0]
        return provider or best_provider, zl or best_zl
    if not provider or not zl:
        log.warning("import-ortho4xp: no cfg and no .ter for %s in %s", tile.name, pack_dir)
    return provider, zl
