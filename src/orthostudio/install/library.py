# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
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
import os
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
ORTHO4XP_MAIN = "Ortho4XP.py"
"""What Ortho4XP's own folder holds at its root."""

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
        keep_built_by: bool = False,
    ) -> LibraryEntry:
        """Insert or update the row of (``tile``, ``kind``, ``path``).

        ``path`` is stored absolute: ``osxp build --out tiles --install`` gave a relative one,
        which ``osxp serve`` then read from its own working directory.

        ``keep_built_by`` is for a caller that does not know who built the pack and must not
        guess: installing one came through here with ``"osxp"`` whatever the row said, so adding
        an imported tile to X-Plane turned it into a tile OrthoStudio XP claimed to have built --
        and Delete, which refuses what it did not build, then deleted it where it stood, inside
        the Ortho4XP folder (a user, 2026-09-20). A caller that does know -- a build, an import --
        says so and is believed, so importing a folder again puts a wrong answer right.
        """
        now = time.time()
        path_s = str(Path(path).absolute())
        if keep_built_by:
            was = self._db.execute(
                "SELECT built_by FROM tiles WHERE lat = ? AND lon = ? AND kind = ? AND path = ?",
                (tile.lat, tile.lon, kind, path_s),
            ).fetchone()
            if was is not None:
                built_by = was[0]
        keys_s = None if keys is None else json.dumps(keys, sort_keys=True)
        self._db.execute(
            """
            INSERT INTO tiles (lat, lon, kind, path, provider, zl, built_by, keys,
                               registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (lat, lon, kind, path) DO UPDATE SET
                provider = excluded.provider, zl = excluded.zl, built_by = excluded.built_by,
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


def ortho4xp_searched(folder: Path) -> list[Path]:
    """Where an import looks for tiles: ``Tiles/`` of the folder, the folder itself when it is not
    Ortho4XP's own, and the build folder the Ortho4XP GUI remembers when one is set. For the page
    to say where it looked when it found nothing: a user pressed Import and could not tell what had
    happened (2026-09-21)."""
    roots, singles = _ortho4xp_roots(Path(folder))
    return [*roots, *singles]


OSXP_PACK_FILE = "orthostudio.toml"
"""What tells one of our own packs from someone else's, whatever either is called."""


ORTHO_TEXTURE_RE = re.compile(r"^\d+_\d+_[A-Za-z0-9@]+\d\d\.dds$")
"""One orthophoto's file name: the two tile numbers, the source and the detail level, which is
how Ortho4XP names them (``22224_10544_BI16.dds``) and how OrthoStudio XP does."""


def looks_like_an_ortho_pack(folder: Path) -> bool:
    """Whether this folder is a scenery pack of photo tiles, whatever it is called.

    Ortho4XP names its packs ``zOrtho4XP_<tile>`` and that name was the only thing the import
    knew, so a user's ``/media/Data/X-Plane_Orthos/EUR_EOX_ZL14`` was refused as "not an Ortho4XP
    installation". He was right about the remedy: a pack is known by what it holds, an
    ``Earth nav data`` with DSFs in it and the textures beside it (2026-09-23).

    Our own packs are left alone: they carry an ``orthostudio.toml`` and the library already
    knows them.

    What says "photo tiles" is the **name of the textures**: an orthophoto is
    ``<til_y>_<til_x>_<provider><zl>.dds``, as Ortho4XP writes it and as we do. A DSF and a
    ``textures`` folder say only "scenery pack": a mesh, an airport and a forest library all have
    both. Run over a real Custom Scenery, the rule that asked only for those took a commercial
    forest pack for 37 632 photo tiles, each a lat/lon its owner never built, with no way to undo
    them but deleting the library by hand (found in review, 2026-09-23).
    """
    if (folder / OSXP_PACK_FILE).is_file():
        return False
    textures = folder / "textures"
    if not textures.is_dir():
        return False
    if next((folder / "Earth nav data").glob("*/*.dsf"), None) is None:
        return False
    try:
        with os.scandir(textures) as entries:
            return any(ORTHO_TEXTURE_RE.match(entry.name) for entry in entries)
    except OSError:
        return False


def holds_ortho4xp_tiles(folder: Path) -> bool:
    """Whether an import of ``folder`` finds at least one pack of photo tiles in it."""
    return next(_ortho4xp_pack_dirs(Path(folder)), None) is not None


def _ortho4xp_roots(folder: Path) -> tuple[list[Path], list[Path]]:
    """The folders holding ``zOrtho4XP_*`` tiles, and those that are one tile each.

    Ortho4XP keeps its tiles in ``Tiles/`` and in the build folder its GUI remembers. A folder that
    is not Ortho4XP's own (no ``Ortho4XP.py``) is taken as one the tiles were built into or moved
    to, holding them or being one of them: a user kept his on another disk, ``M:\\XPTilesZL14``,
    and was refused whatever layout he copied around them (2026-09-22).
    """
    roots: list[Path] = [folder / IMPORT_TILES_DIR]
    singles: list[Path] = []
    if not (folder / ORTHO4XP_MAIN).is_file():
        if folder.name.startswith(IMPORTED_PACK_PREFIX) or looks_like_an_ortho_pack(folder):
            singles.append(folder)
        else:
            roots.append(folder)
    custom = _ortho4xp_custom_build_dir(folder)
    if custom:
        if custom.endswith("/"):
            roots.append(Path(custom.rstrip("/")))
        else:
            singles.append(Path(custom))
    return roots, singles


def _ortho4xp_pack_dirs(folder: Path) -> Iterator[Path]:
    """``zOrtho4XP_*`` directories of the folders :func:`_ortho4xp_roots` names, deduplicated."""
    roots, singles = _ortho4xp_roots(folder)
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            # a folder we may not read, or a drive that stopped answering: skipped, as its
            # neighbours already are. It crashed the import with a bare 500 and the page said
            # only "the engine does not answer", which is the very case this feature is for
            # (found in review, 2026-09-23)
            continue
        for child in children:
            if child.is_dir() and (
                child.name.startswith(IMPORTED_PACK_PREFIX) or looks_like_an_ortho_pack(child)
            ):
                real = child.resolve()
                if real not in seen:
                    seen.add(real)
                    yield child
    for single in singles:
        if single.is_dir() and single.resolve() not in seen:
            seen.add(single.resolve())
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
