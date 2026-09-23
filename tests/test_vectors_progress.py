"""The vectors step says what it is doing.

A user on Linux watched a build sit at 28 % of its Data stage for eight and a half minutes and
then stopped it. His job file holds 517 lines, one of which is a message, and not a single
progress event: the step reported nothing at all, from its first second to its last, so a dense
tile at road level 5 was indistinguishable from a program that had stopped (2026-09-23).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orthostudio.vectors.rule import VectorsJob, _teller


def test_every_family_says_what_it_is_while_the_step_runs(tmp_path: Path) -> None:
    """Run the builder on an empty tile and listen: each family announces itself before it
    starts, which is what a user needs to tell a long road network from a dead program."""
    import numpy as np

    from orthostudio.dem.dem import Dem
    from orthostudio.model import TileRef
    from orthostudio.sources.osm import LAYERS, OsmSnapshot, SnapshotStore
    from orthostudio.vectors.layers import build_layers
    from orthostudio.vectors.rule import LayerRequest, VectorsParams

    tile = TileRef(49, 8)
    osm = tmp_path / "osm"
    for name, spec in LAYERS.items():
        SnapshotStore(osm).save(
            OsmSnapshot(
                tile=tile,
                layer=name,
                selectors=tuple(spec.selectors),
                query="",
                mirror="test",
                fetched_at="2026-09-23T00:00:00Z",
                generator="test",
                osm_base="",
                nodes=(),
                ways=(),
                relations=(),
                digest="d" * 64,
            )
        )
    flat = Dem(
        tile=tile,
        alt_dem=np.full((16, 16), 100.0, dtype=np.float32),
        x0=-0.01,
        y0=-0.01,
        x1=1.01,
        y1=1.01,
        nxdem=16,
        nydem=16,
    )

    seen: list[tuple[float, str]] = []
    build_layers(
        LayerRequest(
            tile=tile,
            params=VectorsParams(tile=tile.name, road_level=5),
            osm=osm,
            dem=flat,
            progress=lambda fraction, message: seen.append((fraction, message)),
        )
    )
    said = [message for _fraction, message in seen]
    assert said == [
        "+49+008: airports",
        "+49+008: roads and railways at level 5",
        "+49+008: lakes and rivers",
        "+49+008: the coastline",
    ]
    assert [f for f, _m in seen] == sorted(f for f, _m in seen), "the fractions only go forward"


def test_the_step_tells_the_page_and_never_stops_a_build_for_it() -> None:
    from orthostudio.model import TileRef

    seen: list[tuple[float, str]] = []
    say = _teller(VectorsJob(progress=lambda f, m: seen.append((f, m))), TileRef(49, 8))
    say(0.25, "roads and railways at level 5")
    assert seen == [(0.25, "+49+008: roads and railways at level 5")]

    def angry(_f: float, _m: str) -> None:
        raise RuntimeError("the page went away")

    _teller(VectorsJob(progress=angry), TileRef(49, 8))(0.5, "lakes and rivers")
    _teller(VectorsJob(), TileRef(49, 8))(0.5, "nobody is listening")


def test_the_builder_passes_the_page_down_to_the_families() -> None:
    """``LayerRequest`` carries it, and ``build_layers`` reads it off the request."""
    import inspect

    from orthostudio.vectors import layers
    from orthostudio.vectors.rule import LayerRequest

    assert "progress" in {f for f in LayerRequest.__dataclass_fields__}
    body = inspect.getsource(layers.build_layers)
    assert 'getattr(request, "progress", None)' in body


def test_the_node_hands_it_the_pages_own_progress() -> None:
    import inspect

    from orthostudio.pipeline import build

    body = inspect.getsource(build._vectors_run)
    assert "progress=ctx.progress" in body


def _unused(_x: Any) -> None:
    return None
