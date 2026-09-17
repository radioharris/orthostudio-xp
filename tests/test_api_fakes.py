"""Shared fakes of the API tests: a synthetic ``build_tiles``, an airport index, fixtures.

Not a test module by itself (no ``test_`` function); imported by ``test_api_app.py`` and
``test_api_jobs.py``. Nothing here touches the network or Ortho4XP.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from orthostudio.api import app as api_app
from orthostudio.api.jobs import _expected_nodes
from orthostudio.errors import OsxpError
from orthostudio.install import packs
from orthostudio.model import ArtifactRef, TileRef
from orthostudio.pipeline.build import BuildReport, BuildSpec, NodeOutcome, TileOutcome
from orthostudio.sched import Done, Event, Failed, Progress, Started, Stats

KINDS = {
    # review 4 / blocker B4: the P3 data roles are expected nodes now (api/jobs.py).
    "osm": "net",
    "dem": "net",
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


@dataclass
class FakeBuild:
    """Plays the scheduler events of every spec; remembers what it "committed".

    ``fail`` maps a role to an error code: that node fails, the ones after it in the tile are
    skipped (``cause``). ``delay_s`` sleeps between nodes (cancel tests). A second call
    reports the nodes committed by the first as hits (the retry semantics).
    """

    fail: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0
    committed: set[str] = field(default_factory=set)
    calls: int = 0
    tmp: Path | None = None

    def __call__(
        self, specs: Sequence[BuildSpec], *, on_event: Callable[[Event], None], env: Any = None
    ) -> BuildReport:
        self.calls += 1
        t0 = time.perf_counter()
        tiles: list[TileOutcome] = []
        built = hits = failed = 0
        total = sum(len(_expected_nodes(s)) for s in specs)
        done = 0
        for spec in specs:
            outcomes: list[NodeOutcome] = []
            root: str | None = None
            for node_id, role in _expected_nodes(spec):
                key = hashlib.sha256(node_id.encode()).hexdigest()
                on_event(Started(node_id, KINDS[role], key))  # type: ignore[arg-type]
                if self.delay_s:
                    time.sleep(self.delay_s)
                if root is not None:
                    err = OsxpError("SYS_CANCELLED")
                    on_event(Failed(node_id, err, cause=root))
                    d = err.to_dict()
                    outcomes.append(NodeOutcome(node_id, role, "r", None, "skipped", 0.0, d, root))
                    failed += 1
                elif role in self.fail:
                    code = self.fail[role]
                    ctx = {"tile": spec.tile.name, "count": 3, "total": 128, "level": spec.level}
                    err = OsxpError(code, context=ctx)
                    on_event(Failed(node_id, err))
                    d = err.to_dict()
                    outcomes.append(NodeOutcome(node_id, role, "r", None, "failed", 0.1, d))
                    failed += 1
                    root = node_id
                else:
                    if role == "textures":
                        for i in range(1, 4):
                            msg = f"{spec.level}: tiles {i}/4 (900 req/s)"
                            on_event(Progress(node_id, i / 4, msg))
                    hit = key in self.committed
                    ref = ArtifactRef(key, "d" * 64, Path("/nonexistent") / key, "r", "dir")
                    on_event(Done(node_id, key, hit, 0.0 if hit else 0.2, ref))
                    self.committed.add(key)
                    status = "hit" if hit else "built"
                    outcomes.append(NodeOutcome(node_id, role, "r", key, status, 0.2))
                    if hit:
                        hits += 1
                    else:
                        built += 1
                done += 1
                on_event(
                    Stats(
                        running=0,
                        pending=total - done,
                        done=done - failed,
                        failed=failed,
                        hits=hits,
                        elapsed_s=time.perf_counter() - t0,
                        eta_s=(total - done) * 0.2,
                    )
                )
            ok = root is None
            tiles.append(
                TileOutcome(spec.tile.name, spec.provider, spec.zl, ok, None, None, False, outcomes)
            )
        return BuildReport(
            tiles=tiles,
            elapsed_s=time.perf_counter() - t0,
            built=built,
            hits=hits,
            failed=failed,
            cancelled=False,
            store_root="/store",
            out_dir="/out",
        )


@dataclass(frozen=True)
class FakeAirport:
    icao: str
    name: str
    lat: float
    lon: float


class FakeIndex:
    rows: ClassVar[list[FakeAirport]] = [
        FakeAirport("LFML", "Marseille Provence", 43.4367, 5.215),
        FakeAirport("LFMN", "Nice Cote d'Azur", 43.6584, 7.2159),
    ]

    def search(self, q: str, limit: int = 10) -> list[FakeAirport]:
        q = q.upper()
        return [a for a in self.rows if q in a.icao or q in a.name.upper()][:limit]

    def get(self, icao: str) -> FakeAirport | None:
        return next((a for a in self.rows if a.icao == icao), None)

    def tiles_around(self, icao: str, radius_km: float) -> list[TileRef]:
        a = self.get(icao)
        if a is None:
            raise KeyError(icao)
        base = TileRef(int(a.lat // 1), int(a.lon // 1))
        return [base] if radius_km < 50 else [base, base.neighbour(0, 1)]


def make_spec(
    tile: str = "+43+005", *, install: bool = False, home: Path | None = None
) -> BuildSpec:
    root = home if home is not None else Path("/tmp/osxp-test")
    return BuildSpec(
        tile=TileRef.parse(tile),
        provider="BI",
        zl=14,
        out_dir=root / "tiles",
        install=install,
        custom_scenery=root / "Custom Scenery" if install else None,
        store_root=root / "store",
        chunks_root=root / "chunks",
        workdir=root / "work",
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("OSXP_HOME", str(h))
    monkeypatch.setenv("OSXP_API_NO_AIRPORTS", "1")
    return h


@pytest.fixture
def xplane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake X-Plane 12 with a Global Scenery DSF of +43+005 and a scenery_packs.ini."""
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    # the status reads it too: the tests say what it answers, whatever runs on the machine
    monkeypatch.setattr(api_app, "xplane_running", lambda: False)
    xp = tmp_path / "X-Plane 12"
    (xp / "Resources").mkdir(parents=True)
    cs = xp / "Custom Scenery"
    cs.mkdir()
    (cs / "scenery_packs.ini").write_bytes(
        b"I\n1000 Version\nSCENERY\n\nSCENERY_PACK *GLOBAL_AIRPORTS*\n"
        b"SCENERY_PACK Custom Scenery/z_autoortho/\n"
    )
    dsf = xp / "Global Scenery" / "X-Plane 12 Global Scenery" / TileRef(43, 5).dsf_relpath
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF")
    monkeypatch.setenv("OSXP_XPLANE_DIR", str(xp))
    return xp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def sse_messages(text: str) -> Iterator[dict[str, Any]]:
    """Parse an SSE body into ``{id, event, data}`` dicts (comments and retry skipped)."""
    for block in text.split("\n\n"):
        msg: dict[str, Any] = {}
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            msg[name] = value.strip()
        if "event" in msg:
            yield msg
