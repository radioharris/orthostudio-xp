# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Downloaded imagery pieces, kept in one SQLite file rather than as millions of small files.

A piece is ``(provider, zoom, x, y)``: a 256 x 256 JPEG, or the provider's word that it has no
imagery there (kept too, so that the sea is not asked for again at every build).

The file has a rollback journal, not a write-ahead log: in the WAL every piece was written twice,
2.5 times its bytes in all, 1.4 with the journal (measured, 2026-09-28, pieces as Live asks for
them); new pages are not journaled, and a crash still leaves the file whole.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

IMAGE = 1
NO_IMAGERY = 2

Key = tuple[int, int, int]  # zoom, x, y


class ChunkCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Opened by the main thread, used by the builder's: one user at a time, never both.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=TRUNCATE")  # the module's docstring
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS chunks (provider TEXT, z INTEGER, x INTEGER, y INTEGER,"
            " status INTEGER, data BLOB, fetched INTEGER, PRIMARY KEY (provider, z, x, y)) "
            "WITHOUT ROWID"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS chunks_fetched ON chunks (fetched)")

    def get_many(self, provider: str, keys: Iterable[Key]) -> dict[Key, tuple[int, bytes | None]]:
        """{key: (status, body)} of the keys the cache holds."""
        out: dict[Key, tuple[int, bytes | None]] = {}
        cur = self.db.cursor()
        for z, x, y in keys:
            row = cur.execute(
                "SELECT status, data FROM chunks WHERE provider=? AND z=? AND x=? AND y=?",
                (provider, z, x, y),
            ).fetchone()
            if row is not None:
                out[(z, x, y)] = (row[0], row[1])
        return out

    def put_many(self, provider: str, rows: Iterable[tuple[Key, int, bytes | None]]) -> None:
        now = int(time.time())
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(provider, z, x, y, status, data, now) for (z, x, y), status, data in rows],
            )

    def size_bytes(self) -> int:
        """Bytes of imagery held (the file itself keeps freed pages for the next pieces)."""
        return self.db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM chunks").fetchone()[0]

    def evict_oldest(self, nbytes: int) -> int:
        """Delete the pieces downloaded longest ago until ``nbytes`` are freed; bytes freed."""
        freed = 0
        while freed < nbytes:
            rows = self.db.execute(
                "SELECT provider, z, x, y, COALESCE(LENGTH(data), 0) FROM chunks ORDER BY fetched "
                "LIMIT 2000"
            ).fetchall()
            if not rows:
                break
            batch = []
            for provider, z, x, y, size in rows:
                batch.append((provider, z, x, y))
                freed += size
                if freed >= nbytes:
                    break
            with self.db:
                self.db.executemany(
                    "DELETE FROM chunks WHERE provider=? AND z=? AND x=? AND y=?", batch
                )
        return freed

    def close(self) -> None:
        self.db.close()
