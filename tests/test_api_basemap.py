# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The street map of the page (``docs/specs/map-zones.md``): a caching proxy over OpenFreeMap.

A user of the X-Plane.Org page asked for an OSM map beside the aerial imagery, as Ortho4XP offers
(2026-09-19). Ortho4XP asks OpenStreetMap's own servers with a browser's User-Agent and a Referer
of its own, which their policy asks applications not to do; this reads OpenFreeMap instead. No
test here touches the network: the upstream is injected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orthostudio.api.basemap import OPENFREEMAP_URL, basemap_router, rewrite_style

anyio_backend = "asyncio"

STYLE = {
    "version": 8,
    "glyphs": f"{OPENFREEMAP_URL}/fonts/{{fontstack}}/{{range}}.pbf",
    "sprite": f"{OPENFREEMAP_URL}/sprites/ofm_f384/ofm",
    "sources": {
        "openmaptiles": {"type": "vector", "url": f"{OPENFREEMAP_URL}/planet"},
        "ne2_shaded": {
            "type": "raster",
            "tiles": [f"{OPENFREEMAP_URL}/natural_earth/ne2sr/{{z}}/{{x}}/{{y}}.png"],
        },
    },
    "layers": [{"id": "water", "type": "fill", "source": "openmaptiles"}],
}
PLANET = {
    "tilejson": "3.0.0",
    "tiles": [f"{OPENFREEMAP_URL}/planet/20260913/{{z}}/{{x}}/{{y}}.pbf"],
}


def _app(tmp_path: Path, answers: dict[str, bytes], asked: list[str]) -> FastAPI:
    def fetch(url: str) -> tuple[bytes | None, str]:
        asked.append(url)
        body = answers.get(url)
        return (body, "") if body is not None else (None, "HTTP 404")

    app = FastAPI()
    app.include_router(basemap_router(lambda: tmp_path, fetch=fetch))
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://osxp")


def test_rewriting_a_style_leaves_no_other_origin() -> None:
    """MapLibre follows the addresses the style gives: rewritten, the page asks this engine only,
    which is what keeps the rule that the page holds no address of another origin.

    They are absolute: the vector tiles are fetched from a web worker, which has no page to
    resolve a relative address against and refuses it ("Failed to parse URL", measured).
    """
    here = "http://127.0.0.1:8641/api/basemap"
    out = rewrite_style(STYLE, here)
    text = json.dumps(out)
    assert OPENFREEMAP_URL not in text
    assert out["glyphs"] == f"{here}/fonts/{{fontstack}}/{{range}}.pbf"
    assert out["sources"]["openmaptiles"]["url"] == f"{here}/planet"
    assert out["sources"]["ne2_shaded"]["tiles"] == [
        f"{here}/natural_earth/ne2sr/{{z}}/{{x}}/{{y}}.png"
    ]
    assert out["layers"] == STYLE["layers"]  # everything else is left alone


@pytest.mark.anyio
async def test_the_style_the_source_and_a_tile_go_through_the_proxy(tmp_path: Path) -> None:
    asked: list[str] = []
    answers = {
        f"{OPENFREEMAP_URL}/styles/liberty": json.dumps(STYLE).encode(),
        f"{OPENFREEMAP_URL}/planet": json.dumps(PLANET).encode(),
        f"{OPENFREEMAP_URL}/planet/20260913/12/2117/1453.pbf": b"\x1a\x0evector-tile",
    }
    app = _app(tmp_path, answers, asked)
    async with _client(app) as c:
        style = await c.get("/api/basemap/style")
        assert style.status_code == 200
        assert style.json()["sources"]["openmaptiles"]["url"] == "http://osxp/api/basemap/planet"
        # read again each time: the addresses inside name the port this engine answers on
        assert style.headers["cache-control"] == "no-cache"

        # the source names where the tiles really are: it is rewritten too, or the page would
        # follow the service's own address and leave this engine
        source = await c.get("/api/basemap/planet")
        assert source.json()["tiles"] == ["http://osxp/api/basemap/planet/20260913/{z}/{x}/{y}.pbf"]

        tile = await c.get("/api/basemap/planet/20260913/12/2117/1453.pbf")
        assert tile.status_code == 200
        assert tile.headers["content-type"].startswith("application/x-protobuf")
        assert tile.content == b"\x1a\x0evector-tile"

        # kept on disk, as the service sent it, and the map cache is what "Free space" empties.
        # The source is kept as planet.json: a file cannot also be the folder of its tiles.
        assert (tmp_path / "openfreemap" / "planet.json").is_file()
        kept = tmp_path / "openfreemap" / "planet" / "20260913" / "12" / "2117" / "1453.pbf"
        assert kept.read_bytes() == b"\x1a\x0evector-tile"
        before = len(asked)
        again = await c.get("/api/basemap/planet/20260913/12/2117/1453.pbf")
        assert again.content == b"\x1a\x0evector-tile" and len(asked) == before


@pytest.mark.anyio
async def test_a_service_that_does_not_answer_leaves_the_imagery_alone(tmp_path: Path) -> None:
    """The street map is an extra: its failure is a 502 with a plain reason, and nothing is
    cached, so the next try asks again."""
    asked: list[str] = []
    app = _app(tmp_path, {}, asked)
    async with _client(app) as c:
        answer = await c.get("/api/basemap/style")
        assert answer.status_code == 502
        error = answer.json()["error"]
        assert error["code"] == "NET_CONNECTION_FAILED" and error["severity"] == "degraded"
        assert "aerial imagery is unaffected" in error["remedy"]
    assert not list(tmp_path.rglob("*.pbf"))
    assert len(asked) == 1
