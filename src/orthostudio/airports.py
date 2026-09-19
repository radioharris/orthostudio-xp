"""Airport index: ``apt.dat`` streamed once into sqlite, then ICAO search and tiles around.

Spec: ``docs/specs/airports-index.md``. Stdlib only.

Usage::

    idx = default_index(None, progress=lambda done, total: ...)   # builds on demand
    idx.search("LFM")                 # -> [Airport(...), ...] (at most 10)
    idx.get("LFML")                   # -> Airport | None
    idx.tiles_around("LFML", 30.0)    # -> [TileRef(43, 5), ...]
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orthostudio.errors import OsxpError
from orthostudio.install.xplane import detect_xplane, global_scenery_dir, is_xplane_dir
from orthostudio.model import TileRef
from orthostudio.pipeline.home import osxp_home

__all__ = [
    "SCHEMA_VERSION",
    "Airport",
    "AirportIndex",
    "AirportKind",
    "BuildStats",
    "ProgressFn",
    "apt_dat_path",
    "default_index",
    "default_index_path",
    "haversine_km",
    "parse_apt_dat",
    "tiles_within",
]

SCHEMA_VERSION = 1
BLOCK_SIZE = 8 * 1024 * 1024
EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE = math.pi * EARTH_RADIUS_KM / 180.0

AirportKind = Literal["land", "sea", "heli"]
ProgressFn = Callable[[int, int], None]

_KINDS: dict[bytes, AirportKind] = {b"1": "land", b"16": "sea", b"17": "heli"}

#: One regex per block: header rows, the three metadata rows we keep, the geometry rows
#: that can serve as a fallback reference point, and the end-of-file marker.
_ROW = re.compile(
    rb"(?m)^(?:"
    rb"(?P<hdr>1|16|17)[ \t]+(?P<hdr_rest>[^\r\n]*)"
    rb"|1302[ \t]+(?P<meta>icao_code|datum_lat|datum_lon)[ \t]+(?P<meta_val>[^\r\n]*)"
    rb"|(?P<geo>100|101|102|14|1201|1300)[ \t]+(?P<geo_rest>[^\r\n]*)"
    rb"|(?P<end>99)[ \t]*$"
    rb")"
)

#: Field indexes (after the row code) of the latitude/longitude pairs used as a fallback.
_GEO_FIELDS: dict[bytes, tuple[tuple[int, int], ...]] = {
    b"100": ((8, 9), (17, 18)),
    b"101": ((3, 4), (6, 7)),
    b"102": ((1, 2),),
    b"14": ((0, 1),),
    b"1201": ((0, 1),),
    b"1300": ((0, 1),),
}


@dataclass(frozen=True, slots=True)
class Airport:
    """One row of the index."""

    icao: str
    ident: str
    name: str
    lat: float
    lon: float
    elev_ft: int | None
    kind: AirportKind
    has_datum: bool
    icao_explicit: bool = True

    @property
    def tile(self) -> TileRef:
        """The 1° tile containing the reference point."""
        return TileRef(math.floor(self.lat), math.floor(self.lon))


@dataclass(slots=True)
class BuildStats:
    """Counters of one build (also written to the ``meta`` table)."""

    airports: int = 0
    land: int = 0
    sea: int = 0
    heli: int = 0
    with_datum: int = 0
    fallback_runway: int = 0
    fallback_other: int = 0
    skipped_no_position: int = 0
    duplicate_idents: int = 0
    shared_icao: int = 0
    seconds: float = 0.0
    source_size: int = 0
    source_mtime_ns: int = 0

    def as_meta(self) -> dict[str, str]:
        return {k: str(getattr(self, k)) for k in self.__slots__}  # type: ignore[attr-defined]


# ---------------------------------------------------------------- parsing


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").strip()


def _latlon(fields: list[bytes], i: int, j: int) -> tuple[float, float] | None:
    try:
        lat, lon = float(fields[i]), float(fields[j])
    except (IndexError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


Row = tuple[str, str, str, float, float, int | None, str, int, int]


class _Record:
    """The airport being assembled while its rows stream by."""

    __slots__ = (
        "datum_lat",
        "datum_lon",
        "elev_ft",
        "fallback",
        "fallback_kind",
        "icao",
        "ident",
        "kind",
        "name",
    )

    def __init__(self, kind: AirportKind, rest: bytes) -> None:
        fields = rest.split(None, 4)
        self.kind = kind
        self.elev_ft: int | None
        try:
            self.elev_ft = int(float(fields[0]))
        except (IndexError, ValueError):
            self.elev_ft = None
        self.ident = _decode(fields[3]).upper() if len(fields) > 3 else ""
        self.name = _decode(fields[4]) if len(fields) > 4 else ""
        self.icao: str | None = None
        self.datum_lat: float | None = None
        self.datum_lon: float | None = None
        self.fallback: tuple[float, float] | None = None
        self.fallback_kind: bytes | None = None

    def finish(self, stats: BuildStats) -> Row | None:
        if not self.ident and not self.icao:
            stats.skipped_no_position += 1
            return None
        icao = self.icao or self.ident
        ident = self.ident or icao
        if (
            self.datum_lat is not None
            and self.datum_lon is not None
            and -90.0 <= self.datum_lat <= 90.0
            and -180.0 <= self.datum_lon <= 180.0
        ):
            lat, lon, has_datum = self.datum_lat, self.datum_lon, 1
            stats.with_datum += 1
        elif self.fallback is not None:
            (lat, lon), has_datum = self.fallback, 0
            if self.fallback_kind in (b"100", b"101", b"102"):
                stats.fallback_runway += 1
            else:
                stats.fallback_other += 1
        else:
            stats.skipped_no_position += 1
            return None
        explicit = 1 if self.icao is not None else 0
        return (icao, ident, self.name, lat, lon, self.elev_ft, self.kind, has_datum, explicit)


def parse_apt_dat(
    apt_dat: Path, *, progress: ProgressFn | None = None, stats: BuildStats | None = None
) -> Iterator[Row]:
    """Stream the airports of ``apt_dat`` as ``Row`` tuples.

    A row is ``(icao, ident, name, lat, lon, elev_ft, kind, has_datum, icao_explicit)``.

    Reads the file in blocks (spec section 2); never holds more than one block. Duplicate
    identifiers are *not* resolved here (the sqlite insert does it): every record is yielded.
    """
    stats = stats if stats is not None else BuildStats()
    total = apt_dat.stat().st_size
    done = 0
    current: _Record | None = None
    with apt_dat.open("rb") as f:
        tail = b""
        first = True
        finished = False
        while not finished:
            block = f.read(BLOCK_SIZE)
            if not block:
                data, tail, finished = tail, b"", True
            else:
                data = tail + block
                cut = data.rfind(b"\n")
                if cut < 0:
                    tail = data
                    continue
                data, tail = data[: cut + 1], data[cut + 1 :]
            if first:
                first = False
                if data.startswith(b"\xef\xbb\xbf"):
                    data = data[3:]
            for m in _ROW.finditer(data):
                if m.group("hdr") is not None:
                    if current is not None:
                        row = current.finish(stats)
                        if row is not None:
                            yield row
                    current = _Record(_KINDS[m.group("hdr")], m.group("hdr_rest"))
                elif current is None:
                    continue
                elif m.group("meta") is not None:
                    key, val = m.group("meta"), m.group("meta_val")
                    if key == b"icao_code":
                        code = _decode(val).upper()
                        if code:
                            current.icao = code
                    else:
                        try:
                            num = float(val)
                        except ValueError:
                            continue
                        if key == b"datum_lat":
                            current.datum_lat = num
                        else:
                            current.datum_lon = num
                elif m.group("geo") is not None:
                    if current.fallback is None:
                        geo = m.group("geo")
                        fields = m.group("geo_rest").split()
                        for i, j in _GEO_FIELDS[geo]:
                            pt = _latlon(fields, i, j)
                            if pt is not None:
                                current.fallback, current.fallback_kind = pt, geo
                                break
                else:  # end marker
                    finished = True
                    break
            done += len(data)
            if progress is not None:
                progress(min(done, total), total)
    if current is not None:
        row = current.finish(stats)
        if row is not None:
            yield row
    if progress is not None:
        progress(total, total)


# ---------------------------------------------------------------- geometry


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _wrap_lon(lon: int) -> int:
    while lon >= 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


def tiles_within(lat: float, lon: float, radius_km: float) -> list[TileRef]:
    """The 1° tiles whose bounding box is within ``radius_km`` of ``(lat, lon)`` (spec 4)."""
    if radius_km < 0:
        raise ValueError("radius_km must be >= 0")
    lat = max(-90.0, min(90.0, lat))
    lon = ((lon + 180.0) % 360.0) - 180.0
    dphi = min(radius_km / KM_PER_DEGREE, 180.0)
    cos_lat = max(math.cos(math.radians(lat)), 1e-9)
    dlam = min(dphi / cos_lat, 180.0)
    lat_lo = max(-90, math.floor(lat - dphi))
    lat_hi = min(89, math.floor(lat + dphi))
    lon_lo = math.floor(lon - dlam)
    lon_hi = math.floor(lon + dlam)
    if lon_hi - lon_lo >= 360:
        lon_lo, lon_hi = -180, 179
    out: set[TileRef] = set()
    for tlat in range(lat_lo, lat_hi + 1):
        clat = min(max(lat, float(tlat)), tlat + 1.0)
        for tlon in range(lon_lo, lon_hi + 1):
            # ``tlon`` may lie outside [-180, 180) here: the distance is measured in the
            # unwrapped frame (the haversine is periodic in longitude) and the tile is
            # stored wrapped.
            clon = min(max(lon, float(tlon)), tlon + 1.0)
            if haversine_km(lat, lon, clat, clon) <= radius_km:
                out.add(TileRef(tlat, _wrap_lon(tlon)))
    return sorted(out)


# ---------------------------------------------------------------- index


_SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE airports (
  ident TEXT PRIMARY KEY,
  icao TEXT NOT NULL,
  icao_explicit INTEGER NOT NULL,
  name TEXT NOT NULL,
  name_lc TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  elev_ft INTEGER,
  kind TEXT NOT NULL,
  has_datum INTEGER NOT NULL
);
CREATE INDEX airports_icao ON airports (icao);
CREATE INDEX airports_name_lc ON airports (name_lc);
"""

_INSERT = """
INSERT OR IGNORE INTO airports
  (icao, ident, icao_explicit, name, name_lc, lat, lon, elev_ft, kind, has_datum)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_COLUMNS = "icao, ident, name, lat, lon, elev_ft, kind, has_datum, icao_explicit"
#: The record X-Plane itself would pick for a code shared by several records (spec 2).
_PREFERRED = "icao_explicit DESC, (ident = icao) DESC, rowid"
_SELECT = f"SELECT {_COLUMNS} FROM airports"


def _row_to_airport(row: tuple[Any, ...]) -> Airport:
    icao, ident, name, lat, lon, elev, kind, has_datum, explicit = row[:9]
    return Airport(
        icao=str(icao),
        ident=str(ident),
        name=str(name),
        lat=float(lat),
        lon=float(lon),
        elev_ft=None if elev is None else int(elev),
        kind=kind,
        has_datum=bool(has_datum),
        icao_explicit=bool(explicit),
    )


class AirportIndex:
    """sqlite index of ``apt.dat``; holds only its path, opens a connection per query."""

    def __init__(self, db: Path) -> None:
        self.db = Path(db)

    # ------------------------------------------------------------ build

    def build(self, apt_dat: Path, *, progress: ProgressFn | None = None) -> BuildStats:
        """(Re)build the index from ``apt_dat`` atomically; returns the counters."""
        apt_dat = Path(apt_dat)
        st = apt_dat.stat()
        stats = BuildStats(source_size=st.st_size, source_mtime_ns=st.st_mtime_ns)
        t0 = time.perf_counter()
        self.db.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db.with_name(self.db.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        try:
            con = sqlite3.connect(tmp)
            try:
                con.execute("PRAGMA journal_mode = OFF")
                con.execute("PRAGMA synchronous = OFF")
                con.executescript(_SCHEMA)
                con.execute("BEGIN")
                batch: list[tuple[object, ...]] = []
                for icao, ident, name, lat, lon, elev, kind, has_datum, explicit in parse_apt_dat(
                    apt_dat, progress=progress, stats=stats
                ):
                    batch.append(
                        (icao, ident, explicit, name, name.lower(), lat, lon, elev, kind, has_datum)
                    )
                    if len(batch) >= 5000:
                        self._flush(con, batch, stats)
                if batch:
                    self._flush(con, batch, stats)
                stats.airports = int(con.execute("SELECT count(*) FROM airports").fetchone()[0])
                for kind in ("land", "sea", "heli"):
                    n = con.execute(
                        "SELECT count(*) FROM airports WHERE kind = ?", (kind,)
                    ).fetchone()
                    setattr(stats, kind, int(n[0]))
                shared = con.execute(
                    "SELECT count(*) FROM "
                    "(SELECT icao FROM airports GROUP BY icao HAVING count(*) > 1)"
                ).fetchone()
                stats.shared_icao = int(shared[0])
                stats.seconds = round(time.perf_counter() - t0, 3)
                meta = stats.as_meta()
                meta.update(
                    {
                        "schema_version": str(SCHEMA_VERSION),
                        "source_path": str(apt_dat),
                        "built_at": str(time.time()),
                    }
                )
                con.executemany("INSERT INTO meta (k, v) VALUES (?, ?)", list(meta.items()))
                con.execute("COMMIT")
            finally:
                con.close()
            os.replace(tmp, self.db)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return stats

    @staticmethod
    def _flush(con: sqlite3.Connection, batch: list[tuple[object, ...]], stats: BuildStats) -> None:
        before = con.total_changes
        con.executemany(_INSERT, batch)
        stats.duplicate_idents += len(batch) - (con.total_changes - before)
        batch.clear()

    def stats(self) -> dict[str, str]:
        """The ``meta`` table (empty when the database does not exist)."""
        if not self.db.exists():
            return {}
        try:
            with self._connect() as con:
                return {str(k): str(v) for k, v in con.execute("SELECT k, v FROM meta")}
        except sqlite3.DatabaseError:
            return {}

    def is_stale(self, apt_dat: Path) -> bool:
        """True when the index is absent, from another schema, or built from another file."""
        meta = self.stats()
        if meta.get("schema_version") != str(SCHEMA_VERSION):
            return True
        try:
            st = Path(apt_dat).stat()
        except OSError:
            return True
        return meta.get("source_size") != str(st.st_size) or meta.get("source_mtime_ns") != str(
            st.st_mtime_ns
        )

    def ensure(self, apt_dat: Path, *, progress: ProgressFn | None = None) -> BuildStats | None:
        """Build when stale; ``None`` when the index was already current."""
        if self.is_stale(apt_dat):
            return self.build(apt_dat, progress=progress)
        return None

    # ------------------------------------------------------------ queries

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A read-only connection, closed when the block ends.

        The ``with`` of a bare ``sqlite3.Connection`` only commits: the file stays open until the
        object is collected, and Windows refuses to replace a database file that is still open,
        so :meth:`build` failed with ``PermissionError`` right after :meth:`is_stale` read it.
        """
        con = sqlite3.connect(f"{self.db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            yield con
        finally:
            con.close()

    def count(self) -> int:
        if not self.db.exists():
            return 0
        with self._connect() as con:
            return int(con.execute("SELECT count(*) FROM airports").fetchone()[0])

    def get(self, icao: str) -> Airport | None:
        """The airport whose ``icao`` (or, failing that, ``ident``) equals ``icao``."""
        q = icao.strip().upper()
        if not q or not self.db.exists():
            return None
        with self._connect() as con:
            row = con.execute(f"{_SELECT} WHERE icao = ?", (q,)).fetchone()
            if row is None:
                row = con.execute(
                    f"{_SELECT} WHERE ident = ? ORDER BY icao LIMIT 1", (q,)
                ).fetchone()
        return None if row is None else _row_to_airport(row)

    def in_bounds(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        *,
        limit: int = 400,
        kinds: Iterable[str] | None = None,
    ) -> list[Airport]:
        """The airports inside that rectangle, the ones with a real ICAO code first.

        For the map: a user asked to see whether a square holds the airport he wants, which the
        aerial imagery does not always say (2026-09-19). The order puts the airports a pilot names
        (an explicit ICAO code, then a name) before the strips that carry only an identifier, so a
        capped answer keeps the useful ones. A rectangle crossing the antimeridian is read as two.
        """
        if not self.db.exists() or limit <= 0 or north < south:
            return []
        kind_list = sorted(set(kinds)) if kinds is not None else None
        if kind_list is not None and not kind_list:
            return []
        spans = [(west, east)] if west <= east else [(west, 180.0), (-180.0, east)]
        rows: list[tuple[Any, ...]] = []
        with self._connect() as con:
            for w, e in spans:
                sql = f"{_SELECT} WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
                params: list[object] = [south, north, w, e]
                if kind_list is not None:
                    sql += " AND kind IN (" + ",".join("?" * len(kind_list)) + ")"
                    params.extend(kind_list)
                sql += f" ORDER BY icao_explicit DESC, {_PREFERRED} LIMIT ?"
                params.append(limit)
                rows.extend(con.execute(sql, params).fetchall())
        return [_row_to_airport(row) for row in rows[:limit]]

    def search(
        self, q: str, limit: int = 10, *, kinds: Iterable[str] | None = None
    ) -> list[Airport]:
        """Identifier prefix or name substring, case-insensitive, ranked (spec section 3)."""
        text = q.strip()
        if not text or limit <= 0 or not self.db.exists():
            return []
        upper, lower = text.upper(), text.lower()
        kind_list = sorted(set(kinds)) if kinds is not None else None
        kind_sql = ""
        params: list[object] = [upper, upper, upper + "￿", upper, upper + "￿", lower]
        if kind_list is not None:
            if not kind_list:
                return []
            kind_sql = " AND kind IN (" + ",".join("?" * len(kind_list)) + ")"
            params.extend(kind_list)
        params.append(limit)
        sql = (
            f"SELECT {_COLUMNS}, "
            "CASE WHEN icao = ?1 OR ident = ?1 THEN 0 "
            "     WHEN (icao >= ?2 AND icao < ?3) OR (ident >= ?4 AND ident < ?5) THEN 1 "
            "     ELSE 2 END AS rank "
            "FROM airports "
            "WHERE ((icao >= ?2 AND icao < ?3) OR (ident >= ?4 AND ident < ?5) "
            "       OR instr(name_lc, ?6) > 0)"
            f"{kind_sql} ORDER BY rank, icao, {_PREFERRED} LIMIT ?"
        )
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [_row_to_airport(r) for r in rows]

    def tiles_around(self, icao: str, radius_km: float) -> list[TileRef]:
        """Tiles whose bounding box is within ``radius_km`` of the airport.

        Raises ``KeyError`` for an unknown identifier.
        """
        apt = self.get(icao)
        if apt is None:
            raise KeyError(icao)
        return tiles_within(apt.lat, apt.lon, radius_km)


# ---------------------------------------------------------------- defaults


def apt_dat_path(xplane_dir: Path) -> Path:
    """``<xp>/Global Scenery/Global Airports/Earth nav data/apt.dat``."""
    return global_scenery_dir(xplane_dir).parent / "Global Airports" / "Earth nav data" / "apt.dat"


def default_index_path() -> Path:
    """``~/.orthostudio/airports.sqlite`` (``$OSXP_HOME`` honoured)."""
    return osxp_home() / "airports.sqlite"


def default_index(xplane_dir: Path | None, *, progress: ProgressFn | None = None) -> AirportIndex:
    """The shared index, built or refreshed on demand from X-Plane's ``apt.dat``.

    Raises ``OsxpError("XP_DIR_NOT_FOUND")`` when no X-Plane folder is given or detected and
    ``OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND")`` when it holds no Global Airports ``apt.dat``.
    """
    xp = Path(xplane_dir) if xplane_dir is not None else detect_xplane()
    if xp is None or not is_xplane_dir(xp):
        raise OsxpError("XP_DIR_NOT_FOUND", context={"path": str(xp) if xp else "(none)"})
    apt = apt_dat_path(xp)
    if not apt.is_file():
        raise OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND", context={"path": str(xp)})
    index = AirportIndex(default_index_path())
    index.ensure(apt, progress=progress)
    return index
