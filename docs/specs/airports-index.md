# Airport index: ICAO search and tiles around an airport (`apt.dat` → sqlite)

Status: P2b, written before the code of `src/orthostudio/airports.py`. Tests:
`tests/test_airports_index.py` (unit, synthetic `apt.dat`), `tests/test_airports_xplane.py`
(`xplane`: reads the real `apt.dat` of the local X-Plane 12 install, never writes into it).
Measurements: section 8.

Origin in Ortho4XP: none. Ortho4XP knows airports only through OSM (`aeroway=aerodrome` polygons
fetched per tile); the user types latitude and longitude by hand. OrthoStudio XP lets the user type
an ICAO code (P2b: "saisie lat/lon/ICAO en attendant la carte") and, in P5, select "the tiles around
these airports" on the map. Decision: **new**, stdlib only (sqlite3, re).

## 1. The rule in plain language

X-Plane ships every airport it knows in `Global Scenery/Global Airports/Earth nav data/apt.dat`
(383 MB, 12 M lines, 38 886 airports on the reference machine, UTF-8). OrthoStudio XP reads that
file once, streaming, keeps one row per airport (identifier, name, latitude, longitude, elevation,
kind) in `~/.orthostudio/airports.sqlite`, and answers three questions without touching
`apt.dat` again:

- `search("LFM")` — the airports whose identifier starts with `LFM` or whose name contains
  `lfm`, case-insensitive, at most 10;
- `get("LFML")` — one airport by identifier;
- `tiles_around("LFML", 30)` — the 1° x 1° tiles whose bounding box lies within 30 km of the
  airport reference point.

The index is rebuilt automatically when `apt.dat` changes (X-Plane update): the build records
the file's size and modification time and compares them on every open.

## 2. Input: `apt.dat` (X-Plane 12 `1200`/`1300` format, observed on the reference machine)

Only these row codes are read (first whitespace-separated token of a line); everything else
(taxiways `110`-`116`, signs, lights, ATC frequencies, `1200`-`1206` traffic flows and
`1300`-`1302` rows other than the three below) is skipped at scan speed.

| Code | Meaning | Use |
|---|---|---|
| `1` | land airport header: `1 <elev_ft> <deprecated> <deprecated> <id> <name...>` | one row, `kind = land` |
| `16` | seaplane base header, same layout | one row, `kind = sea` |
| `17` | heliport header, same layout | one row, `kind = heli` |
| `1302 icao_code XXXX` | metadata: the official ICAO code | overrides the header identifier as `icao` |
| `1302 datum_lat` / `1302 datum_lon` | metadata: the airport reference point | `lat`, `lon` when both present |
| `100` | land runway: lat/lon of both ends at fields 9-10 and 18-19 | fallback centre = midpoint of the first one |
| `101` | water runway: ends at fields 4-5 and 7-8 | idem |
| `102` | helipad: centre at fields 2-3 | fallback centre |
| `14` | tower viewpoint: fields 1-2 | fallback centre |
| `1201` | taxi network node: fields 1-2 | fallback centre |
| `1300` | ramp start: fields 1-2 | fallback centre |
| `99` | end of file | stop |

Counts on the reference file: 31 258 land, 681 sea, 6 947 heliports; only 16 883 records carry
`1302 icao_code` and 17 150 carry `datum_lat`/`datum_lon`. The header identifier is therefore
the primary key (for US airports it is often the FAA code, `5TE`), and `1302 icao_code`, when
present, is stored as `icao` while the header identifier stays available as `ident`; search
matches both. Both are stored upper-cased (the file is upper-case already; this is defensive).

**Reference point.** `datum_lat`/`datum_lon` when both are present and parse as floats within
[-90, 90] / [-180, 180]. Otherwise the **first** geometry row met in the record, in file order,
among `100`, `101`, `102`, `14`, `1201`, `1300` (a runway or helipad is the airport; a tower or
a ramp start is a few hundred metres away, which is irrelevant at the 1° scale used here). A
record with no usable point at all is dropped and counted (`skipped_no_position`). On the
reference file 21 736 records fall back to a runway/helipad, 0 to a tower/ramp and 0 are
dropped (section 8).

**Seaplane bases and heliports are kept** (`kind` column): a user searching `ENQA` (an oil rig
helipad) or a seaplane base gets an answer, and the UI can filter on `kind` later. `search`
takes an optional `kinds` filter; the default is all three.

**Duplicates.** The header identifier is unique in the file (the Gateway enforces it) and is
the primary key; if a hand-made file repeats one, **the first record wins** and the second is
counted (`duplicate_idents`), so the result does not depend on hash order. The *ICAO code*,
on the other hand, is shared by 32 pairs of records on the reference file: typically a closed
airport (`1 51 0 0 PAMB [X] Manokotak`, no `1302 icao_code`) next to its successor
(`1 50 0 0 XPA000C Manokotak` + `1302 icao_code PAMB`), or a glider field and an offshore
heliport both tagged `EHDS`. Both records are kept (`shared_icao` counts the codes); `get(code)`
returns the one X-Plane would: explicit `1302 icao_code` first, then the record whose header
identifier *is* the code, then file order (`icao_explicit DESC, (ident = icao) DESC, rowid`).
`search` lists both, so the closed airport stays reachable by its own identifier.

**Encoding.** The file is read as bytes and decoded as UTF-8 with `errors="replace"` only for
the header and `1302` lines that are kept (names carry accents: `Rørvik Ryum`, `Provence-Alpes-
Côte-d'Azur`); a leading UTF-8 BOM and `\r\n` line ends are tolerated. The first line (`I` or
`A`) and the version line are not checked beyond being skipped, so a hand-written
`apt.dat` for a custom airport pack can be indexed by the same code later.

**Streaming.** The file is read in 8 MiB blocks; the block is scanned with one compiled bytes
regex anchored on line starts for the row codes above, the incomplete tail after the last
`\n` is carried to the next block. Memory stays at one block plus the rows of one airport;
nothing proportional to the file size is ever held. Progress is reported to a callback
`progress(bytes_done, bytes_total)` once per block (the UI shows "Indexing airports… 37 %").

## 3. Output: `airports.sqlite`

```sql
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- schema_version, source_path, source_size, source_mtime_ns, built_at + the BuildStats counters
CREATE TABLE airports (
  ident TEXT PRIMARY KEY,     -- header identifier (upper case)
  icao TEXT NOT NULL,         -- 1302 icao_code, else header identifier (upper case)
  icao_explicit INTEGER NOT NULL, -- 1 when icao comes from a 1302 icao_code row
  name TEXT NOT NULL,
  name_lc TEXT NOT NULL,      -- name lower-cased (str.lower, casefold-free: sqlite LIKE is ASCII-only)
  lat REAL NOT NULL, lon REAL NOT NULL,
  elev_ft INTEGER,
  kind TEXT NOT NULL,         -- 'land' | 'sea' | 'heli'
  has_datum INTEGER NOT NULL  -- 1 when the point is the 1302 datum, 0 when a fallback
);
CREATE INDEX airports_icao ON airports (icao);
CREATE INDEX airports_name_lc ON airports (name_lc);
```

No FTS: 39 k rows. Prefix search on `icao`/`ident` uses the indexes with a range predicate
(`>= q AND < q || char(0xFFFF)`), substring search on `name_lc` is `instr(name_lc, q) > 0`, a
full scan of 39 k short strings (about 5 ms). Ordering of `search`: exact identifier match,
then identifier prefix, then name substring; within a group `icao` ascending, then the
preference order above. `limit` defaults to 10; an empty or whitespace query, or `limit <= 0`,
returns `[]`; `kinds=[]` returns `[]`.

The database is built into `<db>.tmp` in one transaction (`executemany` per 5 000 rows,
`journal_mode = OFF`, `synchronous = OFF`: the file is disposable) and moved into place with
`os.replace`, so a reader never sees a half-built index and a build interrupted by Ctrl-C
leaves the previous index intact. The `.tmp` is removed on failure.

**Staleness.** `AirportIndex.is_stale(apt_dat)` is true when the database is absent, its
`schema_version` differs from the code's, or `(source_size, source_mtime_ns)` differ from
`apt_dat.stat()`. `AirportIndex.ensure(apt_dat, progress=None)` builds when stale and is a no-op
otherwise. `default_index(xplane_dir, *, progress=None)` resolves `xplane_dir` (argument, else
`detect_xplane()`), locates `apt.dat`, raises `OsxpError("XP_DIR_NOT_FOUND")` or
`OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND")` (existing codes; no new code needed) when it cannot,
opens `~/.orthostudio/airports.sqlite` (via `orthostudio.pipeline.home.osxp_home()`, `$OSXP_HOME`
honoured) and calls `ensure`.

**Concurrency.** `AirportIndex` holds only the path; every query opens a short-lived read-only
connection (`mode=ro` URI), which makes it safe to call from any FastAPI worker thread and
lets the build replace the file underneath. The build itself is not guarded against two
processes building at once: both produce the same file and `os.replace` is atomic, the loser's
work is wasted, not harmful.

## 4. `tiles_around(icao, radius_km)`

The tiles whose 1° x 1° bounding box is within `radius_km` of the airport's reference point,
great-circle distance (haversine, R = 6 371.0088 km). For each candidate tile in the
latitude band `floor(lat - dφ) .. floor(lat + dφ)` and longitude band
`floor(lon - dλ) .. floor(lon + dλ)` (`dφ = r / 111.195 km`, `dλ = dφ / cos(lat)`, both
clamped to a whole hemisphere), the nearest point of the box to the airport is the point
clamped to the box in latitude and longitude, and the tile is kept when the haversine distance
to that point is `<= radius_km`. Longitudes wrap through ±180 (`TileRef.neighbour` rules);
latitudes are clamped to [-90, 89]. `radius_km = 0` returns exactly the tile containing the
airport. The result is sorted by `(lat, lon)`, deduplicated. Unknown identifier → `KeyError`.

Clamping to the box then measuring the haversine distance is exact for the nearest point in
the equirectangular sense and within a few metres of the true geodesic minimum at these
scales (a tile edge is a great circle in longitude but a small circle in latitude; the error
is second order in the 1° box and irrelevant for a 1°-quantised answer).

## 5. Public API (contract P2b)

```python
@dataclass(frozen=True, slots=True)
class Airport:
    icao: str; ident: str; name: str; lat: float; lon: float
    elev_ft: int | None; kind: Literal["land", "sea", "heli"]; has_datum: bool
    icao_explicit: bool = True
    @property tile -> TileRef

@dataclass(slots=True)
class BuildStats:   # airports, land, sea, heli, with_datum, fallback_runway, fallback_other,
    ...             # skipped_no_position, duplicate_idents, shared_icao, seconds, source_size,
                    # source_mtime_ns; parser counters count records, sqlite counters count rows

class AirportIndex:
    def __init__(self, db: Path) -> None
    def build(self, apt_dat: Path, *, progress: ProgressFn | None = None) -> BuildStats
    def is_stale(self, apt_dat: Path) -> bool
    def ensure(self, apt_dat: Path, *, progress: ProgressFn | None = None) -> BuildStats | None
    def search(self, q: str, limit: int = 10, *, kinds: Iterable[str] | None = None) -> list[Airport]
    def get(self, icao: str) -> Airport | None
    def tiles_around(self, icao: str, radius_km: float) -> list[TileRef]
    def stats(self) -> dict[str, str]        # the meta table
    def count(self) -> int

def apt_dat_path(xplane_dir: Path) -> Path
def default_index_path() -> Path                          # ~/.orthostudio/airports.sqlite
def default_index(xplane_dir: Path | None, *, progress: ProgressFn | None = None) -> AirportIndex
def tiles_within(lat, lon, radius_km) -> list[TileRef]   # the geometry of section 4, reusable
def haversine_km(lat1, lon1, lat2, lon2) -> float
def parse_apt_dat(apt_dat, *, progress=None, stats=None) -> Iterator[Row]   # the streaming parser

ProgressFn = Callable[[int, int], None]   # (bytes_done, bytes_total)
```

`build(apt_dat, db)` of the contract is spelled `AirportIndex(db).build(apt_dat)`: the class
owns its path so `search`/`get` need no argument (wanted difference from the contract's
prose; the API chantier calls `default_index(...)` and never `build` directly).

## 6. Wanted differences and non-goals

- Not an airport *renderer*: which airports get flattened and textured in a tile is still
  decided by OSM `aeroway` polygons in the vectors step (Ortho4XP rule, unchanged). The index only
  turns a code into coordinates and tiles.
- Custom Scenery airports (`Custom Scenery/*/Earth nav data/apt.dat`) are not indexed in P2b;
  the parser is reusable for them (section 2, no header check) when P5 needs it.
- No IATA search (`1302 iata_code`): could be added as a column later; not requested.

## 7. Acceptance

Unit (synthetic `apt.dat`, 9 records): BOM + CRLF tolerated; `1302 icao_code` overrides the header
identifier; a record without datum takes the first runway midpoint, a heliport without datum its
first `102`; a record with no geometry is dropped and counted; a duplicate identifier keeps the
first; a shared ICAO code resolves to the record with the explicit `1302 icao_code` while `search`
lists both; UTF-8 names with accents round-trip; `search` prefix / substring / limit / empty query /
`kinds`; `tiles_around` at r = 0, at a tile corner (4 tiles), across the antimeridian, and radius >
1° in latitude; `is_stale` after touching the file, `ensure` no-op when fresh; `default_index` with
`OSXP_HOME` in a temporary directory; `.tmp` cleaned on a broken file.

Oracle (this machine): `LFML` → `lat ≈ 43.44`, `lon ≈ 5.22` (`43.436666667`, `5.215` in the
file), `tiles_around("LFML", 30)` contains `+43+005`; `search("marseille")` contains `LFML`;
`count() > 30 000`.

## 8. Measurements (reference machine, M4 Pro 14 cores, `nice -n 10`, 2026-09-12)

`apt.dat` of X-Plane 12.1 Global Airports: 383 351 297 bytes, 12 M lines.

| Measure | Value |
|---|---|
| Full build (parse + sqlite + indexes + `os.replace`), best of 3 | **1.84 s** (wall, `nice -n 10`) |
| Database size | **5.07 MB** (`airports.sqlite`) |
| Airports indexed | 38 886 = 31 258 land + 681 seaplane bases + 6 947 heliports |
| Reference point from `1302 datum_*` | 17 150 (44 %) |
| Fallback to first runway / helipad (`100`/`101`/`102`) | 21 736 (56 %) |
| Fallback to tower / taxi node / ramp start | 0 |
| Dropped (no geometry) | 0 |
| Duplicate header identifiers | 0 |
| ICAO codes shared by two records | 32 (see section 2) |
| Records whose `icao` differs from `ident` | 1 414 |
| `get("LFML")` | 0.17 ms |
| `search("marseille")` (name scan) | 3.4 ms |
| `search("LFM")` (prefix) | 3.7 ms |

Bottleneck of the build: the 8 MiB block regex over 383 MB (~1.2 s); the sqlite side is
~0.4 s. Well under the "build on demand at first use" budget; no incremental update needed.
