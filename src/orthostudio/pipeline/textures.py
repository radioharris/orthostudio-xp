"""Textures of one tile: download the missing tiles, encode what the store lacks, write ``.ter``.

Spec: ``docs/specs/pipeline-textures.md``. The domain rules live in ``orthostudio.imagery``,
``orthostudio.net``, ``orthostudio.textures`` and ``orthostudio.tilefiles``; this module only
composes them:

- plan: read the chunk containers, decide the mask of every texture, key what is complete;
- fetch: one ``Fetcher`` for the run, requests in texture order, containers written as they
  complete, parent tiles and corrupted bodies fetched in further rounds, and the chunks whose
  failure says "try later" asked again in the spaced rounds of a second pass;
- encode: one texture per worker process through the ``texture.dds`` graph rule; hits are
  published by the parent process without touching the pool;
- publish: hard link of the artefact into ``textures/``, ``.ter`` files (and the external
  border mask when masks are not imprinted) into ``terrain/`` and ``textures/``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import errno
import io
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from importlib import resources
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from orthostudio.errors import OsxpError, disk_trouble
from orthostudio.fsutil import atomic_link_or_copy, atomic_write_bytes, atomic_write_text
from orthostudio.graph import Executor, InputRef, Source, Store, digest_bytes
from orthostudio.imagery.chunks import (
    ChunkContainer,
    ChunkEntry,
    ChunkStatus,
    ChunkStore,
    normalize_content_type,
    now_unix,
)
from orthostudio.imagery.grid import TextureId, texture_name, texture_tiles
from orthostudio.imagery.providers import Provider, is_placeholder, tile_url
from orthostudio.net import Fetcher, FetchRequest, FetchResult, FetchStats
from orthostudio.pipeline.home import default_chunks_root, default_store_root
from orthostudio.pipeline.parents import (
    ParentCache,
    ParentKey,
    ParentTile,
    parents_blob,
    resolve_parents,
)
from orthostudio.pipeline.rule import (
    BuildInfo,
    TextureDdsParams,
    dds_key,
    dds_node,
    take_build_info,
)
from orthostudio.textures.assemble import GRID, image_body_complete
from orthostudio.textures.encode import available_encoders, find_nvcompress
from orthostudio.textures.imprint import (
    MASK_THRESHOLD,
    load_mask,
    mask_cell,
    mask_crop_raw,
    needs_mask,
)
from orthostudio.textures.ter import (
    WATER_TRANSITION_PNG,
    TerKind,
    TerParams,
    border_mask_filename,
    ter_center,
    ter_filename,
    ter_text,
    texture_dds_name,
)

__all__ = [
    "CORRUPT_RETRIES",
    "RETRYABLE_CODES",
    "RETRYABLE_STATUSES",
    "SECOND_PASS_MAX_S",
    "SECOND_PASS_PAUSES_S",
    "ProgressSnapshot",
    "TextureJob",
    "TextureOutcome",
    "TexturesReport",
    "TexturesSpec",
    "build_textures",
    "chunk_entry_for",
    "default_workers",
    "merge_jobs",
    "publish_file",
    "write_border_mask",
    "write_report",
    "write_ter_files",
]

log = logging.getLogger("orthostudio.pipeline.textures")

REPORT_NAME = "osxp_textures.json"
_PROGRESS_PERIOD_S = 0.5
_PROGRESS_LINE_PERIOD_S = 5.0
_DEFAULT_ENCODE_S = 0.4
_PLAN_BATCH = 32

POOL_CLOSE_S = 20.0
WORKER_END_S = 2.0
"""How long an encoder is given to honour the signal to end before it is killed."""
"""How long the encoders are given to close at the end of the step before they are taken down.

Closing a pool joins its processes, and one that does not come back holds that line for ever: the
step stayed at 99 %, every file written, Assembly never starting, and stopping the build left the
Python processes running for the user to find in the task manager and kill by hand (a user,
2026-09-23). Twenty seconds is far more than a worker needs to notice it has nothing left to do.
"""

MAX_ENCODE_ROPE_S = 1800.0
"""The most a texture may ever be waited for. Without a ceiling the rope follows the measured
mean, and one pathological encode dragged it into the hours."""

ENCODE_TIMEOUT_S = 600.0
"""How long one texture's worker may take before the build gives up on it.

A texture encodes in a second or so, and this is not a budget but a rope end: a worker that never
answers used to hold the whole step, with nothing in flight and nothing said, until the app was
quit (a user, 2026-09-23). Past this, the texture is one failed texture among many and the build
goes on."""
CORRUPT_RETRIES = 2
"""Extra requests for a 200 body that is not a complete image (Ortho4XP ``max_baddata_retries``)."""

CORRUPTED = "IMG_TILE_CORRUPTED"

SECOND_PASS_PAUSES_S: tuple[float, ...] = (5.0, 15.0, 45.0)
"""Pause before each round of the second pass, one round per value (spec section 4.1).

Seen on 2026-09-13 (Bing ZL16, 212 000 requests): five chunks got no answer to any of their four
transfers for 43 s while their 255 neighbours arrived within the same second, and three tiles
were lost for them. Retries spaced by a second cannot outlive such a stall. Rounds started once
the rest of the tile is done and spaced x3 ask a few stuck chunks again about 5 s, 21-41 s and
66-107 s after the end of the first pass (the upper bounds when every round stalls for its whole
timeout). The bound of the whole pass, however many chunks wait, is ``SECOND_PASS_MAX_S``."""

SECOND_PASS_MAX_S = 180.0
"""Wall time of the whole second pass of a run, pauses, probes and rounds included (spec 4.1).

The textures node holds the build's single network slot meanwhile, so other tiles wait for it.
Three rounds that stall for their whole timeout need 65 s of pauses plus 3 x 21 s, about 130 s;
180 s keeps that schedule whole with a margin for probes and writes, and is about twice the
first pass of a ZL16 tile on the reference line (65-110 s). Retries that do not converge in
that time, after an outage that failed thousands of chunks for example, stop there: the chunks
left stay ``ERROR``, and the next run fetches only them."""

SECOND_PASS_IN_FLIGHT = 8
"""Floor of a round's concurrency: the floor of the AIMD window (``net-download.md`` R2)."""

SECOND_PASS_WAVES = 4
"""A round sends its chunks in about this many waves: ``pending // 4`` in flight, at least the
floor and at most half the provider's ceiling. At a fixed 8, 20 000 chunks failed by an outage
would take 2 500 waves (about 200 s at the 80 ms p50 of Bing) per round; at 64, about 25 s."""

SECOND_PASS_ATTEMPTS = 2
"""Fetcher attempts per chunk within a round (a transfer and its hedge, or one quick retry):
the rounds are the spaced retries."""

SECOND_PASS_PROBES = 2
"""Tiles already answered, asked again before each round. When none is answered the provider
or the line is down: the second pass stops instead of waiting for the next round."""

SECOND_PASS_GIVE_UP_AFTER = 8
"""Failures, without a single usable answer, after which a round is abandoned (the chunks not
asked yet go first in the next round)."""

RETRYABLE_CODES = frozenset({"NET_TIMEOUT", "NET_CONNECTION_FAILED", "NET_RATE_LIMITED"})
"""Failures that get the second pass: no answer at all, or a 429 beyond the fetcher's budget."""

RETRYABLE_STATUSES = frozenset({403, 408, 425, 502, 503, 504})
"""Statuses that get the second pass too: a gateway or its upstream failed (502, 504), the server
said it is unavailable for now (503), it gave up waiting for the request (408) or asked for it
later (425), and **403**, which is how a server that blocks a caller it finds too eager usually
says so. A user's own EOX source served two textures, then answered four thousand chunks in eight
seconds carrying no bytes, and nothing was asked again because a 4xx was final (2026-09-24). A
403 that is a plain refusal costs the bounded rounds of one pass and then says the same thing,
with how many rounds it took. A 500 is the server's own answer for that URL, already asked
``max_attempts`` times, and a 401 or a 451 no waiting changes: those stay final."""

_ANSWERS = (ChunkStatus.OK, ChunkStatus.MISSING, ChunkStatus.PLACEHOLDER)
_FAILURE_DETAILS = 8
"""Failure records kept per texture: the report shows them, and a provider outage at ZL18
(900 000 failed chunks) must not keep a record and a curl message per chunk."""


def _retryable(code: str, status: int) -> bool:
    """The failure says "try later" (``RETRYABLE_CODES``, ``RETRYABLE_STATUSES``)."""
    return code in RETRYABLE_CODES or (
        code in ("NET_SERVER_ERROR", "NET_UNEXPECTED_STATUS") and status in RETRYABLE_STATUSES
    )


# --- public data types -------------------------------------------------------------------------

KindLike = TerKind | str


def _kind(value: KindLike) -> TerKind:
    return value if isinstance(value, TerKind) else TerKind(str(value))


@dataclass(frozen=True, slots=True)
class TextureJob:
    """One texture of the tile and the terrain kinds the DSF references for it."""

    texture: TextureId
    kinds: tuple[TerKind, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", tuple(_kind(k) for k in self.kinds))

    @property
    def has_sea(self) -> bool:
        return any(k.tri_type == 2 for k in self.kinds)


def merge_jobs(jobs: Iterable[TextureJob]) -> list[TextureJob]:
    """One job per texture, kinds merged in first-seen order (spec section 2)."""
    merged: dict[TextureId, list[TerKind]] = {}
    for job in jobs:
        kinds = merged.setdefault(job.texture, [])
        for k in job.kinds:
            if k not in kinds:
                kinds.append(k)
    return [TextureJob(t, tuple(kinds)) for t, kinds in merged.items()]


ProgressFn = Callable[["ProgressSnapshot"], None]


@dataclass(slots=True)
class TexturesSpec:
    """Everything :func:`build_textures` needs (spec section 2).

    ``mask_zl`` is the single source of the mask zoom level: the pipeline copies it into
    ``ter_params.mask_zl`` so that the mask crop and the ``LOAD_CENTER_BORDER`` of the ``.ter``
    can never disagree. ``second_pass_pauses_s`` spaces the rounds of the second pass (section
    4.1), an empty tuple turns it off, and ``second_pass_max_s`` bounds its wall time.
    """

    lat: int
    lon: int
    provider: Provider
    zl: int
    jobs: list[TextureJob]
    out_dir: Path
    chunks_root: Path = field(default_factory=default_chunks_root)
    store_root: Path = field(default_factory=default_store_root)
    mask_lookup: Callable[[int, int], Path | None] | None = None
    mask_zl: int = 14
    ter_params: TerParams = field(default_factory=TerParams)
    sea_texture_blur: float = 0.0
    clean_halo: bool = False
    photo_brightness: float = 0.0
    photo_contrast: float = 0.0
    photo_saturation: float = 0.0
    """Colours of the photo, applied to every texture of the tile (``textures/colour.py``)."""
    photo_shapes_by_texture: Mapping[TextureId, tuple[Any, ...]] = field(default_factory=dict)
    """The zones of the page that reach into a texture, ring in that texture's own pixels
    (``build.photo_zone_shapes``). Their colours are applied to the pixels inside the ring, over
    the three above; a texture no zone reaches keeps the tile's colours alone."""
    idle: Callable[[bool], None] | None = None
    """Told that the run holds its slot without using it, during the pauses of the second pass
    (``NodeContext.set_idle``): a build has one network slot, and a tile waiting for a handful of
    stuck pieces left the whole batch's line idle (a user, 2026-09-18)."""
    workers: int | None = None
    encoder: str = "auto"
    mip_mode: str = "gamma22"
    refine_passes: int = 0
    max_in_flight: int | None = None
    start_in_flight: int | None = None
    """``None``: start at the ceiling (``max_in_flight``, the provider's measured one). From 64,
    one more per round, Esri Clarity took 90 s and half of a tile's pieces to reach its 192
    (2026-09-15); the AIMD still lowers a window that is too wide."""
    hedge_after_s: float = 1.0
    timeout_s: float = 20.0
    max_attempts: int = 4
    req_per_s: float | None = None
    """``None``: the provider's own ``server_req_per_s``, the requests a second it takes."""
    second_pass_pauses_s: tuple[float, ...] = SECOND_PASS_PAUSES_S
    second_pass_max_s: float = SECOND_PASS_MAX_S
    parent_levels: int = 5
    link: bool = True
    progress: ProgressFn | None = None
    quiet: bool = False
    cancel: threading.Event | None = None
    fsync: bool = True


@dataclass(slots=True)
class TextureOutcome:
    """What happened to one texture."""

    name: str
    texture: TextureId
    kinds: tuple[str, ...]
    status: str
    """``built`` | ``hit`` | ``incomplete`` | ``failed`` | ``cancelled``."""
    fmt: str | None = None
    key: str | None = None
    digest: str | None = None
    dds_path: str | None = None
    fetched: int = 0
    cached: int = 0
    placeholders: int = 0
    not_found: int = 0
    errors: int = 0
    retried: int = 0
    from_fallback: int = 0
    unfilled: int = 0
    corrupted: int = 0
    seconds: float = 0.0
    error: dict[str, Any] | None = None
    second_pass: int = 0
    """Chunks that failed with a "try later" error and waited for the second pass."""
    recovered: int = 0
    """Of those, the chunks the second pass obtained."""
    failures: list[dict[str, Any]] = field(default_factory=list)
    """What the first chunks that failed in the run met (code, HTTP status, transfers, curl's
    error line, passes, recovered or not), at most eight."""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["texture"] = list(self.texture)
        return d


@dataclass(slots=True)
class ProgressSnapshot:
    """State of the run for a progress display."""

    tiles_done: int
    tiles_total: int
    parents_done: int
    parents_total: int
    req_per_s: float
    bytes: int
    in_flight: int
    hedges: int
    retries: int
    net_errors: int
    throttled: bool
    textures_total: int
    built: int
    hits: int
    failed: int
    incomplete: int
    encoding: int
    eta_s: float | None
    elapsed_s: float
    second_pass_chunks: int = 0
    """Chunks waiting for the second pass (or in its current round)."""
    second_pass_round: int = 0
    """Round in progress or about to start; 0 outside the second pass."""
    second_pass_rounds: int = 0
    """Rounds the second pass may run; 0 when nothing waits for it."""
    second_pass_wait_s: float = 0.0
    """Seconds left in the pause before the next round."""
    pushed_back: bool = False
    """A source answered 429 and the step is waiting out the delay it asked for. Not
    ``throttled``, which is mostly our own window being lowered and is on throughout a healthy
    download (a user's own screen, 2026-09-24)."""


@dataclass(slots=True)
class TexturesReport:
    """Result of :func:`build_textures` (spec section 2)."""

    tile: str
    provider: str
    zl: int
    out_dir: str
    chunks_root: str
    store_root: str
    encoder: str
    encoder_version: str
    workers: int
    outcomes: list[TextureOutcome] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    network: dict[str, float] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    cancelled: bool = False
    notes: list[str] = field(default_factory=list)
    """Free-text remarks of the caller."""

    @property
    def missing(self) -> list[TextureOutcome]:
        return [o for o in self.outcomes if o.status not in ("built", "hit")]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.cancelled

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "tile": self.tile,
            "provider": self.provider,
            "zl": self.zl,
            "out_dir": self.out_dir,
            "chunks_root": self.chunks_root,
            "store_root": self.store_root,
            "encoder": self.encoder,
            "encoder_version": self.encoder_version,
            "workers": self.workers,
            "cancelled": self.cancelled,
            "counts": dict(self.counts),
            "network": dict(self.network),
            "timings": dict(self.timings),
            "errors": list(self.errors),
            "notes": list(self.notes),
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def write_report(report: TexturesReport, path: Path) -> None:
    atomic_write_text(Path(path), json.dumps(report.to_dict(), indent=1, sort_keys=True))


# --- helpers shared by the parent process and the workers --------------------------------------


def default_workers() -> int:
    """``cpu_count() - 2``, at least 1."""
    return max(1, (os.cpu_count() or 2) - 2)


def resolve_encoder(encoder: str) -> tuple[str, str]:
    """``(encoder, version)`` actually used; raises ``TEX_ENCODER_UNAVAILABLE``."""
    available = available_encoders()
    chosen = available[0] if encoder == "auto" and available else encoder
    if chosen not in available:
        raise OsxpError(
            "TEX_ENCODER_UNAVAILABLE",
            context={"reason": f"requested {encoder!r}, available {available or 'none'}"},
        )
    if chosen == "ispc":
        import ispc_texcomp

        return chosen, str(getattr(ispc_texcomp, "__version__", "unknown"))
    binary = find_nvcompress()
    return chosen, f"nvcompress:{digest_bytes(binary.read_bytes())[:16] if binary else 'unknown'}"


def publish_file(src: Path, dest: Path, *, link: bool = True) -> None:
    """Put ``src`` at ``dest`` atomically: hard link when possible, copy otherwise."""
    atomic_link_or_copy(src, dest, link=link)


def write_ter_files(
    out_dir: Path, t: TextureId, kinds: Iterable[TerKind], params: TerParams
) -> list[Path]:
    """Write ``terrain/<name>[_kind].ter`` for every kind (``textures-ter.md``)."""
    lat_med, lon_med = ter_center(t)
    written: list[Path] = []
    for kind in kinds:
        path = Path(out_dir) / "terrain" / ter_filename(t, kind)
        atomic_write_text(
            path,
            ter_text(t, kind, lat_med=lat_med, lon_med=lon_med, params=params),
            encoding="ascii",
        )
        written.append(path)
    return written


def write_border_mask(out_dir: Path, t: TextureId, crop: np.ndarray) -> Path:
    """Write ``textures/<y>_<x>_ZL<zl>.png``: the **raw** mask crop, ``4096 // factor`` px.

    Ortho4XP saves the crop returned by ``needs_mask`` (``O4_DSF_Utils.py:737-742``) and the
    ``.ter`` declares that resolution in ``LOAD_CENTER_BORDER``; the file is never resampled.
    """
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(crop), mode="L").save(buf, format="PNG")
    return atomic_write_bytes(Path(out_dir) / "textures" / border_mask_filename(t), buf.getvalue())


def ensure_water_transition(out_dir: Path) -> Path:
    """Copy ``water_transition.png`` into ``textures/`` once (Ortho4XP
    ``O4_DSF_Utils.py:308-317``)."""
    dest = Path(out_dir) / "textures" / WATER_TRANSITION_PNG
    if not dest.is_file():
        data = (
            resources.files("orthostudio.pipeline")
            .joinpath("data", WATER_TRANSITION_PNG)
            .read_bytes()
        )
        atomic_write_bytes(dest, data)
    return dest


def chunk_entry_for(
    provider: Provider, r: FetchResult, *, fetched_at: int | None = None
) -> ChunkEntry:
    """Classify one answer into a container entry (spec section 4).

    An ``ERROR`` entry carries its reason code in ``content_type`` (persisted by the
    container format, ``imagery-chunks.md`` section 3).
    """
    when = now_unix() if fetched_at is None else fetched_at
    if r.error is not None:
        return ChunkEntry(ChunkStatus.ERROR, b"", r.error, when)
    if r.status == 200:
        if is_placeholder(provider, r.headers, r.body) is not None:
            return ChunkEntry(ChunkStatus.PLACEHOLDER, b"", "", when)
        ctype = normalize_content_type(r.headers.get("content-type", ""))
        if ctype and not ctype.startswith("image/"):
            return ChunkEntry(ChunkStatus.ERROR, b"", "IMG_BAD_CONTENT_TYPE", when)
        if not image_body_complete(r.body):
            return ChunkEntry(ChunkStatus.ERROR, b"", CORRUPTED, when)
        return ChunkEntry(ChunkStatus.OK, r.body, ctype or "image/jpeg", when)
    if r.status == 404:
        return ChunkEntry(ChunkStatus.MISSING, b"", "", when)
    return ChunkEntry(ChunkStatus.ERROR, b"", "NET_UNEXPECTED_STATUS", when)


def _error_dict(exc: OsxpError, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "code": exc.code,
        "severity": str(exc.severity),
        "message": exc.message,
        "remedy": exc.remedy,
        "context": dict(exc.context),
    }
    d.update(extra)
    return d


def _unlink_quietly(path: Path) -> None:
    """Remove a file that must not be published; a path already gone is fine."""
    with contextlib.suppress(OSError):
        path.unlink()


def _coded(exc: BaseException, *, path: Path | None = None) -> OsxpError:
    """The ``OsxpError`` for an exception escaping a pipeline task."""
    if isinstance(exc, OsxpError):
        return exc
    if isinstance(exc, OSError):
        where = str(path) if path is not None else str(getattr(exc, "filename", "") or "")
        if exc.errno == errno.EMFILE:
            # not a permission: the process ran out of open files ("Fix permissions on the path",
            # with no path, was all a user read, 2026-09-15)
            return OsxpError(
                "SYS_WRITE_FAILED",
                context={"path": where, "reason": str(exc)},
                message=f"Too many files were open at once in OrthoStudio XP ({exc}).",
                remedy="Retry the missing ones; if it happens again, quit OrthoStudio XP and open "
                "it again.",
            )
        # a full disk and a read-only drive say so for themselves, here as everywhere else
        known = disk_trouble(exc)
        if known is not None:
            known.__cause__ = exc
            return known
        return OsxpError("SYS_WRITE_FAILED", context={"path": where, "reason": str(exc)})
    return OsxpError("SYS_INTERNAL_ERROR", context={"type": type(exc).__name__, "detail": str(exc)})


# --- worker -------------------------------------------------------------------------------------


@dataclass(slots=True)
class WorkerJob:
    """Everything a worker needs; small (the chunks are read from the store path)."""

    texture: TextureId
    params: TextureDdsParams
    chunks_path: Path
    chunks_digest: str
    mask_path: Path | None
    mask_digest: str | None
    parents_blob: bytes | None
    parents_digest: str | None
    store_root: Path
    dest: Path
    link: bool
    tmp_dir: Path
    fsync: bool


@dataclass(slots=True)
class WorkerResult:
    key: str
    digest: str
    status: str
    size: int
    seconds: float
    info: BuildInfo | None
    error: dict[str, Any] | None = None


_WORKER_STORE: Store | None = None


def _worker_init() -> None:
    """Workers ignore Ctrl-C: the parent cancels them through the pool (spec section 8)."""
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, signal.SIG_IGN)


def _worker_store(root: Path, fsync: bool) -> Store:
    global _WORKER_STORE
    if _WORKER_STORE is None or _WORKER_STORE.root != Path(root).expanduser().resolve():
        if _WORKER_STORE is not None:
            _WORKER_STORE.close()
        _WORKER_STORE = Store(root, fsync=fsync)
    return _WORKER_STORE


def _worker_run(job: WorkerJob) -> WorkerResult:
    """Build (or hit) the DDS of one texture and publish it. Never raises."""
    t0 = time.perf_counter()
    blob_path: Path | None = None
    try:
        store = _worker_store(job.store_root, job.fsync)
        if job.parents_blob is not None:
            job.tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="parents-", suffix=".blob", dir=job.tmp_dir)
            with os.fdopen(fd, "wb") as f:
                f.write(job.parents_blob)
            blob_path = Path(name)
        node = dds_node(
            job.params,
            chunks_path=job.chunks_path,
            chunks_digest=job.chunks_digest,
            mask_path=job.mask_path,
            mask_digest=job.mask_digest,
            parents_path=blob_path,
            parents_digest=job.parents_digest,
        )
        result = Executor(store).run(node).target
        info = take_build_info(result.key)
        publish_file(result.path, job.dest, link=job.link)
        return WorkerResult(
            result.key, result.digest, result.status, result.size, time.perf_counter() - t0, info
        )
    except Exception as exc:  # pickled back as data, never as an exception
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        if isinstance(cause, OsxpError):
            err = _error_dict(cause)
        elif isinstance(cause, OSError):
            err = _error_dict(_coded(cause, path=job.dest))
        else:
            err = _error_dict(
                OsxpError(
                    "TEX_ENCODE_FAILED",
                    context={
                        "texture": texture_name(job.texture),
                        "encoder": job.params.encoder,
                        "reason": f"{type(cause).__name__}: {cause}",
                    },
                )
            )
        return WorkerResult("", "", "failed", 0, time.perf_counter() - t0, None, err)
    finally:
        if blob_path is not None:
            blob_path.unlink(missing_ok=True)


# --- internal state -----------------------------------------------------------------------------


@dataclass(slots=True)
class _TexState:
    job: TextureJob
    index: int
    container: ChunkContainer
    """Bodies included until the container is written and digested; statuses only afterwards
    (the worker re-reads the file), so the parent process never holds a finished texture."""
    to_fetch: list[int]
    pending: int
    from_store: bool
    chunks_digest: str | None = None
    mask_path: Path | None = None
    mask_digest: str | None = None
    mask_crop: tuple[int, int, int] | None = None
    border_crop: np.ndarray | None = None
    """Raw mask crop to publish as the external border PNG (``imprint_masks_to_dds=False``)."""
    fail_error: dict[str, Any] | None = None
    """Decided at plan time: the texture cannot be built (sea kind without a usable mask)."""
    corrupt_retries: dict[int, int] = field(default_factory=dict)
    failures: dict[int, _ChunkFailure] = field(default_factory=dict)
    """What the first chunks that failed in the run met, recovered ones included (at most
    ``_FAILURE_DETAILS``)."""
    final_errors: int = 0
    """``ERROR`` answers of the first pass whose failure is not "try later"."""
    deferred: set[int] = field(default_factory=set)
    """Chunks waiting for the second pass; their ``ERROR`` entries are on disk meanwhile."""
    new_entries: dict[int, ChunkEntry] = field(default_factory=dict)
    """Answers of the second pass not yet written into the container on disk."""
    last_codes: dict[int, str] = field(default_factory=dict)
    """Reason of the last failed round, for waiting chunks whose reason changed."""
    rounds_asked: set[int] = field(default_factory=set)
    """Rounds of the second pass that asked for chunks of this texture."""
    parents: dict[ParentKey, ParentTile] = field(default_factory=dict)
    waiting_on: set[ParentKey] = field(default_factory=set)
    answered: set[int] = field(default_factory=set)
    outcome: TextureOutcome | None = None
    submitted_at: float = 0.0

    @property
    def texture(self) -> TextureId:
        return self.job.texture


@dataclass(slots=True)
class _ChunkFailure:
    """Why a chunk is not in its container, merged over the passes that asked for it."""

    code: str
    status: int
    transfers: int
    hedged: bool
    elapsed_s: float
    detail: str
    passes: int = 1
    """Passes that asked for the chunk: the first one and each round of the second pass."""
    recovered: bool = False

    @classmethod
    def of(cls, r: FetchResult, code: str) -> _ChunkFailure:
        return cls(code, r.status, r.attempts, r.hedged, r.elapsed, r.detail)

    def again(self, r: FetchResult, code: str) -> None:
        """One more pass failed: keep its reason, count its transfers."""
        self.code, self.status, self.detail, self.elapsed_s = code, r.status, r.detail, r.elapsed
        self.transfers += r.attempts
        self.hedged = self.hedged or r.hedged
        self.passes += 1

    def recovered_by(self, r: FetchResult) -> None:
        self.transfers += r.attempts
        self.passes += 1
        self.recovered = True

    @property
    def retryable(self) -> bool:
        return _retryable(self.code, self.status)

    def to_dict(self, chunk: int, tile: tuple[int, int]) -> dict[str, Any]:
        return {
            "chunk": chunk,
            "tile": list(tile),
            "code": self.code,
            "status": self.status,
            "transfers": self.transfers,
            "hedged": self.hedged,
            "elapsed_s": round(self.elapsed_s, 3),
            "detail": self.detail,
            "passes": self.passes,
            "recovered": self.recovered,
        }


def _answered(entry: ChunkEntry) -> bool:
    """A final answer of the provider: a body, a placeholder, or a 404 that was asked (an entry
    of a new container is ``MISSING`` with no fetch time until its answer arrives)."""
    return entry.status in _ANSWERS and (
        entry.status is not ChunkStatus.MISSING or entry.fetched_at != 0
    )


def _statuses_only(c: ChunkContainer) -> ChunkContainer:
    return ChunkContainer(
        ChunkEntry(e.status, b"", e.content_type, e.fetched_at) for e in c.entries
    )


class _Progress:
    """Stderr renderer: one updating line on a TTY, one line every 5 s otherwise."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.last_line_at = 0.0
        self.width = 0

    def __call__(self, s: ProgressSnapshot) -> None:
        now = time.monotonic()
        if not self.tty and now - self.last_line_at < _PROGRESS_LINE_PERIOD_S:
            return
        self.last_line_at = now
        line = self.render(s)
        if self.tty:
            pad = " " * max(0, self.width - len(line))
            self.stream.write("\r" + line + pad)
            self.width = len(line)
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    def finish(self, s: ProgressSnapshot) -> None:
        line = self.render(s)
        if self.tty:
            pad = " " * max(0, self.width - len(line))
            self.stream.write("\r" + line + pad + "\n")
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    @staticmethod
    def render(s: ProgressSnapshot) -> str:
        eta = "-" if s.eta_s is None else _fmt_s(s.eta_s)
        parents = f" +{s.parents_done}/{s.parents_total} parents" if s.parents_total else ""
        net = (
            f"tiles {s.tiles_done}/{s.tiles_total}{parents} {s.req_per_s:5.0f} req/s "
            f"{s.bytes / 1e6:6.1f} MB {s.in_flight:3d} in flight"
        )
        if s.hedges or s.retries or s.net_errors:
            net += f" (hedges {s.hedges}, retries {s.retries}, errors {s.net_errors})"
        if s.throttled:
            net += " throttled"
        if s.second_pass_chunks and not s.second_pass_round:
            net += f" | {s.second_pass_chunks} chunk(s) to retry"
        elif s.second_pass_chunks:
            wait = f" in {_fmt_s(s.second_pass_wait_s)}" if s.second_pass_wait_s > 0 else ""
            net += (
                f" | retrying {s.second_pass_chunks} chunk(s), round "
                f"{s.second_pass_round}/{s.second_pass_rounds}{wait}"
            )
        tex = f"textures {s.built + s.hits}/{s.textures_total} ({s.built} built, {s.hits} hit"
        if s.failed or s.incomplete:
            tex += f", {s.failed + s.incomplete} missing"
        tex += f", {s.encoding} encoding)"
        return f"[osxp] {_fmt_s(s.elapsed_s):>6} | {net} | {tex} | ETA {eta}"


def _fmt_s(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s" if seconds >= 10 else f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


# --- the pipeline -------------------------------------------------------------------------------


class _Pipeline:
    def __init__(self, spec: TexturesSpec) -> None:
        self.spec = spec
        self.provider = spec.provider
        self.zl = spec.zl
        self.mask_zl = spec.mask_zl
        self.ter_params = dataclasses.replace(spec.ter_params, mask_zl=spec.mask_zl)
        self.out_dir = Path(spec.out_dir)
        self.chunk_store = ChunkStore(spec.chunks_root, fsync=spec.fsync)
        self.parent_cache = ParentCache(spec.chunks_root, spec.provider.code, fsync=False)
        self.store = Store(spec.store_root, fsync=spec.fsync)
        self.workers = default_workers() if spec.workers is None else max(0, spec.workers)
        self.jobs = merge_jobs(spec.jobs)
        self.states: list[_TexState] = []
        self.by_index: dict[int, _TexState] = {}
        self.pool: ProcessPoolExecutor | None = None
        self.encode_slots: asyncio.Semaphore | None = None
        """At most the pool's own workers and two more are ever handed over at once, so the time
        a texture waits for a worker is bounded and the rope below measures the encoding rather
        than the queue (found in review, 2026-09-23)."""
        self.workers_seen: list[Any] = []
        """The worker processes, kept as they appear. Closing a pool sets its own ``_processes``
        to ``None``, so a pool already closed by a cancel had nothing left to take down: the user
        who pressed Stop found the same two Python processes in his task manager as before."""
        self.encode_timeouts = 0
        """Textures whose worker never answered (:data:`ENCODE_TIMEOUT_S`)."""
        self.io_tasks: set[asyncio.Task[None]] = set()
        self.encode_tasks: set[asyncio.Task[None]] = set()
        self.needed_parents: set[ParentKey] = set()
        self.attempted_parents: set[ParentKey] = set()
        self.failed_parents: set[ParentKey] = set()
        self.retry_requests: list[FetchRequest] = []
        self.waiting: set[int] = set()
        self.masks_cache: dict[Path, np.ndarray] = {}
        self.mask_digests: dict[Path, str] = {}
        self.errors: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {
            "textures": len(self.jobs),
            "built": 0,
            "hits": 0,
            "incomplete": 0,
            "failed": 0,
            "cancelled": 0,
            "tiles_total": 0,
            "tiles_cached": 0,
            "tiles_fetched": 0,
            "tiles_placeholder": 0,
            "tiles_not_found": 0,
            "tiles_error": 0,
            "tiles_retried": 0,
            "parents_fetched": 0,
            "parents_total": 0,
            "chunks_from_fallback": 0,
            "chunks_unfilled": 0,
            "chunks_corrupted": 0,
            "chunks_second_pass": 0,
            "chunks_recovered": 0,
            "second_pass_rounds": 0,
            "second_pass_capped": 0,
        }
        self.pauses = tuple(float(p) for p in spec.second_pass_pauses_s)
        if any(p < 0 for p in self.pauses):
            raise ValueError("second_pass_pauses_s must be >= 0")
        if spec.second_pass_max_s < 0:
            raise ValueError("second_pass_max_s must be >= 0")
        self.second_pass_deadline = 0.0
        self.second_pass_capped = False
        self.deadline_handle: asyncio.TimerHandle | None = None
        self.deferred: dict[tuple[int, int], None] = {}
        """Chunks waiting for the second pass, ``(texture index, chunk index)``, in round order."""
        self.round_cancel: asyncio.Event | None = None
        self.round_failures = 0
        self.round_answers = 0
        self.second_pass_round = 0
        self.second_pass_wait_until = 0.0
        self.second_pass_s = 0.0
        self.second_pass_stopped: str | None = None
        self.tiles_done = 0
        self.parents_done = 0
        self.last_stats: FetchStats | None = None
        self.net_totals: dict[str, float] = {
            "requests": 0,
            "bytes": 0,
            "hedges": 0,
            "retries": 0,
            "errors": 0,
        }
        self.encode_seconds: list[float] = []
        self.encoding = 0
        self.t0 = time.perf_counter()
        self.t_fetch_end = 0.0
        self.t_first_submit = 0.0
        self.t_last_done = 0.0
        self.stop = spec.cancel if spec.cancel is not None else threading.Event()
        self.cancel_event = asyncio.Event()
        self.cancelled = False
        self.progress: ProgressFn | None
        self.progress_renderer: _Progress | None = None
        if spec.progress is not None:
            self.progress = spec.progress
        elif spec.quiet:
            self.progress = None
        else:
            self.progress_renderer = _Progress()
            self.progress = self.progress_renderer
        self.encoder, self.encoder_version = resolve_encoder(spec.encoder)
        self.tmp_dir = self.out_dir / ".osxp-tmp"

    # -- run ---------------------------------------------------------------------------------

    async def run(self) -> TexturesReport:
        plan_s = fetch_s = 0.0
        watcher = asyncio.create_task(self._watch_cancel())
        ticker = asyncio.create_task(self._tick()) if self.progress is not None else None
        try:
            plan_t0 = time.perf_counter()
            self._prepare_dirs()
            await self._plan()
            plan_s = time.perf_counter() - plan_t0
            self._start_pool()
            fetch_t0 = time.perf_counter()
            for st in self.states:
                if st.fail_error is not None:
                    self._spawn(self._finish_unbuildable(st), st)
                elif not st.to_fetch:
                    self._container_ready(st)
            await self._fetch_all()
            self.t_fetch_end = time.perf_counter()
            fetch_s = self.t_fetch_end - fetch_t0
            await self._drain_tasks()
        except asyncio.CancelledError:
            # The task itself was cancelled (second Ctrl-C, or the caller): keep what was
            # received, mark the rest cancelled and still return a report.
            self._cancel_now()
            for task in list(self.io_tasks | self.encode_tasks):
                task.cancel()
            await asyncio.gather(*self.io_tasks, *self.encode_tasks, return_exceptions=True)
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_tasks()
        finally:
            watcher.cancel()
            if ticker is not None:
                ticker.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(watcher, *([ticker] if ticker else []), return_exceptions=True)
            try:
                # a second Ctrl-C lands here: whatever it interrupts, the store is closed and
                # the scratch is swept, which a bare ``await`` in a ``finally`` did not promise
                # (found in review, 2026-09-23)
                with contextlib.suppress(BaseException):
                    await self._close_pool()
            finally:
                self._take_workers_down()
                self.store.close()
                shutil.rmtree(self.tmp_dir, ignore_errors=True)
        report = self._report(plan_s, fetch_s)
        if self.progress_renderer is not None:
            self.progress_renderer.finish(self._snapshot())
        return report

    def request_cancel(self) -> None:
        """Ask the run to stop (thread-safe, idempotent); the cooperative path of a Ctrl-C."""
        self.stop.set()

    @property
    def cancel_requested(self) -> bool:
        return self.stop.is_set()

    def _prepare_dirs(self) -> None:
        (self.out_dir / "textures").mkdir(parents=True, exist_ok=True)
        (self.out_dir / "terrain").mkdir(parents=True, exist_ok=True)
        if any(k is TerKind.WATER_OVERLAY for job in self.jobs for k in job.kinds):
            ensure_water_transition(self.out_dir)

    # -- plan --------------------------------------------------------------------------------

    async def _plan(self) -> None:
        for job in self.jobs:
            t = job.texture
            if t.zl != self.zl or t.provider != self.provider.code:
                raise OsxpError(
                    "CFG_VALUE_INVALID",
                    context={"name": "textures", "value": texture_name(t)},
                    message=f"Texture {texture_name(t)} is not {self.provider.code}{self.zl}.",
                    remedy="Give build_textures the textures of one provider and zoom level.",
                )
        # Containers are read in a thread by batches so that progress and cancellation are
        # live during a long plan (ZL17: 800 containers of ~4 MB).
        for start in range(0, len(self.jobs), _PLAN_BATCH):
            batch = self.jobs[start : start + _PLAN_BATCH]
            if self.cancelled:
                read: list[tuple[ChunkContainer | None, OsxpError | None]] = [(None, None)] * len(
                    batch
                )
            else:
                read = await asyncio.gather(
                    *(asyncio.to_thread(self._read_container, job.texture) for job in batch)
                )
            for offset, (job, (container, exc)) in enumerate(zip(batch, read, strict=True)):
                index = start + offset
                t = job.texture
                if exc is not None:
                    self.errors.append(_error_dict(exc, texture=texture_name(t)))
                if container is None:
                    container = ChunkContainer()
                    to_fetch = list(range(len(container)))
                    from_store = False
                else:
                    to_fetch = container.indices(ChunkStatus.ERROR)
                    from_store = True
                st = _TexState(job, index, container, to_fetch, len(to_fetch), from_store)
                st.outcome = TextureOutcome(
                    texture_name(t), t, tuple(k.value for k in job.kinds), "pending"
                )
                self._decide_mask(st)
                if st.fail_error is not None:
                    st.to_fetch, st.pending = [], 0
                else:
                    st.outcome.cached = 256 - len(to_fetch) if from_store else 0
                    self.counts["tiles_cached"] += st.outcome.cached
                    self.counts["tiles_total"] += len(to_fetch)
                self.states.append(st)
                self.by_index[index] = st

    def _read_container(self, t: TextureId) -> tuple[ChunkContainer | None, OsxpError | None]:
        try:
            return self.chunk_store.read(t), None
        except OsxpError as exc:  # corrupted: discard and download again (spec chunks s. 3)
            self.chunk_store.delete(t)
            return None, exc

    def _decide_mask(self, st: _TexState) -> None:
        job, t = st.job, st.texture
        if not job.has_sea:
            return
        if self.spec.mask_lookup is None:
            self._sea_without_mask(st, "no mask lookup")
            return
        if t.zl < self.mask_zl:
            self._sea_without_mask(st, f"zoom level {t.zl} is below mask_zl {self.mask_zl}")
            return
        m_til_x, m_til_y, x0, y0, side = mask_cell(t, self.mask_zl)
        path = self.spec.mask_lookup(m_til_x, m_til_y)
        if path is None:
            self._sea_without_mask(st, f"no mask file for cell {m_til_y}_{m_til_x}")
            return
        full = self.masks_cache.get(path)
        if full is None:
            full = self.masks_cache[path] = load_mask(path)
        crop = mask_crop_raw(full, x0, y0, side)
        if not needs_mask(crop):  # the raw crop, as Ortho4XP (O4_Mask_Utils.py:56-60)
            self._sea_without_mask(st, f"mask maximum {int(crop.max())} <= {MASK_THRESHOLD}")
            return
        if not self.ter_params.imprint_masks_to_dds:
            st.border_crop = crop  # published with the .ter files, DDS stays DXT1
            return
        digest = self.mask_digests.get(path)
        if digest is None:
            digest = self.mask_digests[path] = Source.from_path(path).digest
        st.mask_path, st.mask_digest, st.mask_crop = path, digest, (x0, y0, side)

    def _sea_without_mask(self, st: _TexState, reason: str) -> None:
        """A sea terrain over an unmasked texture would hide X-Plane's water: not built."""
        name = texture_name(st.texture)
        exc = OsxpError(
            "MASK_STALE",
            context={"tile": self._tile_name(), "texture": name},
            message=(
                f"Texture {name} has a sea terrain but no usable mask ({reason}); a WET overlay "
                "over an opaque texture would hide the water, so it is not built."
            ),
            remedy="Rebuild the masks of the tile, then run again: only this texture is retried.",
        )
        st.fail_error = _error_dict(exc)

    def _tile_name(self) -> str:
        return f"{self.spec.lat:+03d}{self.spec.lon:+04d}"

    def _params(self, st: _TexState) -> TextureDdsParams:
        """Params of the recipe; what the texture does not consume is neutralised (K2).

        The imprint settings only matter for a masked texture and ``parent_levels`` only
        when parents were consulted, so an unmasked texture keeps its key when
        ``sea_texture_blur`` changes, and a complete one when the fallback depth does.
        """
        masked = st.mask_digest is not None
        return TextureDdsParams(
            provider=self.provider.code,
            zl=self.zl,
            encoder=self.encoder,
            encoder_version=self.encoder_version,
            mip_mode=self.spec.mip_mode,
            refine_passes=self.spec.refine_passes,
            mask_zl=self.mask_zl if masked else 0,
            mask_crop=st.mask_crop if masked else None,
            sea_texture_blur=self.spec.sea_texture_blur if masked else 0.0,
            clean_halo=self.spec.clean_halo if masked else False,
            parent_levels=self.spec.parent_levels if st.parents else 0,
            photo_brightness=self.spec.photo_brightness,
            photo_contrast=self.spec.photo_contrast,
            photo_saturation=self.spec.photo_saturation,
            photo_shapes=tuple(self.spec.photo_shapes_by_texture.get(st.texture, ())),
        )

    # -- pool --------------------------------------------------------------------------------

    def _start_pool(self) -> None:
        # the bound is set whether the encoders are processes or threads: with ``--workers 0``
        # every texture was handed over at once again, and the rope timed the queue rather than
        # the encoding, which is the whole point of it (found in review, 2026-09-23)
        self.encode_slots = asyncio.Semaphore(max(1, self.workers) + 2)
        if self.workers == 0:
            return
        self.pool = ProcessPoolExecutor(
            max_workers=self.workers, mp_context=get_context("spawn"), initializer=_worker_init
        )

    async def _close_pool(self) -> None:
        """Close the encoders, and take down whatever will not close.

        Nothing here may wait without an end. The step's work is done by the time this runs, so
        an encoder still holding on is not doing anything for anybody; waiting for it costs the
        user his build and leaves its processes behind when he stops it (2026-09-23).
        """
        pool, self.pool = self.pool, None
        if pool is None:
            self._take_workers_down()
            return
        # taken now, while they are still there to take: ``shutdown`` sets the pool's own list to
        # ``None``, and a cancel has usually shut it already (found in review, 2026-09-23)
        procs = getattr(pool, "_processes", None)
        if procs:
            self.workers_seen = list(procs.values())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.to_thread(pool.shutdown, True, cancel_futures=True), POOL_CLOSE_S
            )
        self._take_workers_down()

    def _take_workers_down(self) -> None:
        """End any encoder still running, politely first and then not.

        A worker blocked in a write to a drive that has stopped answering does not die of
        ``terminate``, and the thread still inside ``pool.shutdown`` then holds the interpreter's
        own 300 s join at the end of the run: the user's progress line freezes with nothing
        happening and his Python is still in the task manager, which is the very fault this is
        here to stop (found in review, 2026-09-23). ``kill`` goes through the same guard on the
        process's own return code as ``terminate`` does, so it adds no race that the signal
        before it did not already have.
        """
        alive = [p for p in self.workers_seen if p.exitcode is None and p.is_alive()]
        self.workers_seen = []
        if not alive:
            return
        log.warning(
            "the encoders did not close in %.0f s; %d of them are being ended",
            POOL_CLOSE_S,
            len(alive),
        )
        for proc in alive:
            with contextlib.suppress(Exception):
                proc.terminate()
        deadline = time.monotonic() + WORKER_END_S
        for proc in alive:
            with contextlib.suppress(Exception):
                proc.join(max(0.0, deadline - time.monotonic()))
        for proc in alive:
            if proc.exitcode is None and proc.is_alive():
                log.warning("an encoder ignored the signal to end; it is being killed")
                with contextlib.suppress(Exception):
                    proc.kill()

    # -- fetch -------------------------------------------------------------------------------

    def _request(
        self, key: Any, x: int, y: int, zl: int, *, switch: int | None = None
    ) -> FetchRequest:
        return FetchRequest(
            key=key,
            url=tile_url(self.provider, x, y, zl, switch=switch),
            headers=dict(self.provider.headers),
            host_group=self.provider.code,
        )

    async def _fetch_all(self) -> None:
        requests: list[FetchRequest] = []
        for st in self.states:
            if not st.to_fetch:
                continue
            tiles = texture_tiles(st.texture)
            for i in st.to_fetch:
                x, y = tiles[i]
                requests.append(self._request(("c", st.index, i), x, y, self.zl))
        # Containers that are complete on disk resolve their parents now (they may need a
        # fetch when the parent cache is gone) and publish their hits before the download.
        await self._flush_io()
        if not requests and not self.needed_parents:
            return
        ceiling = self.spec.max_in_flight or self.provider.max_in_flight
        fetcher = Fetcher(
            max_in_flight=ceiling,
            start_in_flight=self.spec.start_in_flight or ceiling,
            hedge_after_s=self.spec.hedge_after_s,
            timeout_s=self.spec.timeout_s,
            max_attempts=self.spec.max_attempts,
            req_per_s=self.spec.req_per_s or self.provider.server_req_per_s,
        )
        async with fetcher:
            if requests:
                await fetcher.fetch_many(
                    requests,
                    on_result=self._on_answer,
                    on_stats=self._on_stats,
                    cancel=self.cancel_event,
                    keep_results=False,
                )
                self._accumulate(fetcher.stats())
            await self._flush_io()
            await self._further_rounds(fetcher)
            await self._second_pass(fetcher)

    async def _further_rounds(self, fetcher: Fetcher) -> None:
        """Parents of missing chunks (a parent that turns out to be a placeholder asks the next
        level up, spec section 5) and corrupted bodies asked again (section 4). Only the I/O
        tasks are awaited between rounds: the pool keeps encoding the complete textures."""
        while (self.needed_parents or self.retry_requests) and not self.cancelled:
            batch = sorted(self.needed_parents)
            self.needed_parents = set()
            self.attempted_parents.update(batch)
            self.counts["parents_total"] += len(batch)
            reqs = [self._request(("p", x, y, zl), x, y, zl) for x, y, zl in batch]
            reqs += self.retry_requests
            self.retry_requests = []
            await fetcher.fetch_many(
                reqs,
                on_result=self._on_answer,
                on_stats=self._on_stats,
                cancel=self.cancel_event,
                keep_results=False,
            )
            self._accumulate(fetcher.stats())
            for index in sorted(self.waiting):
                self._resolve_parents(self.by_index[index])
            await self._flush_io()

    def _accumulate(self, s: FetchStats) -> None:
        self.net_totals["requests"] += s.done
        self.net_totals["bytes"] += s.bytes
        self.net_totals["hedges"] += s.hedges
        self.net_totals["retries"] += s.retries
        self.net_totals["errors"] += s.errors
        self.last_stats = None

    def _on_stats(self, s: FetchStats) -> None:
        self.last_stats = s

    def _on_answer(self, r: FetchResult) -> None:
        if r.key[0] == "p":
            self._on_parent(r)
        else:
            self._on_tile(r)

    def _on_tile(self, r: FetchResult) -> None:
        _, index, i = r.key
        st = self.by_index[index]
        entry = chunk_entry_for(self.provider, r)
        self.tiles_done += 1
        assert st.outcome is not None
        st.outcome.fetched += 1
        self.counts["tiles_fetched"] += 1
        if (
            entry.status is ChunkStatus.ERROR
            and entry.content_type == CORRUPTED
            and st.corrupt_retries.get(i, 0) < CORRUPT_RETRIES
            and not self.cancelled
        ):
            # Ortho4XP asked again for a body that did not decode (max_baddata_retries)
            st.corrupt_retries[i] = st.corrupt_retries.get(i, 0) + 1
            st.outcome.retried += 1
            self.counts["tiles_retried"] += 1
            self.counts["tiles_total"] += 1
            x, y = texture_tiles(st.texture)[i]
            self.retry_requests.append(self._request(("c", index, i), x, y, self.zl))
            return
        st.container.set(i, entry)
        st.answered.add(i)
        st.pending -= 1
        if entry.status is ChunkStatus.PLACEHOLDER:
            st.outcome.placeholders += 1
            self.counts["tiles_placeholder"] += 1
        elif entry.status is ChunkStatus.MISSING:
            st.outcome.not_found += 1
            self.counts["tiles_not_found"] += 1
        elif entry.status is ChunkStatus.ERROR:
            st.outcome.errors += 1
            self.counts["tiles_error"] += 1
            if not _retryable(entry.content_type, r.status):
                st.final_errors += 1
            if len(st.failures) < _FAILURE_DETAILS:
                st.failures[i] = _ChunkFailure.of(r, entry.content_type)
        if st.pending == 0:
            self._container_ready(st)

    def _on_parent(self, r: FetchResult) -> None:
        _, x, y, zl = r.key
        self.parents_done += 1
        entry = chunk_entry_for(self.provider, r)
        if entry.status is ChunkStatus.OK:
            self.parent_cache.put(x, y, zl, entry.data)
            self.counts["parents_fetched"] += 1
        elif entry.status in (ChunkStatus.PLACEHOLDER, ChunkStatus.MISSING):
            self.parent_cache.put(x, y, zl, None)
            self.counts["parents_fetched"] += 1
        else:
            self.failed_parents.add((x, y, zl))

    # -- container -> parents -> schedule -------------------------------------------------------

    def _spawn(
        self,
        coro: Coroutine[Any, Any, None],
        st: _TexState | None,
        *,
        encode: bool = False,
    ) -> None:
        """Run ``coro`` as a task; an exception it leaks fails ``st`` with a coded error."""
        tasks = self.encode_tasks if encode else self.io_tasks
        task = asyncio.get_running_loop().create_task(self._guard(coro, st))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def _guard(self, coro: Coroutine[Any, Any, None], st: _TexState | None) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # nothing may be swallowed: the texture is failed, with a code
            err = _error_dict(_coded(exc))
            if st is not None and st.outcome is not None and st.outcome.status == "pending":
                self._finish(st, "failed", error=err)
            else:
                self.errors.append(err)

    def _container_ready(self, st: _TexState) -> None:
        self._spawn(self._finish_container(st), st)

    def _write_side_files(self, st: _TexState) -> None:
        """The ``.ter`` files and, when masks are not imprinted, the border PNG (thread)."""
        write_ter_files(self.out_dir, st.texture, st.job.kinds, self.ter_params)
        if st.border_crop is not None:
            write_border_mask(self.out_dir, st.texture, st.border_crop)

    def _persist_and_digest(self, st: _TexState, *, write: bool) -> str:
        if write:
            self.chunk_store.write(st.texture, st.container)
        return st.container.digest()

    async def _finish_container(self, st: _TexState) -> None:
        # Persist whatever was received, digest, then drop the bodies: the worker re-reads
        # the file and the parent process only needs the statuses from here on.
        st.chunks_digest = await asyncio.to_thread(
            self._persist_and_digest, st, write=bool(st.to_fetch)
        )
        st.container = _statuses_only(st.container)
        if not st.container.complete():
            errors = st.container.indices(ChunkStatus.ERROR)
            if self._defer(st, errors):
                return  # asked again by the second pass, once the rest of the tile is done
            await asyncio.to_thread(self._write_side_files, st)
            self._finish(st, "incomplete", error=self._incomplete_error(st, errors))
            return
        self._resolve_parents(st)

    def _defer(self, st: _TexState, errors: list[int]) -> bool:
        """Queue the ``ERROR`` chunks of ``st`` for the second pass when every one of them failed
        with a "try later" error. A texture that also holds a final error (a 500, a body that is
        not an image) cannot be completed in this run: waiting for its other chunks would only
        cost time, so it is reported at once, as before."""
        if self.cancelled or not self.pauses or not errors or st.final_errors:
            return False
        transient = RETRYABLE_CODES | {"NET_SERVER_ERROR"}  # 502-504 checked by final_errors
        if any(st.container.get(i).content_type not in transient for i in errors):
            return False  # an ERROR this run did not answer (defensive: all are refetched)
        assert st.outcome is not None
        st.deferred.update(errors)
        for i in errors:
            self.deferred[(st.index, i)] = None
        st.outcome.second_pass += len(errors)
        self.counts["chunks_second_pass"] += len(errors)
        return True

    def _incomplete_error(self, st: _TexState, errors: list[int]) -> dict[str, Any]:
        """``IMG_TILE_MISSING`` for the ``ERROR`` chunks of ``st``, with what each one met: the
        reason code, the HTTP status and curl's error line say what happened on the wire."""
        name = texture_name(st.texture)
        tiles = texture_tiles(st.texture)
        known = [(i, st.failures[i]) for i in errors if i in st.failures]
        codes = sorted({st.container.get(i).content_type for i in errors} - {""})
        rounds = len(st.rounds_asked)
        n = len(errors)
        message = f"{n} chunk{'' if n == 1 else 's'} of texture {name} could not be obtained "
        message += f"from {self.provider.code}"
        if codes:
            message += f" ({', '.join(codes)})"
        if rounds:
            message += f", still failing after {rounds} retry round{'' if rounds == 1 else 's'}"
        if st.deferred and self.second_pass_stopped:
            message += f"; the retries stopped: {self.second_pass_stopped}"
        exc = OsxpError(
            "IMG_TILE_MISSING",
            context={"chunk": f"{n} chunk(s)", "texture": name, "provider": self.provider.code},
            message=message + ".",
        )
        failures = [f.to_dict(i, tiles[i]) for i, f in known]
        return _error_dict(exc, chunks=errors, failures=failures)

    # -- second pass ------------------------------------------------------------------------------

    async def _second_pass(self, fetcher: Fetcher) -> None:
        """Ask again, in spaced rounds at low concurrency, for the chunks whose failure said "try
        later" (spec section 4.1): a transient failure costs time, not the tile."""
        if not self.deferred:
            return
        t0 = time.perf_counter()
        self.second_pass_deadline = t0 + self.spec.second_pass_max_s
        self.deferred = dict.fromkeys(sorted(self.deferred))  # texture order
        try:
            for round_no, pause in enumerate(self.pauses, start=1):
                if not self.deferred or self.cancelled:
                    break
                self.second_pass_round = round_no
                if time.perf_counter() + pause >= self.second_pass_deadline:
                    self._reach_cap()  # the round could only start after the cap: not waited
                    break
                await self._second_pass_pause(pause)
                if self.cancelled or not await self._probe(fetcher, round_no):
                    break
                if self.second_pass_capped:
                    break
                self.counts["second_pass_rounds"] = round_no
                await self._second_pass_round(fetcher, round_no)
                await self._flush_io()
                await self._further_rounds(fetcher)
                if self.second_pass_capped:
                    break
        finally:
            self.second_pass_round = 0
            self.second_pass_s += time.perf_counter() - t0
        if self.cancelled:
            return  # _drain_tasks writes what arrived and marks the waiting textures cancelled
        waiting = sorted({index for index, _ in self.deferred})
        self.deferred.clear()
        for index in waiting:
            st = self.by_index[index]
            self._spawn(self._finish_deferred(st, final=True), st)
        await self._flush_io()

    async def _second_pass_pause(self, seconds: float) -> None:
        """Wait ``seconds``, or less when the run is cancelled (the watcher sets the event within
        0.2 s of a request). The pool keeps encoding meanwhile."""
        if seconds <= 0:
            return
        self.second_pass_wait_until = time.perf_counter() + seconds
        idle = self.spec.idle
        if idle is not None:
            idle(True)  # nothing is asked of the line meanwhile: another tile may download
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.cancel_event.wait(), seconds)
        finally:
            self.second_pass_wait_until = 0.0
            if idle is not None:
                idle(False)

    def _round_limit(self, pending: int) -> int:
        """Requests in flight for a fetch of the second pass (spec section 4.1): about a quarter of
        the pending chunks (``SECOND_PASS_WAVES``), so that a round lasts a few waves whatever
        their number, at least the AIMD floor, and at most half the provider's ceiling. The
        fetcher still admits no more than the group's AIMD window and pauses on a 429."""
        ceiling = self.spec.max_in_flight or self.provider.max_in_flight
        wanted = max(SECOND_PASS_IN_FLIGHT, pending // SECOND_PASS_WAVES)
        return max(1, min(wanted, ceiling // 2))

    def _reach_cap(self) -> None:
        """The second pass has used ``second_pass_max_s``: the fetch in progress is cancelled
        (within a second, what arrived is kept) and no round follows; the chunks still waiting
        keep their ``ERROR`` entries."""
        if self.round_cancel is not None:
            self.round_cancel.set()
        if self.second_pass_capped:
            return
        self.second_pass_capped = True
        self.counts["second_pass_capped"] = 1
        self.second_pass_stopped = (
            f"the second pass reached its time limit ({self.spec.second_pass_max_s:g} s)"
        )

    def _arm_round_cancel(self) -> asyncio.Event:
        """The cancel event of one fetch of the second pass: set by a cancel of the run, by the
        time limit of the second pass, or by the abandon rule of a round."""
        event = asyncio.Event()
        self.round_cancel = event
        if self.cancelled:
            event.set()
        remaining = self.second_pass_deadline - time.perf_counter()
        if remaining <= 0:
            self._reach_cap()
        else:
            self.deadline_handle = asyncio.get_running_loop().call_later(remaining, self._reach_cap)
        return event

    def _disarm_round_cancel(self) -> None:
        if self.deadline_handle is not None:
            self.deadline_handle.cancel()
            self.deadline_handle = None
        self.round_cancel = None

    async def _probe(self, fetcher: Fetcher, round_no: int) -> bool:
        """Ask again for tiles the provider already answered: when one answers, the failures
        are specific to their chunks and a round can succeed; when none does, the provider or
        the line is down, and waiting for the next rounds would only delay the report."""
        tiles = self._probe_tiles(SECOND_PASS_PROBES)
        if not tiles:
            self.second_pass_stopped = "no tile of the run was answered by the provider"
            return False
        reqs = [
            self._request(("k", round_no, n), x, y, self.zl, switch=x + y + round_no)
            for n, (x, y) in enumerate(tiles)
        ]
        try:
            results = await fetcher.fetch_many(
                reqs,
                on_stats=self._on_stats,
                cancel=self._arm_round_cancel(),
                keep_results=False,
                limit_in_flight=self._round_limit(len(reqs)),
                max_attempts=SECOND_PASS_ATTEMPTS,
            )
        finally:
            self._disarm_round_cancel()
        self._accumulate(fetcher.stats())
        if any(r.error is None for r in results):
            return True
        if not self.cancelled and not self.second_pass_capped:
            codes = ", ".join(sorted({str(r.error) for r in results}))
            self.second_pass_stopped = (
                f"before round {round_no} the provider did not answer tiles it had already "
                f"served ({codes})"
            )
        return False

    def _probe_tiles(self, limit: int) -> list[tuple[int, int]]:
        """Tiles with a final answer: from the textures waiting for the second pass first (same
        area, same servers), an image before a 404 or a placeholder, one per texture first."""
        waiting = {index for index, _ in self.deferred}
        order = sorted(self.states, key=lambda s: (s.index not in waiting, s.index))
        picks: list[tuple[int, int]] = []
        for statuses in ((ChunkStatus.OK,), _ANSWERS):
            for per_texture in (1, limit):
                for st in order:
                    o = st.outcome
                    if st.fail_error is not None or o is None:
                        continue
                    if o.cached == 0 and o.fetched <= o.errors:
                        continue  # nothing but errors in this texture: no need to scan it
                    tiles = texture_tiles(st.texture)
                    taken = 0
                    for i, entry in enumerate(st.container.entries):
                        if taken == per_texture:
                            break
                        if entry.status in statuses and _answered(entry) and tiles[i] not in picks:
                            picks.append(tiles[i])
                            taken += 1
                            if len(picks) == limit:
                                return picks
        return picks

    async def _second_pass_round(self, fetcher: Fetcher, round_no: int) -> None:
        """Every waiting chunk once, on another host of the provider when its URL template has
        several (``{switch:}``), abandoned when failures pile up without a single answer."""
        queue = list(self.deferred)
        reqs: list[FetchRequest] = []
        for index, i in queue:
            x, y = texture_tiles(self.by_index[index].texture)[i]
            reqs.append(self._request(("s", index, i), x, y, self.zl, switch=x + y + round_no))
        self.round_failures = self.round_answers = 0
        self.counts["tiles_total"] += len(reqs)
        try:
            results = await fetcher.fetch_many(
                reqs,
                on_result=self._on_second_pass,
                on_stats=self._on_stats,
                cancel=self._arm_round_cancel(),
                keep_results=False,
                limit_in_flight=self._round_limit(len(reqs)),
                max_attempts=SECOND_PASS_ATTEMPTS,
            )
        finally:
            self._disarm_round_cancel()
        self._accumulate(fetcher.stats())
        untried = {(r.key[1], r.key[2]) for r in results if r.error == "SYS_CANCELLED"}
        if untried:
            self.counts["tiles_total"] -= len(untried)
            first = [k for k in self.deferred if k in untried]
            self.deferred = dict.fromkeys(first + [k for k in self.deferred if k not in untried])
        # What arrived for textures still waiting on other chunks is written now: the parent
        # process holds no body between rounds.
        for index in sorted({index for index, _ in queue}):
            st = self.by_index[index]
            if st.new_entries and st.deferred:
                self._spawn(self._finish_deferred(st), st)

    def _on_second_pass(self, r: FetchResult) -> None:
        _, index, i = r.key
        st = self.by_index[index]
        assert st.outcome is not None
        entry = chunk_entry_for(self.provider, r)
        self.tiles_done += 1
        st.outcome.fetched += 1
        self.counts["tiles_fetched"] += 1
        st.rounds_asked.add(self.second_pass_round)
        failure = st.failures.get(i)
        if entry.status is ChunkStatus.ERROR:
            if failure is not None:
                failure.again(r, entry.content_type)
            if entry.content_type != st.container.get(i).content_type:
                st.last_codes[i] = entry.content_type
            else:
                st.last_codes.pop(i, None)
            self.round_failures += 1
            if (
                self.round_answers == 0
                and self.round_failures >= SECOND_PASS_GIVE_UP_AFTER
                and self.round_cancel is not None
            ):
                self.round_cancel.set()  # nothing gets through: the next round tries again
            return
        self.round_answers += 1
        if failure is not None:
            failure.recovered_by(r)
        st.last_codes.pop(i, None)
        self.deferred.pop((index, i), None)
        st.deferred.discard(i)
        st.new_entries[i] = entry
        st.outcome.recovered += 1
        st.outcome.errors -= 1
        self.counts["chunks_recovered"] += 1
        self.counts["tiles_error"] -= 1
        if entry.status is ChunkStatus.PLACEHOLDER:
            st.outcome.placeholders += 1
            self.counts["tiles_placeholder"] += 1
        elif entry.status is ChunkStatus.MISSING:
            st.outcome.not_found += 1
            self.counts["tiles_not_found"] += 1
        if not st.deferred:
            self._spawn(self._finish_deferred(st), st)

    async def _finish_deferred(self, st: _TexState, *, final: bool = False) -> None:
        """Write the answers of the second pass into the container of ``st``; once nothing waits,
        carry on as :meth:`_finish_container` does (parents, key, encode). ``final``: the rounds
        are over, the chunks still waiting stay ``ERROR`` with their last reason."""
        entries = dict(st.new_entries)
        if final:
            when = now_unix()
            for i in sorted(st.deferred):
                code = st.last_codes.get(i)
                if code is not None:
                    entries[i] = ChunkEntry(ChunkStatus.ERROR, b"", code, when)
        if entries:
            st.chunks_digest, st.container = await asyncio.to_thread(
                self._update_container, st.texture, entries
            )
            for i in entries:
                st.new_entries.pop(i, None)
        if st.deferred and not final:
            return  # other chunks of the texture wait for a later round
        if not st.container.complete():
            errors = st.container.indices(ChunkStatus.ERROR)
            await asyncio.to_thread(self._write_side_files, st)
            self._finish(st, "incomplete", error=self._incomplete_error(st, errors))
            return
        self._resolve_parents(st)

    def _update_container(
        self, t: TextureId, entries: dict[int, ChunkEntry]
    ) -> tuple[str, ChunkContainer]:
        """Set ``entries`` in the container written after the first pass (thread); returns its
        new digest and its statuses."""
        container = self.chunk_store.read(t)
        if container is None:
            raise OsxpError(
                "SYS_INTERNAL_ERROR",
                context={
                    "type": "ChunkStore",
                    "detail": f"the container of {texture_name(t)} disappeared during the run",
                },
            )
        for i, entry in entries.items():
            container.set(i, entry)
        self.chunk_store.write(t, container)
        return container.digest(), _statuses_only(container)

    async def _finish_unbuildable(self, st: _TexState) -> None:
        """Sea kind without a usable mask: ``.ter`` written (the DSF references them), no DDS."""
        await asyncio.to_thread(self._write_side_files, st)
        self._finish(st, "failed", error=st.fail_error)

    def _resolve_parents(self, st: _TexState) -> None:
        parents, unknown = resolve_parents(
            st.texture,
            st.container,
            self.parent_cache.lookup,
            self.spec.parent_levels,
            known=st.parents,
        )
        st.parents = parents
        # A parent asked in an earlier round that never answered ends its chain: the chunk
        # falls back to the neighbourhood mean instead of blocking the texture.
        waiting = {
            k
            for k in unknown
            if k not in self.failed_parents
            and not (k in self.attempted_parents and k not in self.needed_parents)
        }
        if waiting:
            st.waiting_on = waiting
            self.needed_parents.update(waiting)
            self.waiting.add(st.index)
            return
        st.waiting_on = set()
        self.waiting.discard(st.index)
        self._schedule(st)

    def _schedule(self, st: _TexState) -> None:
        if self.cancelled:
            self._finish(st, "cancelled")
            return
        t = st.texture
        blob = parents_blob(t, st.parents) if st.parents else None
        parents_digest = digest_bytes(blob) if blob is not None else None
        params = self._params(st)
        chunks_digest = st.chunks_digest
        assert chunks_digest is not None
        key = dds_key(
            params,
            chunks_digest=chunks_digest,
            mask_digest=st.mask_digest,
            parents_digest=parents_digest,
        )
        assert st.outcome is not None
        st.outcome.key = key
        dest = self.out_dir / "textures" / texture_dds_name(t)
        if self.store.has(key):
            info = self.store.info(key)
            assert info is not None
            refs = [
                InputRef("chunks", chunks_digest),
                InputRef("mask", st.mask_digest),
                InputRef("parents", parents_digest),
            ]
            self.store.touch(key, refs)
            self._spawn(self._publish_hit(st, info.path, info.digest, dest), st)
            return
        job = WorkerJob(
            texture=t,
            params=params,
            chunks_path=self.chunk_store.path(t),
            chunks_digest=chunks_digest,
            mask_path=st.mask_path,
            mask_digest=st.mask_digest,
            parents_blob=blob,
            parents_digest=parents_digest,
            store_root=Path(self.spec.store_root),
            dest=dest,
            link=self.spec.link,
            tmp_dir=self.tmp_dir,
            fsync=self.spec.fsync,
        )
        self._spawn(self._encode_one(st, job, dest), st, encode=True)

    async def _encode_one(self, st: _TexState, job: WorkerJob, dest: Path) -> None:
        """Wait for a worker, hand the texture over, then wait for its answer.

        Every texture of a tile whose image pieces are already cached used to be handed to the
        pool at once, so the rope below timed the queue and not the encoding: on a four-core
        machine the last third of a tile passed it, was marked failed, and took the tile down
        with it (found in review, 2026-09-23). Only as many as the pool can hold are handed over.
        """
        slots = self.encode_slots
        if slots is None:
            await self._hand_over(st, job, dest)
            return
        async with slots:
            await self._hand_over(st, job, dest)

    async def _hand_over(self, st: _TexState, job: WorkerJob, dest: Path) -> None:
        """Give one texture to a worker and wait for its answer.

        ``_schedule`` looked at the stop before this texture queued for a slot, and the wait is
        where a stop arrives: handed to a pool that has just been shut, the texture came back as
        an internal error and the report told the user to file a bug for having pressed Stop
        (found in review, 2026-09-23).
        """
        if self.cancelled:
            self._finish(st, "cancelled")
            return
        st.submitted_at = time.perf_counter()
        if not self.t_first_submit:
            self.t_first_submit = st.submitted_at
        self.encoding += 1
        try:
            if self.pool is None:
                awaitable: Any = asyncio.to_thread(_worker_run, job)
            else:
                fut: Future[WorkerResult] = self.pool.submit(_worker_run, job)
                awaitable = asyncio.wrap_future(fut)
                procs = getattr(self.pool, "_processes", None)
                if procs:
                    self.workers_seen = list(procs.values())
        except RuntimeError:  # "cannot schedule new futures after shutdown": the stop, again
            self.encoding -= 1
            if self.cancelled:
                self._finish(st, "cancelled")
                return
            raise
        except BaseException:
            self.encoding -= 1  # it never reached a worker, so nothing will count it down
            raise
        await self._after_worker(st, awaitable, dest)

    async def _publish_hit(self, st: _TexState, src: Path, digest: str, dest: Path) -> None:
        try:
            await asyncio.to_thread(publish_file, src, dest, link=self.spec.link)
            await asyncio.to_thread(self._write_side_files, st)
        except OSError as exc:
            self._finish(st, "failed", error=_error_dict(_coded(exc, path=dest)))
            return
        assert st.outcome is not None
        st.outcome.digest = digest
        st.outcome.dds_path = str(dest)
        st.outcome.fmt = "bc3" if st.mask_digest else "bc1"
        self._finish(st, "hit")

    def _encode_deadline(self) -> float:
        """How long one texture may wait for its answer before the build gives up on it.

        Not a budget: a rope end, so that a worker which never comes back cannot hold the step.
        It has to be long enough that no healthy texture ever reaches it, and the wait before a
        worker even starts is part of it: every texture of a tile is handed to the pool at once
        when its image pieces are already in the cache.

        A user's own reports: 14 workers, 719 textures, 6.7 s each at the median and 60.6 s at
        the worst, the pool busy throughout (2026-09-23). A fixed 300 s would have fired on the
        last third of that tile, marked them failed and refused the tile, repeatably, on a build
        that worked.

        The wait before a worker starts is no longer part of it: ``_encode_one`` hands over only
        as many textures as the pool can hold, so this times the encoding. What is left is a
        floor wide enough for the slowest machine and a ceiling so that a worker which never
        answers cannot hold the step for hours (both found in review, 2026-09-23).
        """
        seen = self.encode_seconds
        typical = (sum(seen) / len(seen)) if seen else _DEFAULT_ENCODE_S
        return min(MAX_ENCODE_ROPE_S, max(ENCODE_TIMEOUT_S, 20.0 * typical))

    async def _after_worker(self, st: _TexState, awaitable: Any, dest: Path) -> None:
        t = st.texture
        rope_s = self._encode_deadline()
        try:
            result: WorkerResult = await asyncio.wait_for(awaitable, rope_s)
        except TimeoutError:
            # A worker that never comes back would hold the step: nothing in flight, nothing
            # said, and the end of the step waiting on it (a user, 2026-09-23). Past the rope it
            # is one failed texture, said plainly, and the build carries on.
            self.encoding -= 1
            self.encode_timeouts += 1
            log.warning(
                "%s: encoder did not answer in %.0f s (%d so far); the texture is marked failed",
                texture_name(t),
                rope_s,
                self.encode_timeouts,
            )
            stuck = OsxpError(
                "TEX_ENCODE_FAILED",
                context={
                    "texture": texture_name(t),
                    "encoder": self.encoder,
                    "reason": f"no answer in {rope_s:.0f} s",
                },
            )
            self._finish(st, "failed", error=_error_dict(stuck))
            return
        except asyncio.CancelledError:
            self.encoding -= 1
            self._finish(st, "cancelled")
            return
        except Exception as pool_exc:  # pool broken, pickling...
            self.encoding -= 1
            err = OsxpError(
                "TEX_ENCODE_FAILED",
                context={
                    "texture": texture_name(t),
                    "encoder": self.encoder,
                    "reason": f"{type(pool_exc).__name__}: {pool_exc}",
                },
            )
            self._finish(st, "failed", error=_error_dict(err))
            return
        self.encoding -= 1
        assert st.outcome is not None
        st.outcome.seconds = result.seconds
        try:
            await asyncio.to_thread(self._write_side_files, st)
        except OSError as exc:
            self._finish(st, "failed", error=_error_dict(_coded(exc)))
            return
        if result.error is not None:
            self._finish(st, "failed", error=result.error)
            return
        st.outcome.digest = result.digest
        st.outcome.dds_path = str(dest)
        st.outcome.fmt = result.info.fmt if result.info else ("bc3" if st.mask_digest else "bc1")
        if result.info is not None:
            st.outcome.from_fallback = result.info.from_fallback
            st.outcome.unfilled = result.info.unfilled
            self.counts["chunks_from_fallback"] += result.info.from_fallback
            self.counts["chunks_unfilled"] += result.info.unfilled
            self.encode_seconds.append(result.seconds)
            if result.info.unfilled >= GRID * GRID:
                # nothing at all came back, so what the encoder wrote is one flat grey square
                # painted from its own emptiness. It was called built, the DSF shipped it, and
                # the user flew grey ground with nothing said (found in review, 2026-09-23). A
                # square outside what a source covers, or a level finer than it holds, lands
                # here for every texture of the tile.
                await asyncio.to_thread(_unlink_quietly, dest)
                st.outcome.dds_path = None
                st.outcome.digest = None
                self._finish(st, "incomplete", error=self._nothing_came_back(st))
                return
            if result.info.unfilled:
                unfilled_exc = OsxpError(
                    "IMG_TILE_MISSING",
                    context={
                        "chunk": f"{result.info.unfilled} chunk(s) without any parent",
                        "texture": texture_name(t),
                        "provider": self.provider.code,
                    },
                )
                self.errors.append(_error_dict(unfilled_exc, texture=texture_name(t)))
            if result.info.corrupted:
                await self._mark_corrupted(st, result.info.corrupted)
        self._finish(st, result.status)

    def _nothing_came_back(self, st: _TexState) -> dict[str, Any]:
        """The texture for which the source answered with nothing at all."""
        name = texture_name(st.texture)
        exc = OsxpError(
            "IMG_TILE_MISSING",
            context={
                "chunk": f"all {GRID * GRID} chunk(s)",
                "texture": name,
                "provider": self.provider.code,
            },
            message=(
                f"{self.provider.code} has no imagery at all for texture {name}: every one of "
                f"its {GRID * GRID} pieces was refused."
            ),
            remedy=(
                "That square is outside what this source covers, or the detail level is finer "
                "than it holds. Choose another source in the Plan, or a lower level."
            ),
        )
        return _error_dict(exc, texture=name)

    async def _mark_corrupted(self, st: _TexState, indices: tuple[int, ...]) -> None:
        """Bodies that passed the structural check but did not decode: ``ERROR`` on disk so
        the next run asks for them again (spec section 4); the texture stays degraded."""
        t = st.texture
        assert st.outcome is not None
        st.outcome.corrupted = len(indices)
        self.counts["chunks_corrupted"] += len(indices)
        try:
            await asyncio.to_thread(self._flip_to_error, t, indices)
        except OSError as exc:
            self.errors.append(_error_dict(_coded(exc), texture=texture_name(t)))
        exc_c = OsxpError(
            CORRUPTED,
            context={
                "provider": self.provider.code,
                "chunk": f"{len(indices)} chunk(s) of {texture_name(t)}",
            },
            message=(
                f"Texture {texture_name(t)}: {len(indices)} chunk(s) did not decode and were "
                "filled from parents or neighbours; they are downloaded again at the next run."
            ),
        )
        self.errors.append(_error_dict(exc_c, texture=texture_name(t), chunks=list(indices)))

    def _flip_to_error(self, t: TextureId, indices: tuple[int, ...]) -> None:
        container = self.chunk_store.read(t)
        if container is None:
            return
        when = now_unix()
        for i in indices:
            container.set(i, ChunkEntry(ChunkStatus.ERROR, b"", CORRUPTED, when))
        self.chunk_store.write(t, container)

    def _finish(self, st: _TexState, status: str, *, error: dict[str, Any] | None = None) -> None:
        assert st.outcome is not None
        st.outcome.status = status
        st.outcome.error = error
        if st.failures:
            tiles = texture_tiles(st.texture)
            st.outcome.failures = [f.to_dict(i, tiles[i]) for i, f in sorted(st.failures.items())]
        if error is not None:
            self.errors.append(dict(error, texture=texture_name(st.texture)))
        if status == "built":
            self.counts["built"] += 1
        elif status == "hit":
            self.counts["hits"] += 1
        elif status == "incomplete":
            self.counts["incomplete"] += 1
        elif status == "cancelled":
            self.counts["cancelled"] += 1
        else:
            self.counts["failed"] += 1
        self.t_last_done = time.perf_counter()

    # -- tasks, progress, cancellation -------------------------------------------------------------

    async def _flush_io(self) -> None:
        """Let container writes, parent resolutions and hit publications run (not encodes)."""
        await self._flush(self.io_tasks)

    @staticmethod
    async def _flush(tasks: set[asyncio.Task[None]]) -> None:
        pending = [t for t in tasks if not t.done()]
        while pending:
            await asyncio.gather(*pending, return_exceptions=True)
            pending = [t for t in tasks if not t.done()]

    async def _drain_tasks(self) -> None:
        await self._flush(self.io_tasks | self.encode_tasks)
        for st in self.states:
            assert st.outcome is not None
            if st.outcome.status == "pending":
                if self.cancelled:
                    if st.new_entries:
                        try:
                            _, st.container = await asyncio.to_thread(
                                self._update_container, st.texture, dict(st.new_entries)
                            )
                            st.new_entries.clear()
                        except (OSError, OsxpError) as exc:
                            self.errors.append(
                                _error_dict(_coded(exc), texture=texture_name(st.texture))
                            )
                    if st.pending > 0 and st.answered:
                        # keep what was received; the rest is retried at the next run
                        when = now_unix()
                        for i in st.to_fetch:
                            if i not in st.answered:
                                st.container.set(
                                    i, ChunkEntry(ChunkStatus.ERROR, b"", "SYS_CANCELLED", when)
                                )
                        try:
                            await asyncio.to_thread(
                                self.chunk_store.write, st.texture, st.container
                            )
                        except OSError as exc:
                            self.errors.append(
                                _error_dict(_coded(exc), texture=texture_name(st.texture))
                            )
                    self._finish(st, "cancelled")
                elif st.waiting_on:
                    # parents never answered; encode with what we have
                    self.failed_parents.update(st.waiting_on)
                    st.waiting_on = set()
                    self._resolve_parents(st)
                    await self._flush(self.io_tasks | self.encode_tasks)
        for st in self.states:
            assert st.outcome is not None
            if st.outcome.status == "pending":
                if self.cancelled:
                    self._finish(st, "cancelled")
                else:
                    stuck = OsxpError(
                        "SYS_INTERNAL_ERROR",
                        context={
                            "type": "PipelineState",
                            "detail": f"texture {texture_name(st.texture)} was left pending "
                            f"(waiting on {len(st.waiting_on)} parent(s), {st.pending} tile(s), "
                            f"{len(st.deferred)} chunk(s) of the second pass)",
                        },
                    )
                    self._finish(st, "failed", error=_error_dict(stuck))

    def _snapshot(self) -> ProgressSnapshot:
        s = self.last_stats
        req_per_s = s.req_per_s if s is not None else 0.0
        remaining_tiles = self.counts["tiles_total"] - self.tiles_done
        done_tex = self.counts["built"] + self.counts["hits"] + self.counts["failed"]
        done_tex += self.counts["incomplete"] + self.counts["cancelled"]
        remaining_tex = self.counts["textures"] - done_tex
        eta: float | None = None
        if remaining_tiles > 0 and req_per_s > 0:
            eta = remaining_tiles / req_per_s
        mean_encode = (
            sum(self.encode_seconds) / len(self.encode_seconds)
            if self.encode_seconds
            else _DEFAULT_ENCODE_S
        )
        if remaining_tex > 0:
            eta = (eta or 0.0) + remaining_tex * mean_encode / max(1, self.workers)
        wait_s = 0.0
        if self.second_pass_wait_until:
            wait_s = max(0.0, self.second_pass_wait_until - time.perf_counter())
            eta = (eta or 0.0) + wait_s
        return ProgressSnapshot(
            tiles_done=self.tiles_done,
            tiles_total=self.counts["tiles_total"],
            parents_done=self.parents_done,
            parents_total=self.counts["parents_total"],
            req_per_s=req_per_s,
            bytes=int(self.net_totals["bytes"] + (s.bytes if s is not None else 0)),
            in_flight=s.in_flight if s is not None else 0,
            hedges=int(self.net_totals["hedges"] + (s.hedges if s is not None else 0)),
            retries=int(self.net_totals["retries"] + (s.retries if s is not None else 0)),
            net_errors=int(self.net_totals["errors"] + (s.errors if s is not None else 0)),
            throttled=bool(s.throttled) if s is not None else False,
            pushed_back=bool(s.pushed_back) if s is not None else False,
            textures_total=self.counts["textures"],
            built=self.counts["built"],
            hits=self.counts["hits"],
            failed=self.counts["failed"],
            incomplete=self.counts["incomplete"],
            encoding=self.encoding,
            eta_s=eta,
            elapsed_s=time.perf_counter() - self.t0,
            second_pass_chunks=len(self.deferred),
            second_pass_round=self.second_pass_round,
            second_pass_rounds=len(self.pauses) if self.deferred else 0,
            second_pass_wait_s=wait_s,
        )

    async def _tick(self) -> None:
        assert self.progress is not None
        while True:
            await asyncio.sleep(_PROGRESS_PERIOD_S)
            self.progress(self._snapshot())

    async def _watch_cancel(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(0.2)
        self._cancel_now()

    def _cancel_now(self) -> None:
        """Stop the fetcher and the pool (loop thread); idempotent."""
        self.cancelled = True
        self.cancel_event.set()
        if self.round_cancel is not None:
            self.round_cancel.set()
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)

    # -- report ---------------------------------------------------------------------------------

    def _report(self, plan_s: float, fetch_s: float) -> TexturesReport:
        total = time.perf_counter() - self.t0
        encode_wall = (self.t_last_done - self.t_first_submit) if self.t_first_submit else 0.0
        requests = self.net_totals["requests"]
        network = dict(self.net_totals)
        active_s = fetch_s - self.second_pass_s  # the pauses of the second pass are not a rate
        network["req_per_s_mean"] = requests / active_s if active_s > 0 and requests else 0.0
        report = TexturesReport(
            tile=self._tile_name(),
            provider=self.provider.code,
            zl=self.zl,
            out_dir=str(self.out_dir),
            chunks_root=str(self.spec.chunks_root),
            store_root=str(self.spec.store_root),
            encoder=self.encoder,
            encoder_version=self.encoder_version,
            workers=self.workers,
            outcomes=[st.outcome for st in self.states if st.outcome is not None],
            errors=self.errors,
            counts=dict(self.counts),
            network=network,
            timings={
                "plan_s": plan_s,
                "fetch_s": fetch_s,
                "second_pass_s": self.second_pass_s,
                "encode_wall_s": max(0.0, encode_wall),
                "encode_cpu_s": sum(self.encode_seconds),
                "total_s": total,
            },
            cancelled=self.cancelled,
        )
        if self.cancelled:
            report.errors.append(_error_dict(OsxpError("SYS_CANCELLED")))
        missing = report.missing
        if missing and not self.cancelled:
            exc = OsxpError(
                "TEX_MISSING", context={"count": len(missing), "tile": self._tile_name()}
            )
            report.errors.append(_error_dict(exc, textures=[o.name for o in missing]))
        return report


def build_textures(spec: TexturesSpec) -> TexturesReport:
    """Build the textures and terrain files of one tile (spec section 1). Synchronous.

    A first ``SIGINT`` (Ctrl-C) takes the cooperative cancellation path: the download stops
    within a second, received tiles are persisted, the report comes back with
    ``cancelled=True``. A second one cancels the loop task; the report is still returned after
    the partial containers are written. ``KeyboardInterrupt`` is never raised from here on
    platforms where the loop can handle signals.
    """
    if not spec.jobs:
        raise ValueError("no textures to build")
    pipeline = _Pipeline(spec)

    async def main() -> TexturesReport:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        installed = False

        def on_sigint() -> None:
            if not pipeline.cancel_requested:
                pipeline.request_cancel()
            elif task is not None:
                task.cancel()

        try:
            loop.add_signal_handler(signal.SIGINT, on_sigint)
            installed = True
        except (NotImplementedError, RuntimeError, ValueError, AttributeError):
            pass  # Windows or not the main thread: Ctrl-C keeps its default behaviour
        try:
            return await pipeline.run()
        finally:
            if installed:
                loop.remove_signal_handler(signal.SIGINT)

    return asyncio.run(main())


def texture_count_tiles(jobs: Iterable[TextureJob]) -> int:
    """256 tiles per texture: the upper bound of requests of a cold run."""
    return 256 * sum(1 for _ in jobs)
