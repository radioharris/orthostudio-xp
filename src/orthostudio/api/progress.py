"""Progress and time remaining of a whole job, across the phases of a build.

Spec: ``docs/specs/api.md`` section 5.6. ``build_tiles`` downloads OSM data in a phase 0 of its
own, then runs the main graph on another scheduler, and each scheduler's ``Stats`` only knows
its own nodes: a six-tile batch read "2 of 4 done" during phase 0 -- 50 % on the page, while the
four downloads were a fifth of the job -- and its elapsed time restarted with the main graph.
The job keeps its own account instead, over every node it expects, with its own clock.

**Weights.** A node weighs the seconds it is expected to take on the reference Mac (M4 Pro):
:data:`ROLE_SECONDS` for a tile built alone, moving to :data:`BATCH_SECONDS` in a batch that
keeps every slot busy (the costs the scheduler learnt over fifteen builds agree with the
latter). The textures node weighs its textures:
:data:`TEXTURE_DOWNLOAD_S` each when their chunks must be downloaded from a provider that takes
Bing's 128 requests at once (eight times more from one that takes 16), :data:`TEXTURE_CACHED_S`
when they are on disk -- a warm tile encoded 234 textures in 11 s, a cold one took 66 to 110 s
for 216. Progress is the weighted share of the work ended. A node that does not run -- a hit, or
a node skipped or cancelled before it started -- counts as ended but weighs nothing: its
expected cost is zero, and with its full weight a retry whose first second is sixty hits read
70 % at once, and a single tile whose OSM data was a hit read 48 % after one second of thirty.

**Time remaining.** The same weights, corrected by what the job has shown so far:

* a speed per group of nodes (OSM downloads, textures to download, textures from the cache,
  everything else): the wall seconds of the nodes that ran over their weights, damped by one
  pseudo-observation at the expected speed so that a single node does not swing the estimate.
  For the textures to download, the expected speed is the one this line showed on the latest
  builds of the same provider (:func:`line_factor`, read from their texture reports) when there
  are any: the line gave 0.19-0.27 s a texture on one batch and 0.23-0.47 s on the next, and a
  six-tile batch on a fast evening was predicted a third too long for its first half from the
  constant alone (``docs/benchmarks/batch-6-tiles-zl16.md``);
  Downloads and cached textures are apart because the line limits one and the processor the
  other: a warm tile's encoding said nothing of the next tile's download, and read as such it
  moved the estimate by 85 s in a second. The download group matters most: the same line gave
  1 400 req/s one night and 450 the next evening;
* a running node that reports fine progress -- the textures, from a few percent -- is extrapolated
  from its recent rate (:data:`RATE_TAU_S`), counted from the moment it started moving: a warm
  tile sits at 50 % for six seconds, its download half being free, then encodes; a cold one
  downloads at 400 req/s while the fetcher opens its connections and at 1 200 req/s fifteen
  seconds later. The extrapolation is trusted as it gains fraction *and* time, and also feeds
  its group's speed, because the textures still queued share the line of the one that runs;
* the remaining seconds then queue the way the scheduler runs them: in lanes of its slots --
  one network slot, so the elevation and textures nodes of a batch run one after the other --
  and along each tile's chain, elevation to install; the main graph ends with the slowest lane
  or chain. Before it, phase 0 downloads one tile at a time, then the main graph is declared
  (:data:`DECLARE_S`).

The range around the estimate narrows as the share of the remaining work whose group speed
was observed grows: from -22 %/+67 % to -10 %/+30 %. What the page shows then moves smoothly
(:class:`EtaSmoother`): the predicted end by at most :data:`ETA_SLEW` of the time left per
second (:data:`ETA_SLEW_MIN_S` near the end), the width of the range by :data:`BAND_SLEW`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from orthostudio.errors import OsxpError
from orthostudio.imagery.grid import TEXTURE_TILES, TextureId, texture_at
from orthostudio.pipeline.build import BuildSpec
from orthostudio.zones import zone_list_textures

__all__ = [
    "BAND_MAX",
    "BAND_MIN",
    "OSM_LANE",
    "ROLE_KIND",
    "ROLE_SECONDS",
    "TEXTURE_CACHED_S",
    "TEXTURE_DOWNLOAD_S",
    "Estimate",
    "EtaSmoother",
    "NodeLike",
    "TextureCost",
    "estimate",
    "line_factor",
    "node_seconds",
    "observe_progress",
    "read_line_history",
    "texture_cost",
    "texture_seconds",
    "weight_of",
    "weighted_progress",
]

ROLE_SECONDS: dict[str, float] = {
    "osm": 26.0,
    "dem": 2.5,
    "coastline": 0.05,
    "vectors": 6.3,
    "mesh": 2.6,
    "masks": 0.85,
    "xp12": 0.4,
    "dsf": 1.8,
    "overlay": 3.2,
    "pack": 0.3,
    "install": 0.15,
    "textures": 65.0,
}
"""Expected seconds per role with the native recipes, for a tile built alone (three ``OrthoStudio XP
build`` runs of one tile on the reference Mac, 2026-09-13). ``textures`` is only a fallback:
the node's weight is :func:`texture_seconds`."""
BATCH_SECONDS: dict[str, float] = {
    "dem": 6.2,
    "coastline": 0.05,
    "vectors": 11.1,
    "mesh": 8.4,
    "masks": 1.7,
    "xp12": 0.4,
    "dsf": 5.5,
    "overlay": 2.8,
    "pack": 0.8,
    "install": 0.15,
}
"""The same roles inside a batch that keeps every slot busy (the six-tile job of 2026-09-13):
they share the machine, a mesh takes 1.4-3.6 s alone and 5-12 s next to five others. Weights
move from :data:`ROLE_SECONDS` at one tile to these at :data:`BATCH_TILES` tiles."""
BATCH_TILES = 3
"""Tiles from which a batch fills the three subprocess slots."""
DEFAULT_SECONDS = 1.0
TEXTURE_DOWNLOAD_S = 0.30
"""One texture whose 256 chunks are downloaded then encoded, from a provider that takes
:data:`REFERENCE_IN_FLIGHT` requests at once (Bing here: about 1 000 req/s)."""
TEXTURE_CACHED_S = 0.05
"""One texture whose chunks are already on disk: encoding only."""
REFERENCE_IN_FLIGHT = 128
"""The line's download rate scales with ``max_in_flight / 128``: the throughput of a line is the
requests in flight over its latency (``osxp plan``'s model, ``estimate.ProbeResult``), up to the
rate of the server itself (:func:`texture_download_s`)."""
TEXTURE_SAMPLE = 256
"""At most this many textures of a tile are looked up in the chunk store (a stat each)."""
ROLE_KIND: dict[str, str] = {
    "osm": "net",
    "dem": "subprocess",
    "coastline": "io",
    "vectors": "subprocess",
    "mesh": "subprocess",
    "masks": "subprocess",
    "xp12": "io",
    "dsf": "cpu",
    "textures": "net",
    "overlay": "subprocess",
    "pack": "io",
    "install": "io",
}
"""The scheduler kind of each role, as ``pipeline.build.declare`` gives it: ``dem`` is ``net``
when the relief is downloaded (``relief = view``), X-Plane 12's own is read from its DSFs. The
``osm`` rows queue on the Overpass lane (:data:`OSM_LANE`), not on their kind's slots."""
OSM_LANE = "overpass"
"""``pipeline.build.OVERPASS_LANE``: the OSM downloads of a build, one tile at a time."""
CHAIN_ROLES = frozenset({"dem", "vectors", "mesh", "masks", "dsf", "textures", "pack", "install"})
DECLARE_S = 0.3
"""Declaring the main graph (0.2 s for one tile)."""
SPEED_BOUNDS = (0.1, 10.0)
"""A group's speed stays within these. Wide on purpose: the downloads of this very line took
0.17 to 0.55 s a texture, a throttled or distant provider several times the weight on top of
its concurrency, and a bound that is too tight hides it for the whole queue -- the ETA of the
textures still waiting is their weights times this factor. The pseudo-observation keeps a
single early node from reaching the bounds."""
SPEED_PRIOR_S: dict[str, float] = {
    "osm": 20.0,
    "textures": 30.0,
    "textures_cached": 10.0,
    "compute": 15.0,
}
"""The pseudo-observation, in seconds of weight at the expected speed, of each group."""
HISTORY_REPORTS = 6
"""The latest texture reports of a provider that tell a job how fast the line is."""
HISTORY_READ = 24
"""Reports read at most, newest first, to find them (another provider's are skipped)."""
HISTORY_HALF_LIFE_H = 12.0
"""A report's say halves every twelve hours: this evening's line is the best guess for the next
build, this morning's a weaker one."""
HISTORY_MIN_CHUNKS = 2560
"""A report speaks for the line when it downloaded ten textures' worth of chunks at least (a
warm tile's report measures the processor)."""
CHUNKS_PER_TEXTURE = TEXTURE_TILES * TEXTURE_TILES
LINE_REQ_PER_S = CHUNKS_PER_TEXTURE / (TEXTURE_DOWNLOAD_S - TEXTURE_CACHED_S)
"""Requests per second of the reference line at :data:`REFERENCE_IN_FLIGHT` (1 024: Bing)."""
SPEED_MEMORY_S = 180.0
"""An ended node's say in its group's speed halves every ``ln 2 x`` this: the line of the
textures drifts during a long batch (1 230 req/s on one tile, 450 three tiles later)."""
REPORT_GRACE_S = 2.0
"""A node's rate stands while its last report is this recent (reports come every half second);
after, the silence itself slows the rate down."""
RATE_TAU_S = 10.0
"""Time constant of a running node's recent rate (an exponential average of its progress
between reports): long enough to smooth half-second reports, short enough to forget the slow
start of the fetcher and to follow a line that slows."""
SILENT_S = 60.0
"""A node silent for this long is not extrapolated any more, and the estimate falls back to what
the weights say. The rate decays with the silence, and the time left is what is missing divided
by it: a step that stopped reporting made that division approach zero and the page announced
1 308 980 335 hours remaining (a user, 2026-09-23)."""
ETA_MAX_S = 24 * 3600.0
"""Beyond a day, an estimate is not information: the page is told nothing rather than a number
nobody can act on."""
EXTRAPOLATE_FROM = 0.02
"""A running node is extrapolated once it has gained this much since it started moving..."""
EXTRAPOLATE_MIN_S = 2.0
"""...and this many seconds have passed since then."""
TRUST_AT = 0.15
"""Gain at which the extrapolation may replace the weight entirely (from none at
:data:`EXTRAPOLATE_FROM`, linearly: switching it on at once moved an estimate by a quarter)."""
TRUST_AFTER_S = 12.0
"""...and seconds of movement it takes as well (from none at :data:`EXTRAPOLATE_MIN_S`): a
warm tile gained 17 % in its first half-second of encoding, which the gain alone trusted
fully."""
QUEUE_TRUST_FROM_S = 15.0
QUEUE_TRUST_AFTER_S = 45.0
"""A running node's rate speaks for the queue of its group (the textures still waiting) only
after this much movement, fully at the second value: a cold tile downloads at 400 req/s while
the fetcher opens its connections, which read as the speed of the four tiles behind it
added two minutes to the estimate for ten seconds."""
OVERRUN_SHARE = 0.15
"""A node running past its expected time is still given this share of it."""
FAILED_COUNTS_FROM = 0.9
"""A failed node informs its group's speed when it had reported this much done (the textures
node fails at the very end, when a few chunks are missing, after doing all the work)."""
BAND_MIN = 0.20
BAND_MAX = 0.45
BAND_LOW_SHARE = 0.5
"""The range is asymmetric: a build ends late by more than it ends early (the line slowed from
1 230 to 450 req/s during one batch, the last chunks of a texture were retried for 26 s), so
the range runs from ``1 - 0.5 x band`` to ``1 + 1.5 x band`` of the estimate."""
ETA_SLEW = 0.04
"""The published end moves by at most this share of the time left per second: a new node
that changes the estimate by a minute on a job of eight takes a dozen seconds to show it
entirely, instead of a jump the next estimate half undoes (+40 s then -24 s within a second on
the real stream)."""
ETA_SLEW_MIN_S = 2.0
"""...and by at least this many seconds per second, so the last half-minute follows the job
(4 % of 20 s left would be under a second)."""
BAND_SLEW = 0.01
"""The width of the range (``band``) changes by at most this much per second."""
ENDED = frozenset({"done", "hit", "failed", "skipped", "cancelled"})


class NodeLike(Protocol):
    """What the estimate reads of a node row (``api.jobs._NodeState``). Times are one clock's
    seconds (the job's)."""

    node: str
    role: str
    group: str
    """The speed group (``SPEED_PRIOR_S``); empty: from the role."""
    kind: str
    weight_s: float
    status: str
    fraction: float
    hit: bool | None
    wall_s: float
    started_at: float | None
    ended_at: float | None
    fraction0: float | None
    """The fraction the node reported last before it rose, and when (``fraction0_at``)."""
    fraction0_at: float | None
    fraction_at: float | None
    """When the node reported its current ``fraction``."""
    rate: float | None
    """Its recent rate, fraction per second, since it started moving (:func:`observe_progress`)."""


@dataclass(frozen=True, slots=True)
class Estimate:
    progress: float
    """0-1, weighted; not yet held non-decreasing (the job does that)."""
    eta_s: float | None
    low_s: float | None
    high_s: float | None
    band: float = BAND_MAX
    """The relative width behind ``low_s`` / ``high_s`` (:data:`BAND_MIN`-:data:`BAND_MAX`)."""


@dataclass(frozen=True, slots=True)
class TextureCost:
    seconds: float
    download_s: float
    """The part of ``seconds`` that is downloading (the rest is encoding cached chunks)."""

    @property
    def group(self) -> str:
        """The speed group of the node: the line or the processor, whichever weighs more."""
        return "textures" if self.download_s >= 0.5 * self.seconds else "textures_cached"


@dataclass(slots=True)
class EtaSmoother:
    """The range a page shows, moving at a bounded pace (module docstring).

    ``update`` takes each fresh estimate and returns ``(low_s, high_s)``: the published end
    (``now + eta``) moves toward the estimated one by at most ``max(ETA_SLEW x time left,
    ETA_SLEW_MIN_S)`` per second and never lies in the past; the band by :data:`BAND_SLEW` per
    second. The first estimate is published as it is.
    """

    end: float | None = None
    band: float = BAND_MAX
    at: float | None = None

    def update(self, now: float, eta_s: float | None, band: float) -> tuple[float, float] | None:
        if eta_s is None:
            return None
        target = now + max(0.0, eta_s)
        if self.end is None or self.at is None:
            end, width = target, band
        else:
            dt = max(0.0, now - self.at)
            step = dt * max(ETA_SLEW * max(0.0, self.end - now), ETA_SLEW_MIN_S)
            end = min(max(target, self.end - step), self.end + step)
            width = min(max(band, self.band - dt * BAND_SLEW), self.band + dt * BAND_SLEW)
        end = max(end, now)
        self.end, self.band, self.at = end, width, now
        left = end - now
        return (
            left * (1.0 - BAND_LOW_SHARE * width),
            left * (1.0 + (2.0 - BAND_LOW_SHARE) * width),
        )


# -- weights ------------------------------------------------------------------------------------


def node_seconds(
    role: str,
    *,
    textures_s: float | None = None,
    tiles: int = 1,
) -> float:
    """The weight of a node: its role's seconds, for a batch of ``tiles`` tiles."""
    if role == "textures" and textures_s is not None:
        return textures_s
    alone = ROLE_SECONDS.get(role, DEFAULT_SECONDS)
    batch = BATCH_SECONDS.get(role, alone)
    share = min(1.0, max(0.0, (tiles - 1) / (BATCH_TILES - 1)))
    return alone + (batch - alone) * share


def _tile_grid(spec: BuildSpec) -> tuple[TextureId, int, int]:
    """First texture, rows and columns of the textures covering the tile at its level (the
    rectangle of ``textures_covering``, without listing it)."""
    lat, lon = spec.tile.lat, spec.tile.lon
    first = texture_at(lat + 1, lon, spec.zl, spec.provider)
    last = texture_at(lat, lon + 1, spec.zl, spec.provider)
    rows = (last.til_y - first.til_y) // TEXTURE_TILES + 1
    cols = (last.til_x - first.til_x) // TEXTURE_TILES + 1
    return first, rows, cols


def _share_cached(
    textures: Sequence[TextureId], has_chunks: Callable[[TextureId], bool] | None
) -> float:
    if has_chunks is None or not textures:
        return 0.0
    sample = textures[:: max(1, len(textures) // TEXTURE_SAMPLE)]
    return sum(1 for t in sample if has_chunks(t)) / len(sample)


def texture_download_s(in_flight: int | None, server_req_per_s: float | None = None) -> float:
    """Seconds to download the chunks of one texture (encoding apart) from a provider.

    The line's rate scales with the requests in flight (:data:`LINE_REQ_PER_S` at
    :data:`REFERENCE_IN_FLIGHT`; ``None``: 128), and never passes ``server_req_per_s``, the rate
    the server itself gave (``Provider.server_req_per_s``): counted from its 192 requests at once
    alone, Esri Clarity was expected three times faster than its 440 req/s, and Japan's server five
    times faster than its 103 (2026-09-15)."""
    flight = REFERENCE_IN_FLIGHT if not in_flight or in_flight < 1 else in_flight
    rate = LINE_REQ_PER_S * flight / REFERENCE_IN_FLIGHT
    if server_req_per_s is not None and server_req_per_s > 0:
        rate = min(rate, server_req_per_s)
    return CHUNKS_PER_TEXTURE / rate


def texture_seconds(
    spec: BuildSpec,
    has_chunks: Callable[[TextureId], bool] | None = None,
    *,
    zones: bool = True,
    in_flight: int | None = None,
) -> float:
    """Expected seconds of the textures node of ``spec`` (:func:`texture_cost`)."""
    return texture_cost(spec, has_chunks, zones=zones, in_flight=in_flight).seconds


def texture_cost(
    spec: BuildSpec,
    has_chunks: Callable[[TextureId], bool] | None = None,
    *,
    zones: bool = True,
    in_flight: int | None = None,
    server_req_per_s: float | None = None,
) -> TextureCost:
    """Expected seconds of the textures node of ``spec``, and how much of it is downloading.

    Its textures are those covering the tile at its level plus those its ``zone_list`` adds
    (the upper bound ``osxp plan`` uses before the DSF exists); each costs
    :data:`TEXTURE_CACHED_S` or :data:`TEXTURE_DOWNLOAD_S` depending on whether its chunks are
    on disk, which ``has_chunks`` answers for a sample of at most :data:`TEXTURE_SAMPLE` per
    group (``None``: everything is downloaded; ``zones=False``: the tile alone, no geometry).
    The download part is :func:`texture_download_s` for the provider's ``in_flight`` and
    ``server_req_per_s``.
    """
    first, rows, cols = _tile_grid(spec)
    count = rows * cols
    side = max(1, math.isqrt(TEXTURE_SAMPLE))
    grid = [
        TextureId(first.til_x + c * TEXTURE_TILES, first.til_y + r * TEXTURE_TILES, spec.zl,
                  spec.provider)
        for r in range(0, rows, max(1, math.ceil(rows / side)))
        for c in range(0, cols, max(1, math.ceil(cols / side)))
    ]  # fmt: skip
    extra: list[TextureId] = []
    zone_list = (spec.config.get("zone_list") or ()) if zones else ()
    if zone_list:
        try:
            found = zone_list_textures(zone_list, spec.tile)
        except OsxpError:
            found = set()  # the build refuses it with its own error; the tile's weight stays
        extra = sorted(t for t in found if not _in_grid(t, spec, first, rows, cols))
    tile_share = _share_cached(grid, has_chunks)
    zone_share = _share_cached(extra, has_chunks)
    fetch_s = texture_download_s(in_flight, server_req_per_s)
    downloads = count * (1.0 - tile_share) + len(extra) * (1.0 - zone_share)
    textures = count + len(extra)
    return TextureCost(textures * TEXTURE_CACHED_S + downloads * fetch_s, downloads * fetch_s)


def _in_grid(t: TextureId, spec: BuildSpec, first: TextureId, rows: int, cols: int) -> bool:
    if t.zl != spec.zl or t.provider != spec.provider:
        return False
    dy, dx = t.til_y - first.til_y, t.til_x - first.til_x
    return 0 <= dy < rows * TEXTURE_TILES and 0 <= dx < cols * TEXTURE_TILES


# -- estimate -----------------------------------------------------------------------------------


def _group(n: NodeLike) -> str:
    if n.group in SPEED_PRIOR_S:
        return n.group
    return n.role if n.role in ("osm", "textures") else "compute"


def weight_of(n: NodeLike) -> float:
    """The node's weight in progress: zero when it does not run (a hit, a skip, a cancel
    before it started)."""
    if n.status == "hit" or (n.status in ("skipped", "cancelled") and n.started_at is None):
        return 0.0
    return max(0.0, n.weight_s)


def weighted_progress(nodes: Sequence[NodeLike]) -> float:
    """Weighted share of the work ended (1 for an ended node, its fraction while it runs); by
    count when nothing weighs (every node a hit)."""
    total = sum(weight_of(n) for n in nodes)
    if total <= 0:
        return sum(_fraction(n) for n in nodes) / len(nodes) if nodes else 0.0
    return sum(weight_of(n) * _fraction(n) for n in nodes) / total


def _fraction(n: NodeLike) -> float:
    if n.status in ENDED:
        return 1.0
    if n.status == "running":
        return min(1.0, max(0.0, n.fraction))
    return 0.0


def observe_progress(n: NodeLike, fraction: float, now: float) -> None:
    """Record a progress report on a running row: offset, recent rate, fraction.

    Until the node first rises above its offset, the offset follows it (a node that sits at 50 %
    has not started its measurable part). Then ``rate`` is the node's average rate since it
    started moving at the first report, and an exponential average of the rate between
    reports afterwards (:data:`RATE_TAU_S`).
    """
    f = min(1.0, max(0.0, fraction))
    prev_f, prev_at = n.fraction, n.fraction_at
    if (
        n.fraction0 is None
        or n.fraction0_at is None
        or (n.fraction0_at == prev_at and f <= n.fraction0)
    ):
        n.fraction0, n.fraction0_at, n.rate = f, now, None
    elif n.rate is None:
        span = now - n.fraction0_at
        n.rate = (f - n.fraction0) / span if span > 0 else None
    elif prev_at is not None and now > prev_at:
        weight = 1.0 - math.exp(-(now - prev_at) / RATE_TAU_S)
        n.rate = weight * max(0.0, f - prev_f) / (now - prev_at) + (1.0 - weight) * n.rate
    n.fraction, n.fraction_at = f, now


def _extrapolation(n: NodeLike, now: float) -> tuple[float, float] | None:
    """``(seconds left, trust 0-1)`` from the node's recent rate since it started moving."""
    if n.status != "running" or n.fraction0 is None or n.fraction0_at is None or not n.rate:
        return None
    gained = n.fraction - n.fraction0
    span = now - n.fraction0_at
    if gained < EXTRAPOLATE_FROM or span < EXTRAPOLATE_MIN_S:
        return None
    last = n.fraction_at if n.fraction_at is not None else now
    silence = max(0.0, now - last)
    if silence >= SILENT_S:  # nothing to extrapolate from: the weights answer instead
        return None
    rate = n.rate * math.exp(-max(0.0, silence - REPORT_GRACE_S) / RATE_TAU_S)
    if rate <= 0.0:
        return None
    left = (1.0 - n.fraction) / rate - silence
    by_gain = (gained - EXTRAPOLATE_FROM) / (TRUST_AT - EXTRAPOLATE_FROM)
    by_time = (span - EXTRAPOLATE_MIN_S) / (TRUST_AFTER_S - EXTRAPOLATE_MIN_S)
    return max(0.0, left), min(1.0, max(0.0, min(by_gain, by_time)))


def _queue_trust(n: NodeLike, now: float, trust: float) -> float:
    """How much a running node's extrapolation speaks for the other nodes of its group."""
    if n.fraction0_at is None:
        return 0.0
    moving = now - n.fraction0_at
    by_time = (moving - QUEUE_TRUST_FROM_S) / (QUEUE_TRUST_AFTER_S - QUEUE_TRUST_FROM_S)
    return min(trust, max(0.0, min(1.0, by_time)))


def read_line_history(
    logs: Path, *, limit: int = HISTORY_READ
) -> list[tuple[float, dict[str, Any]]]:
    """The latest texture reports under ``logs`` (``textures-<tile>-<level>-<key>.json``, written
    by ``build_tiles``), newest first, as ``(mtime, report)``; unreadable ones are skipped."""
    try:
        found = sorted(
            ((p.stat().st_mtime, p) for p in Path(logs).glob("textures-*.json")), reverse=True
        )
    except OSError:
        return []
    out: list[tuple[float, dict[str, Any]]] = []
    for mtime, path in found[:limit]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            out.append((mtime, doc))
    return out


def line_factor(
    reports: Iterable[tuple[float, Mapping[str, Any]]],
    *,
    provider: str,
    now: float,
    in_flight: int | None = None,
    server_req_per_s: float | None = None,
) -> float | None:
    """The speed factor of the textures to download that this line showed on recent builds.

    Each report of ``provider`` that downloaded :data:`HISTORY_MIN_CHUNKS` chunks at least and
    was not cancelled gives its seconds per downloaded texture, encoding included (its whole
    time less :data:`TEXTURE_CACHED_S` per texture of cached chunks), over the weight of such a
    texture for ``in_flight`` and ``server_req_per_s`` (:func:`texture_cost`). The factor is the
    median of the latest :data:`HISTORY_REPORTS`, each weighing half as much every
    :data:`HISTORY_HALF_LIFE_H` hours before ``now`` (epoch seconds, like the ``mtime`` of
    ``reports``): a texture retried in a second pass does not move it. ``None`` below two reports.
    """
    model = TEXTURE_CACHED_S + texture_download_s(in_flight, server_req_per_s)
    samples: list[tuple[float, float]] = []
    for at, doc in reports:
        try:
            if doc.get("provider") != provider or doc.get("cancelled"):
                continue
            counts, timings = doc.get("counts") or {}, doc.get("timings") or {}
            fetched = float(counts.get("tiles_fetched") or 0.0)
            total = float(timings.get("total_s") or 0.0)
            cached = float(counts.get("tiles_cached") or 0.0) / CHUNKS_PER_TEXTURE
        except (AttributeError, TypeError, ValueError):
            continue
        if fetched < HISTORY_MIN_CHUNKS or total <= 0:
            continue
        per_texture = (total - TEXTURE_CACHED_S * cached) / (fetched / CHUNKS_PER_TEXTURE)
        if per_texture > 0 and math.isfinite(per_texture) and math.isfinite(at):
            samples.append((at, per_texture / model))
    latest = sorted(samples, reverse=True)[:HISTORY_REPORTS]
    if len(latest) < 2:
        return None
    weighted = sorted(
        (factor, 0.5 ** (max(0.0, now - at) / 3600.0 / HISTORY_HALF_LIFE_H))
        for at, factor in latest
    )
    half = sum(w for _factor, w in weighted) / 2.0
    lo, hi = SPEED_BOUNDS
    seen = 0.0
    for factor, w in weighted:
        seen += w
        if seen >= half:
            return min(hi, max(lo, factor))
    return min(hi, max(lo, weighted[-1][0]))


def _speeds(
    nodes: Sequence[NodeLike], now: float, prior: Mapping[str, float] | None = None
) -> tuple[dict[str, float], dict[str, float]]:
    """Speed factor of each group, and the weight seconds observed in it. ``prior`` is the
    speed each group's pseudo-observation is at (1 when absent: the weights as they are)."""
    wall: dict[str, float] = dict.fromkeys(SPEED_PRIOR_S, 0.0)
    weight: dict[str, float] = dict.fromkeys(SPEED_PRIOR_S, 0.0)
    for n in nodes:
        if n.weight_s <= 0 or n.hit:
            continue
        g = _group(n)
        if n.status == "done" or (n.status == "failed" and n.fraction >= FAILED_COUNTS_FROM):
            if n.wall_s > 0:
                age = now - n.ended_at if n.ended_at is not None else 0.0
                say = math.exp(-max(0.0, age) / SPEED_MEMORY_S)
                wall[g] += say * n.wall_s
                weight[g] += say * n.weight_s
        elif n.status == "running" and n.started_at is not None:
            ext = _extrapolation(n, now)
            if ext is not None:
                left, trust = ext
                say = _queue_trust(n, now, trust)
                wall[g] += say * (now - n.started_at + left)
                weight[g] += say * n.weight_s
    lo, hi = SPEED_BOUNDS
    start = prior or {}
    speeds = {
        g: min(hi, max(lo, (wall[g] + k * start.get(g, 1.0)) / (weight[g] + k)))
        for g, k in SPEED_PRIOR_S.items()
    }
    return speeds, weight


def _remaining(n: NodeLike, now: float, speed: float) -> float:
    expected = n.weight_s * speed
    if n.status != "running":
        return expected
    elapsed = now - n.started_at if n.started_at is not None else 0.0
    base = max(expected - elapsed, expected * OVERRUN_SHARE)
    ext = _extrapolation(n, now)
    if ext is None:
        return base
    left, trust = ext
    return trust * left + (1.0 - trust) * base


def estimate(
    nodes: Iterable[NodeLike],
    *,
    now: float,
    phase: str,
    declared: bool,
    declare_s: float = DECLARE_S,
    phase_started_at: float | None = None,
    slots: Mapping[str, int] | None = None,
    prior: Mapping[str, float] | None = None,
) -> Estimate:
    """Progress and time remaining of the job whose rows are ``nodes`` (module docstring).

    ``phase`` is ``data`` while phase 0 runs, else ``build``; ``declared`` tells whether the
    main graph's nodes are known yet (``phase_started_at`` is when ``build`` began); ``prior``
    the speed groups start from (:func:`line_factor` for ``textures``).
    """
    rows = list(nodes)
    if not rows:
        return Estimate(0.0, None, None, None)
    progress = weighted_progress(rows)
    speeds, observed = _speeds(rows, now, prior)
    osm_left = 0.0
    # The downloads a build runs in its graph wait on their lane, one tile after the other; a
    # tile's chain starts once its own is in (none to wait for with a stored snapshot).
    osm_waits: dict[str, float] = {}
    for n in rows:
        if n.role == "osm" and n.status not in ENDED:
            osm_left += _remaining(n, now, speeds[_group(n)])
            osm_waits[n.node.split("/", 1)[0]] = osm_left
    lanes: dict[str, list[float]] = {}
    chains: dict[tuple[str, str], float] = {}
    tile_chain: dict[str, float] = {}
    left_by_group: dict[str, float] = dict.fromkeys(SPEED_PRIOR_S, 0.0)
    for n in rows:
        if n.status in ENDED:
            continue
        g = _group(n)
        left = _remaining(n, now, speeds[g])
        left_by_group[g] += n.weight_s * (1.0 - _fraction(n))
        if n.role == "osm":
            continue  # counted above
        lanes.setdefault(n.kind or ROLE_KIND.get(n.role, "cpu"), []).append(left)
        if n.role in CHAIN_ROLES:
            parts = n.node.split("/")
            if len(parts) >= 3:
                key = (parts[0], parts[1])
                chains[key] = chains.get(key, 0.0) + left
            else:
                tile_chain[parts[0]] = tile_chain.get(parts[0], 0.0) + left
    per_slot = dict(slots or {})
    in_graph = phase != "data" and bool(osm_waits)
    waits = osm_waits if in_graph else {}
    first = min(waits.values()) if waits else 0.0
    build = 0.0
    for kind, lefts in lanes.items():
        width = max(1, int(per_slot.get(kind, 1)))
        # the images of the first tile to come cannot start before its OSM data is in
        start = first if kind == "net" else 0.0
        build = max(build, start + sum(lefts) / width, max(lefts))
    for tile, left in tile_chain.items():
        own = [v for (t, _level), v in chains.items() if t == tile]
        build = max(build, waits.get(tile, 0.0) + left + (max(own) if own else 0.0))
    for (tile, _level), left in chains.items():
        build = max(build, waits.get(tile, 0.0) + left)
    if in_graph:
        build = max(build, osm_left / max(1, int(per_slot.get(OSM_LANE, 1))))
    if phase == "data":
        eta = osm_left + declare_s + build
    elif not declared:
        since = now - phase_started_at if phase_started_at is not None else 0.0
        eta = max(0.0, declare_s - since) + build
    else:
        eta = build
    remaining_weight = sum(left_by_group.values())
    if remaining_weight > 0:
        confidence = (
            sum(
                left * observed[g] / (observed[g] + left)
                for g, left in left_by_group.items()
                if left > 0
            )
            / remaining_weight
        )
    else:
        confidence = 1.0
    band = BAND_MAX - (BAND_MAX - BAND_MIN) * confidence
    if not math.isfinite(eta) or eta > ETA_MAX_S:
        return Estimate(progress, None, None, None)
    low = eta * (1.0 - BAND_LOW_SHARE * band)
    high = eta * (1.0 + (2.0 - BAND_LOW_SHARE) * band)
    return Estimate(progress, eta, low, high, band)
