"""``osxp plan``: what a build will cost before it runs (spec ``pipeline-build.md`` section 5).

Nothing is built. The node statuses come from ``Scheduler.plan`` (keys computed as far as
the built inputs allow), the texture list from the DSF artefact when it is in the store
(exact) or from the imagery grid (every texture of the tile, plus the textures of its
``zone_list`` at their zoom levels: ``map-zones.md`` 5), the requests from the chunk store,
the time from the learnt costs and, online, from a 20-request probe of the provider.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId, texture_tiles, textures_covering
from orthostudio.imagery.providers import Provider, cache_name, tile_url
from orthostudio.net import FetchRequest, fetch_all
from orthostudio.pipeline.build import (
    DEFAULT_PER_TEXTURE_S,
    PER_TEXTURE_COST,
    TEXTURES_JSON,
    BuildEnv,
    BuildSpec,
    TileNodes,
    declare,
    make_scheduler,
    texture_jobs_from_json,
)
from orthostudio.sched import PlanEntry
from orthostudio.zones import zone_list_textures

__all__ = [
    "BC1_BYTES",
    "BC3_BYTES",
    "DEFAULT_KB_PER_REQUEST",
    "DEFAULT_REQ_PER_S",
    "Estimate",
    "NodePlan",
    "ProbeResult",
    "TileEstimate",
    "estimate",
    "probe_network",
    "render_text",
]

BC1_BYTES = 11_184_952
"""DXT1 4096² with 13 mips (``docs/benchmarks/baseline-ortho4xp.md``)."""
BC3_BYTES = 22_369_776
DEFAULT_KB_PER_REQUEST = 13.0
"""Mean Bing ZL14 body of the reference tile (57 MB / 4 352)."""
DEFAULT_REQ_PER_S = 400.0
"""Conservative sustained throughput without a probe (``net-download.md`` measured 1 000+)."""
OTHER_ARTEFACTS_GB = 0.25
"""DSF, mesh, vectors and their npz twins per tile, roughly."""
PROBE_REQUESTS = 20


@dataclass(slots=True)
class NodePlan:
    id: str
    role: str
    status: str
    seconds: float
    key: str | None


@dataclass(slots=True)
class ProbeResult:
    """``requests`` chunks fetched concurrently in ``seconds``: one round trip of the line.

    Twenty concurrent requests finish in about one latency, so ``seconds`` is the per-request
    latency of the provider seen from here; the sustained throughput of a build is then
    ``in_flight / latency`` (the fetcher keeps ``in_flight`` transfers open, 64-128 for Bing:
    measured 1 300 req/s at 0.1 s), what :attr:`throughput` reports.
    """

    requests: int
    seconds: float
    bytes: int
    errors: int
    in_flight: int = 128
    """Concurrency the build will use (the provider's ``max_in_flight``)."""
    server_req_per_s: float | None = None
    """The provider's ``server_req_per_s``. A server that limits itself answers slower as the load
    grows (Esri Clarity: 0.30 s at 128 requests at once, 0.56 s at 256), so ``in_flight`` over the
    latency of a probe of twenty requests overstates it."""

    @property
    def req_per_s(self) -> float:
        """Rate of the probe itself (a lower bound: one round of ``requests``)."""
        return self.requests / self.seconds if self.seconds > 0 else 0.0

    @property
    def latency_s(self) -> float:
        return self.seconds

    @property
    def throughput(self) -> float:
        """Estimated sustained req/s at ``in_flight`` concurrency, capped at 2 000 and at the
        server's own rate (:attr:`server_req_per_s`)."""
        if self.seconds <= 0:
            return 0.0
        rate = min(2000.0, max(self.req_per_s, self.in_flight / self.seconds))
        if self.server_req_per_s is not None and self.server_req_per_s > 0:
            rate = min(rate, self.server_req_per_s)
        return rate

    @property
    def kb_per_request(self) -> float:
        good = max(1, self.requests - self.errors)
        return self.bytes / good / 1000.0

    @property
    def mb_per_s(self) -> float:
        return self.bytes / 1e6 / self.seconds if self.seconds > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "seconds": round(self.seconds, 3),
            "bytes": self.bytes,
            "errors": self.errors,
            "in_flight": self.in_flight,
            "req_per_s": round(self.req_per_s, 1),
            "latency_s": round(self.latency_s, 3),
            "throughput_req_per_s": round(self.throughput, 1),
            "kb_per_request": round(self.kb_per_request, 2),
            "mb_per_s": round(self.mb_per_s, 3),
        }


@dataclass(slots=True)
class TileEstimate:
    tile: str
    provider: str
    zl: int
    nodes: list[NodePlan]
    textures_total: int
    textures_exact: bool
    textures_masked: int | None
    requests: int
    download_mb: float
    dds_gb: float
    compute_s: float
    network_s: float
    notes: list[str] = field(default_factory=list)
    textures_zones: int = 0
    """Textures the tile's zones add on top of its own, included in ``textures_total`` (upper
    bound only: an exact count, read from the DSF, already holds them)."""

    @property
    def hits(self) -> int:
        return sum(1 for n in self.nodes if n.status == "hit")

    @property
    def builds(self) -> int:
        return sum(1 for n in self.nodes if n.status != "hit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile": self.tile,
            "provider": self.provider,
            "zl": self.zl,
            "nodes": [
                {
                    "id": n.id,
                    "role": n.role,
                    "status": n.status,
                    "seconds": round(n.seconds, 2),
                    "key": n.key,
                }
                for n in self.nodes
            ],
            "hits": self.hits,
            "builds": self.builds,
            "textures": {
                "total": self.textures_total,
                "exact": self.textures_exact,
                "masked": self.textures_masked,
                "zones": self.textures_zones,
            },
            "requests": self.requests,
            "download_mb": round(self.download_mb, 1),
            "dds_gb": round(self.dds_gb, 3),
            "compute_s": round(self.compute_s, 1),
            "network_s": round(self.network_s, 1),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class Estimate:
    tiles: list[TileEstimate]
    probe: ProbeResult | None
    store_root: str
    workers: int
    disk_free_gb: float
    disk_needed_gb: float
    per_texture_s: float
    req_per_s: float
    kb_per_request: float

    @property
    def disk_ok(self) -> bool:
        return self.disk_free_gb > 2 * self.disk_needed_gb

    @property
    def compute_s(self) -> float:
        return sum(t.compute_s for t in self.tiles)

    @property
    def network_s(self) -> float:
        return sum(t.network_s for t in self.tiles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "store_root": self.store_root,
            "workers": self.workers,
            "tiles": [t.to_dict() for t in self.tiles],
            "probe": None if self.probe is None else self.probe.to_dict(),
            "assumptions": {
                "per_texture_s": round(self.per_texture_s, 3),
                "req_per_s": round(self.req_per_s, 1),
                "kb_per_request": round(self.kb_per_request, 2),
            },
            "compute_s": round(self.compute_s, 1),
            "network_s": round(self.network_s, 1),
            "disk_free_gb": round(self.disk_free_gb, 1),
            "disk_needed_gb": round(self.disk_needed_gb, 2),
            "disk_ok": self.disk_ok,
        }


# -- probe ---------------------------------------------------------------------------------------


def probe_network(
    provider: Provider, texture: TextureId, *, requests: int = PROBE_REQUESTS
) -> ProbeResult:
    """Fetch ``requests`` chunks of ``texture`` (not stored) and measure the line."""
    tiles = texture_tiles(texture)[:requests]
    reqs = [
        FetchRequest(
            key=i,
            url=tile_url(provider, x, y, texture.zl),
            headers=dict(provider.headers),
            host_group=provider.code,
        )
        for i, (x, y) in enumerate(tiles)
    ]
    t0 = time.perf_counter()
    # No rate here on purpose: a probe measures the line, and pacing it measures our own
    # pacing. Passing the provider's ceiling made every group start at a quarter of it and the
    # probe report a quarter of the truth, so an online estimate came out three to four times
    # too long (found in review, 2026-09-24). Twenty requests is what the window already bounds.
    results = fetch_all(reqs, max_in_flight=len(reqs), start_in_flight=len(reqs), hedge_after_s=2.0)
    seconds = time.perf_counter() - t0
    errors = sum(1 for r in results if r.error is not None or r.status != 200)
    size = sum(len(r.body) for r in results if r.error is None and r.status == 200)
    return ProbeResult(
        len(reqs), seconds, size, errors, provider.max_in_flight, provider.server_req_per_s
    )


# -- estimate ------------------------------------------------------------------------------------


def _requests_for(store: ChunkStore, textures: Sequence[TextureId]) -> int:
    total = 0
    for t in textures:
        try:
            container = store.read(t)
        except OsxpError:
            container = None
        if container is None:
            total += 256
        else:
            total += len(container.indices(ChunkStatus.ERROR))
    return total


def _tile_textures(
    env: BuildEnv, nodes: TileNodes, entries: dict[str, PlanEntry]
) -> tuple[list[TextureId], bool, int | None, int]:
    """``(textures, exact, masked, zone textures)`` of one tile.

    Exact when the DSF is in the store (its texture list, zones included). Otherwise an upper bound:
    every texture of the tile at its level, then, for each ``zone_list`` entry, the textures at the
    entry's level whose square overlaps it (``orthostudio.zones.zone_list_textures``) that are not
    already counted.
    """
    spec = nodes.spec
    dsf_entry = entries.get(nodes.dsf.id)
    if dsf_entry is not None and dsf_entry.status == "hit" and dsf_entry.key is not None:
        path = env.store.path(dsf_entry.key) / TEXTURES_JSON
        if path.is_file():
            jobs = texture_jobs_from_json(path)
            masked = sum(1 for j in jobs if j.has_sea)
            return [j.texture for j in jobs], True, masked, 0
    lat, lon = spec.tile.lat, spec.tile.lon
    textures = textures_covering(lat + 1, lon, lat, lon + 1, spec.zl, spec.provider)
    zone_list = spec.config.get("zone_list") or ()
    extra = sorted(zone_list_textures(zone_list, spec.tile) - set(textures)) if zone_list else []
    return textures + extra, False, None, len(extra)


def estimate(
    specs: Sequence[BuildSpec],
    *,
    online: bool = False,
    env: BuildEnv | None = None,
    probe: ProbeResult | None = None,
) -> Estimate:
    """Plan the build of ``specs`` (spec section 5); ``online`` runs the 20-request probe."""
    env = env if env is not None else BuildEnv.create(specs)
    scheduler = make_scheduler(env.store, env, cpu_in_threads=True)
    graphs = declare(specs, scheduler, env, planning=True)
    entries = {e.node_id: e for e in scheduler.plan([g.target.id for g in graphs])}
    per_texture_entry = scheduler.costs.entry(PER_TEXTURE_COST)
    per_texture = per_texture_entry.ewma if per_texture_entry else DEFAULT_PER_TEXTURE_S
    chunks = ChunkStore(
        env.chunks_root, folders={code: cache_name(p) for code, p in env.registry.items()}
    )  # where a build would look, a source of the user's under its address too

    tiles: list[TileEstimate] = []
    first_probe_texture: tuple[Provider, TextureId] | None = None
    for g in graphs:
        spec = g.spec
        plans = [
            NodePlan(node.id, role, e.status, e.seconds, e.key)
            for role, node in g.by_role.items()
            if (e := entries.get(node.id)) is not None
        ]
        textures, exact, masked, zone_textures = _tile_textures(env, g, entries)
        notes: list[str] = []
        if not exact:
            notes.append(
                "texture count is the whole tile (no DSF in the store yet): sea-only cells "
                "produce no texture and every texture is counted as DXT1"
            )
        if zone_textures:
            notes.append(
                f"{zone_textures} texture(s) added for the zones at their zoom levels: every "
                "texture a zone overlaps, while the tile's own textures under the zones stay "
                "counted (an upper bound)"
            )
        requests = _requests_for(chunks, textures)
        if requests and first_probe_texture is None:
            for t in textures:
                c = chunks.read(t) if chunks.has(t) else None
                if c is None or c.indices(ChunkStatus.ERROR):
                    first_probe_texture = (env.provider(t.provider), t)
                    break
        tex_entry = entries.get(g.textures.id)
        textures_build = tex_entry is None or tex_entry.status != "hit"
        compute = sum(p.seconds for p in plans if p.status != "hit" and p.role != "textures")
        if textures_build:
            compute += len(textures) * per_texture
        n_masked = masked or 0
        dds_bytes = n_masked * BC3_BYTES + (len(textures) - n_masked) * BC1_BYTES
        tiles.append(
            TileEstimate(
                tile=spec.tile.name,
                provider=spec.provider,
                zl=spec.zl,
                nodes=plans,
                textures_total=len(textures),
                textures_exact=exact,
                textures_masked=masked,
                requests=requests if textures_build else 0,
                download_mb=0.0,
                dds_gb=dds_bytes / 1e9 if textures_build else 0.0,
                compute_s=compute,
                network_s=0.0,
                notes=notes,
                textures_zones=zone_textures,
            )
        )
    if probe is None and online and first_probe_texture is not None:
        provider, texture = first_probe_texture
        probe = probe_network(provider, texture)
    kb = probe.kb_per_request if probe is not None and probe.bytes else DEFAULT_KB_PER_REQUEST
    probed = probe is not None and probe.throughput > 0
    req_per_s = probe.throughput if probed and probe is not None else DEFAULT_REQ_PER_S
    for te in tiles:
        te.download_mb = te.requests * kb / 1000.0
        te.network_s = te.requests / req_per_s if req_per_s > 0 else 0.0
    try:
        free = shutil.disk_usage(env.store_root).free / 1e9
    except OSError:
        free = 0.0
    needed = sum(t.dds_gb + t.download_mb / 1000.0 for t in tiles)
    needed += OTHER_ARTEFACTS_GB * sum(1 for t in tiles if t.builds)
    return Estimate(
        tiles=tiles,
        probe=probe,
        store_root=str(env.store_root),
        workers=env.workers,
        disk_free_gb=free,
        disk_needed_gb=needed,
        per_texture_s=per_texture,
        req_per_s=req_per_s,
        kb_per_request=kb,
    )


def _fmt_s(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    m, s = divmod(round(seconds), 60)
    return f"{m} min {s:02d} s"


def render_text(est: Estimate) -> str:
    """The two-line summary per tile of spec section 5 (plus disk)."""
    lines: list[str] = []
    for t in est.tiles:
        exact = "exact" if t.textures_exact else "upper bound"
        masked = "" if t.textures_masked is None else f", {t.textures_masked} masked"
        if t.textures_zones:
            masked += f", {t.textures_zones} for the zones"
        lines.append(
            f"{t.tile} {t.provider}{t.zl}: {t.textures_total} textures ({exact}{masked}), "
            f"{t.requests} requests ({t.download_mb:.0f} MB), {t.dds_gb:.2f} GB of DDS; "
            f"{t.hits} node(s) in the store, {t.builds} to build"
        )
        source = (
            f"probe: {est.probe.latency_s * 1000:.0f} ms latency, ~{est.req_per_s:.0f} req/s at "
            f"{est.probe.in_flight} in flight, {est.kb_per_request:.0f} kB/req"
            if est.probe is not None
            else f"assumed {est.req_per_s:.0f} req/s, {est.kb_per_request:.0f} kB/req; "
            "--online probes"
        )
        lines.append(f"  network: your line   ~{_fmt_s(t.network_s)} ({source})")
        parts = [
            f"{p.role} {_fmt_s(p.seconds)}"
            for p in t.nodes
            if p.status != "hit" and p.role != "textures"
        ]
        if any(p.role == "textures" and p.status != "hit" for p in t.nodes):
            parts.append(f"textures {t.textures_total} x {est.per_texture_s:.2f} s")
        detail = ", ".join(parts) if parts else "everything is in the store"
        lines.append(
            f"  compute: your Mac    ~{_fmt_s(t.compute_s)} ({est.workers} workers; {detail})"
        )
        for note in t.notes:
            lines.append(f"  note: {note}")
    disk = (
        f"disk: {est.disk_free_gb:.0f} GB free at {est.store_root}, "
        f"{est.disk_needed_gb:.2f} GB needed"
    )
    lines.append(disk if est.disk_ok else disk + "  (LOW: free space first)")
    return "\n".join(lines)
