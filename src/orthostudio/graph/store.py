# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""On-disk artefact store: content-keyed files or directories plus a sqlite (WAL) index.

Layout: ``<root>/<rule>/<key[:2]>/<key>`` is the artefact (a file or a directory, per rule
kind). A build writes into ``<root>/<rule>/<key[:2]>/<key>.tmp-<pid>-<rand>/out`` and commits
with a single ``rename``; the index row is inserted after the rename. See
docs/specs/graph-keys.md for the crash matrix.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orthostudio.fsutil import fsync_dir as _fsync_dir
from orthostudio.fsutil import replace as fs_replace
from orthostudio.graph.digest import check_digest, digest_path
from orthostudio.graph.errors import (
    ArtifactInUseError,
    CommitError,
    IncompatibleIndexError,
    MissingArtifactError,
    StoreError,
)
from orthostudio.graph.keys import parse_recipe
from orthostudio.graph.rule import Kind

__all__ = [
    "SCHEMA_VERSION",
    "ArtifactInfo",
    "Build",
    "FsckReport",
    "GcReport",
    "InputRef",
    "Provenance",
    "Store",
]

log = logging.getLogger("orthostudio.graph.store")

SCHEMA_VERSION = 1
INDEX_FILENAME = "index.sqlite"
TMP_MARKER = ".tmp-"
TMP_HARD_MAX_AGE_S = 86400.0
"""Age after which a ``*.tmp-*`` directory is swept even if its owner pid still exists."""
_OPEN_RETRY_S = 15.0
DEFERRED_RULES = frozenset({"texture.dds", "tile.dsf", "tile.textures"})
"""Rules whose artefacts are committed without forcing each of their files to disk.

A tile at ZL17 is some 700 textures and two folders of 700 ``.ter`` files each: forcing every one
of them as it was written made a hard disk under Windows wait at each, and a user's images crawl
at 0.6 to 0.8 texture a second (reproduced 2026-09-28; 2.4 with nothing forced). They are forced
as a group instead, by :meth:`Store.make_durable`, before a tile is handed to X-Plane
(``pipeline/pack.py``); :meth:`Store.recover` checks, when a build starts, the ones no durable
point reached, which a power cut may have left torn; a commit finding one of them on disk
without its row keeps it only when it is the very artefact it has just built. The artefacts of
other rules are few and forced as before."""
DURABLE_FILE = "durable.json"
"""The durable point, in the store's root: every artefact of :data:`DEFERRED_RULES` indexed
before ``until`` has its files on the disk."""
DURABLE_MARGIN_S = 30.0
"""How far behind the moment it asked the index a durable point stays: an artefact is indexed a
few milliseconds after its ``created_at``, and a clock set back a little must not hide one."""
INDEX_LOCKED_PATIENCE_S = 300.0
"""How long a write asks again for the index while another process holds it, before it fails.

SQLite's own wait (``busy_timeout``) gives up after 30 s. On a hard disk, the forced writes of a
build held the index longer than that: 12 textures of 695 failed "database is locked", and their
tile with them (2026-09-28, the hard disk of a user's slow builds reproduced). Well below the
600 s a texture may take (``pipeline/textures.ENCODE_TIMEOUT_S``)."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    key          TEXT PRIMARY KEY,
    rule         TEXT NOT NULL,
    version      INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('file', 'dir')),
    digest       TEXT NOT NULL,
    size         INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    last_used_at REAL NOT NULL,
    uses         INTEGER NOT NULL DEFAULT 1,
    recipe       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_rule ON artifacts (rule);
CREATE INDEX IF NOT EXISTS artifacts_rule_digest ON artifacts (rule, digest);
CREATE INDEX IF NOT EXISTS artifacts_lru ON artifacts (last_used_at);
CREATE TABLE IF NOT EXISTS edges (
    child      TEXT NOT NULL REFERENCES artifacts (key) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    digest     TEXT,
    parent_key TEXT REFERENCES artifacts (key) ON DELETE SET NULL,
    PRIMARY KEY (child, name)
);
CREATE INDEX IF NOT EXISTS edges_parent ON edges (parent_key);
CREATE TABLE IF NOT EXISTS pins (
    name      TEXT PRIMARY KEY,
    key       TEXT NOT NULL REFERENCES artifacts (key) ON DELETE CASCADE,
    pinned_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pins_key ON pins (key);
"""


@dataclass(frozen=True, slots=True)
class ArtifactInfo:
    """Index row of a stored artefact."""

    key: str
    rule: str
    version: int
    kind: Kind
    digest: str
    size: int
    created_at: float
    last_used_at: float
    uses: int
    path: Path


@dataclass(frozen=True, slots=True)
class InputRef:
    """One named input as recorded in the index: its digest and, if an artefact, its key."""

    name: str
    digest: str | None
    key: str | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    """Answer of :meth:`Store.why`: where a key comes from and who depends on it."""

    info: ArtifactInfo
    params: dict[str, Any]
    inputs: tuple[InputRef, ...]
    recipe: str
    pins: tuple[str, ...]
    dependents: tuple[tuple[str, str], ...]

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def refcount(self) -> int:
        return len(self.pins) + len(self.dependents)


@dataclass(frozen=True, slots=True)
class GcReport:
    deleted: tuple[str, ...]
    freed_bytes: int
    size_before: int
    size_after: int


@dataclass(frozen=True, slots=True)
class FsckReport:
    rows_without_files: tuple[str, ...]
    files_without_rows: tuple[Path, ...]
    tmp_leftovers: tuple[Path, ...]
    digest_mismatches: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (
            self.rows_without_files
            or self.files_without_rows
            or self.tmp_leftovers
            or self.digest_mismatches
        )


@dataclass(frozen=True, slots=True)
class RecoverReport:
    """What :meth:`Store.recover` found: the artefacts it checked and the torn ones it dropped."""

    checked: int
    dropped: tuple[str, ...]


class Build:
    """An in-progress artefact: write into :attr:`out`, then :meth:`commit` or :meth:`abort`."""

    def __init__(self, store: Store, rule: str, key: str, kind: Kind) -> None:
        self._store = store
        self.rule = rule
        self.key = check_digest(key, "key")
        self.kind: Kind = kind
        shard = store.root / rule / key[:2]
        shard.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{secrets.token_hex(4)}"
        self.tmp_dir = shard / f"{key}{TMP_MARKER}{token}"
        self.tmp_dir.mkdir()
        self.out = self.tmp_dir / "out"
        if kind == "dir":
            self.out.mkdir()
        self.scratch = self.tmp_dir / "scratch"
        self.scratch.mkdir()
        self._done = False

    def __enter__(self) -> Build:
        return self

    def __exit__(self, *exc: object) -> None:
        if not self._done:
            self.abort()

    def abort(self) -> None:
        """Drop the temporary directory; nothing reaches the index."""
        self._done = True
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def commit(
        self, *, version: int, recipe: str, inputs: Iterable[InputRef]
    ) -> tuple[ArtifactInfo, bool]:
        """Rename ``out`` into place and index it.

        Returns ``(info, adopted)``; ``adopted`` is True when an artefact with this key was
        already on disk (another process won, or a crash left a renamed artefact without
        its row): our output is discarded and the on-disk one is indexed instead.
        """
        if self._done:
            raise StoreError("build already committed or aborted")
        out = self.out
        if out.is_symlink() or (self.kind == "file" and not out.is_file()):
            raise CommitError(f"rule {self.rule!r} left no regular file at {out}")
        if self.kind == "dir" and not out.is_dir():
            raise CommitError(f"rule {self.rule!r} left no directory at {out}")
        try:
            forced = self._store.fsync and self.rule not in DEFERRED_RULES
            if forced:
                _fsync_tree(out)
            digest, size = digest_path(out)
            final = self._store.artifact_path(self.rule, self.key)
            adopted = final.exists()
            if not adopted:
                try:
                    os.rename(out, final)
                except OSError:
                    if not final.exists():
                        raise
                    adopted = True
            if adopted:
                found = digest_path(final)
                if found[0] != digest and self.rule in DEFERRED_RULES:
                    # Not forced to disk, the one found may be what a power cut left of it
                    # (renamed, never indexed): ours is whole, and it takes the name.
                    if _replace(out, final):
                        adopted = False
                    else:
                        found = digest_path(final)  # another process put its own there first
                if adopted:
                    digest, size = found
            if forced:
                _fsync_dir(final.parent)
            info = self._store._index(
                key=self.key,
                rule=self.rule,
                version=version,
                kind=self.kind,
                digest=digest,
                size=size,
                recipe=recipe,
                inputs=list(inputs),
            )
        finally:
            self._done = True
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        return info, adopted


class Store:
    """Artefact store rooted at ``root``. Safe for use from several threads and processes."""

    def __init__(
        self,
        root: str | Path,
        *,
        fsync: bool = True,
        tmp_max_age_s: float = 3600.0,
        tmp_hard_max_age_s: float = TMP_HARD_MAX_AGE_S,
    ):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self._lock = threading.RLock()
        self._durable_lock = threading.Lock()
        self._db = self._open_index()
        if self.durable_until() is None:
            # a new store, or one every earlier version forced file by file: all durable
            self._write_durable(time.time())
        self.sweep_tmp(tmp_max_age_s, hard_max_age_s=tmp_hard_max_age_s)

    # -- lifecycle -----------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _open_index(self) -> sqlite3.Connection:
        """Connect, switch to WAL and create the schema.

        Several processes creating the same index at the same instant can see the WAL switch
        or the first schema write fail with "database is locked" without the busy handler
        being consulted; the whole opening is retried for up to ``_OPEN_RETRY_S``.
        """
        deadline = time.monotonic() + _OPEN_RETRY_S
        delay = 0.01
        while True:
            db = sqlite3.connect(
                self.root / INDEX_FILENAME,
                timeout=30.0,
                isolation_level=None,
                check_same_thread=False,
            )
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA busy_timeout = 30000")
                db.execute("PRAGMA journal_mode = WAL")
                db.execute("PRAGMA synchronous = NORMAL")
                db.execute("PRAGMA foreign_keys = ON")
                self._init_schema(db)
                return db
            except sqlite3.OperationalError as exc:
                db.close()
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                if time.monotonic() > deadline:
                    raise
                log.debug("index at %s busy while opening (%s), retrying", self.root, exc)
                time.sleep(delay)
                delay = min(delay * 2, 0.25)

    def _init_schema(self, db: sqlite3.Connection) -> None:
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            row = db.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO meta (k, v) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row["v"]) != SCHEMA_VERSION:
                raise IncompatibleIndexError(
                    f"index schema {row['v']} at {self.root}, this OrthoStudio XP expects "
                    f"{SCHEMA_VERSION}"
                )
        except BaseException:
            db.execute("ROLLBACK")
            raise
        else:
            db.execute("COMMIT")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._begin_immediate()
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def _begin_immediate(self) -> None:
        """``BEGIN IMMEDIATE``, asked again while another process holds the index, for up to
        :data:`INDEX_LOCKED_PATIENCE_S`: each ask already waits ``busy_timeout`` (30 s)."""
        deadline = time.monotonic() + INDEX_LOCKED_PATIENCE_S
        while True:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                if not _index_busy(exc) or time.monotonic() >= deadline:
                    raise
                log.warning("index at %s held elsewhere (%s), asking again", self.root, exc)

    # -- durability ----------------------------------------------------------------------

    def durable_until(self) -> float | None:
        """The durable point (:data:`DURABLE_FILE`): every artefact of :data:`DEFERRED_RULES`
        indexed before it has its files on the disk. ``None`` when unreadable."""
        try:
            data = json.loads((self.root / DURABLE_FILE).read_text(encoding="utf-8"))
            return float(data["until"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_durable(self, until: float) -> None:
        """Write the durable point after the one flush of the disk's own cache a group needs:
        it covers every file :func:`_flush_data` sent to the disk before, and comes before the
        point can reach the disk itself."""
        path = self.root / DURABLE_FILE
        tmp = path.with_name(f"{path.name}{TMP_MARKER}{os.getpid()}-{secrets.token_hex(4)}")
        with contextlib.suppress(OSError):  # a store on a disk that cannot be written
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps({"until": until}) + "\n")
                    f.flush()
                    _flush_disk(f.fileno())
                fs_replace(tmp, path)  # asked again on Windows while a reader holds the point
            finally:
                tmp.unlink(missing_ok=True)
            _fsync_dir(self.root)

    def _deferred_since(self, since: float | None) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(DEFERRED_RULES))
        with self._lock:
            return self._db.execute(
                f"SELECT key, rule, kind, digest FROM artifacts "
                f"WHERE rule IN ({marks}) AND created_at > ? ORDER BY created_at",
                (*sorted(DEFERRED_RULES), since if since is not None else float("-inf")),
            ).fetchall()

    def make_durable(self) -> int:
        """Force to disk the artefacts of :data:`DEFERRED_RULES` indexed since the durable
        point, then move the point up to now (less :data:`DURABLE_MARGIN_S`); returns how many
        were forced. Files the system has already written cost it little: this runs once a tile,
        where forcing each file as it was written held the whole build. A file it cannot send to
        the disk raises ``PermissionError``, and the point stays where it was."""
        with self._durable_lock:
            since = self.durable_until()
            asked = time.time()
            rows = self._deferred_since(since)
            shards: set[Path] = set()
            for row in rows:
                path = self.artifact_path(str(row["rule"]), str(row["key"]))
                try:
                    if row["kind"] == "file":
                        _flush_data(path)
                    else:
                        _flush_tree_data(path)
                except FileNotFoundError:
                    continue  # deleted since: nothing to keep
                shards.add(path.parent)
            for shard in shards:
                _flush_dir_data(shard)
            with self._lock, contextlib.suppress(sqlite3.Error):
                self._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self._write_durable(max(since or 0.0, asked - DURABLE_MARGIN_S))
            return len(rows)

    def recover(self) -> RecoverReport:
        """When a build starts: check the artefacts of :data:`DEFERRED_RULES` indexed since the
        durable point against their digest, drop the torn ones (a power cut, a crash of the
        system or a disk unplugged before a durable point), and make the rest durable.

        After a build that ended normally there is next to nothing to check. A build still
        running in another process has its fresh artefacts checked too, which reads them and
        finds them whole."""
        rows = self._deferred_since(self.durable_until())
        dropped: list[str] = []
        for row in rows:
            key = str(row["key"])
            path = self.artifact_path(str(row["rule"]), key)
            if not path.exists():
                continue  # the row alone: has() drops it the next time it is asked
            try:
                whole = digest_path(path)[0] == row["digest"]
            except OSError:
                whole = False
            if not whole:
                log.warning("artefact %s was not whole on disk; dropped", key[:16])
                self.delete(key, force=True)
                dropped.append(key)
        self.make_durable()
        return RecoverReport(checked=len(rows), dropped=tuple(dropped))

    # -- paths ---------------------------------------------------------------------------

    def artifact_path(self, rule: str, key: str) -> Path:
        """Where the artefact of ``rule`` with ``key`` lives (whether or not it exists)."""
        return self.root / rule / key[:2] / key

    # -- reads ---------------------------------------------------------------------------

    def has(self, key: str) -> bool:
        """True if the key is indexed and its files are present; heals a row without files."""
        with self._lock:
            row = self._db.execute("SELECT rule FROM artifacts WHERE key = ?", (key,)).fetchone()
            if row is None:
                return False
            if self.artifact_path(row["rule"], key).exists():
                return True
            log.warning("artefact %s indexed but missing on disk; dropping its row", key[:16])
            with self._tx():
                self._db.execute("DELETE FROM artifacts WHERE key = ?", (key,))
            return False

    def info(self, key: str) -> ArtifactInfo | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM artifacts WHERE key = ?", (key,)).fetchone()
        return None if row is None else self._row_info(row)

    def path(self, key: str) -> Path:
        """Path of a present artefact, or raise :class:`MissingArtifactError`."""
        if not self.has(key):
            raise MissingArtifactError(key)
        info = self.info(key)
        assert info is not None
        return info.path

    def digest_of(self, key: str) -> str | None:
        """Content digest of a present artefact, None when absent."""
        if not self.has(key):
            return None
        row = self._db.execute("SELECT digest FROM artifacts WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["digest"])

    def keys_with_digest(self, rule: str, digest: str) -> list[str]:
        """Keys of ``rule`` whose output bytes are ``digest`` (early-cutoff witnesses)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT key FROM artifacts WHERE rule = ? AND digest = ? ORDER BY created_at",
                (rule, digest),
            ).fetchall()
        return [str(r["key"]) for r in rows]

    def iter_artifacts(self, rule: str | None = None) -> Iterator[ArtifactInfo]:
        with self._lock:
            if rule is None:
                rows = self._db.execute("SELECT * FROM artifacts ORDER BY created_at").fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM artifacts WHERE rule = ? ORDER BY created_at", (rule,)
                ).fetchall()
        for row in rows:
            yield self._row_info(row)

    def total_size(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(size), 0) AS s FROM artifacts").fetchone()
        return int(row["s"])

    def __len__(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"])

    # -- writes --------------------------------------------------------------------------

    def begin(self, rule: str, key: str, kind: Kind) -> Build:
        """Start building the artefact ``key`` of ``rule``; use as a context manager."""
        return Build(self, rule, key, kind)

    def touch(self, key: str, inputs: Iterable[InputRef] | None = None) -> None:
        """Record a use of ``key`` (LRU) and, if given, re-point its input edges."""
        with self._tx():
            cur = self._db.execute(
                "UPDATE artifacts SET last_used_at = ?, uses = uses + 1 WHERE key = ?",
                (time.time(), key),
            )
            if cur.rowcount == 0:
                raise MissingArtifactError(key)
            if inputs is not None:
                self._write_edges(key, list(inputs))

    def _index(
        self,
        *,
        key: str,
        rule: str,
        version: int,
        kind: Kind,
        digest: str,
        size: int,
        recipe: str,
        inputs: list[InputRef],
    ) -> ArtifactInfo:
        with self._tx():
            # taken once the index is ours: a durable point asks for rows by this time, and a
            # write that waited for the index must not look older than rows indexed meanwhile
            now = time.time()
            self._db.execute(
                """
                INSERT INTO artifacts
                    (key, rule, version, kind, digest, size, created_at, last_used_at, uses, recipe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (key) DO UPDATE SET
                    version = excluded.version, kind = excluded.kind, digest = excluded.digest,
                    size = excluded.size, recipe = excluded.recipe,
                    last_used_at = excluded.last_used_at, uses = artifacts.uses + 1
                """,
                (key, rule, version, kind, digest, size, now, now, recipe),
            )
            self._write_edges(key, inputs)
        info = self.info(key)
        assert info is not None
        return info

    def _write_edges(self, child: str, inputs: list[InputRef]) -> None:
        self._db.execute("DELETE FROM edges WHERE child = ?", (child,))
        self._db.executemany(
            "INSERT INTO edges (child, name, digest, parent_key) VALUES (?, ?, ?, ?)",
            [(child, i.name, i.digest, i.key) for i in inputs],
        )

    # -- pins and references -------------------------------------------------------------

    def pin(self, name: str, key: str) -> None:
        """Name a root the collector must keep (with everything it transitively uses)."""
        if not self.has(key):
            raise MissingArtifactError(key)
        with self._tx():
            self._db.execute(
                "INSERT OR REPLACE INTO pins (name, key, pinned_at) VALUES (?, ?, ?)",
                (name, key, time.time()),
            )

    def unpin(self, name: str) -> bool:
        with self._tx():
            cur = self._db.execute("DELETE FROM pins WHERE name = ?", (name,))
        return cur.rowcount > 0

    def pins(self, key: str | None = None) -> dict[str, str]:
        """Pinned roots as ``{name: key}``, optionally only those on ``key``."""
        with self._lock:
            if key is None:
                rows = self._db.execute("SELECT name, key FROM pins ORDER BY name").fetchall()
            else:
                rows = self._db.execute(
                    "SELECT name, key FROM pins WHERE key = ? ORDER BY name", (key,)
                ).fetchall()
        return {str(r["name"]): str(r["key"]) for r in rows}

    def reachable(self, keys: Iterable[str]) -> set[str]:
        """The ``keys`` that are stored, and every stored artefact they were built from.

        Follows the recorded inputs (``edges.parent_key``) transitively; a source input or an
        absent one has no key and ends the walk. What ``osxp clean`` keeps.
        """
        with self._lock:
            known = {str(r["key"]) for r in self._db.execute("SELECT key FROM artifacts")}
            rows = self._db.execute(
                "SELECT child, parent_key FROM edges WHERE parent_key IS NOT NULL"
            ).fetchall()
        inputs: dict[str, list[str]] = {}
        for row in rows:
            inputs.setdefault(str(row["child"]), []).append(str(row["parent_key"]))
        seen: set[str] = set()
        stack = [k for k in keys if k in known]
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            stack.extend(p for p in inputs.get(key, ()) if p in known and p not in seen)
        return seen

    def refcount(self, key: str) -> int:
        """Pins on ``key`` plus artefacts that list it as an input."""
        with self._lock:
            row = self._db.execute(
                """
                SELECT (SELECT COUNT(*) FROM pins WHERE key = ?)
                     + (SELECT COUNT(*) FROM edges WHERE parent_key = ?) AS n
                """,
                (key, key),
            ).fetchone()
        return int(row["n"])

    def dependents(self, key: str) -> list[tuple[str, str]]:
        """``(child_key, input_name)`` of every artefact built from ``key``."""
        with self._lock:
            rows = self._db.execute(
                "SELECT child, name FROM edges WHERE parent_key = ? ORDER BY child, name", (key,)
            ).fetchall()
        return [(str(r["child"]), str(r["name"])) for r in rows]

    # -- deletion and collection ---------------------------------------------------------

    def delete(self, key: str, *, force: bool = False) -> bool:
        """Remove an artefact. Refuses (ArtifactInUseError) if referenced, unless ``force``."""
        with self._lock:
            info = self.info(key)
            if info is None:
                return False
            if not force and self.refcount(key) > 0:
                raise ArtifactInUseError(f"{key[:16]} is pinned or used by another artefact")
            doomed = _move_aside(info.path)
            with self._tx():
                self._db.execute("DELETE FROM artifacts WHERE key = ?", (key,))
        _remove_path(doomed)
        return True

    def gc(self, *, quota_bytes: int | None = None, min_age_s: float = 0.0) -> GcReport:
        """Collect unreferenced artefacts, least recently used first.

        Without a quota every unreferenced artefact goes (cascading: freeing a child may free
        its parents). With a quota, collection stops as soon as the total size fits.
        Artefacts younger than ``min_age_s`` are never collected.
        """
        size_before = self.total_size()
        deleted: list[str] = []
        freed = 0
        total = size_before
        while True:
            if quota_bytes is not None and total <= quota_bytes:
                break
            with self._lock:
                rows = self._db.execute(
                    """
                    SELECT a.key, a.rule, a.size FROM artifacts a
                    WHERE a.created_at <= ?
                      AND NOT EXISTS (SELECT 1 FROM pins p WHERE p.key = a.key)
                      AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.parent_key = a.key)
                    ORDER BY a.last_used_at ASC, a.created_at ASC
                    """,
                    (time.time() - min_age_s,),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                if quota_bytes is not None and total <= quota_bytes:
                    break
                key, rule, size = str(row["key"]), str(row["rule"]), int(row["size"])
                doomed = _move_aside(self.artifact_path(rule, key))
                with self._tx():
                    cur = self._db.execute("DELETE FROM artifacts WHERE key = ?", (key,))
                if cur.rowcount:
                    _remove_path(doomed)
                    deleted.append(key)
                    freed += size
                    total -= size
        return GcReport(tuple(deleted), freed, size_before, self.total_size())

    def sweep_tmp(
        self, max_age_s: float = 0.0, *, hard_max_age_s: float | None = TMP_HARD_MAX_AGE_S
    ) -> list[Path]:
        """Remove abandoned ``*.tmp-*`` build directories.

        A directory is abandoned when it is older than ``max_age_s`` **and** the process that
        created it (the pid in ``<key>.tmp-<pid>-<rand>``) is gone, or when it is older than
        ``hard_max_age_s`` whatever its owner (a pid reused by an unrelated process must not
        pin it for ever; ``None`` disables the cap). A live build of another process, however
        long it runs, is never swept (P2a review: a stage or a textures node of more than an
        hour lost its output to any other process opening the store).
        """
        removed: list[Path] = []
        now = time.time()
        cutoff = now - max_age_s
        hard_cutoff = None if hard_max_age_s is None else now - hard_max_age_s
        for p in self._iter_tmp():
            try:
                mtime = p.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime > cutoff:
                continue
            owner = _tmp_owner_pid(p.name)
            dead = owner is not None and not _pid_alive(owner)
            expired = hard_cutoff is not None and mtime <= hard_cutoff
            if dead or expired:
                shutil.rmtree(p, ignore_errors=True)
                removed.append(p)
        return removed

    def fsck(self, *, repair: bool = False, verify: bool = False) -> FsckReport:
        """Compare index and disk; with ``repair`` drop dead rows, orphan files and tmp dirs."""
        rows_without: list[str] = []
        files_without: list[Path] = []
        mismatches: list[str] = []
        with self._lock:
            rows = self._db.execute("SELECT key, rule, digest FROM artifacts").fetchall()
        indexed: dict[Path, tuple[str, str]] = {}
        for row in rows:
            key, rule, digest = str(row["key"]), str(row["rule"]), str(row["digest"])
            p = self.artifact_path(rule, key)
            if not p.exists():
                rows_without.append(key)
            else:
                indexed[p] = (key, digest)
                if verify and digest_path(p)[0] != digest:
                    mismatches.append(key)
        for p in self._iter_finals():
            if p not in indexed:
                files_without.append(p)
        tmps = list(self._iter_tmp())
        if repair:
            with self._tx():
                self._db.executemany(
                    "DELETE FROM artifacts WHERE key = ?", [(k,) for k in rows_without]
                )
            for p in files_without:
                _remove_path(p)
            for p in tmps:
                shutil.rmtree(p, ignore_errors=True)
            for key in mismatches:
                self.delete(key, force=True)
        return FsckReport(tuple(rows_without), tuple(files_without), tuple(tmps), tuple(mismatches))

    # -- provenance ----------------------------------------------------------------------

    def why(self, key: str) -> Provenance:
        """Explain where ``key`` comes from: rule, frozen params, inputs, pins, dependents."""
        info = self.info(key)
        if info is None:
            raise MissingArtifactError(key)
        with self._lock:
            edge_rows = self._db.execute(
                "SELECT name, digest, parent_key FROM edges WHERE child = ? ORDER BY name", (key,)
            ).fetchall()
        doc = parse_recipe(info_recipe(self, key))
        by_name = {str(r["name"]): r for r in edge_rows}
        inputs = tuple(
            InputRef(
                name,
                digest,
                None if name not in by_name else _opt_str(by_name[name]["parent_key"]),
            )
            for name, digest in sorted(doc["inputs"].items())
        )
        return Provenance(
            info=info,
            params=dict(doc["params"]),
            inputs=inputs,
            recipe=json.dumps(doc, sort_keys=True, separators=(",", ":")),
            pins=tuple(sorted(self.pins(key))),
            dependents=tuple(self.dependents(key)),
        )

    def explain(self, key: str, *, depth: int = 0, _indent: str = "") -> str:
        """Human-readable :meth:`why`, recursing ``depth`` levels into artefact inputs."""
        p = self.why(key)
        i = p.info
        head = (
            f"{_indent}{i.rule}@{i.version}  key {_short(i.key)}  digest {_short(i.digest)}  "
            f"{_human(i.size)}  {i.kind}  built {_iso(i.created_at)}  used {i.uses}x"
        )
        if p.pins:
            head += "  pins: " + ", ".join(p.pins)
        lines = [head, f"{_indent}  params  {json.dumps(p.params, sort_keys=True)}"]
        if p.inputs:
            lines.append(f"{_indent}  inputs")
            width = max(len(x.name) for x in p.inputs)
            for ref in p.inputs:
                if ref.digest is None:
                    lines.append(f"{_indent}    {ref.name:<{width}}  <- absent")
                elif ref.key is None:
                    lines.append(
                        f"{_indent}    {ref.name:<{width}}  <- source {_short(ref.digest)}"
                    )
                else:
                    ci = self.info(ref.key)
                    label = f"{ci.rule}@{ci.version} " if ci else ""
                    lines.append(
                        f"{_indent}    {ref.name:<{width}}  <- {label}key {_short(ref.key)}  "
                        f"digest {_short(ref.digest)}"
                    )
                    if depth > 0 and ci is not None:
                        lines.append(
                            self.explain(ref.key, depth=depth - 1, _indent=_indent + "      ")
                        )
        if p.dependents:
            deps = ", ".join(f"{_short(c)} (as {n!r})" for c, n in p.dependents)
            lines.append(f"{_indent}  dependents  {deps}")
        return "\n".join(lines)

    # -- internals -----------------------------------------------------------------------

    def _row_info(self, row: sqlite3.Row) -> ArtifactInfo:
        return ArtifactInfo(
            key=str(row["key"]),
            rule=str(row["rule"]),
            version=int(row["version"]),
            kind=row["kind"],
            digest=str(row["digest"]),
            size=int(row["size"]),
            created_at=float(row["created_at"]),
            last_used_at=float(row["last_used_at"]),
            uses=int(row["uses"]),
            path=self.artifact_path(str(row["rule"]), str(row["key"])),
        )

    def _iter_shards(self) -> Iterator[Path]:
        for rule_dir in sorted(self.root.iterdir()):
            if not rule_dir.is_dir() or rule_dir.name.startswith("."):
                continue
            for shard in sorted(rule_dir.iterdir()):
                if shard.is_dir() and len(shard.name) == 2:
                    yield shard

    def building_pids(self) -> set[int]:
        """The other live processes building into this store right now.

        Read from the ``<key>.tmp-<pid>-<rand>`` directories of builds in progress, the same
        evidence :meth:`sweep_tmp` trusts; this process is left out. ``osxp clean --all`` refuses
        to run while the set is not empty: it collects even what was used a second ago.
        """
        pids: set[int] = set()
        for p in self._iter_tmp():
            owner = _tmp_owner_pid(p.name)
            if owner is not None and owner != os.getpid() and _pid_alive(owner):
                pids.add(owner)
        return pids

    def _iter_tmp(self) -> Iterator[Path]:
        for shard in self._iter_shards():
            for p in shard.iterdir():
                if TMP_MARKER in p.name and p.is_dir():
                    yield p

    def _iter_finals(self) -> Iterator[Path]:
        for shard in self._iter_shards():
            for p in shard.iterdir():
                if len(p.name) == 64 and TMP_MARKER not in p.name:
                    yield p


def info_recipe(store: Store, key: str) -> str:
    """Stored canonical recipe of ``key``."""
    with store._lock:
        row = store._db.execute("SELECT recipe FROM artifacts WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise MissingArtifactError(key)
    return str(row["recipe"])


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)


def _short(hexdigest: str) -> str:
    return hexdigest[:12]


def _human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if x < 1024 or unit == "GiB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GiB"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _move_aside(p: Path) -> Path:
    """Rename an artefact out of the way before it is removed, and say where it went.

    Removing the files takes many system calls, and anything can stop it: a second Ctrl-C, the
    app quitting while Free space runs, one ``.dds`` held open by X-Plane or an antivirus. The
    row went first, so what was left behind was a **half-emptied artefact at the name a finished
    one has**, and the next build of that tile adopted it: it threw away what it had just built
    correctly, indexed the remains, and said it had succeeded. A pack short of its textures is
    grey ground in X-Plane, and the key is a hit for ever, so building it again never mends it
    (found in review, 2026-09-23).

    Renamed first, an interruption leaves a ``*.tmp-*`` directory, which :meth:`sweep_tmp`
    already knows how to clear, and never a partial artefact. The other order is harmless: a row
    whose files are gone is dropped by :meth:`has` the next time it is looked up.
    """
    aside = p.with_name(f"{p.name}{TMP_MARKER}{os.getpid()}-{secrets.token_hex(4)}")
    try:
        os.rename(p, aside)
    except OSError:
        return p  # not there, or the file system will not have it: remove it where it lies
    return aside


def _replace(out: Path, final: Path) -> bool:
    """Put ``out`` at ``final`` in place of what is there. False when another process put its own
    there in between, which is kept: it was just written, and is whole."""
    doomed = _move_aside(final)
    if doomed == final and final.exists():
        # what may be torn cannot be moved (a file held open): refuse rather than adopt it
        raise StoreError(f"{final} is in the way and cannot be moved aside")
    try:
        os.rename(out, final)
    except OSError:
        if doomed != final:
            _remove_path(doomed)
        if not final.exists():
            raise
        return False
    if doomed != final:
        _remove_path(doomed)
    return True


def _remove_path(p: Path) -> None:
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    except FileNotFoundError:
        pass


def _fsync_file(p: Path) -> None:
    # Windows flushes a handle opened for writing only (``FlushFileBuffers``): ``os.fsync`` on a
    # read-only one fails with EBADF, and every commit of the store failed with it.
    if os.name != "nt":
        fd = os.open(p, os.O_RDONLY)
    else:
        try:
            fd = os.open(p, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except PermissionError:
            return  # a read-only file: it was flushed when it was written
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(p: Path) -> None:
    if p.is_file():
        _fsync_file(p)
        return
    for dirpath, _dirs, files in os.walk(p):
        for name in files:
            f = Path(dirpath) / name
            if f.is_file() and not f.is_symlink() and not _linked_texture(f):
                _fsync_file(f)


def _linked_texture(f: Path) -> bool:
    """A DDS this artefact holds as a hard link to a ``texture.dds`` artefact
    (``pipeline/textures.publish_file``), whose data was forced to disk when that artefact was
    committed: forcing it again protects nothing. A tile's textures folder forced every one of
    them a second time, and on a hard disk under Windows each such flush waits for the disk
    (2026-09-28). A copy (``link=False``) has one name only, and is forced as any file."""
    if f.suffix.lower() != ".dds":
        return False
    try:
        return f.stat().st_nlink > 1
    except OSError:
        return False


def _flush_data(p: Path) -> None:
    """Send a file's bytes to the disk, leaving the disk's own cache to the one flush that ends
    a durable point (:func:`_flush_disk`, in :meth:`Store._write_durable`).

    ``FlushFileBuffers`` empties that cache at every file, and on a hard disk busy with the next
    tile's images each such flush waited for all of them: a tile's group took 8 and 15 minutes
    in a test (2026-09-28). ``NtFlushBuffersFileEx`` with ``FLUSH_FLAGS_NO_SYNC`` (Windows 8 and
    later, NTFS) writes the bytes only, ``FlushFileBuffers`` when it is missing. On a Mac,
    ``fsync`` already leaves the disk's cache alone."""
    if os.name == "nt":
        fd = _open_for_flush(p)
        try:
            if not _nt_flush_no_sync(fd):
                os.fsync(fd)
        finally:
            os.close(fd)
        return
    fd = os.open(p, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


FLUSH_OPEN_ATTEMPTS = 10
"""How many times :func:`_open_for_flush` asks for a file another process holds (2.75 s)."""


def _open_for_flush(p: Path) -> int:
    """A handle Windows can flush: it flushes those opened for writing only. A file another
    process holds without letting others write (a scanner, X-Plane) is asked again for an
    instant, then refused with ``PermissionError``: a durable point must never vouch for a file
    it could not send to the disk, where a commit that forced its files could say so of one."""
    for attempt in range(FLUSH_OPEN_ATTEMPTS):
        try:
            return os.open(p, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except PermissionError:
            if attempt == FLUSH_OPEN_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def _flush_dir_data(d: Path) -> None:
    """A folder's entries sent to the disk, as :func:`_flush_data` (nothing to do on Windows)."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _flush_tree_data(p: Path) -> None:
    for dirpath, _dirs, files in os.walk(p):
        for name in files:
            f = Path(dirpath) / name
            if f.is_file() and not f.is_symlink() and not _linked_texture(f):
                _flush_data(f)


def _flush_disk(fd: int) -> None:
    """Flush a file and the disk's own cache with it, everything sent to it before included:
    ``FlushFileBuffers`` on Windows, ``F_FULLFSYNC`` on a Mac, ``fsync`` elsewhere."""
    if sys.platform == "darwin":
        import fcntl

        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass  # a file system without it: fsync is what it has
    os.fsync(fd)


_FLUSH_FLAGS_NO_SYNC = 0x00000002
_NT_FLUSH: Any = None
"""``ntdll.NtFlushBuffersFileEx`` once looked up, ``False`` where it is missing."""


def _nt_flush_no_sync(fd: int) -> bool:
    """``NtFlushBuffersFileEx(FLUSH_FLAGS_NO_SYNC)``: False when it is missing or refuses (a file
    system other than NTFS), and the caller flushes the usual way."""
    global _NT_FLUSH
    import ctypes

    if _NT_FLUSH is None:
        try:
            from ctypes import wintypes

            fn = ctypes.WinDLL("ntdll").NtFlushBuffersFileEx  # type: ignore[attr-defined]
            fn.argtypes = [
                wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p
            ]  # fmt: skip
            fn.restype = ctypes.c_long
            _NT_FLUSH = fn
        except (OSError, AttributeError, ImportError):
            _NT_FLUSH = False
    if not _NT_FLUSH:
        return False
    import msvcrt

    status_block = (ctypes.c_size_t * 2)()  # IO_STATUS_BLOCK: the status (or a pointer), a size
    try:
        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
        status = _NT_FLUSH(handle, _FLUSH_FLAGS_NO_SYNC, None, 0, ctypes.byref(status_block))
    except OSError:
        return False
    return int(status) == 0


def _index_busy(exc: sqlite3.OperationalError) -> bool:
    """Whether SQLite gave up waiting for the index, rather than failing for another reason."""
    text = str(exc)
    return "locked" in text or "busy" in text


def _tmp_owner_pid(name: str) -> int | None:
    """The pid recorded in a ``<key>.tmp-<pid>-<rand>`` directory name, or ``None``."""
    _, _, token = name.partition(TMP_MARKER)
    pid_text, _, _ = token.partition("-")
    return int(pid_text) if pid_text.isdigit() else None


def _pid_alive(pid: int) -> bool:
    """True when a process with ``pid`` exists (unknown counts as alive: never sweep blindly)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, OSError):
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """``OpenProcess`` + ``GetExitCodeProcess`` (``os.kill(pid, 0)`` would *terminate* it)."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # ERROR_ACCESS_DENIED: exists, not ours
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # liveness unknown: keep the directory
        return True
