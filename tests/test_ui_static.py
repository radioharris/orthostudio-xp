"""Static checks of the web page (``docs/specs/ui.md`` section 4, ``map-zones.md`` section 7).

The directory is served exactly as ``create_app`` will serve it (``/`` → ``index.html``,
``/static/*`` → the rest) through FastAPI's ``StaticFiles`` and an in-process ASGI transport,
so no server, no browser and no network are involved. The i18n dictionary and the map's
geometry (``geo.js``) are run under ``node`` (skipped when ``node`` is missing).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from orthostudio.imagery.grid import (
    texture_at,
    texture_bbox,
    textures_covering,
    tile_to_wgs84,
    wgs84_to_gtile,
)
from orthostudio.ui import INDEX_FILE, STATIC_FILES, ui_dir

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

UI = ui_dir()
NODE = shutil.which("node")
PAGE_MODULES = ("app.js", "find.js", "geo.js", "map.js", "settings.js", "sources.js", "zoom.js")
"""The page's own modules: every visible string of theirs goes through a literal t() key."""
TEXT_FILES = ("index.html", "i18n.js", "styles.css", *PAGE_MODULES)
LEAFLET_SCRIPT = '<script src="static/vendor/leaflet/leaflet.js">'
LEAFLET_CSS = '<link rel="stylesheet" href="static/vendor/leaflet/leaflet.css">'
ZONE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MOCK_FILES = {
    "status": ("version", "xplane", "doctor", "home"),
    "providers": None,
    "settings": ("essential", "advanced", "expert"),
    "settings_schema": ("properties", "x-levels"),
    "airports": None,
    "plan": ("network", "compute", "disk", "tiles", "warnings"),
    "jobs": None,
    "job": ("id", "status", "tiles", "errors", "stats"),
    "job_done": ("id", "status", "tiles", "errors", "stats", "report"),
    "job_events": ("format", "osm", "osm_hit", "tile"),
    "library": None,
    "zones": ("format", "revision", "zones", "problems"),
}
STEPS = ("data", "terrain", "coast", "imagery", "assembly", "install")
JOURNAL_EVENTS = {"started", "progress", "done", "failed", "stats", "log", "finished"}
STATS_KEYS = {"running", "pending", "done", "failed", "hits", "elapsed_s", "progress", "eta_low_s"}
STATS_KEYS |= {"eta_high_s", "phase"}
NODE_KEYS = {"node", "role", "status", "key", "hit", "wall_s", "fraction", "weight_s"}


def _app() -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=UI), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(UI / INDEX_FILE)

    return app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client() -> AsyncClient:
    transport = ASGITransport(app=_app())
    return AsyncClient(transport=transport, base_url="http://ui")


def _index_refs() -> list[str]:
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    return [r for r in refs if not r.startswith(("#", "data:"))]


def _node_json(module: str, expression: str) -> Any:
    """Import ``module`` of the page as ``m`` under node and return ``expression`` as JSON."""
    if NODE is None:
        pytest.skip("node is not installed")
    script = f"import('./{module}').then(m => process.stdout.write(JSON.stringify({expression})))"
    return _run_node(script)


def _run_node(script: str, env: dict[str, str] | None = None) -> Any:
    """Run the ES module ``script`` from the page's directory and return what it writes, as JSON.

    The script goes through stdin, not ``-e``: the mock prelude alone is longer than a Windows
    command line (``WinError 206``). The output is read as UTF-8 whatever the locale says.
    """
    assert NODE is not None
    out = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        cwd=UI,
        capture_output=True,
        encoding="utf-8",
        check=True,
        env=env,
    )
    return json.loads(out.stdout)


def _i18n_tables() -> dict[str, dict[str, str]]:
    return _node_json("i18n.js", "m.STRINGS")


# -- serving ---------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_index_and_referenced_files_are_served(client: AsyncClient) -> None:
    async with client:
        res = await client.get("/")
        assert res.status_code == 200
        assert "<title>OrthoStudio XP</title>" in res.text
        for ref in _index_refs():
            assert ref.startswith("static/"), ref
            assert (UI / ref.removeprefix("static/")).is_file(), ref
            got = await client.get(f"/{ref}")
            assert got.status_code == 200, ref
        for name in STATIC_FILES:
            assert (UI / name).is_file()


@pytest.mark.anyio
async def test_mock_files_are_served_and_valid(client: AsyncClient) -> None:
    async with client:
        for name, keys in MOCK_FILES.items():
            res = await client.get(f"/static/mock/{name}.json")
            assert res.status_code == 200, name
            doc = res.json()
            if keys is None:
                assert isinstance(doc, list), name
            else:
                for key in keys:
                    assert key in doc, (name, key)


# -- hygiene ---------------------------------------------------------------------------------


def test_no_external_url() -> None:
    # The page's own files only: Leaflet's (vendor/) carry its licence and bug-tracker links in
    # comments, and the page disables its attribution prefix, the only link it would render.
    for name in TEXT_FILES:
        text = (UI / name).read_text(encoding="utf-8")
        # an SVG's namespace is a name, never fetched: the lists' arrows are drawn in the stylesheet
        text = text.replace("http://www.w3.org/2000/svg", "")
        assert not re.search(r"https?://", text), name
    for path in (UI / "mock").glob("*.json"):
        assert not re.search(r"https?://", path.read_text(encoding="utf-8")), path.name
    assert "attributionControl.setPrefix(false)" in (UI / "map.js").read_text(encoding="utf-8")


def test_no_framework_or_cdn_script() -> None:
    # Decision M1 of docs/specs/map-zones.md: Leaflet 1.9.4 is the one library, vendored under
    # static/vendor/leaflet/ (same origin, no CDN) and loaded as a classic script before app.js.
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    scripts = re.findall(r"<script[^>]*>", html)
    assert scripts == [LEAFLET_SCRIPT, '<script type="module" src="static/app.js">']
    links = re.findall(r'<link rel="stylesheet"[^>]*>', html)
    assert links == [LEAFLET_CSS, '<link rel="stylesheet" href="static/styles.css">']
    assert (UI / "vendor" / "leaflet" / "LICENSE").is_file()
    banner = (UI / "vendor" / "leaflet" / "leaflet.js").read_text(encoding="utf-8")[:200]
    assert "Leaflet 1.9.4" in banner


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_syntax() -> None:
    assert NODE is not None
    for path in UI.glob("*.js"):
        subprocess.run([NODE, "--check", str(path)], check=True)


# -- i18n ------------------------------------------------------------------------------------


def test_every_literal_key_exists_in_both_languages() -> None:
    tables = _i18n_tables()
    assert set(tables) == {"fr", "en"}
    assert set(tables["fr"]) == set(tables["en"])
    code = "\n".join((UI / name).read_text(encoding="utf-8") for name in PAGE_MODULES)
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    keys = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", code))
    keys |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
    assert keys, "no t() call found"
    for lang in ("fr", "en"):
        missing = sorted(k for k in keys if k not in tables[lang])
        assert not missing, (lang, missing)


def test_an_engine_that_answers_with_an_error_is_announced() -> None:
    """A user on Windows saw the fields greyed and nothing else: the engine was answering with an
    error, and the only sign was one line at the foot of the page (flusi.info, 2026-09-20). The
    banner that already announces an engine older than the page says this too."""
    code = (UI / "app.js").read_text(encoding="utf-8")
    load = re.search(r"\nasync function loadStatus\(\) \{.*?\n\}\n", code, re.S)
    assert load is not None
    assert "state.engineError = errorMessage(err)" in load.group(0)
    assert "renderEngineBanner()" in load.group(0)
    assert "state.engineError = null" in load.group(0)  # gone as soon as it answers again
    banner = re.search(r"\nfunction renderEngineBanner\(\) \{.*?\n\}\n", code, re.S)
    assert banner is not None
    assert 't("app.engine_error"' in banner.group(0) and 't("app.engine_outdated")' in banner.group(
        0
    )
    assert "location.reload()" in banner.group(0)


def test_no_dynamic_t_call() -> None:
    """Dynamic keys must go through tOpt() so the literal-key check stays complete."""
    for name in PAGE_MODULES:
        code = (UI / name).read_text(encoding="utf-8")
        dynamic = re.findall(r"""\bt\(\s*(?!["'])[^)]*\)""", code)
        assert dynamic == [], (name, dynamic)


def test_languages_differ() -> None:
    tables = _i18n_tables()
    same = [k for k, v in tables["fr"].items() if v == tables["en"][k]]
    # A few keys are legitimately identical (Plan, ZL, OrthoStudio XP…); most must be translated.
    assert len(same) < len(tables["fr"]) // 4, same


# -- contract mapping ------------------------------------------------------------------------


def test_role_step_mapping_is_the_engines() -> None:
    """The page maps node roles to the six steps as ``orthostudio.api.stages`` does; the rule names
    of the P2b contract (older mock files and journals) and the last segment of a node id
    still map."""
    from orthostudio.api.stages import ROLE_STAGE, STAGES

    got = _node_json(
        "app.js",
        """({steps: m.STEPS, roles: m.ROLE_STEP, rules: m.NODE_STEP, of: [
          m.stepOfNode("+46+006/BI16/dsf#2"), m.stepOfNode("+46+006/osm", "osm"),
          m.stepOfNode("+43+005/tile.dsf#2"), m.stepOfNode("+43+005/tile.textures"),
          m.stepOfNode("+46+006/BI16/textures", "dsf"), m.stepOfNode("+46+006/constructor"),
          m.stepOfNode(null, "toString"), m.stepOfNode("+46+006/what")]})""",
    )
    assert tuple(got["steps"]) == STAGES == STEPS
    assert got["roles"] == ROLE_STAGE
    assert set(got["rules"].values()) <= set(STEPS)
    # the role wins over the id; unknown names and Object's own keys map to nothing
    assert got["of"] == ["assembly", "data", "assembly", "imagery", "assembly", None, None, None]


def _mock_json(name: str) -> Any:
    return json.loads((UI / "mock" / f"{name}.json").read_text(encoding="utf-8"))


def test_mock_events_follow_the_engine_journal() -> None:
    """``mock/job_events.json`` holds journal entries in the engine's flat shape (api.md 5.2):
    ``tile``, ``stage``, ``node``, ``role`` on every node entry, never ``step``, ``node_id`` or a
    ``data`` envelope; the mock adds ``seq`` and ``ts`` when it writes them."""
    from orthostudio.api.stages import ROLE_STAGE

    doc = _mock_json("job_events")
    assert doc["format"] == "osxp-mock-journal-1"
    fields = {
        "started": {"tile", "stage", "node", "role", "key", "kind", "weight_s"},
        "progress": {"tile", "stage", "node", "role", "fraction", "message", "weight_s"},
        "done": {"tile", "stage", "node", "role", "key", "hit", "wall_s", "weight_s"},
        "failed": {"tile", "stage", "node", "role", "error", "skipped", "cause", "weight_s"},
        "log": {"tile", "stage", "node", "role", "message"},
    }
    roles: dict[str, set[str]] = {}
    for part in ("osm", "osm_hit", "tile"):
        for entry in doc[part]:
            data = {k: v for k, v in entry.items() if k not in ("delay_ms", "only")}
            event = data.pop("event")
            assert event in JOURNAL_EVENTS, entry
            assert isinstance(entry["delay_ms"], int) and entry["delay_ms"] >= 0, entry
            assert entry.get("only") in (None, "ok", "failing"), entry
            assert not {"step", "node_id", "data", "seq", "ts"} & set(data), entry
            assert set(data) == fields[event], entry
            assert data["tile"] == "{tile}" and data["node"].startswith("{tile}/"), entry
            role = data["node"].split("/")[-1]
            assert data["role"] == role and ROLE_STAGE[role] == data["stage"], entry
            roles.setdefault(part, set()).add(role)
            if event == "progress":
                assert 0 <= data["fraction"] <= 1
            if event == "done":
                assert isinstance(data["hit"], bool) and data["wall_s"] >= 0
                assert (data["weight_s"] == 0) is data["hit"]  # a hit weighs nothing
            if event == "failed":
                error = data["error"]
                assert {"code", "severity", "action", "message", "remedy"} <= set(error)
                assert data["skipped"] is (data["cause"] is not None)
                assert (data["weight_s"] == 0) is data["skipped"]  # skipped before it started
    assert roles["osm"] == roles["osm_hit"] == {"osm"}
    assert doc["osm_hit"][0]["event"] == "done" and doc["osm_hit"][0]["hit"] is True
    assert roles["tile"] == set(ROLE_STAGE) - {"osm"}
    tile = doc["tile"]
    assert any(e["event"] == "done" and e["hit"] for e in tile)  # a cache hit in every build
    failing = [e for e in tile if e.get("only") == "failing"]
    codes = [e["error"]["code"] for e in failing]
    assert codes == ["TEX_MISSING", *["SYS_UPSTREAM_FAILED"] * 2]
    assert failing[0]["error"]["action"] == "retry"


def test_mock_jobs_follow_the_engine_state() -> None:
    """``job.json`` (a job that just started, the mock's template) and ``job_done.json`` (a real
    6-tile build, 3 tiles installed, 3 missing a texture) answer like ``Job.state()``; ``jobs.json``
    like ``Job.summary()``."""
    from orthostudio.api.stages import ROLE_STAGE

    summary = {"id", "status", "created_at", "started_at", "finished_at", "install", "tiles"}
    summary |= {"provider", "zl", "relief", "ok"}
    state = summary | {"request", "errors", "stats", "eta", "last_seq", "report", "decisions"}
    for name in ("job", "job_done"):
        doc = _mock_json(name)
        assert set(doc) == state, name
        assert set(doc["stats"]) == STATS_KEYS, name
        for tile in doc["tiles"]:
            assert tuple(tile["stages"]) == STEPS, name
            for stage, st in tile["stages"].items():
                assert set(st) == {"status", "fraction", "wall_s", "nodes"}
                for node in st["nodes"]:
                    assert set(node) == NODE_KEYS, node
                    assert ROLE_STAGE[node["role"]] == stage and node["node"].startswith(
                        tile["tile"]
                    )
        for err in doc["errors"]:
            assert err["action"] in {"retry", "settings", "none"}
            assert err["severity"] in {"blocking", "degraded", "info"}
            assert "traceback" not in err["context"]
    done = _mock_json("job_done")
    statuses = [t["status"] for t in done["tiles"]]
    assert statuses.count("done") == 3 and statuses.count("failed") == 3
    assert [d["installed"] for d in done["decisions"] if d["kind"] == "pack"] == [True] * 3
    assert {k: done[k] for k in summary} | {"tiles": [t["tile"] for t in done["tiles"]]} == (
        _mock_json("jobs")[0]
    )


def test_mock_schema_is_the_engines() -> None:
    """The mock answers GET /api/settings/schema with the engine's own schema, and its settings
    are a valid document of it."""
    from orthostudio.config import Settings, settings_schema

    doc = json.loads((UI / "mock" / "settings_schema.json").read_text(encoding="utf-8"))
    assert doc == json.loads(json.dumps(settings_schema()))
    Settings.model_validate(_mock_json("settings"))


# -- zones and map geometry (docs/specs/map-zones.md sections 3 and 7) ------------------------


def test_mock_zones_follow_the_document_format() -> None:
    doc = json.loads((UI / "mock" / "zones.json").read_text(encoding="utf-8"))
    providers = {
        p["code"]: p for p in json.loads((UI / "mock" / "providers.json").read_text("utf-8"))
    }
    assert doc["format"] == "osxp-zones-1"
    # GET /api/zones: the revision the page sends back in If-Match, and no problem in the fixture
    assert re.fullmatch(r"[0-9a-f]{64}", doc["revision"]) and doc["problems"] == []
    assert 0 < len(doc["zones"]) <= 500
    ids = [z["id"] for z in doc["zones"]]
    assert len(set(ids)) == len(ids)
    for zone in doc["zones"]:
        assert set(zone) <= {"id", "name", "zl", "provider", "photo", "polygon"}, zone["id"]
        assert {"id", "name", "zl", "provider", "polygon"} <= set(zone), zone["id"]
        assert ZONE_ID_RE.match(zone["id"]), zone["id"]
        assert isinstance(zone["name"], str) and len(zone["name"]) <= 80
        assert isinstance(zone["zl"], int) and 12 <= zone["zl"] <= 20
        if zone["provider"] is not None:
            assert zone["zl"] <= providers[zone["provider"]]["max_zl"]
        ring = zone["polygon"]
        assert 3 <= len(ring) <= 2000 and ring[0] != ring[-1], zone["id"]
        for lon, lat in ring:
            assert -180 <= lon <= 180 and -85 <= lat <= 85, zone["id"]
    # The page's own validator (used by the mock PUT) accepts it; a zone above its source's
    # maximum and a crossing ring are refused, as the engine refuses them.
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    bow_tie = [[0, 0], [1, 1], [0, 1], [1, 0]]
    too_fine = {"id": "a", "name": "", "zl": 19, "provider": "JP", "polygon": square}
    crossing = {"id": "b", "name": "", "zl": 18, "provider": None, "polygon": bow_tie}
    docs = [doc, *({"format": "osxp-zones-1", "zones": [z]} for z in (too_fine, crossing))]
    registry = json.dumps(list(providers.values()))
    calls = ", ".join(f"m.validateZonesDocument({json.dumps(d)}, {registry})" for d in docs)
    problems = _node_json("geo.js", f"[{calls}]")
    assert problems[0] is None
    assert problems[1]["zone"] == "a" and "JP" in problems[1]["reason"]
    assert problems[2]["zone"] == "b"


POINTS = [  # (lat, lon, zl): airports, both hemispheres, a coarse and the finest level
    (46.2381, 6.1089, 18),
    (43.4393, 5.2214, 17),
    (-33.9465, 151.1731, 16),
    (40.6413, -73.7781, 19),
    (64.1300, -21.9400, 12),
    (-22.8100, -43.2506, 20),
]


def test_texture_square_is_the_engine_texture() -> None:
    """Ctrl+click: the zone is the texture of orthostudio.imagery.grid containing the point."""
    calls = ", ".join(f"m.textureSquare({lon}, {lat}, {zl})" for lat, lon, zl in POINTS)
    squares = _node_json("geo.js", f"[{calls}]")
    for (lat, lon, zl), square in zip(POINTS, squares, strict=True):
        lat_max, lon_min, lat_min, lon_max = texture_bbox(texture_at(lat, lon, zl, "BI"))
        expected = [lon_min, lat_min, lon_max, lat_min, lon_max, lat_max, lon_min, lat_max]
        got = [c for vertex in square for c in vertex]
        assert got == pytest.approx(expected, abs=1e-9), (lat, lon, zl)


def test_snapped_point_is_the_ortho4xp_grid_corner() -> None:
    """Ctrl+Shift+click: Ortho4XP's newPointGrid (next corner once 8 tiles of 16 in)."""
    calls = ", ".join(f"m.snapToTextureCorner({lon}, {lat}, {zl})" for lat, lon, zl in POINTS)
    corners = _node_json("geo.js", f"[{calls}]")
    for (lat, lon, zl), corner in zip(POINTS, corners, strict=True):
        tex = texture_at(lat, lon, zl, "BI")
        gx, gy = wgs84_to_gtile(lat, lon, zl)
        x = tex.til_x + 16 if gx - tex.til_x >= 8 else tex.til_x
        y = tex.til_y + 16 if gy - tex.til_y >= 8 else tex.til_y
        exp_lat, exp_lon = tile_to_wgs84(x, y, zl)
        assert corner == pytest.approx([exp_lon, exp_lat], abs=2e-9), (lat, lon, zl)


def test_zone_tiles_and_sizes() -> None:
    got = _node_json(
        "geo.js",
        """({
          four: m.tilesTouched([[5.9, 45.9], [6.1, 45.9], [6.1, 46.1], [5.9, 46.1]]),
          edge: m.tilesTouched([[6, 46.2], [7, 46.2], [7, 46.8], [6, 46.8]]),
          square: m.zoneTextureKeys(m.textureSquare(6.1089, 46.2381, 18), 18, "+46+006").length,
          outside: m.zoneTextureKeys(m.textureSquare(6.1089, 46.2381, 18), 18, "+46+005").length,
          tile: [m.tileTextureCount("+43+005", 16), m.tileTextureCount("-34+151", 14)],
          simple: [m.polygonIsSimple([[0, 0], [1, 0], [1, 1], [0, 1]]),
                   m.polygonIsSimple([[0, 0], [1, 1], [0, 1], [1, 0]]),
                   m.polygonIsSimple([[0, 0], [2, 0], [1, 0], [1, 1]])],
          insert: m.insertIndexForZl([{zl: 19}, {zl: 18}, {zl: 17}], 18),
          ids: [m.newZoneId([]), m.newZoneId([{id: "zabc"}], 1, () => 0)],
          reach: [m.tilesInBounds([[6.09, 46.22], [6.13, 46.25], [6.09, 46.25]]),
                  m.tilesInBounds([[5.9, 45.9], [6.1, 46.1], [6.1, 45.9], [5.9, 46.1]]),
                  m.tilesInBounds([[6, 46.2], [7, 46.8], ["x", 1]]),
                  m.tilesInBounds("not a polygon")],
        })""",
    )
    # what a request sends: every tile a zone's bounding box reaches, valid polygon or not
    assert got["reach"] == [
        ["+46+006"],
        ["+45+005", "+45+006", "+46+005", "+46+006"],
        ["+46+006"],
        [],
    ]
    assert got["four"] == ["+45+005", "+45+006", "+46+005", "+46+006"]
    # a zone along the meridians 6 and 7 touches +46+005 and +46+007 without covering them
    assert got["edge"] == ["+46+006"]
    # a texture zone overlaps its own texture only (the engine's 1e-10 deg² rule)
    assert got["square"] == 1 and got["outside"] == 0
    for count, (lat, lon, zl) in zip(got["tile"], [(43, 5, 16), (-34, 151, 14)], strict=True):
        assert count == len(textures_covering(lat + 1, lon, lat, lon + 1, zl, "BI"))
    assert got["simple"] == [True, False, False]
    assert got["insert"] == 1  # a finer zone goes above the coarser ones (Ortho4XP's ZL order)
    assert all(ZONE_ID_RE.match(i) for i in got["ids"])


# -- country borders (docs/specs/map-zones.md section 7.2) -----------------------------------

BORDERS = UI / "vendor" / "borders" / "borders.json"


def _decode_borders(doc: dict[str, Any]) -> dict[str, list[list[list[float]]]]:
    """``osxp-borders-1`` (tools/borders/build_borders.py) read without geo.js."""
    out: dict[str, list[list[list[float]]]] = {name: [] for name in doc["classes"]}
    for line in doc["lines"]:
        x = y = 0
        points = []
        for i in range(1, len(line), 2):
            x += line[i]
            y += line[i + 1]
            points.append([y / doc["scale"], x / doc["scale"]])
        out[doc["classes"][line[0]]].append(points)
    return out


def test_borders_are_vendored_with_their_source() -> None:
    doc = json.loads(BORDERS.read_text(encoding="utf-8"))
    assert doc["format"] == "osxp-borders-1"
    assert doc["classes"] == ["international", "disputed"]
    readme = (BORDERS.parent / "README.md").read_text(encoding="utf-8")
    assert "Natural Earth" in readme and "public domain" in readme
    assert "vendor/borders/borders.json" in STATIC_FILES
    assert '"static/vendor/borders/borders.json"' in (UI / "map.js").read_text(encoding="utf-8")
    lines = _decode_borders(doc)
    assert all(lines.values())
    # The France-Switzerland border passes west of Geneva: a file regenerated without the land
    # borders, or with latitude and longitude swapped, fails here.
    assert any(
        46.1 < lat < 46.4 and 5.9 < lon < 6.3
        for line in lines["international"]
        for lat, lon in line
    )


def test_decode_borders_reads_the_format_and_refuses_another() -> None:
    doc = json.loads(BORDERS.read_text(encoding="utf-8"))
    by_class: dict[int, list[list[int]]] = {}
    for line in sorted(doc["lines"], key=len):
        by_class.setdefault(line[0], []).append(line)
    sample = {**doc, "lines": [line for lines in by_class.values() for line in lines[:2]]}
    assert {line[0] for line in sample["lines"]} == {0, 1}
    assert _node_json("geo.js", f"m.decodeBorders({json.dumps(sample)})") == _decode_borders(sample)
    head = {"format": "osxp-borders-1", "scale": 10000, "classes": ["international"]}
    wrong = [
        {**head, "format": "osxp-zones-1", "lines": []},
        {**head, "lines": [[0, 1, 2, 3]]},  # one point and a half
        {**head, "lines": [[1, 1, 2, 3, 4]]},  # no class 1
    ]
    verdicts = _node_json(
        "geo.js",
        f"{json.dumps(wrong)}.map((d) => {{ try {{ m.decodeBorders(d); return 'read'; }} "
        "catch (e) { return 'refused'; } })",
    )
    assert verdicts == ["refused", "refused", "refused"]


# -- zones persistence, engine errors and the disk line (review of the map and zones page) -----


def _function_body(code: str, name: str) -> str:
    match = re.search(rf"\n(?:export )?(?:async )?function {name}\(.*?\n\}}\n", code, re.S)
    assert match is not None, name
    return match.group(0)


def test_saved_zones_with_problems_stay_listed() -> None:
    """A zone the engine flags stays in the list, marked; problems naming no zone of the list are
    kept for the notice; ids stay unique so that a row never acts on its twin."""
    square = [[6.1, 46.2], [6.2, 46.2], [6.2, 46.3], [6.1, 46.3]]
    doc = {
        "format": "osxp-zones-1",
        "revision": "ab" * 32,
        "zones": [
            {"id": "a", "name": "A", "zl": 18, "provider": None, "polygon": square},
            {"id": "b", "name": "B", "zl": 19, "provider": "USGS", "polygon": square},
            {"id": "a", "name": "twin", "zl": 17, "provider": None, "polygon": square},
            {"name": "no id", "zl": 16, "provider": None, "polygon": square},
        ],
        "problems": [
            {
                "zone": "b",
                "index": 1,
                "code": "ZONE_INVALID",
                "reason": "zl 19 is above 18",
                "message": "Zone b is invalid: zl 19 is above 18.",
            },
            {
                "zone": None,
                "index": 4,
                "code": "ZONE_INVALID",
                "reason": "not an object",
                "message": "Zone entry 4 of zones.json could not be read.",
            },
            {
                "zone": None,
                "index": None,
                "code": "ZONE_INVALID",
                "reason": "/h/zones.json: not JSON",
                "message": "",
            },
        ],
    }
    read = f"m.readZonesDocument({json.dumps(doc)})"
    got = _node_json("map.js", f"(r => ({{...r, marks: [...r.marks]}}))({read})")
    assert [z["name"] for z in got["zones"]] == ["A", "B", "twin", "no id"]
    ids = [z["id"] for z in got["zones"]]
    assert ids[:2] == ["a", "b"] and len(set(ids)) == 4
    assert all(ZONE_ID_RE.match(i) for i in ids)
    assert got["revision"] == "ab" * 32
    assert got["marks"] == [["b", "zl 19 is above 18"]]
    assert got["fileProblems"] == [
        "Zone entry 4 of zones.json could not be read.",
        "/h/zones.json: not JSON",
    ]
    empty = _node_json("map.js", "m.readZonesDocument(null)")
    assert empty == {"zones": [], "tiles": {}, "revision": None, "marks": {}, "fileProblems": []}


def test_saves_are_conditional_and_survive_closing_the_page() -> None:
    got = _node_json("map.js", '[m.ifMatch("ab12"), m.ifMatch(""), m.ifMatch(null)]')
    assert got == [{"If-Match": '"ab12"'}, {"If-Match": '""'}, {}]
    # keepalive below 60 KB of UTF-8 only: browsers refuse a keepalive body above 64 KiB
    limits = _node_json(
        "app.js",
        '[m.KEEPALIVE_MAX_BYTES, m.keepaliveAllowed("x".repeat(59999)), '
        'm.keepaliveAllowed("x".repeat(60000)), m.keepaliveAllowed("\u00e9".repeat(29999)), '
        'm.keepaliveAllowed("\u00e9".repeat(30000))]',
    )
    assert limits == [60000, True, False, True, False]
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "headers: ifMatch(zs.revision), keepalive: true" in map_js
    assert '"ZONE_CONFLICT"' in map_js and "reloadAfterConflict" in map_js
    assert '"visibilitychange"' in map_js and '"beforeunload"' in map_js
    # Every request to the engine goes through api() (headers, mock, keepalive rule); the one
    # fetch left reads a static file of the page itself, the country borders (map-zones.md 7.2).
    assert map_js.count("fetch(") == map_js.count("fetch(BORDERS_URL)") == 1
    # Estimate and Build always send the zones of the list (none of them when it could not load)
    assert "zones," in _function_body(app_js, "planRequest")
    assert "zonesForRequest" in _function_body(app_js, "planRequest")


def test_zone_errors_show_the_engine_message() -> None:
    got = _node_json(
        "app.js",
        '[m.codeWords({code: "ZONE_INVALID", message: "Zone lsgg is invalid: zl 19 is above 18.",'
        ' remedy: "Fix it."}), m.codeWords({code: "ZONE_TOO_MANY", message: "Too many zones for'
        ' +46+006: 300, at most 254."}), m.codeWords({code: "XP_RUNNING", message: "running"}),'
        ' m.codeWords({code: "NEW_CODE", message: "m", remedy: "r"})]',
    )
    assert got[0] == ["Zone lsgg is invalid: zl 19 is above 18.", "Fix it."]
    assert got[1] == ["Too many zones for +46+006: 300, at most 254.", ""]
    assert got[2][0] != "running"  # a code the page knows keeps the page's words
    assert got[3] == ["m", "r"]


def test_disk_line_follows_the_engine_verdict() -> None:
    """Step 3 shows needed_gb and the engine's ok (free > 2 x needed), never its own rule."""
    got = _node_json(
        "app.js",
        "[m.diskVerdict({free_gb: 5, dds_gb: 2, needed_gb: 3, ok: false}),"
        " m.diskVerdict({free_gb: 1, dds_gb: 2, needed_gb: 3, ok: true}),"
        " m.diskVerdict({free_gb: 7, dds_gb: 3}), m.diskVerdict({free_gb: 6, dds_gb: 3}),"
        " m.diskVerdict({})]",
    )
    assert got[0] == {"free": 5, "needed": 3, "ok": False}  # more free than needed, still not ok
    assert got[1]["ok"] is True  # the engine's verdict wins
    assert got[2]["ok"] is True and got[3]["ok"] is False  # an older answer: the same rule
    assert got[4] == {"free": None, "needed": None, "ok": None}
    panel = _function_body((UI / "app.js").read_text(encoding="utf-8"), "renderPlanPanel")
    assert "diskVerdict(plan.disk)" in panel and "dds_gb" not in panel
    disk = json.loads((UI / "mock" / "plan.json").read_text(encoding="utf-8"))["disk"]
    assert disk["ok"] == (disk["free_gb"] > 2 * disk["needed_gb"])


def test_ui_dir_is_the_package_directory() -> None:
    assert ui_dir() == Path(__file__).resolve().parents[1] / "src" / "orthostudio" / "ui"


def test_the_plan_says_how_many_tiles_one_build_takes() -> None:
    """A pilot flying IFR long haul asked whether 64 tiles a build was on purpose (2026-09-22): the
    engine refused more with a list error. The cap is 500 now, the page knows it and says it in
    plain words before asking anything, and a Build then starts nothing."""
    import pytest
    from pydantic import ValidationError

    from orthostudio.api.models import MAX_TILES, PlanRequest

    js = (UI / "app.js").read_text(encoding="utf-8")
    level = re.search(r"const MAX_BUILD_TILES = (\d+);", js)
    assert level is not None and int(level.group(1)) == MAX_TILES == 500
    body = re.search(r"\nfunction estimate\(\) \{.*?\n\}\n", js, re.S)
    assert body is not None
    code = body.group(0)
    assert code.index("state.tiles.length > MAX_BUILD_TILES") < code.index("await planRequest()")
    names = [f"+{lat:02d}+{lon:03d}" for lat in range(10, 40) for lon in range(0, 20)]
    PlanRequest.model_validate({"tiles": names[:500], "zoom_level": 14})
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({"tiles": names[:501], "zoom_level": 14})
    for lang in ("en", "fr"):
        text = _node_json(
            "i18n.js",
            f'(globalThis.document = {{documentElement: {{}}}}, m.setLanguage("{lang}"),'
            ' m.t("plan.too_many_tiles", {max: 500, n: 612}))',
        )
        assert "500" in text and "612" in text, text


def test_the_page_and_the_engine_agree_on_the_api_level() -> None:
    """The page asks for a restart when the engine is older than itself (a ``osxp serve`` started
    before an update showed "Not Found" and a blank map): both numbers move together."""
    from orthostudio.api.app import API_LEVEL

    level = re.search(r"const PAGE_API_LEVEL = (\d+);", (UI / "app.js").read_text(encoding="utf-8"))
    assert level is not None and int(level.group(1)) == API_LEVEL
    assert (
        json.loads((UI / "mock" / "status.json").read_text(encoding="utf-8"))["api_level"]
        == API_LEVEL
    )


# -- library (docs/specs/ui.md section 2.3) ---------------------------------------------------

LIBRARY_ROW_KEYS = {
    "photo",  # the colours the pack was built with, or null
    "built",  # {facts, at}: what the pack was built with, and when (null for what we did not build)
    "tile",
    "kind",
    "provider",
    "zl",
    "path",
    "name",
    "built_by",
    "installed",
    "keys",
    "registered_at",
    "updated_at",
    "size_bytes",
    "present",
    "overlay",
}
MOCK_PRELUDE = """
import { readFileSync } from "node:fs";
const query = `mock=1&fail=${process.env.OSXP_FAIL || ""}&speed=${process.env.OSXP_SPEED || ""}`;
globalThis.location = new URL(`http://ui/index.html?${query}`);
globalThis.fetch = async (url) => {
  const file = String(url).replace(/^static\\//, "");
  return { ok: true, status: 200, json: async () => JSON.parse(readFileSync(file, "utf8")) };
};
const m = await import("./app.js");
const i = await import("./i18n.js");
const call = (method, path, body) => m.mockApi(method, path, body).then(
  (ok) => ({ ok }),
  (err) => ({ status: err.status, code: err.detail?.error?.code ?? null }),
);
"""


def _node_mock(script: str, fail: str = "", speed: float = 1) -> Any:
    """Run ``script`` after ``MOCK_PRELUDE`` (the page's mock API under node, its JSON files read
    from disk) and return what it writes as JSON."""
    if NODE is None:
        pytest.skip("node is not installed")
    return _run_node(
        MOCK_PRELUDE + script, env={**os.environ, "OSXP_FAIL": fail, "OSXP_SPEED": str(speed)}
    )


def _tile_order(row: dict[str, Any]) -> tuple[int, int, str, str]:
    return int(row["tile"][:3]), int(row["tile"][3:]), row["kind"], row["path"]


def test_mock_library_looks_like_the_engine() -> None:
    """One tile pack row per tile, plus one row of the shared overlays pack per OrthoStudio XP tile,
    in the engine's order; the status counts tiles, not rows."""
    rows = json.loads((UI / "mock" / "library.json").read_text(encoding="utf-8"))
    status = json.loads((UI / "mock" / "status.json").read_text(encoding="utf-8"))
    assert rows == sorted(rows, key=_tile_order)
    ortho = [r for r in rows if r["kind"] == "ortho"]
    overlay = [r for r in rows if r["kind"] == "overlay"]
    assert len(ortho) + len(overlay) == len(rows) and overlay
    for r in rows:
        assert set(r) == LIBRARY_ROW_KEYS, r
        assert r["built_by"] in {"osxp", "ortho4xp"}, r
        assert r["name"] == Path(r["path"]).name
    tiles = [r["tile"] for r in ortho]
    assert len(set(tiles)) == len(tiles)
    assert status["library_count"] == len(tiles)
    for r in ortho:
        prefix = "zOrthoStudio_" if r["built_by"] == "osxp" else "zOrtho4XP_"
        assert r["name"] == f"{prefix}{r['tile']}" and r["zl"]
        assert (r["size_bytes"] is None) == (r["present"] is False), r["tile"]
    for r in overlay:
        assert r["name"] == "yOrthoStudio_Overlays" and r["tile"] in tiles
        assert r["zl"] is None and r["size_bytes"] is None
    assert sorted(r["tile"] for r in overlay) == sorted(
        r["tile"] for r in ortho if r["built_by"] == "osxp"
    )
    # every rendering path of the screen: in X-Plane or not, files missing, both builders
    assert {r["installed"] for r in ortho} == {True, False}
    assert any(r["present"] is False and r["built_by"] == "osxp" for r in ortho)
    assert {r["built_by"] for r in ortho} == {"osxp", "ortho4xp"}


def test_library_table_and_delete_dialog_markup() -> None:
    """No Path column; the empty row spans the table; a native dialog asks before deleting."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    table = re.search(r'<table[^>]*id="library-table".*?</thead>', html, re.S)
    assert table is not None
    spans = [re.search(r'colspan="(\d+)"', th) for th in re.findall(r"<th\b[^>]*>", table.group(0))]
    columns = sum(int(span.group(1)) if span else 1 for span in spans)
    keys = re.findall(r'data-i18n="([^"]+)"', table.group(0))
    assert "library.path" not in keys and "library.zl" not in keys
    # Sizes count the cache's hard-linked textures, which Store counts too: the header says so
    size = re.search(r'<th\b[^>]*data-i18n="library.size"[^>]*>', table.group(0))
    assert size is not None and 'data-i18n-title="library.size_help"' in size.group(0)
    render = _function_body((UI / "app.js").read_text(encoding="utf-8"), "renderLibrary")
    assert f"colspan: {columns}" in render  # the "no tile" row spans the whole table
    assert "libraryTiles(state.library)" in render  # overlay rows are not rows of their own
    dialog = re.search(r'<dialog id="library-delete".*?</dialog>', html, re.S)
    assert dialog is not None and 'method="dialog"' in dialog.group(0)
    assert 'value="delete"' in dialog.group(0) and "autofocus" in dialog.group(0)


def test_library_rows_and_the_delete_question() -> None:
    got = _node_mock(
        """
        const rows = (await call("GET", "/api/library")).ok;
        const name = "zOrthoStudio_+45+005";
        const base = {tile: "+45+005", name, path: `/h/${name}`};
        const ask = (extra) => m.deleteQuestion({...base, present: true, ...extra});
        process.stdout.write(JSON.stringify({
          tiles: m.libraryTiles(rows).map((e) => e.tile),
          noKind: m.libraryTiles([{tile: "+1+1"}, {tile: "+1+1", kind: "overlay"}]).length,
          installed: ask({installed: true, size_bytes: 1.2e9}),
          notInstalled: ask({installed: false, size_bytes: 1.2e9}),
          noSize: ask({installed: true, size_bytes: null}),
          missing: [ask({installed: true, present: false}),
                    ask({installed: false, present: false})],
          size: i.fmtBytes(1.2e9),
          freed: [1.1e9, 0, 830].map((bytes) => m.deletedMessage("+45+005", bytes)),
          freedSize: i.fmtBytes(1.1e9),
          deleted: i.t("library.deleted", {tile: "+45+005"}),
          warned: [m.deletedMessage("+45+005", 1.1e9, "no clean"),
                   m.deletedMessage("+45+005", 1.1e9, null)],
          later: i.t("library.deleted_cache_later", {tile: "+45+005"}),
          requests: ["install", "uninstall", "delete"].map((a) => m.libraryRequest(base, a)),
        }));
        """
    )
    rows = json.loads((UI / "mock" / "library.json").read_text(encoding="utf-8"))
    assert got["tiles"] == [r["tile"] for r in rows if r["kind"] == "ortho"]
    assert got["noKind"] == 1  # a row without kind (an older engine) is a tile pack

    def says(question: dict[str, Any], words: str) -> bool:
        return any(words in line for line in question["body"])

    for question in (got["installed"], got["notInstalled"], got["noSize"], *got["missing"]):
        assert "+45+005" in question["title"]
    # X-Plane is named only when the tile is in it, the size only when it is known
    assert says(got["installed"], "X-Plane") and not says(got["notInstalled"], "X-Plane")
    assert says(got["installed"], got["size"]) and says(got["notInstalled"], got["size"])
    assert not re.search(r"\d", " ".join(got["noSize"]["body"]))
    assert says(got["missing"][0], "X-Plane") and not says(got["missing"][1], "X-Plane")
    assert not any(re.search(r"\d", " ".join(q["body"])) for q in got["missing"])
    assert got["freedSize"] in got["freed"][0] and got["freed"][1] == got["deleted"]
    assert got["freed"][2] == got["deleted"]  # a fresh tile frees next to nothing at once
    # the engine's warning (the cache space could not be freed yet): no size, "later"
    assert got["warned"][0] == got["later"] and got["freedSize"] not in got["later"]
    assert got["freedSize"] in got["warned"][1]  # warning null: the space freed, as before
    # the name alone is ambiguous (two builds of the tile in two output folders share it): the
    # path goes too, for every change of a row
    assert got["requests"] == [
        {
            "path": f"/api/library/zOrthoStudio_%2B45%2B005/{action}",
            "body": {"path": "/h/zOrthoStudio_+45+005"},
        }
        for action in ("install", "uninstall", "delete")
    ]


LIBRARY_REFUSAL_CODES = (
    "XP_RUNNING",
    "SYS_BUSY",
    "SYS_PACK_NOT_OSXP",
    "SYS_WRITE_FAILED",
    "SYS_WORKING_DIR_INVALID",
    "XP_PACK_CONFLICT",
)


def test_library_refusals_are_in_plain_words() -> None:
    """The engine's text for these codes speaks of installing, paths, orthostudio.toml or osxp
    clean; the Library says what happened and what to do, with no Settings button under the card."""
    engine = {
        "message": "/Users/x/.orthostudio/tiles/zOrthoStudio_+45+005/orthostudio.toml could not be "
        "written.",
        "remedy": "Run osxp clean, then run Install again.",
        "severity": "info",
        "action": "settings",
    }
    got = _node_mock(
        f"""
        const engine = {json.dumps(engine)};
        const codes = {json.dumps(LIBRARY_REFUSAL_CODES)};
        process.stdout.write(JSON.stringify({{
          known: codes.map((code) => m.libraryCardContent({{...engine, code}})),
          other: m.libraryCardContent({{...engine, code: "NET_TIMEOUT", action: "retry"}}),
        }}));
        """
    )
    seen = set()
    for code, card in zip(LIBRARY_REFUSAL_CODES, got["known"], strict=True):
        assert card["error"]["code"] == code
        assert card["error"]["action"] == "none" and card["error"]["severity"] == "blocking"
        message, remedy = card["words"]
        assert message and remedy and (message, remedy) not in seen, code
        seen.add((message, remedy))
        for words in (message, remedy):
            assert not re.search(r"orthostudio\.toml|osxp clean|/|Install again", words), (
                code,
                words,
            )
    assert got["other"]["words"] is None
    assert got["other"]["error"] == {**engine, "code": "NET_TIMEOUT", "action": "retry"}


def test_a_failed_library_change_reloads_before_its_card() -> None:
    """A refusal may come after the engine changed something (a deletion stopped halfway, a tile
    deleted meanwhile): the library, the map and the status bar are read again, as after a
    success, before the card shows; a reload that fails keeps the rows shown."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    change = _function_body(app_js, "runLibraryChange")
    assert change.count("await loadLibrary()") == 1 and change.count("loadStatus()") == 1
    assert "renderLibrary()" not in change  # never the rows of before the change
    assert change.index("await loadLibrary()") < change.index("errors.append(")
    load = _function_body(app_js, "loadLibrary")
    assert "planMap?.libraryChanged()" in load and "state.library = []" not in load
    # install and uninstall send the row's path too, as JSON (the engine refuses another type)
    assert "libraryRequest(e, action)" in _function_body(app_js, "libraryAction")
    assert 'init.headers["Content-Type"] = "application/json"' in _function_body(app_js, "api")


def test_mock_api_changes_the_library_like_the_engine() -> None:
    script = """
    const rows = (await call("GET", "/api/library")).ok;
    const pick = (fn) => rows.find((e) => e.kind === "ortho" && fn(e));
    const send = (e, action, body) => {
      const r = m.libraryRequest(e, action);
      return call("POST", r.path, body === undefined ? r.body : body);
    };
    const installed = pick((e) => e.built_by === "osxp" && e.installed && e.present);
    const idle = pick((e) => e.built_by === "osxp" && !e.installed && e.present);
    const missing = pick((e) => e.built_by === "osxp" && e.present === false);
    const imported = pick((e) => e.built_by === "ortho4xp" && !e.installed);
    const before = (await call("GET", "/api/status")).ok.library_count;
    const nowhere = {path: "/not/in/the/library"};
    const out = {
      elsewhere: [await send(idle, "install", nowhere), await send(installed, "uninstall", nowhere),
                  await send(installed, "delete", nowhere)],
      add: await send(imported, "install"),
      imported: await send(imported, "delete"),
      installed: await send(installed, "delete"),
      idle: await send(idle, "delete"),
      missing: await send(missing, "delete"),
    };
    const after = (await call("GET", "/api/library")).ok;
    out.gone = [installed, idle, missing].map((e) => after.filter((r) => r.tile === e.tile).length);
    out.kept = after.filter((r) => r.path === imported.path).map((r) => r.installed);
    out.count = [before, (await call("GET", "/api/status")).ok.library_count];
    process.stdout.write(JSON.stringify(out));
    """
    got = _node_mock(script)
    # the row's path picks the pack, for every change: a path the library does not have is refused
    assert [answer["status"] for answer in got["elsewhere"]] == [422, 422, 422]
    assert got["add"]["ok"]["format"] == "osxp-install-1" and got["kept"] == [True]
    assert got["imported"] == {"status": 409, "code": "SYS_PACK_NOT_OSXP"}  # never deleted
    for name in ("installed", "idle", "missing"):
        answer = got[name]["ok"]
        assert answer["format"] == "osxp-delete-1" and answer["warning"] is None, name
    for name in ("installed", "idle"):
        assert got[name]["ok"]["pack_deleted"] is True and got[name]["ok"]["freed_bytes"] > 0
    assert got["installed"]["ok"]["removed_from_xplane"] is True
    assert got["idle"]["ok"]["removed_from_xplane"] is False
    assert got["missing"]["ok"]["freed_bytes"] == 0  # the engine only forgets it
    assert got["gone"] == [0, 0, 0]  # the tile's overlay row goes with it
    assert got["count"][1] == got["count"][0] - 3

    refusals = """
    const rows = (await call("GET", "/api/library")).ok;
    const send = (e, action) => {
      const r = m.libraryRequest(e, action);
      return call("POST", r.path, r.body);
    };
    const ours = rows.filter((e) => e.kind === "ortho" && e.built_by === "osxp" && e.present);
    const out = [await send(ours.find((e) => e.installed), "delete")];
    out.push(await send(ours.find((e) => !e.installed), "delete"));
    out.push(await send(ours.find((e) => e.installed), "uninstall"));
    process.stdout.write(JSON.stringify(out));
    """
    # X-Plane running: every change is refused, the delete of a tile X-Plane does not show too
    assert _node_mock(refusals, fail="xplane") == [{"status": 409, "code": "XP_RUNNING"}] * 3
    busy = _node_mock(refusals, fail="busy")
    assert busy[:2] == [{"status": 409, "code": "SYS_BUSY"}] * 2

    clean = """
    const rows = (await call("GET", "/api/library")).ok;
    const idle = rows.find((e) => e.built_by === "osxp" && e.kind === "ortho" && !e.installed);
    const r = m.libraryRequest(idle, "delete");
    const answer = await call("POST", r.path, r.body);
    const left = (await call("GET", "/api/library")).ok.filter((e) => e.tile === idle.tile).length;
    process.stdout.write(JSON.stringify({answer, left}));
    """
    warned = _node_mock(clean, fail="clean")
    assert isinstance(warned["answer"]["ok"]["warning"], str) and warned["left"] == 0


def test_library_keys_are_all_used() -> None:
    """The columns and buttons the Library dropped (Path, Provider, ZL, Install...) left no key."""
    tables = _i18n_tables()
    code = "\n".join((UI / name).read_text(encoding="utf-8") for name in PAGE_MODULES)
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", code))
    used |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
    for lang in ("fr", "en"):
        unused = sorted(k for k in tables[lang] if k.startswith("library.") and k not in used)
        assert not unused, (lang, unused)


def test_the_map_paints_a_tile_installed_by_its_own_pack_only() -> None:
    """A tile's overlay row reports the shared overlays pack, which stays in X-Plane while any
    OrthoStudio XP tile is: counted, it kept painting a tile removed from X-Plane as installed."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    body = re.search(r"\n  function installedTiles\(\) \{.*?\n  \}\n", map_js, re.S)
    assert body is not None
    assert '(e.kind == null || e.kind === "ortho")' in body.group(0)


# -- works: the job's events, its panel and its report (docs/specs/ui.md section 2.2) ----------


def _job_state(tiles: tuple[str, ...], *, last_seq: int = 1) -> dict[str, Any]:
    """GET /api/jobs/{id} of a job that just started: every node pending, as the engine lists."""

    def node(nid: str) -> dict[str, Any]:
        role = nid.split("/")[-1]
        return {"node": nid, "role": role, "status": "pending", "key": None, "hit": None}

    def stage(*ids: str) -> dict[str, Any]:
        nodes = [{**node(i), "wall_s": 0.0, "fraction": 0.0} for i in ids]
        return {"status": "pending", "fraction": 0.0, "wall_s": 0.0, "nodes": nodes}

    return {
        "id": "20260913-183959-fe0d",
        "status": "running",
        "created_at": 1789317599.0,
        "started_at": 1789317599.0,
        "finished_at": None,
        "install": True,
        "provider": "BI",
        "zl": 16,
        "tiles": [
            {
                "tile": t,
                "status": "pending",
                "errors": [],
                "stages": {
                    "data": stage(f"{t}/osm", f"{t}/dem", f"{t}/coastline", f"{t}/vectors"),
                    "terrain": stage(f"{t}/mesh"),
                    "coast": stage(f"{t}/masks"),
                    "imagery": stage(f"{t}/BI16/textures"),
                    "assembly": stage(
                        f"{t}/xp12", f"{t}/BI16/dsf", f"{t}/overlay", f"{t}/BI16/pack"
                    ),
                    "install": stage(f"{t}/BI16/install"),
                },
            }
            for t in tiles
        ],
        "errors": [],
        "stats": None,
        "last_seq": last_seq,
        "report": None,
        "decisions": [],
    }


TEXTURES_7 = {"tile": "+46+007", "stage": "imagery", "node": "+46+007/BI16/textures"}
TEXTURES_7["role"] = "textures"
OSM = {"stage": "data", "role": "osm"}
TEX_MISSING = {
    "schema": 1,
    "code": "TEX_MISSING",
    "severity": "blocking",
    "action": "retry",
    "message": "1 of 216 BI16 texture(s) of tile +46+007 could not be built (IMG_TILE_MISSING).",
    "remedy": "Run osxp build again.",
    "context": {"count": 1, "tile": "+46+007"},
    "cause": None,
}
REAL_EVENTS = [  # the journal of a real build (trimmed): phase data, then the build
    {"seq": 2, "ts": 0.046, "event": "started", "tile": "+46+007", "node": "+46+007/osm", **OSM},
    {"seq": 3, "ts": 0.047, "event": "progress", "tile": "+46+007", "node": "+46+007/osm", **OSM}
    | {"fraction": 0.0, "message": "+46+007: 4 OSM layers"},
    {"seq": 4, "ts": 1.048, "event": "stats", "stats": {"running": 1, "pending": 3, "done": 0}}
    | {"elapsed_s": 1.002, "progress": 0.01, "eta_low_s": None, "eta_high_s": None},
    {"seq": 5, "ts": 1.2, "event": "done", "tile": "+46+006", "node": "+46+006/osm", **OSM}
    | {"key": "2bb2d1bf6c0e", "hit": True, "wall_s": 0.0},
    {"seq": 29, "ts": 25.23, "event": "done", "tile": "+46+007", "node": "+46+007/osm", **OSM}
    | {"key": "4b40c0a128bf", "hit": False, "wall_s": 25.184},
    {"seq": 124, "ts": 113.618, "event": "started", "tile": "+46+007", "stage": "data"}
    | {"node": "+46+007/coastline", "role": "coastline", "key": "f68065df", "kind": "io"},
    {"seq": 128, "ts": 113.63, "event": "done", "tile": "+46+007", "stage": "data"}
    | {"node": "+46+007/coastline", "role": "coastline", "key": "f68065df", "hit": False}
    | {"wall_s": 0.011},
    {"seq": 258, "ts": 153.308, "event": "started", **TEXTURES_7, "key": "3aba2dc1", "kind": "net"},
    {"seq": 265, "ts": 155.223, "event": "progress", **TEXTURES_7, "fraction": 0.5}
    | {"message": "BI16: tiles 0/0, textures 0/213 (0 req/s)"},
    {"seq": 266, "ts": 155.225, "event": "log", **TEXTURES_7}
    | {"message": "BI16: tiles 0/0, textures 0/213 (0 req/s)"},
    {
        "seq": 300,
        "ts": 160.0,
        "event": "progress",
        **TEXTURES_7,
        "fraction": 0.4,
        "message": "slow",
    },
    {"seq": 1019, "ts": 334.988, "event": "failed", **TEXTURES_7, "error": TEX_MISSING}
    | {"skipped": False, "cause": None},
    {"seq": 1020, "ts": 334.988, "event": "failed", "tile": "+46+007", "stage": "assembly"}
    | {"node": "+46+007/BI16/pack", "role": "pack", "skipped": True}
    | {"cause": "+46+007/BI16/textures", "error": {**TEX_MISSING, "code": "SYS_UPSTREAM_FAILED"}},
    {
        "seq": 1021,
        "ts": 335.0,
        "event": "progress",
        **TEXTURES_7,
        "fraction": 0.9,
        "message": "late",
    },
]


def test_the_reducer_reads_the_engine_events() -> None:
    """The flat journal entries move the steps (``stage`` and ``node``, ``stats`` unwrapped, an OSM
    row that turns into a hit); an entry the job already reflects changes nothing; the fields of the
    P2b contract (``step``, ``node_id``, flat stats) are still read."""
    state = _job_state(("+46+006", "+46+007"))
    got = _node_mock(
        f"""
        const job = m.normalizeJob({json.dumps(state)}, 1000);
        const events = {json.dumps(REAL_EVENTS)};
        const slow = m.normalizeJob({json.dumps(state)});
        for (const e of events.slice(0, 11)) m.applyEvent(slow, e.event, e);
        const bar = slow.tiles[1].steps.imagery;
        const applied = events.map((e) => m.applyEvent(job, e.event, e, 5000));
        const steps = (tile) => Object.fromEntries(Object.entries(
          job.tiles.find((t) => t.tile === tile).steps,
        ).map(([s, v]) => [s, [v.status, Math.round(v.fraction * 1000) / 1000]]));
        const out = {{applied, t6: steps("+46+006"), t7: steps("+46+007"), stats: job.stats,
          statsAt: job.statsAt, errors: job.errors.map((e) => [e.code, e.tile, e.step, e.action]),
          log: job.log, seq: job.seq, phaseTiles: m.dataPhaseTiles(job),
          slow: [bar.status, bar.fraction, bar.nodes["+46+007/BI16/textures"].fraction],
          late: job.tiles[1].steps.imagery.nodes["+46+007/BI16/textures"],
          again: events.map((e) => m.applyEvent(job, e.event, e, 9000))}};
        const old = m.normalizeJob({json.dumps(_job_state(("+43+005",), last_seq=10))});
        out.skipped = m.applyEvent(old, "done", {{seq: 10, event: "done", tile: "+43+005",
          stage: "terrain", node: "+43+005/mesh", role: "mesh", hit: false, wall_s: 3}});
        const p2b = m.normalizeJob({{id: "j", status: "running", tiles: [], errors: []}});
        out.p2b = [m.applyEvent(p2b, "started", {{node_id: "+43+005/tile.dsf", step: "assembly"}}),
          m.applyEvent(p2b, "started", {{node_id: "+43+005/tile.textures"}}),
          m.applyEvent(p2b, "progress", {{node_id: "+43+005/tile.textures", fraction: 0.35}}),
          m.applyEvent(p2b, "stats", {{running: 1, elapsed_s: 2.0, eta_s: 150}}, 7000)];
        out.p2bSteps = [p2b.tiles[0].steps.assembly.status, p2b.tiles[0].steps.imagery.fraction,
          p2b.stats, p2b.statsAt];
        process.stdout.write(JSON.stringify(out));
        """
    )
    assert got["applied"] == [True] * len(REAL_EVENTS)
    # +46+006 had its map data already: a hit alone does not start its Data step (these entries
    # carry no weights, as an older engine's: the fraction counts rows)
    assert got["t6"]["data"] == ["pending", 0.25]
    assert got["t6"]["imagery"] == ["pending", 0]
    # +46+007: OSM and coastline done, two rows of four not started: waiting
    assert got["t7"]["data"] == ["waiting", 0.5]
    # a progress below the one before leaves the bar where it was
    assert got["slow"] == ["running", 0.5, 0.4]
    # a progress after the failure moves the row's fraction (as the engine's), not its step
    assert got["late"]["status"] == "failed" and got["late"]["fraction"] == 0.9
    assert got["t7"]["imagery"][0] == "failed"
    assert got["t7"]["assembly"][0] == "skipped"
    assert got["stats"] == REAL_EVENTS[2]["stats"] and got["statsAt"] == 5000
    # one card for the failure, none for the node skipped because of it
    assert got["errors"] == [["TEX_MISSING", "+46+007", "imagery", "retry"]]
    assert got["log"] == [
        {
            "at": 1789317599.0 + 155.225,
            "ts": 155.225,
            "level": "info",
            "tile": "+46+007",
            "message": "BI16: tiles 0/0, textures 0/213 (0 req/s)",
        }
    ]
    assert got["seq"] == 1021 and got["phaseTiles"] == 1
    assert got["again"] == [False] * len(REAL_EVENTS)  # the stream replayed: nothing twice
    assert got["skipped"] is False  # already in the state read (seq <= last_seq)
    assert got["p2b"] == [True, True, True, True]
    assert got["p2bSteps"] == [
        "running",
        0.35,
        {"running": 1, "elapsed_s": 2.0, "eta_s": 150},
        7000,
    ]


STEP_CASES = [  # rows as (status, fraction, expected seconds, started): the engine decides
    [("pending", 0, 5, False), ("pending", 0, 3, False)],
    [("running", 0.5, 5, True), ("pending", 0, 3, False)],
    [("done", 1, 5, True), ("pending", 0, 3, False)],  # real work done, the rest waits
    [("hit", 1, 5, False), ("pending", 0, 3, False), ("pending", 0, 1, False)],  # hits alone
    [("hit", 1, 5, False), ("running", 0.25, 2, True)],
    [("hit", 1, 5, False), ("hit", 1, 3, False)],  # nothing weighs: by count
    [("hit", 1, 5, False), ("done", 1, 3, True)],
    [
        ("done", 1, 0.4, True),
        ("done", 1, 5.5, True),
        ("done", 1, 2.8, True),
        ("pending", 0, 0.8, False),
    ],
    [("done", 1, 6.2, True), ("skipped", 0, 11.1, False), ("running", 0.2, 8.4, True)],
    [("cancelled", 0.4, 60.8, True), ("skipped", 0, 0.8, False)],
    [("cancelled", 0, 60.8, False), ("pending", 0, 0.8, False)],  # cancelled before it started
    [("failed", 0.9977, 60.8, True), ("cancelled", 0, 1, False), ("running", 0.5, 2, True)],
    [("hit", 1, 26.0, False), ("done", 1, 0.0504, True), ("running", 0.3333, 11.1234, True)],
    [("hit", 1, 26.0, False), ("pending", 0, 0, False)],  # nothing weighs, one row ended
]


def test_step_totals_follow_the_engines_rules() -> None:
    """A step's status and fraction are the engine's (``stage_status``, ``weighted_progress``),
    recomputed by the page from the rows as the engine sends them (``_NodeState.to_dict``: the
    weight a row has in its stage, rounded)."""
    from orthostudio.api.jobs import _NodeState, stage_status
    from orthostudio.api.progress import weighted_progress

    cases, expected = [], []
    for rows in STEP_CASES:
        nodes = [
            _NodeState(
                f"+46+006/n{i}",
                "mesh",
                "terrain",
                status=status,
                fraction=fraction,
                weight_s=weight,
                started_at=1.0 if started else None,
            )
            for i, (status, fraction, weight, started) in enumerate(rows)
        ]
        cases.append([n.to_dict() for n in nodes])
        expected.append([stage_status([n.status for n in nodes]), weighted_progress(nodes)])
    got = _node_json(
        "app.js",
        f"{json.dumps(cases)}.map((rows) => m.stepTotals({{nodes: Object.fromEntries(rows.map("
        "(n) => [n.node, {...n, weight: n.weight_s}]))}))",
    )
    for rows, (status, fraction), page in zip(STEP_CASES, expected, got, strict=True):
        assert page["status"] == status, rows
        assert page["fraction"] == pytest.approx(fraction, abs=2e-4), rows
    # the page's words for them exist (waiting included)
    assert {"pending", "running", "waiting", "done", "hit", "skipped", "cancelled", "failed"} >= {
        status for status, _ in expected
    }


ENGINE_TILES = ("+46+005", "+46+006", "+46+007")
ENGINE_ROWS = (  # a tile's main graph: (row, kind, rule, start, entries (after, what, value))
    ("dem", "net", "orthostudio.dem", 0, ((0, "started", 0), (3, "done", 3))),
    ("coastline", "io", "orthostudio.coastline", 0, ((0, "started", 0), (0.01, "done", 0.01))),
    ("xp12", "io", "xp12.rasters", 0.02, ((0, "started", 0), (0, "hit", 0))),
    ("overlay", "subprocess", "tile.overlay", 0.05, ((0, "started", 0), (2.5, "done", 2.5))),
    ("vectors", "subprocess", "orthostudio.vectors", 3, ((0, "started", 0), (6, "done", 6))),
    ("mesh", "subprocess", "orthostudio.mesh", 9, (
        (0, "started", 0), (2, "progress", 0.5), (5, "done", 5),
    )),
    ("masks", "subprocess", "orthostudio.masks", 14, ((0, "started", 0), (1, "done", 1))),
    ("BI16/dsf", "cpu", "tile.dsf", 14.5, ((0, "started", 0), (3.5, "done", 3.5))),
    ("BI16/textures", "net", "tile.textures", 18, (
        (0, "started", 0), (4, "progress", 0.25), (8, "progress", 0.5), (12, "progress", 0.9),
        (14, "done", 14),
    )),
    ("BI16/pack", "io", "tile.pack", 32, ((0, "started", 0), (1, "done", 1))),
    ("BI16/install", "io", "tile.install", 33, ((0, "started", 0), (0.2, "done", 0.2))),
)  # fmt: skip


def _engine_event(what: str, node: str, kind: str, value: float) -> Any:
    """One scheduler event of ``ENGINE_ROWS`` for the engine's ``Job``."""
    from orthostudio.errors import OsxpError
    from orthostudio.model import ArtifactRef
    from orthostudio.sched import Done, Failed, Progress, Started

    key = "a" * 64
    if what == "started":
        return Started(node, kind, key)
    if what == "progress":
        return Progress(node, value, "")
    if what == "failed":
        return Failed(node, OsxpError("TEX_MISSING"), None)
    ref = ArtifactRef(key, "0" * 64, Path("/nonexistent"), "r", "dir")
    return Done(node, key, what == "hit", 0.0 if what == "hit" else value, ref)


def _engine_job(path: Path) -> dict[str, Any]:
    """Drive the engine's ``Job`` through a three-tile build and write its journal and its state
    after every engine event to ``path``: +46+005 has its map data (its OSM row is reused) and
    finds its elevation, terrain and coast in the store, the two others download theirs one after
    the other; every tile finds its XP12 rasters in the store; +46+007 misses a texture, so its
    pack and install are skipped. The expectations of the page come from what the engine
    computed."""
    from orthostudio.api.jobs import Job
    from orthostudio.errors import OsxpError
    from orthostudio.model import ArtifactRef, TileRef
    from orthostudio.pipeline.build import BuildSpec, Phase
    from orthostudio.sched import Done, Failed, Progress, Started

    key = "a" * 64
    ref = ArtifactRef(key, "0" * 64, Path("/nonexistent"), "r", "dir")
    warm, cold, failing = ENGINE_TILES
    declared = tuple(
        (f"{tile}/{row}", kind, rule)
        for tile in ENGINE_TILES
        for row, kind, rule, _, _ in ENGINE_ROWS
    )
    stream: list[tuple[float, Any]] = [
        (0.0, Phase("data", nodes=((f"{cold}/osm", "net", "orthostudio.osm"),
                                   (f"{failing}/osm", "net", "orthostudio.osm")),
                    reused=((f"{warm}/osm", None),))),
        (20.0, Phase("build")),
        (20.5, Phase("build", nodes=declared)),
    ]  # fmt: skip
    for start, tile in ((0.1, cold), (9.5, failing)):
        node = f"{tile}/osm"
        stream.append((start, Started(node, "net", key)))
        stream.append((start + 4, Progress(node, 0.4, "")))
        stream.append((start + 9, Done(node, key, False, 9.0, ref)))
    for i, tile in enumerate(ENGINE_TILES):
        textures = f"{tile}/BI16/textures"
        for row, kind, _rule, start, entries in ENGINE_ROWS:
            node = f"{tile}/{row}"
            at = 21.0 + 2 * i + start
            if tile == failing and row in ("BI16/pack", "BI16/install"):
                error = OsxpError("SYS_UPSTREAM_FAILED")
                stream.append((21.0 + 2 * i + 32, Failed(node, error, textures)))
                continue
            for after, what, value in entries:
                if tile == warm and row in ("dem", "mesh", "masks") and what != "started":
                    if what != "done":
                        continue  # found in the store: no progress
                    what = "hit"
                if tile == failing and row == "BI16/textures" and what == "done":
                    what = "failed"
                at_entry = at if what == "hit" else at + after
                stream.append((at_entry, _engine_event(what, node, kind, value)))
    stream.sort(key=lambda item: (item[0], not isinstance(item[1], Phase)))

    class Clock:
        t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = Clock()
    specs = [
        BuildSpec(
            tile=TileRef.parse(tile),
            zl=16,
            install=True,
            provider="BI",
            out_dir=Path("/nonexistent/tiles"),
            custom_scenery=Path("/nonexistent/Custom Scenery"),
            store_root=Path("/nonexistent/store"),
            chunks_root=Path("/nonexistent/chunks"),
            relief="xplane",
        )
        for tile in ENGINE_TILES
    ]
    job = Job(
        specs, install=True, request=None, journal_path=path.with_suffix(".jsonl"), clock=clock
    )
    job.begin()
    doc: dict[str, Any] = {"initial": job.state(), "states": {}}
    for t, event in stream:
        clock.t = t
        job.on_event(event)
        doc["states"][str(job.last_seq)] = job.state()
    clock.t = stream[-1][0] + 1
    job._finish("failed", None, None)
    doc["final"] = job.state()
    doc["journal"] = job.events()
    path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


ENGINE_REPLAY = """
const doc = JSON.parse(readFileSync(process.env.OSXP_REPLAY, "utf8"));
const seqs = Object.keys(doc.states).map(Number).sort((a, b) => a - b);
const stateAt = (seq) => doc.states[String(seqs.find((x) => x >= seq) ?? seqs[seqs.length - 1])];
const T0 = 1789317599;
const read = (state, nowMs) =>
  m.normalizeJob(structuredClone({...state, created_at: T0, started_at: T0}), nowMs);
const PARTIAL = new Set(["pending", "waiting", "running"]);

function run(mode, lag) {
  let job = read(doc.initial, 0);
  let shown = {};
  const recedes = [];
  const mismatches = [];
  const statuses = new Set();
  let compared = 0;
  let late = null;
  const buffer = [];
  const observe = (seq) => {
    for (const tile of job.tiles) for (const s of m.STEPS) {
      const k = `${tile.tile} ${s}`;
      const v = m.stepView(job, tile, s);
      statuses.add(v.status);
      const was = shown[k];
      if (was && PARTIAL.has(was.status) && PARTIAL.has(v.status) && v.pct < was.pct) {
        recedes.push([mode, lag, seq, k, `${was.status} ${was.pct}`, `${v.status} ${v.pct}`]);
      }
      shown[k] = v;
    }
  };
  const answer = (state, nowMs) => {
    const fresh = read(state, nowMs);
    fresh.log = job.log;
    fresh.logSeq = job.logSeq;
    for (const e of buffer.splice(0)) if (e.event !== "log") m.applyEvent(fresh, e.event, e, nowMs);
    job = fresh;
  };
  for (const e of doc.journal) {
    const nowMs = e.ts * 1000;
    const changed = m.applyEvent(job, e.event, e, nowMs);
    if (late) buffer.push(e);
    observe(e.seq);
    if (mode === "local" && doc.states[String(e.seq)]) {
      compared += 1;
      for (const t of doc.states[String(e.seq)].tiles) {
        const mine = job.tiles.find((x) => x.tile === t.tile);
        for (const [s, st] of Object.entries(t.stages)) {
          const p = mine.steps[s];
          if (p.status !== st.status || Math.abs(p.fraction - st.fraction) > 2e-4) {
            mismatches.push([e.seq, t.tile, s, p.status, p.fraction, st.status, st.fraction]);
          }
        }
      }
    }
    if (mode === "local") continue;
    if (late && e.seq >= late.due) {
      answer(late.state, nowMs);
      late = null;
      observe(e.seq);
    }
    if (changed && (e.event === "done" || e.event === "failed") && !late) {
      if (mode === "late") late = { state: stateAt(e.seq), due: e.seq + lag };
      else {
        answer(stateAt(mode === "ahead" ? e.seq + lag : e.seq), nowMs);
        observe(e.seq);
      }
    }
  }
  answer(doc.final, 10 ** 7);
  observe(null);
  const final = [];
  for (const t of doc.final.tiles) {
    for (const [s, st] of Object.entries(t.stages)) {
      const v = m.stepView(job, job.tiles.find((x) => x.tile === t.tile), s);
      final.push([t.tile, s, st.status, v.status]);
    }
  }
  return { compared, mismatches, recedes, statuses: [...statuses].sort(), final };
}
const runs = [run("local", 0), run("ontime", 0), run("late", 2), run("late", 12), run("ahead", 2),
  run("ahead", 12)];
process.stdout.write(JSON.stringify(runs));
"""


def test_the_steps_follow_the_engine_whenever_its_state_is_read(tmp_path: Path) -> None:
    """A build driven through the engine's ``Job``: the page applies its journal and reads its
    state after each node ends, the answer arriving at once, a few entries late (the entries in
    between applied again to it) or already ahead of the stream. Without any read, what the page
    recomputes from the entries is the engine's state after every one of them (status and
    fraction: the rows' ``weight_s``, the stage rules); whenever the reads arrive, no step's bar
    moves back while it shows a share of its step (review of 2026-09-13: sixteen recedes a
    second on a real six-tile job); at the end the page shows the engine's statuses."""
    path = tmp_path / "engine-job.json"
    doc = _engine_job(path)
    for entry in doc["journal"]:
        if entry["event"] in ("started", "progress", "done", "failed"):
            assert isinstance(entry["weight_s"], float | int), entry  # what the page weighs by
    runs = _node_mock(
        f"process.env.OSXP_REPLAY = {json.dumps(str(path))};\n" + ENGINE_REPLAY, speed=1
    )
    local = runs[0]
    assert local["compared"] == len(doc["states"]) and local["mismatches"] == []
    seen = {status for status in local["statuses"]}
    assert {"pending", "running", "waiting", "done", "hit"} <= seen
    for run in runs:
        assert run["recedes"] == [], run["recedes"][:5]
        # once the job ended the engine says skipped or failed; "stopped" is an older engine's case
        assert [page for *_, page in run["final"]] == [engine for *_, engine, _ in run["final"]]
    final = {(t, s): status for t, s, status, _ in runs[0]["final"]}
    assert final[("+46+007", "imagery")] == "failed"
    assert final[("+46+007", "assembly")] == "skipped"
    assert final[("+46+005", "data")] == "done"


def test_a_refresh_replaces_the_steps_with_the_engines() -> None:
    """``normalizeJob`` rebuilds the steps from ``stages`` at every read, even for tiles that
    already have steps; a running step's bar does not move back between two reads."""
    early = _job_state(("+46+006",), last_seq=12)
    state = _job_state(("+46+006",), last_seq=40)
    stages = state["tiles"][0]["stages"]
    stages["imagery"] |= {"status": "done", "fraction": 1.0, "wall_s": 14.6}
    stages["imagery"]["nodes"][0] |= {"status": "done", "fraction": 1.0, "wall_s": 14.6}
    stages["data"] |= {"status": "running", "fraction": 0.6}
    state["errors"] = [{"code": "TEX_MISSING", "stage": "imagery", "tile": "+46+006"}]
    textures = {"tile": "+46+006", "stage": "imagery", "node": "+46+006/BI16/textures"}
    got = _node_mock(
        f"""
        const job = m.normalizeJob({json.dumps(early)});
        const row = {{...{json.dumps(textures)}, role: "textures"}};
        m.applyEvent(job, "started", row);
        m.applyEvent(job, "progress", {{...row, fraction: 0.2}});
        const before = [job.tiles[0].steps.imagery.status, job.tiles[0].steps.imagery.fraction];
        // the next read: the tile object still carries the steps the events moved
        const fresh = {json.dumps(state)};
        fresh.tiles[0].steps = job.tiles[0].steps;
        const again = m.normalizeJob(fresh, 123);
        const data = again.tiles[0].steps.data;
        m.applyEvent(again, "done", {{tile: "+46+006", stage: "data", node: "+46+006/dem",
          role: "dem", hit: false, wall_s: 4}});
        process.stdout.write(JSON.stringify({{before, imagery: again.tiles[0].steps.imagery,
          dataAfter: [data.status, data.fraction], zoom: again.zoom_level, seq: again.seq,
          statsAt: again.statsAt, step: again.errors[0].step}}));
        """
    )
    assert got["before"] == ["running", 0.2]
    assert got["imagery"]["status"] == "done" and got["imagery"]["fraction"] == 1
    assert got["imagery"]["nodes"]["+46+006/BI16/textures"]["status"] == "done"
    # the engine said 0.6; without weights one row of four done is 0.25 for the page: kept at 0.6
    assert got["dataAfter"] == ["waiting", 0.6]
    assert got["zoom"] == 16 and got["seq"] == 40 and got["statsAt"] is None
    assert got["step"] == "imagery"


def test_progress_elapsed_and_remaining() -> None:
    """The header's numbers are the whole job's: ``stats.progress`` and a clock from
    ``stats.elapsed_s``; from an older engine (no ``progress`` or ``phase``) the steps' fractions
    and the clock since ``started_at``; nothing to estimate yet is ``null``."""
    state = _job_state(("+46+006",))
    stages = state["tiles"][0]["stages"]
    for node in stages["data"]["nodes"]:
        node["status"] = "done"
    stages["data"]["status"] = "done"
    stages["imagery"] |= {"status": "running", "fraction": 0.5}
    stages["install"] = {"status": "skipped", "fraction": 0.0, "wall_s": 0.0, "nodes": []}
    got = _node_mock(
        f"""
        const job = m.normalizeJob({json.dumps(state)}, 0);
        const running = {{status: "running", started_at: 1000, statsAt: 5000,
          stats: {{elapsed_s: 100, progress: 0.3, phase: "build"}}}};
        const older = {{...running, stats: {{elapsed_s: 4, eta_low_s: 10, eta_high_s: 15}}}};
        process.stdout.write(JSON.stringify({{
          engine: m.jobProgress({{stats: {{progress: 0.42}}, tiles: job.tiles}}),
          fallback: m.jobProgress(job),
          done: m.jobProgress({{status: "done", tiles: []}}),
          elapsed: [m.jobElapsed(running, 8000), m.jobElapsed(older, 1060000),
            m.jobElapsed({{status: "failed", started_at: 1000, finished_at: 1538.5,
              stats: {{elapsed_s: 424}}}}), m.jobElapsed({{status: "cancelled",
              stats: {{elapsed_s: 12}}}}), m.jobElapsed({{status: "queued"}})],
          eta: [m.etaRange({{eta_low_s: null, eta_high_s: null}}), m.etaRange({{eta_low_s: 60,
            eta_high_s: 90}}), m.etaRange({{eta_s: 100}}), m.etaRange(null)],
        }}));
        """
    )
    assert got["engine"] == 0.42 and got["done"] == 1
    # 12 nodes: data's 4 done, imagery's 1 at a half, 7 waiting; install without nodes not counted
    assert got["fallback"] == pytest.approx(4.5 / 11)
    assert got["elapsed"] == [103, 60, 538.5, 12, None]
    assert got["eta"] == [[None, None], [60, 90], [80, 150], [None, None]]


def test_the_data_phase_names_the_layers_it_downloads() -> None:
    """Phase 0 downloads the OSM layers airports, big and small roads, coastline and water
    (``orthostudio.sources.osm.LAYERS``): the hint said "roads, water, buildings"."""
    from orthostudio.sources.osm import LAYERS

    assert {spec.name for spec in LAYERS.values()} == {
        "airports",
        "big_roads",
        "small_roads",
        "coastline",
        "water",
    }
    tables = _i18n_tables()
    words = {"en": ("airports", "roads", "coastline", "water"), "fr": ("aéroports", "routes")}
    words["fr"] += ("côtes", "eau")
    for lang, expected in words.items():
        for key in ("works.phase_data", "works.phase_data_any"):
            text = tables[lang][key]
            assert all(w in text for w in expected), (lang, key)
            assert "build" not in text.split("(")[1].split(")")[0], (lang, key)
            assert "bâtiment" not in text, (lang, key)


def test_the_log_is_appended_to_and_never_rebuilt() -> None:
    """Opening the log survived one event only: the panel was rebuilt on each. The log's element is
    built once per job and language, new lines are appended (the oldest leave past 500), and it
    follows new lines only when scrolled to the end."""
    got = _node_json(
        "app.js",
        """(() => { const [a, b, c, d, x] = [{}, {}, {}, {}, {}];
          const show = (shown, log) => {
            const r = m.logDelta(shown, log);
            return [r.drop, r.add.map((l) => log.indexOf(l))];
          };
          return [show([], [a, b]), show([a, b], [a, b, c]), show([a, b, c], [b, c, d]),
            show([a], [x, d]), show([a, b], [a, b])]; })()""",
    )
    assert got == [[0, [0, 1]], [0, [2]], [1, [2]], [1, [0, 1]], [0, []]]
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    render = _function_body(app_js, "renderJob")
    build = _function_body(app_js, "buildJobView")
    update = _function_body(app_js, "updateJobView")
    log = _function_body(app_js, "updateLog")
    assert "details" not in render and "details" not in update
    assert build.count('h("details"') == 1 and "logDetails.open" in build
    assert "logDelta(v.logLines, log)" in log and "clear(" not in log
    assert "logAtBottom" in build and "v.logFollow" in log
    # events redraw through the throttle (four times a second at most), never directly; one read of
    # the job at a time
    assert "again = true" in _function_body(app_js, "watchJob")
    after_first_draw = _function_body(app_js, "watchJob").split("const refresh")[1]
    assert (
        after_first_draw.count("scheduleRenderJob()") == 2 and "renderJob()" not in after_first_draw
    )
    assert "RENDER_EVERY_MS = 250" in app_js


def test_the_report_counts_each_tile_once() -> None:
    """A real 6-tile build: 3 tiles installed and 3 missing a texture. The report gave both
    ``installed`` and a ``pack`` decision for the same tiles and read "Install 6 / 6"; the missing
    textures were counted from the textures decisions and again from the errors."""
    done = _mock_json("job_done")
    packs = [d for d in done["decisions"] if d["kind"] == "pack"]
    missing = sum(d["missing"] for d in done["decisions"] if d["kind"] == "textures")
    both = {
        "tiles": [{"tile": "+1+001"}, {"tile": "+1+002"}],
        "report": {
            "tiles": [
                {"tile": "+1+001", "installed": True, "pack_bytes": 100},
                {"tile": "+1+002", "installed": None},
            ]
        },
        "decisions": [
            {"tile": "+1+001", "kind": "pack", "bytes": 999, "installed": True},
            {"tile": "+1+002", "kind": "pack", "bytes": 50, "installed": True},
        ],
        "errors": [{"code": "TEX_MISSING", "tile": "+1+003", "context": {"count": 2}}],
    }
    got = _node_mock(
        f"""
        const done = m.normalizeJob({json.dumps(done)});
        const both = {json.dumps(both)};
        const pass = (d) => Object.fromEntries(m.decisionCounts({{decisions: [{{kind: "textures",
          tile: "+1+001", total: 10, built: 10, hits: 0, missing: 0, ...d}}]}}));
        process.stdout.write(JSON.stringify({{done: m.reportTotals(done),
          counts: Object.fromEntries(m.decisionCounts(done)), both: m.reportTotals(both),
          bothCounts: Object.fromEntries(m.decisionCounts(both)),
          none: m.reportTotals({{tiles: [{{tile: "+1+001"}}], report: {{}}, decisions: []}}),
          secondPass: [pass({{second_pass: 12, recovered: 0}}),
            pass({{second_pass: 12, recovered: 9}}), pass({{second_pass: 0, recovered: 0}}),
            pass({{}})]}}));
        """
    )
    assert got["done"] == {"tiles": 6, "installed": 3, "bytes": sum(d["bytes"] for d in packs)}
    assert got["counts"]["TEX_MISSING"] == missing == 4
    assert got["counts"]["SYS_UPSTREAM_FAILED"] == 6
    # the report's tile wins; the decision fills in what it does not say
    assert got["both"] == {"tiles": 2, "installed": 2, "bytes": 150}
    assert got["bothCounts"] == {"TEX_MISSING": 2}  # no textures decision for that tile
    assert got["none"] == {"tiles": 1, "installed": 0, "bytes": None}
    # a second pass that brought nothing back still shows both rows, zero included
    with_zero, recovered, no_pass, older = got["secondPass"]
    assert with_zero["textures_second_pass"] == 12 and with_zero["textures_recovered"] == 0
    assert recovered["textures_recovered"] == 9 and with_zero["TEX_MISSING"] == 0
    for counts in (no_pass, older):
        assert "textures_second_pass" not in counts and "textures_recovered" not in counts


MOCK_BUILD = """
const watch = async (id) => {
  let job = m.normalizeJob((await call("GET", `/api/jobs/${id}`)).ok);
  const entries = [];
  let reads = 0;
  let ended;
  const finished = new Promise((resolve) => { ended = resolve; });
  const names = ["started", "progress", "done", "failed", "stats", "log", "finished"];
  const source = await m.mockSubscribe(id, Object.fromEntries(names.map((name) => [name, (data) => {
    entries.push(data);
    const changed = m.applyEvent(job, name, data);
    if (name === "finished") ended();
    else if (changed && (name === "done" || name === "failed")) {
      call("GET", `/api/jobs/${id}`).then(({ok}) => {
        reads += 1;
        const fresh = m.normalizeJob(ok);
        fresh.log = job.log;
        fresh.logSeq = job.logSeq;
        for (const e of entries.filter((x) => x.seq > ok.last_seq)) m.applyEvent(fresh, e.event, e);
        job = fresh;
      });
    }
  }])));
  await finished;
  await new Promise((resolve) => setTimeout(resolve, 150));
  source.close();
  const state = (await call("GET", `/api/jobs/${id}`)).ok;
  return { job, state, entries, reads };
};
"""


def test_a_mock_build_streams_what_the_engine_would() -> None:
    """``?mock=1``: a 3-tile build journals the engine's entries (dense ``seq``, flat node entries
    with their ``weight_s``, ``stats`` with the whole job's progress, elapsed time, a range once
    known and the phase); the first tile's OSM row turns into a hit, the others download their map
    data one after the other, each tile's build going on once its own is in, every tile has a
    cache hit, the last misses a texture. The mock computes its steps
    with the page's own rules, so comparing the two would prove nothing: the rules are checked
    against the engine (``test_step_totals_follow_the_engines_rules``,
    ``test_the_steps_follow_the_engine_whenever_its_state_is_read``)."""
    got = _node_mock(
        MOCK_BUILD
        + """
        const body = {tiles: ["+43+005", "+43+006", "+44+005"], provider: "BI", zoom_level: 16,
          install: true, zones: []};
        const {job_id: id} = (await call("POST", "/api/jobs", body)).ok;
        const busy = await call("POST", "/api/jobs", body);
        const run = await watch(id);
        const list = (await call("GET", "/api/jobs")).ok;
        process.stdout.write(JSON.stringify({id, busy, list: list.map((j) => [j.id, j.status]),
          entries: run.entries, reads: run.reads,
          errors: run.job.errors.map((e) => [e.code, e.tile, e.step]), status: run.job.status,
          state: run.state, progress: m.jobProgress(run.job), log: run.job.log.length}));
        """,
        speed=40,
    )
    entries = got["entries"]
    assert [e["seq"] for e in entries] == list(range(1, len(entries) + 1))
    assert got["busy"] == {"status": 409, "code": "SYS_BUSY"}
    assert got["list"][0] == [got["id"], "failed"]
    stats = [e["stats"] for e in entries if e["event"] == "stats"]
    assert all(set(s) == STATS_KEYS for s in stats)
    assert [s["progress"] for s in stats] == sorted(s["progress"] for s in stats)
    assert {s["phase"] for s in stats} == {"build"}
    assert stats[0]["eta_low_s"] is None and any((s["eta_low_s"] or 0) > 0 for s in stats)
    # this mock build ends *failed*: the bar stops where the work stopped. A build that really
    # finishes is set to 1 by the engine itself (``Job._stats_now``), and the page's fallback
    # has the same rule (found in review, 2026-09-23).
    assert 0.98 < stats[-1]["progress"] < 1
    assert [s["elapsed_s"] for s in stats] == sorted(s["elapsed_s"] for s in stats)
    for e in entries:
        assert e["event"] in JOURNAL_EVENTS
        assert "step" not in e and "node_id" not in e and "data" not in e
    osm = [(e["tile"], e["event"], e.get("hit")) for e in entries if e.get("role") == "osm"]
    assert osm[0] == ("+43+005", "done", True)  # already there: a hit, never started
    assert ("+43+006", "started", None) in osm and ("+44+005", "done", False) in osm

    def seq(tile: str, role: str, event: str) -> int:
        return next(e["seq"] for e in entries if e.get("node", "").startswith(tile) and
                    e.get("role") == role and e["event"] == event)  # fmt: skip

    # a tile's build waits for its own map data, not for the other tiles'
    assert seq("+44+005", "osm", "done") < seq("+44+005", "vectors", "started")
    assert seq("+43+005", "vectors", "started") < seq("+44+005", "osm", "done")
    hits = {e["tile"] for e in entries if e["event"] == "done" and e["hit"] and e["role"] != "osm"}
    assert hits == {"+43+005", "+43+006", "+44+005"}
    assert got["reads"] > 0
    weights = [e["weight_s"] for e in entries if e["event"] in ("started", "progress", "done")]
    assert all(isinstance(w, int | float) and w >= 0 for w in weights) and max(weights) > 0
    # ends failed, so the bar stops where the work stopped (see the stats assertion above)
    assert got["status"] == "failed" and 0.98 < got["progress"] < 1 and got["log"] > 3
    assert got["errors"] == [["TEX_MISSING", "+44+005", "imagery"]]
    stages = {
        t["tile"]: {s: v["status"] for s, v in t["stages"].items()} for t in got["state"]["tiles"]
    }
    assert stages["+44+005"] == dict(
        zip(STEPS, ["done", "done", "done", "failed", "skipped", "skipped"], strict=True)
    )
    assert stages["+43+005"] == dict.fromkeys(STEPS, "done")
    assert got["state"]["report"]["tiles"][2]["ok"] is False


def test_mock_retry_reuses_the_build_and_cancel_stops_it() -> None:
    """A retry is a new job with the same tiles, where what the job before built comes back as
    hits; Stop ends the running nodes with SYS_CANCELLED and the job as cancelled."""
    got = _node_mock(
        MOCK_BUILD
        + """
        const done = (await call("GET", "/api/jobs/20260912-174000-7a3c")).ok;
        const retry = (await call("POST", "/api/jobs/20260912-174000-7a3c/retry")).ok;
        const run = await watch(retry.job_id);
        const stages = run.state.tiles.flatMap((t) => Object.values(t.stages));
        const nodes = stages.flatMap((s) => s.nodes);
        process.stdout.write(JSON.stringify({retry, status: run.state.status,
          hits: nodes.filter((n) => n.status === "hit").length,
          tiles: done.tiles.length,
          ran: nodes.filter((n) => n.status === "done").map((n) => n.node)}));
        """,
        speed=40,
    )
    assert got["retry"]["retry_of"] == "20260912-174000-7a3c"
    assert got["status"] == "done"
    # the three tiles that failed build their textures, pack and install again; nothing else runs
    failed = ("+46+008", "+46+009", "+46+010")
    roles = ("textures", "pack", "install")
    assert sorted(got["ran"]) == sorted(f"{t}/BI16/{r}" for t in failed for r in roles)
    assert got["hits"] == got["tiles"] * 12 - len(got["ran"])

    # slower here: Stop must find the map data download of the tile under way
    stop = _node_mock(
        """
        const body = {tiles: ["+43+005"], provider: "BI", zoom_level: 16, install: false};
        const {job_id: id} = (await call("POST", "/api/jobs", {...body, zones: []})).ok;
        const heard = [];
        const names = ["started", "progress", "done", "failed", "stats", "log", "finished"];
        await m.mockSubscribe(id, Object.fromEntries(names.map((n) => [n, (d) => heard.push(d)])));
        // the download starts 75 ms after the job and lasts 600 ms at this speed
        await new Promise((resolve) => setTimeout(resolve, 200));
        const cancel = await call("POST", `/api/jobs/${id}/cancel`);
        const again = await call("POST", `/api/jobs/${id}/cancel`);
        const stopped = (await call("GET", `/api/jobs/${id}`)).ok;
        process.stdout.write(JSON.stringify({cancel, again, status: stopped.status,
          install: stopped.tiles[0].stages.install, report: stopped.report, last: heard.at(-1),
          cancelled: heard.filter((e) => e.event === "failed").map((e) => [e.role, e.error.code]),
        }));
        """,
        speed=4,
    )
    assert (
        stop["cancel"]["ok"]["status"] == "cancelled" and stop["cancel"]["ok"]["cancel_requested"]
    )
    assert stop["again"] == {"status": 409, "code": "SYS_BUSY"}
    assert stop["status"] == "cancelled" and stop["report"] is None
    assert stop["cancelled"] == [["osm", "SYS_CANCELLED"]]
    assert stop["last"]["event"] == "finished" and stop["last"]["status"] == "cancelled"
    assert stop["install"] == {"status": "skipped", "fraction": 0, "wall_s": 0, "nodes": []}


def test_imagery_shows_the_download_rate_the_engine_reports() -> None:
    """The Imagery step reads its MB/s in the textures node's line (build.py
    ``textures_progress_message``), and Data in the OSM node's (``osm_progress_message``): the
    formats must move together. A user asked for the rate wherever the network works."""
    from orthostudio.model import TileRef
    from orthostudio.pipeline import build as build_mod
    from orthostudio.pipeline import textures as textures_mod
    from orthostudio.sources.osm import osm_progress_message

    snapshot = textures_mod.ProgressSnapshot(
        tiles_done=10, tiles_total=20, parents_done=0, parents_total=0, req_per_s=1392.4,
        bytes=0, in_flight=0, hedges=0, retries=0, net_errors=0, throttled=False,
        textures_total=4, built=1, hits=1, failed=0, incomplete=0, encoding=0, eta_s=None,
        elapsed_s=1.0,
    )  # fmt: skip
    with_rate = build_mod.textures_progress_message("BI16", snapshot, 21.64)
    without = build_mod.textures_progress_message("BI16", snapshot)
    job = {"status": "running", "install": True, "tiles": []}
    imagery = {"status": "running", "fraction": 0.42, "message": with_rate, "nodes": {}}
    tile = {"tile": "+46+006", "steps": {"imagery": imagery}}
    osm_line = osm_progress_message(
        TileRef(46, 6),
        ["airports", "big_roads", "water", "coastline"],
        ["airports"],
        4_200_000,
        3.0,
    )
    relief_line = build_mod.dem_download_message(TileRef(46, 6), 2, 9_300_000, 3.0)
    data = {"status": "running", "fraction": 0.3, "message": osm_line, "nodes": {}}
    tile["steps"]["data"] = data
    got = _node_json(
        "app.js",
        f"[m.downloadRate({json.dumps(with_rate)}), m.downloadRate({json.dumps(without)}), "
        f"m.stepView({json.dumps(job)}, {json.dumps(tile)}, 'imagery'), "
        f"m.stepView({json.dumps(job)}, {json.dumps(tile)}, 'data'), "
        f"m.downloadRate({json.dumps(relief_line)})]",
    )
    assert got[0] == 21.6 and got[1] is None
    assert got[2]["text"] == "42%" and got[2]["detail"] == "21.6 MB/s"
    assert got[2]["help"] == with_rate  # the whole line stays in the tooltip
    assert got[3]["detail"] == "1.4 MB/s" and got[3]["help"] == osm_line
    assert got[4] == 3.1  # a downloaded relief


def test_a_started_build_empties_the_selection_and_the_job_list_follows() -> None:
    """After a 6-tile build, the user added 3 tiles and got a job of 9: the selection had kept the
    6. And the job list still marked the previous job, drawn before the new one was watched."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    build = _function_body(app_js, "build")
    assert build.index('api("POST", "/api/jobs"') < build.index("state.tiles = [];")
    assert build.index("state.tiles = [];") < build.index("renderTiles();")
    watch = _function_body(app_js, "watchJob")
    assert watch.index("state.jobId = jobId;") < watch.index("renderJobList();")


def test_the_library_frees_disk_space_after_asking() -> None:
    """A user deleted every tile and got no space back, then asked for a button that empties the
    cache and the images: the Library measures (GET /api/disk), asks, then frees (POST
    /api/clean). The mock answers like the engine, a running build included."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    for element in (
        'id="disk-space"',
        'id="disk-free"',
        'id="disk-images"',
        'id="disk-relief"',
        'id="disk-confirm"',
    ):
        assert element in html, element
    # the relief is its own choice, counted apart: a user emptied everything and 1.4 GB of
    # elevation cells stayed, because nothing counted or freed them (2026-09-18)
    disk = "{unused_bytes: 5, images_bytes: 7, mapcache_bytes: 1, relief_bytes: 9}"
    plans = _node_json(
        "app.js",
        f"[m.freeSpacePlan({disk}, false, false), m.freeSpacePlan({disk}, true, false), "
        f"m.freeSpacePlan({disk}, false, true), m.freeSpacePlan(null, true, true)]",
    )
    assert plans == [
        {"unused": 5, "pictures": 0, "heights": 0, "total": 5},
        {"unused": 5, "pictures": 8, "heights": 0, "total": 13},
        {"unused": 5, "pictures": 0, "heights": 9, "total": 14},
        {"unused": 0, "pictures": 0, "heights": 0, "total": 0},
    ]
    script = """
    const before = (await call("GET", "/api/disk")).ok;
    const freed = (await call("POST", "/api/clean", {images: true, relief: true})).ok;
    const after = (await call("GET", "/api/disk")).ok;
    process.stdout.write(JSON.stringify({before, freed, after}));
    """
    got = _node_mock(script)
    assert got["before"]["unused_bytes"] > 0 and got["before"]["building"] is False
    assert got["before"]["relief_bytes"] > 0
    assert got["freed"]["freed_bytes"] == got["before"]["unused_bytes"]
    assert got["freed"]["relief_freed_bytes"] == got["before"]["relief_bytes"]
    assert got["after"]["unused_bytes"] == got["after"]["images_bytes"] == 0
    assert got["after"]["relief_bytes"] == 0
    refused = 'process.stdout.write(JSON.stringify(await call("POST", "/api/clean", {})));'
    busy = _node_mock(refused, fail="busy")
    assert busy == {"status": 409, "code": "SYS_BUSY"}


def test_a_zone_whose_level_changes_goes_to_its_place() -> None:
    """A user drew zones at ZL19, ZL18 and another, then set the last one to ZL15: it stayed above
    the ZL18 one and hid it. A changed level now moves the zone where a new zone of that level
    goes (sharpest first), and a zone less sharp than the tiles says it will look blurrier."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    body = re.search(r"\n  function placeByZl\(z\) \{.*?\n  \}\n", map_js, re.S)
    assert body is not None and "insertIndexForZl(zs.zones, z.zl)" in body.group(0)
    change = map_js.index('detail.addEventListener("change"')
    assert map_js.index("placeByZl(z);", change) < map_js.index("changed();", change)
    assert 't("zones.below_tiles"' in map_js
    order = _node_json(
        "geo.js",
        "(() => { const zones = [{id: 'a', zl: 19}, {id: 'b', zl: 18}, {id: 'c', zl: 17}];"
        " const place = (z) => zones.splice(m.insertIndexForZl(zones, z.zl), 0, z);"
        " const c = zones.pop(); c.zl = 15; place(c);"
        " const b = zones.splice(1, 1)[0]; b.zl = 15; place(b);"
        " return zones.map((z) => z.id); })()",
    )
    assert order == ["a", "b", "c"]  # same level: the zone changed last goes first


def test_the_trash_deletes_the_zones_in_the_list_after_asking() -> None:
    """A user deleting a dozen zones row by row asked for a trash that deletes them all: shown above
    the list when it has rows, it asks first, then deletes the zones the list shows, which the
    usual debounced PUT saves (docs/specs/ui.md, Step 2). Those of other tiles, which the list
    leaves out, stay: the trash never deletes what cannot be seen (2026-09-22)."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    head_re = r'<div class="zone-list-head" id="zone-list-head" hidden>.*?\n {10}</div>'
    head = re.search(head_re, html, re.S)
    assert head is not None
    assert 'id="zones-clear"' in head.group(0) and 'data-i18n="zones.clear"' in head.group(0)
    assert html.index('id="zone-list-head"') < html.index('id="zone-list"')
    dialog = re.search(r'<dialog id="zones-clear-confirm".*?</dialog>', html, re.S)
    assert dialog is not None
    assert 'value="keep" autofocus' in dialog.group(0) and 'value="clear"' in dialog.group(0)

    map_js = (UI / "map.js").read_text(encoding="utf-8")
    body = re.search(r"\n  async function removeAllZones\(\) \{.*?\n  \}\n", map_js, re.S)
    assert body is not None
    code = body.group(0)
    assert "zs.zones.length - shown" in code  # the dialog says how many stay
    assert code.index("await confirmRemoveAll(") < code.index("const gone = new Set(listed()")
    steps = (
        "!gone.has(z.id)",
        "zs.marks.delete(id)",
        "if (gone.has(zs.selected)) zs.selected = null;",
        "changed();",
        't("zones.cleared", { n: gone.size })',
    )
    for step in steps:
        assert step in code, step
    assert "zs.zones.splice(0);" not in code  # never every zone at once any more
    assert 'dialog.returnValue === "clear"' in map_js
    assert 't("zones.clear_others", { n: others })' in map_js
    assert '$("zone-list-head").hidden = !zs.loaded || shown === 0;' in map_js
    assert '$("zones-clear")?.addEventListener("click", removeAllZones);' in map_js
    for lang in ("en", "fr"):
        texts = _node_json(
            "i18n.js",
            f'(globalThis.document = {{documentElement: {{}}}}, m.setLanguage("{lang}"),'
            ' ["zones.clear", "zones.clear_title", "zones.clear_others",'
            ' "zones.clear_confirm"].map((k) => m.t(k, {n: 3})))',
        )
        assert all(texts) and not any("toutes" in s or " all" in s for s in texts), texts


def test_the_zone_list_shows_the_zones_of_the_chosen_tiles() -> None:
    """A helicopter pilot with a zone per landing site went through all of them to reach the few of
    one square (X-Plane.Org, 2026-09-22): step 2 lists the zones touching a chosen tile, the
    selected one and those with a problem to fix; a line under the list names the others and shows
    them. The order is the list's, which decides overlaps, and the arrows move a zone among the
    rows shown, over those left out."""
    got = _node_json(
        "geo.js",
        """(() => {
          const sq = (lon, lat) =>
            [[lon + 0.1, lat + 0.1], [lon + 0.3, lat + 0.1], [lon + 0.3, lat + 0.3]];
          const zones = [
            {id: "a", zl: 19, polygon: sq(6, 46)},
            {id: "b", zl: 18, polygon: sq(8, 47)},
            {id: "c", zl: 17, polygon: sq(6, 46)},
            {id: "d", zl: 17, polygon: [[5.9, 45.9], [6.1, 45.9], [6.1, 46.1]]},
          ];
          const ids = (list) => list && list.map((z) => z.id);
          const shown = m.listedZones(zones, ["+46+006"]);
          return {
            shown: ids(shown),
            none: ids(m.listedZones(zones, [])),
            all: ids(m.listedZones(zones, ["+46+006"], {all: true})),
            kept: ids(m.listedZones(zones, ["+46+006"], {keep: (z) => z.id === "b"})),
            other: ids(m.listedZones(zones, ["+47+008"])),
            up: ids(m.movedInList(zones, shown, "c", -1)),
            down: ids(m.movedInList(zones, shown, "a", 1)),
            swap: ids(m.movedInList(zones, zones, "c", -1)),
            top: m.movedInList(zones, shown, "a", -1),
            bottom: m.movedInList(zones, shown, "d", 1),
          };
        })()""",
    )
    assert got["shown"] == ["a", "c", "d"]  # b is in +47+008; d spans four tiles, +46+006 too
    assert got["none"] == got["all"] == ["a", "b", "c", "d"]  # no tile chosen, or all asked for
    assert got["kept"] == ["a", "b", "c", "d"]  # the selected zone is listed wherever it is
    assert got["other"] == ["b"]
    assert got["up"] == ["c", "a", "b", "d"]  # c passes a, the row above, and b on the way
    assert got["down"] == ["b", "c", "a", "d"]  # a passes c, and b on the way: only a moves
    assert got["swap"] == ["a", "c", "b", "d"]  # everything shown: the swap it always was
    assert got["top"] is None and got["bottom"] is None

    map_js = (UI / "map.js").read_text(encoding="utf-8")
    render = re.search(r"\n  function renderList\(\) \{.*?\n  \}\n", map_js, re.S)
    assert render is not None and "const rows = listed();" in render.group(0)
    assert "renderListHead();" in render.group(0)
    listed = re.search(r"\n  function listed\(.*?\n  \}\n", map_js, re.S)
    assert listed is not None
    assert "z.id === zs.selected || zs.marks.has(z.id) || z.polygon.length < 3" in listed.group(0)
    move = re.search(r"\n  function moveZone\(id, delta\) \{.*?\n  \}\n", map_js, re.S)
    assert move is not None and "movedInList(zs.zones, listed(), id, delta)" in move.group(0)
    assert "index === count - 1" in map_js  # the last row shown, not the last zone
    # a zone of another tile clicked on the map joins the list, and leaves it once deselected
    relist = "if (next ? !shown.includes(next) : shown.some((id) => !rows.has(id))) renderList();"
    assert relist in map_js
    toggle = map_js.index('$("zones-others-toggle")?.addEventListener("click"')
    assert map_js.index("zs.showAll = !zs.showAll;", toggle) < map_js.index("renderList();", toggle)

    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    at = [html.index(f'id="{name}"') for name in ("zone-list", "zones-others", "zones-help")]
    assert at == sorted(at)  # under the list, above the shortcuts
    for lang, words in (
        ("en", "outside the selected tiles"),
        ("fr", "hors des tuiles sélectionnées"),
    ):
        texts = _node_json(
            "i18n.js",
            f'(globalThis.document = {{documentElement: {{}}}}, m.setLanguage("{lang}"),'
            ' [m.t("zones.others", {n: 12}), m.t("zones.others_show"),'
            ' m.t("zones.others_hide")])',
        )
        assert "12" in texts[0] and words in texts[0] and texts[1] and words in texts[2], texts


def test_the_job_list_empties_after_asking() -> None:
    """A user asked to empty the Works list: a trash beside its title, shown when a finished job is
    listed, asks first, then POST /api/jobs/clear; a running job stays, and the job shown leaves
    the screen when it was cleared. The mock clears like the engine."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    head = re.search(r'<div class="jobs-head">.*?\n {8}</div>', html, re.S)
    assert head is not None and 'id="jobs-clear"' in head.group(0)
    assert html.index('id="jobs-clear"') < html.index('id="job-list"')
    dialog = re.search(r'<dialog id="jobs-clear-confirm".*?</dialog>', html, re.S)
    assert dialog is not None
    assert 'value="keep" autofocus' in dialog.group(0) and 'value="clear"' in dialog.group(0)

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    code = _function_body(app_js, "clearJobs")
    assert code.index("await confirmClearJobs(") < code.index('api("POST", "/api/jobs/clear")')
    assert code.index('api("POST", "/api/jobs/clear")') < code.index("await refreshJobList();")
    assert "await followJobGone();" in code and 't("works.cleared"' in code
    gone = _function_body(app_js, "followJobGone")
    assert "forgetShownJob();" in gone and "watchJob(next.id)" in gone
    listing = _function_body(app_js, "renderJobList")
    assert "!state.jobs.some((j) => !jobActive(j))" in listing
    assert '$("jobs-clear").addEventListener("click", clearJobs);' in app_js

    script = """
    const request = {tiles: ["+46+006"], provider: "BI", zoom_level: 16};
    const run = (await call("POST", "/api/jobs", request)).ok;
    const before = (await call("GET", "/api/jobs")).ok.map((j) => [j.id, j.status]);
    const cleared = (await call("POST", "/api/jobs/clear")).ok;
    const after = (await call("GET", "/api/jobs")).ok.map((j) => j.id);
    const gone = await call("GET", "/api/jobs/20260912-174000-7a3c");
    const out = JSON.stringify({run: run.job_id, before, cleared, after, gone});
    process.stdout.write(out, () => process.exit(0));  // the running mock job keeps node alive
    """
    got = _node_mock(script)
    assert ["20260912-174000-7a3c", "failed"] in got["before"] and len(got["before"]) == 2
    assert got["cleared"] == {"removed": ["20260912-174000-7a3c"]}
    assert got["after"] == [got["run"]]  # the running job stays
    assert got["gone"]["status"] == 404


def test_one_finished_build_can_leave_the_list_alone() -> None:
    """A user asked to remove a finished build from the Works list without emptying it (2026-09-24):
    a bin on the row, only on a row that has finished, DELETE /api/jobs/{id}. Its progress and its
    journal go with it, the tiles it built stay, and the keyboard is left on the row that takes its
    place. A build running or waiting has no bin, and the engine refuses it anyway (409).
    """
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    listing = _function_body(app_js, "renderJobList")
    assert "if (!jobActive(j)) row.append(forgetButton(j, tiles));" in listing, "finished rows only"
    code = _function_body(app_js, "forgetJob")
    assert code.index('api("DELETE", `/api/jobs/${encodeURIComponent(j.id)}`)') < code.index(
        "await refreshJobList();"
    )
    assert "await followJobGone();" in code, "the same rule the whole-list trash follows"
    assert "state.jobsClearing" in code and ".focus({ preventScroll: true })" in code

    # the bin says what goes, and both languages have the words
    tables = _i18n_tables()
    for lang in ("fr", "en"):
        assert "works.forget" in tables[lang] and "works.forget_one" in tables[lang]

    script = """
    const request = {tiles: ["+46+006"], provider: "BI", zoom_level: 16};
    const run = (await call("POST", "/api/jobs", request)).ok;
    const live = await call("DELETE", "/api/jobs/" + run.job_id);
    const past = await call("DELETE", "/api/jobs/20260912-174000-7a3c");
    const left = (await call("GET", "/api/jobs")).ok.map((j) => j.id);
    const again = await call("DELETE", "/api/jobs/20260912-174000-7a3c");
    const out = JSON.stringify({run: run.job_id, live, past, left, again});
    process.stdout.write(out, () => process.exit(0));  // the running mock job keeps node alive
    """
    got = _node_mock(script)
    assert got["live"]["status"] == 409, "a build under way is refused"
    assert got["live"]["code"] == "SYS_BUSY"
    assert got["past"]["ok"] == {"job_id": "20260912-174000-7a3c", "removed": True}
    assert got["left"] == [got["run"]], "the running build stays, and only it"
    assert got["again"]["status"] == 404, "gone is gone"


def test_the_step_1_trash_unselects_every_tile_at_once() -> None:
    """A user asked for a trash in step 1 of the Plan to remove every selected tile: on the line of
    the tile count, shown when tiles are selected, it empties the selection without asking (nothing
    on the disk changes, and the selection is not saved), drops the estimate and says how many
    tiles it unselected (docs/specs/ui.md, Step 1)."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    row = re.search(r'<div class="tile-count-row">.*?\n {10}</div>', html, re.S)
    assert row is not None and 'id="tile-count"' in row.group(0)
    assert re.search(r'<button [^>]*id="tiles-clear"[^>]* hidden>', row.group(0))
    assert 'data-i18n="plan.tiles_clear"' in row.group(0)
    ids = ("tile-chips", "tiles-clear", "selection-size")
    places = [html.index(f'id="{name}"') for name in ids]
    assert places == sorted(places)
    assert '<section class="plan-step" id="tiles-panel" tabindex="-1"' in html

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    code = _function_body(app_js, "clearTiles")
    assert "confirm" not in code and "showModal" not in code  # a selection is unselected at once
    assert code.index("state.tiles = [];") < code.index("renderTiles();")
    steps = ("renderTiles();", "renderZlOptions();", "planChanged();")
    for step in (*steps, '$("tiles-panel").focus(', 't("plan.tiles_cleared"'):
        assert step in code, step
    assert '$("tiles-clear").hidden = !state.tiles.length;' in _function_body(app_js, "renderTiles")
    assert '$("tiles-clear").addEventListener("click", clearTiles);' in app_js


MOCK_CALL = """
const api = (method, path, body) => m.mockApi(method, path, body).then(
  (ok) => ({ ok }),
  (err) => ({ status: err.status, error: err.detail?.error ?? null }),
);
const until = async (test) => {
  for (let i = 0; i < 400 && !(await test()); i += 1) await new Promise((r) => setTimeout(r, 25));
};
const body = (tiles) => ({ tiles, provider: "BI", zoom_level: 16, install: false, zones: [] });
"""


def test_a_build_asked_for_during_another_waits_in_the_queue() -> None:
    """A user asked to start builds while one runs: the page asks with ``queue`` and the build
    waits for its turn, as in the engine. The Plan stays for the next ones and step 4 says
    beforehand that it will wait; Works says that a build waits and can take it out of the queue;
    off Works the page follows the build under way; Quit says that the waiting builds are cancelled
    too (docs/specs/ui.md, Step 4 and Works)."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    build = _function_body(app_js, "build")
    assert "{ ...req, install, queue: true }" in build
    waits = build[build.index("if (res.queue_position > 0) {") : build.index("} else {")]
    assert 't("plan.queued"' in waits and "await refreshJobList();" in waits
    assert "showScreen" not in waits
    assert 't("plan.tiles_in_build_refused"' in build and 't("plan.busy_deleting")' in build
    assert 't("step4.queue_note")' in _function_body(app_js, "renderBuildActions")
    assert "/retry`, { queue: true })" in _function_body(app_js, "retryJob")
    view = _function_body(app_js, "updateJobView")
    assert 't("works.queued_note")' in view and 't("works.unqueue")' in view
    assert 't("quit.queued"' in _function_body(app_js, "confirmQuit")
    follow = _function_body(app_js, "followBuildUnderWay")
    assert 'state.screen === "works"' in follow and 'j.status === "running"' in follow
    assert "followBuildUnderWay();" in _function_body(app_js, "jobsChanged")
    assert "jobsChanged();" in _function_body(app_js, "refreshJobList")

    script = """
    const first = (await api("POST", "/api/jobs", { ...body(["+46+006"]), queue: true })).ok;
    const busy = await api("POST", "/api/jobs", body(["+46+007"]));
    const second = (await api("POST", "/api/jobs", { ...body(["+46+007"]), queue: true })).ok;
    const third = (await api("POST", "/api/jobs", { ...body(["+46+008"]), queue: true })).ok;
    const tiles = ["+46+009", "+46+006", "+46+007"];
    const refused = await api("POST", "/api/jobs", { ...body(tiles), queue: true });
    const active = (await api("GET", "/api/status")).ok.active_job;
    const job = async (id) => (await api("GET", `/api/jobs/${id}`)).ok;
    const waiting = await job(second.job_id);
    const unqueued = (await api("POST", `/api/jobs/${third.job_id}/cancel`)).ok;
    await until(async () => (await job(second.job_id)).status === "running");
    const ids = [first, second, third].map((x) => x.job_id);
    const [firstEnd, secondRun, thirdEnd] = await Promise.all(ids.map(job));
    const fourth = (await api("POST", "/api/jobs", { ...body(["+46+006"]), queue: true })).ok;
    const quit = (await api("POST", "/api/quit", { force: true })).ok;
    const listed = (await api("GET", "/api/jobs")).ok.map((j) => [j.id, j.status]);
    const out = { first, busy: busy.error.code, second, third, active, unqueued, fourth, quit,
      listed, refused: [refused.status, refused.error.code, refused.error.context],
      waiting: [waiting.status, waiting.started_at],
      firstEnd: [firstEnd.status, firstEnd.finished_at],
      secondRun: [secondRun.status, secondRun.started_at],
      thirdEnd: [thirdEnd.status, thirdEnd.started_at] };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(MOCK_CALL + script, speed=4)  # the first build lasts a few seconds
    first, second = got["first"]["job_id"], got["second"]["job_id"]
    assert got["first"]["queue_position"] == 0 and got["first"]["status"] == "running"
    assert got["busy"] == "SYS_BUSY"  # without queue, as before
    assert got["second"]["status"] == "queued" and got["second"]["queue_position"] == 1
    assert got["third"]["queue_position"] == 2
    assert got["refused"] == [
        409, "SYS_TILE_IN_BUILD", {"tiles": ["+46+006", "+46+007"], "jobs": sorted([first, second])}
    ]  # fmt: skip
    assert got["active"] == first and got["waiting"] == ["queued", None]
    assert got["unqueued"]["cancel_requested"] is True
    # the second started once the first had ended (the mock's clock runs faster: no date to compare)
    assert got["firstEnd"][0] == "done" and got["secondRun"][0] == "running"
    assert got["secondRun"][1] is not None
    assert got["thirdEnd"] == ["cancelled", None]  # taken out of the queue, never started
    assert got["fourth"]["queue_position"] == 1  # +46+006 is free again: its build ended
    fourth = got["fourth"]["job_id"]
    assert got["quit"] == {"stopping": True, "cancelled": second, "queued_cancelled": [fourth]}
    assert dict(got["listed"])[fourth] == "cancelled"


def test_a_tile_in_a_build_cannot_be_chosen_again() -> None:
    """A user asked what a click on a tile being built did: it was chosen again, and with the
    queue it would have been built twice. A tile in a build, under way or waiting, is not chosen
    (a toast says why; the other ways of adding tiles leave it out and say so). In the Library the
    buttons of such a tile wait for the build's end, and deleting waits for the builds to end. The
    mock refuses like the engine (docs/specs/ui.md, Step 1 and Library)."""
    jobs = """[
      {id: "a", status: "running", tiles: ["+46+006", "+46+007"]},
      {id: "b", status: "queued", tiles: ["+46+007", "+46+008"]},
      {id: "c", status: "done", tiles: ["+46+009"]},
      {id: "d", status: "running", tiles: [
        {tile: "+47+006", status: "done", steps: {install: {status: "done", nodes: {n: {}}}}},
        {tile: "+47+007", status: "running", steps: {data: {status: "running", nodes: {n: {}}}}},
        {tile: "+47+008", status: "failed", steps: {data: {status: "failed", nodes: {n: {}}}}},
      ]},
    ]"""
    got = _node_json("app.js", f"[...m.tilesInBuilds({jobs}).entries()]")
    # a summary says nothing of its tiles; a job's state does: the finished +47+006 is free again
    assert got == [["+46+006", "a"], ["+46+007", "a"], ["+46+008", "b"], ["+47+007", "d"],
                   ["+47+008", "d"]]  # fmt: skip

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    toggle = _function_body(app_js, "toggleTile")
    assert toggle.index("tilesInBuilds(activeJobs()).has(name)") < toggle.index("addTiles([name])")
    assert 't("plan.tile_in_build"' in toggle
    add = _function_body(app_js, "addTiles")
    assert "if (building.has(n)) skipped.push(n);" in add
    assert "return { skipped, capped };" in add
    # the cap lived in the mouse sweep alone, so a flight plan added nine hundred squares and the
    # estimate then refused the whole selection (found in review, 2026-09-23)
    assert "state.tiles.length >= MAX_BUILD_TILES" in add, "every way of adding squares is capped"
    assert "sayTilesInBuild(addTiles(" in _function_body(
        app_js, "addRouteTiles"
    ) or "addTiles(names)" in _function_body(app_js, "addRouteTiles")
    for name in ("addTilesFromText", "addTileFromLatLon", "addTilesFromIcao"):
        assert "sayTilesInBuild(addTiles(" in _function_body(app_js, name), name
    row = _function_body(app_js, "libraryRow")
    assert row.count("disabled: busy || inBuild") == 2 and "disabled: busy || building" in row
    assert 't("library.in_build")' in row
    library = _function_body(app_js, "renderLibrary")
    assert '$("library-building").hidden = !activeJobs().length;' in library
    assert "SYS_TILE_IN_BUILD: () =>" in app_js
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert re.search(
        r'<p [^>]*id="library-building" data-i18n="library.building_note" hidden>', html
    )

    script = """
    await api("GET", "/api/library");
    const job = (await api("POST", "/api/jobs", body(["+43+005", "+44+005"]))).ok;
    const osxp = await api("POST", "/api/library/+43+005/uninstall", {});
    const ortho4xp = await api("POST", "/api/library/+44+005/uninstall", {});
    const deleted = await api("POST", "/api/library/+43+006/delete", {});
    await api("POST", `/api/jobs/${job.job_id}/cancel`);
    const after = await api("POST", "/api/library/+43+005/uninstall", {});
    const out = { job: job.job_id, osxp: [osxp.status, osxp.error?.code, osxp.error?.context],
      ortho4xp: Boolean(ortho4xp.ok), deleted: [deleted.status, deleted.error?.code],
      after: Boolean(after.ok) };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(MOCK_CALL + script)
    assert got["osxp"] == [409, "SYS_TILE_IN_BUILD", {"tiles": ["+43+005"], "jobs": [got["job"]]}]
    assert got["ortho4xp"] is True  # the Ortho4XP tile of a square being built stays free
    assert got["deleted"] == [409, "SYS_BUSY"]
    assert got["after"] is True


def test_a_tile_its_build_has_finished_is_free_again() -> None:
    """A user was refused +27+035, built and installed, while the two other tiles of its build
    ran: a tile the running build has finished can be chosen and queued again, in the mock as in
    the engine; one still being built stays refused."""
    script = """
    const job = (await api("POST", "/api/jobs", body(["+46+006", "+46+007"]))).ok;
    const read = async () => (await api("GET", `/api/jobs/${job.job_id}`)).ok;
    await until(async () => {
      const s = await read();
      return s.tiles[0].status === "done" || s.status !== "running";
    });
    const state = await read();
    const again = await api("POST", "/api/jobs", { ...body(["+46+006"]), queue: true });
    const other = await api("POST", "/api/jobs", { ...body(["+46+007"]), queue: true });
    await api("POST", "/api/quit", { force: true });
    const out = { running: state.status, first: state.tiles[0].status, other: other.error?.code,
      again: again.ok ? again.ok.queue_position : again.error?.code };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(MOCK_CALL + script, speed=2)
    assert got == {"running": "running", "first": "done", "again": 1, "other": "SYS_TILE_IN_BUILD"}


def test_the_cost_follows_the_plan_and_build_needs_no_estimate_first() -> None:
    """A user found pressing Estimate before Build tedious, and chose a live estimate: step 3
    works the cost out by itself a moment after each change of the tiles, the source, the level,
    the zones or the settings, dimming the figures of before; an answer older than the last change
    is dropped. Build is there as soon as tiles are chosen, sums the estimate up under its buttons,
    and waits only when the estimate was refused or the disk cannot hold the build, saying why; a
    click during a change waits for its estimate. The top bar and the status bar are pinned
    (docs/specs/ui.md, Steps 3 and 4)."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    changed = _function_body(app_js, "planChanged")
    assert "estimateTimer = setTimeout(estimate, ESTIMATE_DELAY_MS);" in changed
    assert "state.planStale = true;" in changed and "estimateSeq += 1;" in changed
    assert re.search(r"const ESTIMATE_DELAY_MS = \d{3};", app_js)
    estimate = _function_body(app_js, "estimate")
    assert estimate.count("if (seq !== estimateSeq) return;") == 1  # a late answer is dropped
    assert estimate.index('api("POST", "/api/plan", req)') < estimate.index(
        "if (seq !== estimateSeq)"
    )
    assert "persistSettings" not in estimate  # a live estimate saves nothing
    for name in ("addTiles", "removeTile", "clearTiles"):
        assert "planChanged();" in _function_body(app_js, name), name
    # the zones changing re-plans, and redraws the Library, which marks a tile whose square
    # now asks for other colours (2026-09-18)
    assert "onZonesChanged: () => {" in app_js
    assert "planChanged();" in app_js and "renderLibrary();" in app_js
    # the source, step 1's level, and the level of a group of the flight plan (2026-09-22)
    assert app_js.count("planChanged();\n    planMap?.planChanged();") == 3
    assert "state.plan = null;" not in "".join(
        _function_body(app_js, n) for n in ("addTiles", "removeTile", "clearTiles", "build")
    )
    actions = _function_body(app_js, "renderBuildActions")
    assert "const blocked = !tiles || refused || short || Boolean(state.buildStarting);" in actions
    assert "state.buildStarting = true;" in _function_body(app_js, "build")  # never asked twice
    for key in ("step4.need_tiles", "step4.refused", "step4.disk_short", "step4.summary"):
        assert f't("{key}"' in actions, key
    build = _function_body(app_js, "build")
    waits = "await (estimateTimer || !estimateRun ? estimate() : estimateRun);"
    assert build.index(waits) < build.index("diskVerdict(state.plan.disk).ok === false) return;")
    assert build.index("diskVerdict(state.plan.disk)") < build.index('api("POST", "/api/jobs"')
    panel = _function_body(app_js, "renderPlanPanel")
    assert 'panel.classList.toggle("is-stale", Boolean(plan) && working);' in panel

    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert re.search(
        r'<button [^>]*id="estimate-btn" data-i18n="plan.estimate_again" hidden>', html
    )
    assert '<div id="plan-panel" class="plan-panel">' in html  # no live region re-read each time
    assert 'id="build-note" aria-live="polite"' in html and 'id="build-queue" hidden' in html
    i18n = (UI / "i18n.js").read_text(encoding="utf-8")
    assert "step4.need_estimate" not in i18n and '"plan.estimate":' not in i18n
    css = (UI / "styles.css").read_text(encoding="utf-8")
    topbar = css[css.index(".topbar {") : css.index("}", css.index(".topbar {"))]
    statusbar = css[css.index(".statusbar {") : css.index("}", css.index(".statusbar {"))]
    assert "position: sticky;" in topbar and "top: 0;" in topbar
    assert "position: sticky; bottom: 0;" in statusbar
    assert ".map-col { position: sticky; top: calc(var(--topbar-h) + 12px);" in css
    assert "bottom: var(--statusbar-h);" in css and "scroll-padding-top:" in css
    assert "trackStatusbarHeight();" in _function_body(app_js, "boot")
    assert "min-height: 30px;" in css and "height: 30px;" not in css.replace("min-height", "")


def test_the_sources_are_grouped_by_what_they_cover_bing_and_esri_first() -> None:
    """A user found the sources of several countries mixed in one list, and asked for Bing and
    Esri first: step 1 lists the whole world's (in the engine's order: Bing Maps, Esri), then the
    countries' that cover every tile chosen, the user's own, and the other countries' by country,
    each with its country ("Netherlands · PDOK 2020"); NL, the same imagery as PDOK, is listed
    once. A source that misses a chosen tile says so under the list; the zones' list and the
    Settings question follow the same order (docs/specs/ui.md, Step 1)."""
    providers = _mock_json("providers")
    script = f"""(() => {{
      const rows = {json.dumps(providers)};
      const codes = (g) => Object.fromEntries(
        Object.entries(g).map(([k, v]) => [k, v.map((p) => p.code)]));
      const mine = {{code: "Mine", name: "Mine", custom: true, extent: null, extent_bounds: null}};
      return {{
        none: codes(m.sourceGroups(rows, [], "BI")),
        netherlands: codes(m.sourceGroups([...rows, mine], ["+52+004", "+51+005"], "BI")),
        border: codes(m.sourceGroups(rows, ["+52+004", "+46+006"], "NL")),
        labels: rows.filter((p) => ["BI", "PDOK20", "USGS"].includes(p.code)).map(m.sourceLabel),
        covers: [m.sourceCovers(rows[0], "+27+034"),
                 m.sourceCovers(rows.find((p) => p.code === "SP"), "+40+000"),
                 m.sourceCovers(rows.find((p) => p.code === "SP"), "+52+004")],
        missing: m.tilesNotCovered(rows.find((p) => p.code === "JP"), ["+35+139", "+46+006"]),
        addresses: ["https://a/{{zoom}}/{{x}}/{{y}}.jpg", "https://a/t?q={{quadkey}}",
                    "ftp://a/{{x}}/{{y}}/{{zoom}}", "https://a/15/1/2.jpg",
                    "https://a/{{x}}/{{y}}"].map(m.sourceAddressProblem),
      }};
    }})()"""
    got = _node_json("sources.js", script)
    countries = ["Lux", "PDOK", "PDOK18", "PDOK19", "PDOK20", "SP", "JP", "USGS"]
    assert got["none"] == {"world": ["BI", "Arc", "Arc@"], "tiles": [], "mine": [], "other": [
        "JP", "Lux", "PDOK", "PDOK18", "PDOK19", "PDOK20", "SP", "USGS"]}  # fmt: skip
    assert sorted(got["none"]["other"]) == sorted(countries)  # NL (same as PDOK) left out
    assert got["netherlands"]["tiles"] == ["PDOK", "PDOK18", "PDOK19", "PDOK20"]
    assert got["netherlands"]["mine"] == ["Mine"] and got["netherlands"]["world"][0] == "BI"
    assert got["border"]["tiles"] == [] and "NL" in got["border"]["other"]  # the one selected
    assert got["labels"] == [
        "Bing Maps",
        "Netherlands · PDOK 2020",
        "United States · USGS The National Map",
    ]
    assert got["covers"] == [True, True, False] and got["missing"] == ["+46+006"]
    assert got["addresses"] == [None, None, "scheme", "place", "place"]

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    options = _function_body(app_js, "renderSourceOptions")
    assert 'for (const key of ["world", "tiles", "mine", "other"])' in options
    assert 'h("optgroup", { label: sourceGroupTitle(key, state.tiles.length > 0) }' in options
    assert "renderSourceCoverage();" in options
    assert 't("plan.source_coverage"' in _function_body(app_js, "renderSourceCoverage")
    assert "renderSourceOptions();" in _function_body(app_js, "renderTiles")
    assert 'renderProviders(state.settings?.essential?.provider || "BI");' in _function_body(
        app_js, "boot"
    )
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    assert (
        "sourceGroups(list, [], current)" in map_js
        and 'for (const key of ["world", "mine", "other"])' in map_js
    )
    settings_js = (UI / "settings.js").read_text(encoding="utf-8")
    assert "[...groups.world, ...groups.mine, ...groups.other]" in settings_js
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert re.search(
        r'<small class="help is-fail" id="provider-coverage" role="status" hidden>', html
    )


def test_a_user_adds_a_source_of_their_own_at_their_own_risk() -> None:
    """A user asked for sources OrthoStudio XP does not ship (Google Maps, used a lot in the USA):
    OrthoStudio XP does not ship them, but "My sources…" under step 1's list adds one. The dialog
    says its terms apply to the user, checks the address before sending it, tries one tile where
    the page is looking, adds the source (chosen at once) and removes it unless a zone uses it.
    The mock answers like the engine, the refusal of a country's source outside its country
    included (docs/specs/ui.md, Step 1)."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    dialog = re.search(r'<dialog id="sources-dialog".*?</dialog>', html, re.S)
    assert dialog is not None
    needles = ('data-i18n="sources.warning"', 'id="source-name"', 'id="source-url"',
               'id="source-zl"', 'id="source-try"', 'id="sources-list"',
               'type="submit"')  # fmt: skip
    for needle in needles:
        assert needle in dialog.group(0), needle
    assert 'id="sources-open" data-i18n="sources.open"' in html
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    form = _function_body(app_js, "sourceForm")
    assert "sourceAddressProblem(form.url_template)" in form
    trying = _function_body(app_js, "trySource")
    assert trying.index("sourceForm(false)") < trying.index('api("POST", "/api/sources/test"')
    assert "...sourceTryPoint()" in trying
    added = _function_body(app_js, "addSource")
    assert "await reloadProviders(added.code);" in added
    assert 't("sources.in_use"' in _function_body(app_js, "removeSource")
    for wiring in ('$("sources-open").addEventListener("click", openSources);',
                   '$("sources-form").addEventListener("submit", addSource);',
                   '$("source-try").addEventListener("click", trySource);'):  # fmt: skip
        assert wiring in app_js, wiring

    script = """
    const address = "https://tiles.example/{zoom}/{x}/{y}.jpg";
    const post = (path, body) => api("POST", path, body);
    const bad = await post("/api/sources", { name: "Bad", url_template: "ftp://x/{x}/{y}/{zoom}" });
    const where = { url_template: address, lat: 46.5, lon: 6.5 };
    const tried = (await post("/api/sources/test", where)).ok;
    const mine = { name: "My satellite", url_template: address };
    const added = (await post("/api/sources", { ...mine, max_zl: 20 })).ok;
    const twice = (await post("/api/sources", mine)).ok;
    const custom = async () => (await api("GET", "/api/providers")).ok
      .filter((p) => p.custom).map((p) => p.code);
    const listed = await custom();
    const plan = (tiles, provider) =>
      post("/api/plan", { tiles, provider, zoom_level: 16, zones: [] });
    const planned = await plan(["+46+006"], added.code);
    const outside = await plan(["+27+034"], "PDOK20");
    const unknown = await api("DELETE", "/api/sources/BI");
    const removed = (await api("DELETE", `/api/sources/${twice.code}`)).ok;
    const after = await custom();
    const out = { bad: [bad.status, bad.error.code], tried, added, twice: twice.code, listed,
      planned: Boolean(planned.ok), unknown: unknown.status, removed, after,
      outside: [outside.status, outside.error.code, outside.error.context] };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(MOCK_CALL + script, speed=20)
    assert got["bad"] == [422, "CFG_VALUE_INVALID"]
    assert got["tried"]["ok"] is True and got["tried"]["image"] == "jpeg"
    assert got["added"]["code"] == "Mysatellite" and got["added"]["custom"] is True
    assert got["added"]["max_zl"] == 20 and got["twice"] == "Mysatellite_2"
    assert got["listed"] == ["Mysatellite", "Mysatellite_2"] and got["planned"] is True
    refusal = {"provider": "PDOK20", "extent": "Netherlands", "tiles": "+27+034"}
    assert got["outside"] == [422, "CFG_PROVIDER_OUT_OF_COVERAGE", refusal]
    assert got["unknown"] == 404
    assert got["removed"] == {"removed": "Mysatellite_2", "settings_provider": None}
    assert got["after"] == ["Mysatellite"]


def test_the_library_says_when_roads_come_twice_and_mends_it_in_one_click() -> None:
    """A user asked what happens with AutoOrtho or XPME and OrthoStudio XP together: X-Plane draws
    every active pack's overlays, so a square both have gets its roads, forests and buildings
    twice. The Library says it above the table and on the row, and one click leaves those roads to
    the other pack; a square whose roads went to a pack no longer active says so too, and one click
    takes OrthoStudio XP's back (docs/specs/ui.md, Library)."""
    got = _node_json("app.js", '["yAutoOrtho_Overlays", "XPME_Overlays", "yOrtho4XP_Overlays", '
                     '"yOther_Overlays"].map(m.overlayPackLabel)')  # fmt: skip
    assert got == ["AutoOrtho", "XPME", "Ortho4XP", "yOther_Overlays"]
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    notices = _function_body(app_js, "renderOverlayNotices")
    assert 'tiles("double")' in notices and 'tiles("missing")' in notices
    assert 'setOverlays(doubles.map((e) => e.tile), "others")' in notices
    assert 'setOverlays(missing.map((e) => e.tile), "own")' in notices
    marks = _function_body(app_js, "overlayPill")
    assert 'onclick: () => setOverlays([e.tile], "own")' in marks
    assert "renderOverlayNotices(rows);" in _function_body(app_js, "renderLibrary")
    assert '"/api/library/overlays"' in _function_body(app_js, "setOverlays")
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert '<div class="library-overlays" id="library-overlays" role="status" hidden></div>' in html

    script = """
    const row = async () => (await api("GET", "/api/library")).ok
      .find((e) => e.kind === "ortho" && e.tile === "+43+005").overlay;
    const before = await row();
    const set = async (use) =>
      (await api("POST", "/api/library/overlays", { use, tiles: ["+43+005"] })).ok;
    const left = await set("others");
    const leftRow = await row();
    const back = await set("own");
    const out = { before, left, leftRow, back, after: await row() };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(MOCK_CALL + script)
    double = {"state": "double", "others": ["yAutoOrtho_Overlays"]}
    left = {"state": "left", "others": ["yAutoOrtho_Overlays"]}
    assert got["before"] == double and got["leftRow"] == left and got["after"] == double
    assert got["left"] == {"changed": ["+43+005"], "states": {"+43+005": left}}
    assert got["back"] == {"changed": ["+43+005"], "states": {"+43+005": double}}


def test_borders_that_failed_to_load_are_tried_again() -> None:
    """A failed reading of the borders used to grey the box out until the page was reloaded (the
    likely cause, OrthoStudio XP being restarted, lasts seconds): it is tried again after a growing
    delay, and at once when the box is ticked again or the browser is back online."""
    delays = _node_json("map.js", "[0, 1, 2, 3, 4, 9].map((n) => m.bordersRetryDelay(n))")
    assert delays == [5000, 5000, 15000, 60000, 300000, 300000]
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    load = re.search(r"\n  async function loadBorders\(\) \{.*?\n  \}\n", map_js, re.S)
    assert load is not None
    assert "borders.tries += 1;" in load.group(0)
    assert "setTimeout(" in load.group(0) and "bordersRetryDelay(borders.tries)" in load.group(0)
    toggle = re.search(r"\n  function bordersToggle\(\) \{.*?\n  \}\n", map_js, re.S)
    assert toggle is not None and "disabled" not in toggle.group(0)
    assert 'window.addEventListener("online"' in map_js


# -- settings in plain words (docs/specs/ui.md 2.4, settings-plain-language.md) ---------------


def test_every_setting_is_offered_somewhere() -> None:
    """A question, a field under For experts, or the retired list: a setting added to the engine
    cannot be forgotten by the page, and each expert field has its plain label and note."""
    from orthostudio.config import leaf_properties

    got = _node_json(
        "settings.js",
        "{covered: m.coveredPaths(), labels: [...m.EXPERT_GROUPS.flatMap((g) => g.fields),"
        " ...m.RETIRED].map((p) => [p, m.fieldLabel(p), m.fieldHint(p)])}",
    )
    assert got["covered"] == sorted(leaf_properties())
    for path, label, hint in got["labels"]:
        assert label != path and hint, path


def test_a_pack_that_brings_its_own_scenery_answers_the_overlay_question() -> None:
    """A user with simHeaven X-World kept our overlays too and had everything twice
    (2026-09-20). When the engine finds such a pack in X-Plane, the recommended answer is the
    other one, and both answers say why."""
    doc = _mock_json("settings")
    plain = _node_json("settings.js", f"m.questionChoices('overlays', {json.dumps(doc)}, {{}})")
    assert [c["value"] for c in plain] == ["xplane", "none"]
    assert plain[0]["recommended"] and not plain[1].get("recommended")
    assert not plain[0].get("note")

    found = _node_json(
        "settings.js",
        f"m.questionChoices('overlays', {json.dumps(doc)}, {{ownScenery: ['simHeaven X-World']}})",
    )
    assert not found[0].get("recommended") and found[1]["recommended"]
    assert "simHeaven X-World" in found[0]["note"] and "simHeaven X-World" in found[1]["note"]


def test_questions_offer_valid_answers_and_one_recommended_each() -> None:
    from orthostudio.config import leaf_properties

    leaves = leaf_properties()
    doc = _mock_json("settings")
    providers = _mock_json("providers")
    script = (
        "Object.fromEntries(['provider', 'detail', 'airports', 'coast', 'width', 'water', 'lakes',"
        " 'relief', 'holes', 'overlays'].map((id) => [id, m.questionChoices(id, "
        f"{json.dumps(doc)}, {{providers: {json.dumps(providers)}}})]))"
    )
    choices = _node_json("settings.js", script)
    paths = {
        "detail": "essential.zoom_level",
        "airports": "essential.airports.mode",
        "coast": "essential.coast_transition.profile",
        "width": "essential.coast_transition.width_m",
        "water": "essential.water_rendering",
        "lakes": "advanced.ratio_water_pct",
        "relief": "essential.relief.source",
        "holes": "essential.relief.fill_nodata",
        "overlays": "essential.overlays",
    }
    for qid, options in choices.items():
        assert sum(1 for c in options if c.get("recommended")) == 1, qid
        assert all(c["label"] for c in options), qid
        prop = leaves.get(paths.get(qid, ""), {})
        for c in options:
            if "enum" in prop:
                assert c["value"] in prop["enum"], (qid, c)
            elif prop.get("type") in ("integer", "number"):
                assert prop.get("minimum", 0) <= c["value"] <= prop.get("maximum", 10**6), (qid, c)
    assert [c["value"] for c in choices["detail"]] == [15, 16, 17, 18]
    assert choices["provider"][0]["value"] == "BI"
    # a value set by hand is one more choice, never hidden
    doc["advanced"]["ratio_water_pct"] = 40
    doc["essential"]["airports"]["mode"] = "existing"
    extra = _node_json(
        "settings.js",
        f"[m.questionChoices('lakes', {json.dumps(doc)}), m.questionChoices('airports', "
        f"{json.dumps(doc)})].map((cs) => cs.map((c) => c.value))",
    )
    assert extra == [[10, 25, 50, 40], ["icao", "on", "off", "existing"]]


def test_presets_set_their_answers_and_are_recognised() -> None:
    doc = _mock_json("settings")
    got = _node_json(
        "settings.js",
        f"(() => {{ const d = {json.dumps(doc)}; const before = m.matchingPreset(d);"
        " const out = {before};"
        " for (const id of ['recommended', 'best', 'light']) {"
        "   const x = m.applyPreset(structuredClone(d), id);"
        "   out[id] = {match: m.matchingPreset(x), zl: x.essential.zoom_level,"
        "     airports: x.essential.airports.mode, provider: x.essential.provider,"
        "     overlays: x.essential.overlays, curvature: x.advanced.curvature_tol}; }"
        " return out; })()",
    )
    assert got["before"] is None  # Ortho4XP's defaults: no extra detail at airports
    assert got["recommended"] == {
        "match": "recommended", "zl": 16, "airports": "icao", "provider": "BI",
        "overlays": "xplane", "curvature": 2.0,
    }  # fmt: skip
    assert (got["best"]["match"], got["best"]["zl"], got["best"]["airports"]) == ("best", 17, "on")
    assert (got["light"]["match"], got["light"]["zl"], got["light"]["airports"]) == (
        "light", 15, "off"
    )  # fmt: skip


def test_the_fade_in_three_steps_and_the_answers_that_go_with_it() -> None:
    doc = _mock_json("settings")
    got = _node_json(
        "settings.js",
        f"(() => {{ const d = {json.dumps(doc)}; const out = {{}};"
        " const ct = () => structuredClone(d.essential.coast_transition);"
        " out.bad = m.applyThreeSteps(d, '100, 200'); out.unchanged = ct();"
        " out.ok = m.applyThreeSteps(d, '100; 200 50'); out.three = ct();"
        " out.text = m.threeStepsText(d); out.widthShown = m.questionShown('width', d);"
        " m.answer(d, 'coast', 'rocks'); out.rocks = ct();"
        " m.applyThreeSteps(d, '1, 2, 3'); m.applyThreeSteps(d, '');"
        " out.back = d.essential.coast_transition; out.list = m.parseList('0, .for, 22001');"
        " return out; })()",
    )
    assert got["bad"] is False and got["unchanged"] == {"profile": "sand", "width_m": 100}
    assert got["ok"] is True and got["three"] == {"profile": "3steps", "width_m": [100, 200, 50]}
    assert got["text"] == "100, 200, 50" and got["widthShown"] is False
    assert got["rocks"] == {"profile": "rocks", "width_m": 100}
    assert got["back"] == {"profile": "sand", "width_m": 100}
    assert got["list"] == [0, ".for", 22001]


def test_the_plan_sums_up_the_settings_in_words() -> None:
    doc = _mock_json("settings")
    doc["essential"]["airports"]["mode"] = "icao"
    doc["essential"]["overlays"] = "none"
    got = _node_json("settings.js", f"m.settingsSummary({json.dumps(doc)})")
    assert got == [
        "main airports sharper (Very sharp · ZL18)",
        "coast fading gradually over about 100 m",
        "photo over X-Plane's water",
        "X-Plane 12 relief",
        "no overlays from OrthoStudio XP",
    ]
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert 'id="plan-settings-summary"' in html and 'id="plan-settings-change"' in html
    assert 'id="settings-tabs"' not in html and 'id="settings-questions"' in html
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "renderFields" not in app_js and 'from "./settings.js"' in app_js


def test_settings_start_with_x_plane_and_the_simheaven_answer() -> None:
    """A user asked for the X-Plane folder first, and did not find the simHeaven option at the end
    of the list: both come first, and the question says the word."""
    code = (UI / "settings.js").read_text(encoding="utf-8")
    body = re.search(r"\nfunction renderQuestions\(box, view\) \{.*?\n\}\n", code, re.S)
    assert body is not None
    order = [
        body.group(0).index(f'questionBox(view, "{qid}"')
        for qid in ("xplane", "overlays", "provider", "detail", "relief")
    ]
    assert order == sorted(order)
    tables = _i18n_tables()
    assert tables["en"]["settings.q.overlays_help"].startswith("Using simHeaven X-World?")
    assert "simHeaven X-World" in tables["fr"]["settings.q.overlays_help"]


def test_quit_and_the_file_manager_from_the_page() -> None:
    """A user asked for a Quit button, and for a button that opens the Finder, the Windows File
    Explorer or the Linux file manager: the top bar quits after asking, the Library, the disk
    space and the X-Plane question show their folders; the mock answers like the engine."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    for element in ('id="quit-btn"', 'id="quit-confirm"', 'id="stopped"', 'id="disk-reveal"'):
        assert element in html, element
    assert 'id="tpl-folder-icon"' in html and '<th scope="col" colspan="3">' in html
    labels = _node_json("app.js", '["mac", "win", "lin", undefined].map((p) => m.revealLabel(p))')
    assert labels == [
        "Show in Finder",
        "Show in File Explorer",
        "Open the folder",
        "Open the folder",
    ]
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    quit_body = _function_body(app_js, "quitOsxp")
    assert quit_body.index("await confirmQuit(") < quit_body.index('api("POST", "/api/quit"')
    assert '$("stopped").hidden = false;' in quit_body
    assert '$("quit-btn").hidden = !s.can_quit;' in app_js
    script = """
    const quit = (await call("POST", "/api/quit", {})).ok;
    const shown = (await call("POST", "/api/reveal", {path: "/Users/pilot/.orthostudio"})).ok;
    const refused = await call("POST", "/api/reveal", {path: "relative"});
    const status = (await call("GET", "/api/status")).ok;
    process.stdout.write(JSON.stringify({quit, shown, refused, platform: status.platform}));
    """
    got = _node_mock(script)
    assert got["quit"] == {"stopping": True, "cancelled": None, "queued_cancelled": []}
    assert got["shown"] == {"revealed": "/Users/pilot/.orthostudio"}
    assert got["refused"] == {"status": 403, "code": "SYS_FORBIDDEN_PATH"}
    assert got["platform"] == "mac"


def test_a_save_drops_an_estimate_that_no_longer_holds() -> None:
    """Saving changes what a build would cost, so the estimate on the Plan is worked out again,
    and leaving Settings with answers not saved says so.

    This test used to check that the Plan *took* the saved detail level, which a user had asked
    for on 2026-09-14. That was undone on 2026-09-21, for the reason
    test_settings_are_where_the_next_plan_starts_and_nothing_more has: the two screens were
    writing over each other in both directions. The estimate is what survives of it."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "planChanged();" in _function_body(app_js, "saveSettings")
    assert 't("settings.left_unsaved")' in _function_body(app_js, "showScreen")


def test_step_3_says_when_x_plane_is_not_found() -> None:
    """A user opened the Windows app in a virtual machine whose X-Plane is on the Mac around it:
    "Estimate" answered "XP_GLOBAL_SCENERY_NOT_FOUND: Global Scenery is not installed in <not
    detected>." and nothing on what to do. Step 3 says it before any estimate, with a button to the
    X-Plane question; a refusal is a card with its remedy and a Settings button; the status is read
    again after Save, so that the folder just chosen shows."""
    got = _node_json(
        "app.js",
        "[m.xplaneNotice({xplane: {path: null, detected: false, running: false}}, "
        "{essential: {xplane_dir: null}}), "
        "m.xplaneNotice({xplane: {path: null, detected: false}}, "
        '{essential: {xplane_dir: "E:\\\\X-Plane 12"}}), '
        'm.xplaneNotice({xplane: {path: "C:\\\\X-Plane 12", detected: true}}, null), '
        "m.xplaneNotice(null, null)]",
    )
    assert got[0].startswith("X-Plane 12 was not found on this computer.")
    assert "E:\\X-Plane 12" in got[1] and got[2] is None and got[3] is None
    tables = _node_json(
        "i18n.js",
        '(globalThis.document = {documentElement: {}}, ["fr", "en"].map((lang) => '
        '(m.setLanguage(lang), [m.codeText("XP_DIR_NOT_FOUND"), '
        'm.codeText("XP_GLOBAL_SCENERY_NOT_FOUND")])))',
    )
    for words in (w for lang in tables for pair in lang for w in pair):
        assert words and "<not detected>" not in words and "Essential" not in words, words
    assert tables[0] != tables[1]

    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert '<div class="plan-error" id="plan-error" role="alert" hidden></div>' in html
    assert 'id="plan-xplane-choose" data-i18n="plan.xplane_choose"' in html
    assert html.index('id="plan-xplane"') < html.index('id="plan-panel"')  # above the cost
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "renderPlanXplane();" in _function_body(app_js, "renderStatus")
    assert "renderPlanXplane();" in _function_body(app_js, "renderPlanSettings")
    body = _function_body(app_js, "estimate")
    assert 'showPlanError(null, refusal, "estimate");' in body and "xplaneRefused(refusal);" in body
    body = _function_body(app_js, "build")
    assert "showPlanError(null, err);" in body and "xplaneRefused(err);" in body
    for name in ("estimate", "build"):
        assert "errorMessage(" not in _function_body(app_js, name), name
    assert "errorCard(" in _function_body(app_js, "renderPlanError")
    assert "renderPlanError();" in _function_body(app_js, "rerenderAll")
    assert "onclick: () => openSettingsFor(err)" in _function_body(app_js, "errorCard")
    assert 'focusSettingsField("q-xplane-dir");' in _function_body(app_js, "chooseXplaneFolder")
    assert "$(id)" in _function_body(app_js, "focusSettingsField")
    save = _function_body(app_js, "saveSettings")
    order = ("await loadStatus();", "showPlanError(null);", 'renderSettings(t("settings.saved"));')
    assert [save.index(line) for line in order] == sorted(save.index(line) for line in order)
    # "empty = the detected folder" is no hint when none was detected
    questions = (UI / "settings.js").read_text(encoding="utf-8")
    assert 'placeholder: found ? t("settings.q.xplane_placeholder") : ""' in questions

    script = """
    const before = (await call("GET", "/api/status")).ok;
    const saved = (await call("GET", "/api/settings")).ok;
    const plan = {tiles: ["+43+005"], provider: "BI", zoom_level: 16, zones: []};
    const refused = await call("POST", "/api/plan", plan);
    const job = await call("POST", "/api/jobs", {...plan, install: true});
    const detail = await m.mockApi("POST", "/api/plan", plan).catch((err) => err.detail.error);
    await call("PUT", "/api/settings", {...saved, essential: {...saved.essential,
                                         xplane_dir: "C:\\\\X-Plane 12"}});
    const after = (await call("GET", "/api/status")).ok;
    const estimated = await call("POST", "/api/plan", plan);
    process.stdout.write(JSON.stringify({before: before.xplane, doctor: before.doctor,
      dir: saved.essential.xplane_dir, refused, job, detail, after: after.xplane,
      estimated: Boolean(estimated.ok)}));
    """
    mocked = _node_mock(script, fail="no-xplane", speed=100)
    assert mocked["before"] == {"path": None, "detected": False, "running": False}
    assert mocked["dir"] is None
    (check,) = [c for c in mocked["doctor"] if c["name"] == "xplane"]
    assert check["status"] == "warn" and check["summary"].startswith("X-Plane 12 not found")
    assert mocked["refused"] == mocked["job"] == {"status": 422, "code": "XP_DIR_NOT_FOUND"}
    assert mocked["detail"]["action"] == "settings" and mocked["detail"]["remedy"]
    assert mocked["after"] == {"path": "C:\\X-Plane 12", "detected": True, "running": False}
    assert mocked["estimated"] is True


def test_the_data_folder_is_asked_beside_x_plane_and_its_refusals_are_explained() -> None:
    """The tiles and the downloaded imagery on an external disk (a user asked, 2026-09-15): Settings
    ask for the folder under X-Plane's, with the system's dialog; the status bar and the Library say
    when its disk is unplugged; the engine's refusals come in the page's words with the folder, the
    reason and the tiles they name, and a button to where the fix is."""
    from orthostudio.home import _DATA_DIR_REFUSALS

    questions = (UI / "settings.js").read_text(encoding="utf-8")
    render = _function_body(questions, "renderQuestions")
    xplane = render.index('questionBox(view, "xplane"')
    assert xplane < render.index("box.append(dataQuestion(view));")
    assert render.index("box.append(dataQuestion(view));") < render.index('"overlays"')
    body = _function_body(questions, "dataQuestion")
    assert 'id: "q-data-dir"' in body and "view.chooseFolder(" in body
    assert 'setPath(d, "essential.data_dir", path);' in body
    assert 't("settings.q.data_kept")' in body and "dataFormats(view.platform)" in body
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "dataDir: state.status?.data_dir || null," in _function_body(app_js, "renderSettings")
    assert 'focusSettingsField("q-data-dir")' in _function_body(app_js, "openSettingsFor")
    assert 'pill(t("status.data_missing"), "fail")' in _function_body(app_js, "renderStatus")
    assert 't("disk.data_missing"' in _function_body(app_js, "renderDisk")
    assert "revealPath(dataFolderShown(state.status))" in app_js
    # "Default values" is about the look of the tiles: the folders of the computer stay
    assert "defaultsKeepingFolders(state.schema, state.settingsDraft)" in app_js
    schema = _mock_json("settings_schema")
    draft = {"essential": {"xplane_dir": "D:\\X-Plane 12", "data_dir": "E:\\OrthoStudio"}}
    back = _node_json("settings.js", f"m.defaultsKeepingFolders({json.dumps(schema)}, "
                                     f"{json.dumps(draft)})")  # fmt: skip
    assert back["essential"]["xplane_dir"] == "D:\\X-Plane 12"
    assert back["essential"]["data_dir"] == "E:\\OrthoStudio"
    assert back["essential"]["zoom_level"] == 16 and back["essential"]["provider"] == "BI"

    whys = json.dumps(sorted(_DATA_DIR_REFUSALS))
    script = f"""
    globalThis.document = {{ documentElement: {{}}, getElementById: () => null }};
    const i = await import("./i18n.js");
    const s = await import("./settings.js");
    const a = await import("./app.js");
    const t7 = "/Volumes/T7/OrthoStudio";
    const out = {{}};
    for (const lang of ["fr", "en"]) {{
      i.setLanguage(lang);
      out[lang] = {{
        home: s.dataFolderLine({{ path: "/Users/pilot/.orthostudio", chosen: false }}),
        chosen: s.dataFolderLine({{ path: t7, chosen: true, present: true }}),
        away: s.dataFolderLine({{ path: t7, chosen: true, present: false }}),
        unknown: s.dataFolderLine(null),
        formats: ["mac", "win", "lin", null].map((p) => s.dataFormats(p)),
        why: Object.fromEntries({whys}.map((why) => [why,
          i.codeText("CFG_DATA_DIR_INVALID", {{ path: "/Volumes/T7", reason: "r", why }})])),
        missing: i.codeText("CFG_DATA_DIR_MISSING", {{ path: "/Volumes/T7" }}),
        kept: i.t("settings.q.data_kept"),
      }};
    }}
    const disk = (present) => ({{ path: "/Volumes/T7", chosen: true, present }});
    out.shown = [
      a.dataFolderShown({{ home: "/h", data_dir: disk(true) }}),
      a.dataFolderShown({{ home: "/h", data_dir: disk(false) }}),
      a.dataFolderShown({{ home: "/h" }}),
      a.dataFolderShown(null),
    ];
    process.stdout.write(JSON.stringify(out));
    """
    got = _run_node(script)
    assert got["shown"] == ["/Volumes/T7", None, "/h", None]
    assert got["fr"] != got["en"]
    for lang in ("fr", "en"):
        words = got[lang]
        assert "/Users/pilot/.orthostudio" in words["home"]["text"] and not words["home"]["warn"]
        assert "/Volumes/T7/OrthoStudio" in words["chosen"]["text"] and not words["chosen"]["warn"]
        assert "/Volumes/T7/OrthoStudio" in words["away"]["text"] and words["away"]["warn"]
        assert words["unknown"] is None
        mac, win, lin, unknown = words["formats"]
        assert "APFS" in mac and win == "NTFS" and "ext4" in lin and unknown == mac
        pairs = [*words["why"].values(), words["missing"]]
        for message, remedy in pairs:
            assert message and remedy and "{" not in message + remedy, (lang, message, remedy)
        assert all("/Volumes/T7" in message for message, _ in [*words["why"].values()])
        assert "exFAT" in words["why"]["links"][0] and "/Volumes/T7" in words["missing"][0]
        # nothing to delete first: the tiles of the folder before keep working (user, 2026-09-15)
        assert "X-Plane" in words["kept"] and "Library" not in words["kept"]
        assert "Bibliothèque" not in words["kept"]

    script = """
    const plan = {tiles: ["+43+005"], provider: "BI", zoom_level: 16, zones: []};
    const status = (await call("GET", "/api/status")).ok;
    const saved = (await call("GET", "/api/settings")).ok;
    const refused = await call("POST", "/api/plan", plan);
    const job = await call("POST", "/api/jobs", {...plan, install: true});
    const exfat = await m.mockApi("PUT", "/api/settings", {...saved, essential: {...saved.essential,
      data_dir: "/Volumes/EXFAT/OrthoStudio"}}).catch((err) => ({status: err.status,
      ...err.detail.error}));
    await call("PUT", "/api/settings", {...saved, essential: {...saved.essential,
      data_dir: "/Volumes/T7/OrthoStudio"}});
    const after = (await call("GET", "/api/status")).ok;
    const estimated = await call("POST", "/api/plan", plan);
    process.stdout.write(JSON.stringify({status: status.data_dir, dir: saved.essential.data_dir,
      refused, job, exfat, after: after.data_dir, estimated: Boolean(estimated.ok)}));
    """
    mocked = _node_mock(script, fail="no-data-disk", speed=100)
    away = {"path": "/Volumes/SSD/OrthoStudio", "chosen": True, "present": False}
    assert mocked["status"] == away and mocked["dir"] == away["path"]
    assert mocked["refused"] == mocked["job"] == {"status": 422, "code": "CFG_DATA_DIR_MISSING"}
    exfat = mocked["exfat"]
    assert exfat["status"] == 422 and exfat["code"] == "CFG_DATA_DIR_INVALID"
    assert exfat["context"]["why"] == "links"
    assert mocked["after"] == {"path": "/Volumes/T7/OrthoStudio", "chosen": True, "present": True}
    assert mocked["estimated"] is True
    plain = _node_mock(
        'process.stdout.write(JSON.stringify((await call("GET", "/api/status")).ok))'
    )
    assert plain["data_dir"] == {"path": plain["home"], "chosen": False, "present": True}


def test_the_status_bar_counts_the_tiles_as_soon_as_the_library_changes() -> None:
    """A user saw "0 tile(s) in the library" stay in the status bar after builds (2026-09-15): the
    count was read with the status only, which a build ending did not read again. Every read of the
    library now counts its tiles there, and a build that ends reads the status again for the
    sizes of the store and of the downloaded images."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    load = _function_body(app_js, "loadLibrary")
    count = "state.status.library_count = new Set(libraryTiles(state.library).map((e) => e.tile))"
    assert count in load and load.index(count) < load.index("renderStatus();")
    watch = _function_body(app_js, "watchJob")
    finished = watch[watch.index('if (event === "finished") {') :]
    assert (
        finished.index("loadLibrary();")
        < finished.index("loadStatus();")
        < finished.index("} else if")
    )


def test_settings_keep_their_scroll_position_when_an_answer_changes() -> None:
    """A user scrolled to the bottom of Settings: every answer changed brought the top of the
    screen back. The screen is drawn again on each answer, and removing the answer just clicked,
    which has the focus, made the browser lay the page out while it was half drawn."""
    code = (UI / "settings.js").read_text(encoding="utf-8")
    body = _function_body(code, "renderSettingsView")
    assert body.index("keptScroll()") < body.index("renderPresets(")
    assert body.index("restoreFocus(") < body.index("scroll?.restore();")
    got = _node_json(
        "settings.js",
        """(() => {
          const calls = [];
          globalThis.window = {scrollX: 0, scrollY: 640, scrollTo: (x, y) => calls.push([x, y])};
          const kept = m.keptScroll();
          window.scrollY = 137;  // the page, half drawn, scrolled up
          kept.restore();
          const same = m.keptScroll();
          same.restore();  // nothing moved: no scroll
          delete globalThis.window;
          return {calls, none: m.keptScroll()};
        })()""",
    )
    assert got == {"calls": [[0, 640]], "none": None}


def test_the_plan_map_shows_what_the_running_build_does() -> None:
    """A user building tiles lost the blue of the selection (it empties on purpose when a build
    starts), saw no green appear as tiles went into X-Plane, and asked to see on the map what is
    being built. The map draws the running build's tiles: the one worked on pulses, a waiting one
    is dashed, a failed one dashed red; the Library is read again as each tile is installed and
    when the build ends; a page opened during a build follows it."""
    got = _node_json(
        "app.js",
        """(() => {
          const step = (status, n = 1) => ({status, nodes: Object.fromEntries(
            Array.from({length: n}, (_, i) => [`n${i}`, {}]))});
          const tile = (name, status, steps) => ({tile: name, status, steps});
          const job = {status: "running", tiles: [
            tile("+46+006", "running", {data: step("done"), terrain: step("running"),
              install: step("pending", 0)}),
            tile("+46+007", "running", {data: step("waiting"), terrain: step("pending")}),
            tile("+46+008", "pending", {data: step("pending")}),
            tile("+46+009", "failed", {data: step("failed")}),
            tile("+46+010", "done", {data: step("done")}),
            tile("+47+006", "running", {data: step("done"), imagery: step("hit"),
              install: step("pending", 0)}),
          ]};
          return [[...m.buildingTiles(job)], [...m.buildingTiles({...job, status: "done"})],
                  [...m.buildingTiles(null)]];
        })()""",
    )
    assert got[0] == [
        ["+46+006", "working"],
        ["+46+007", "queued"],
        ["+46+008", "queued"],
        ["+46+009", "failed"],
    ]
    assert got[1] == [] and got[2] == []

    map_js = (UI / "map.js").read_text(encoding="utf-8")
    found = re.search(r"\n  function renderGrid\(\) \{.*?\n  \}\n", map_js, re.S)
    assert found is not None
    grid = found.group(0)
    assert "osxp-tile-${building.get(name)}" in grid
    assert grid.index("osxp-tile-installed") < grid.index("osxp-tile-${building.get(name)}")
    assert "JSON.stringify([...buildingNow().entries()])" in map_js  # redrawn on change only
    css = (UI / "styles.css").read_text(encoding="utf-8")
    # marks are outlines, never fills (2026-09-18): the tile worked on pulses on its stroke
    assert re.search(r"\.osxp-tile-working \{[^}]*fill: none;[^}]*animation: tile-pulse", css)
    assert re.search(r"@keyframes tile-pulse \{[^}]*stroke-opacity", css)
    assert ".osxp-tile-queued {" in css and "stroke-dasharray" in css.split(".osxp-tile-queued")[1]
    assert ".osxp-tile-failed {" in css and "@keyframes tile-pulse" in css
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "planMap?.jobChanged();" in _function_body(app_js, "renderJob")
    watch = _function_body(app_js, "watchJob")
    assert "loadLibrary();" in watch and 'data?.role === "install") loadLibrarySoon();' in watch
    assert "building: () => buildingOnMap()," in app_js
    assert "const out = buildingTiles(state.job);" in _function_body(app_js, "buildingOnMap")
    boot = _function_body(app_js, "boot")
    assert "watchJob(state.status.active_job)" in boot


def test_settings_name_the_author_and_the_licence() -> None:
    """The notices GPL v3 asks an interactive program to show (its Appropriate Legal Notices): who
    holds the copyright, the licence, and that there is no warranty, which a reuse of the page must
    keep showing (the user asked, 2026-09-22; NOTICE says the same)."""
    words = _node_json(
        "i18n.js",
        '(globalThis.document = {documentElement: {}}, ["fr", "en"].map((lang) => '
        '(m.setLanguage(lang), m.t("settings.credits"))))',
    )
    for text in words:
        assert text.startswith("OrthoStudio XP © 2026 radioharris"), text
        assert "GPL v3" in text and "LICENSE" in text and "OpenStreetMap" in text
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert 'data-i18n="settings.credits">OrthoStudio XP © 2026 radioharris' in html


def test_the_data_folder_can_be_found_and_its_refusal_read() -> None:
    """A French tester (2026-09-22) could not find his tiles in the hidden `~/.orthostudio`, and
    tried an external disk "without success": the refusal of an exFAT disk showed at the foot of
    the form, under every question, and began "Settings rejected by the engine". The status bar
    now shows the folder in the file manager, and the refusal says why in the Save bar, with where
    a disk's format is read."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    status = _function_body(app_js, "renderStatus")
    assert "const shown = dataFolderShown(s);" in status and "revealPath(shown)" in status
    assert "status-reveal" in status and "revealLabel(s.platform)" in status
    rules = dict(_css_rules((UI / "styles.css").read_text(encoding="utf-8")))
    assert "height: 18px;" in rules[".status-home .status-reveal"]  # the bar keeps its height
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    actions = html[html.index('<div class="actions settings-actions">') :]
    assert actions.index('id="settings-error"') < actions.index("</div>")  # in the Save bar
    assert "flex-basis: 100%;" in rules[".settings-actions .error-inline"]
    words = _node_json(
        "i18n.js",
        '(globalThis.document = {documentElement: {}}, ["fr", "en"].map((lang) => '
        '(m.setLanguage(lang), [m.t("settings.invalid", {detail: "x"}), '
        'm.codeText("CFG_DATA_DIR_INVALID", {path: "/Volumes/T7", why: "links"})])))',
    )
    (fr_saved, fr_links), (en_saved, en_links) = words
    assert fr_saved == "Non enregistré : x" and en_saved == "Not saved: x"
    assert "Utilitaire de disque" in fr_links[1] and "Disk Utility" in en_links[1]


def test_a_missing_x_plane_region_is_said_plainly() -> None:
    """Building Hawaii, a user read "the Global DSF was missing" and, from the engine, command-line
    options (2026-09-22): the region was not installed in his X-Plane 12. The page names the square
    and says what to do; the estimate of step 3 already stops on it, before anything downloads."""
    words = _node_json(
        "i18n.js",
        '(globalThis.document = {documentElement: {}}, ["fr", "en"].map((lang) => '
        '(m.setLanguage(lang), m.codeText("DSF_GLOBAL_SCENERY_MISSING", {tile: "+19-156"}))))',
    )
    for message, remedy in words:
        assert "+19-156" in message and "DSF" not in message
        assert "X-Plane" in remedy and "--" not in remedy
    assert "installeur" in words[0][1] and "installer" in words[1][1]


def test_a_table_wider_than_its_box_keeps_its_scrollbar() -> None:
    """Where a scrollbar takes room, WebKit added a table's after placing what follows it: the line
    under step 3's per-tile table overlapped the table until the page moved (a user, 2026-09-22).
    A table that is wider keeps its scrollbar from the start, checked whenever tables are drawn."""
    if NODE is None:
        pytest.skip("node is not installed")
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    helper = _function_body(app_js, "markWideTables")
    run = """
      const made = [];
      function wrap(scrollWidth, clientWidth) {
        const classes = new Set(["table-wrap"]);
        const toggle = (name, on) => (on ? classes.add(name) : classes.delete(name));
        made.push(classes);
        return { scrollWidth, clientWidth, classList: { toggle } };
      }
      // wider than its box, as wide, and hidden (a screen not shown measures nothing)
      const document = { querySelectorAll: () => [wrap(535, 378), wrap(378, 378), wrap(0, 0)] };
    """
    end = "markWideTables(); process.stdout.write(JSON.stringify(made.map((c) => [...c])));"
    got = _run_node(run + helper + end)
    assert got == [["table-wrap", "is-wide"], ["table-wrap"], ["table-wrap"]]
    rules = dict(_css_rules((UI / "styles.css").read_text(encoding="utf-8")))
    assert "overflow-x: scroll;" in rules[".table-wrap.is-wide"]
    assert "overflow-x: auto;" in rules[".table-wrap"]
    # wherever tables are drawn, and when the window's size changes
    for name in ("showScreen", "renderPlanPanel"):
        assert "markWideTables();" in _function_body(app_js, name), name
    assert _function_body(app_js, "renderLibrary").count("markWideTables();") == 2
    assert 'window.addEventListener("resize", markWideTables);' in _function_body(app_js, "boot")


def test_a_chosen_tile_a_build_would_change_is_marked_at_a_glance() -> None:
    """Under the chips, what a build would change is a warning line; a user asked it to line up
    and stand apart from the tile's line, and the tile's chip to take its colour (2026-09-22)."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "dataset: { tile: name }" in _function_body(app_js, "renderTiles")
    built = _function_body(app_js, "renderTilesBuilt")
    assert 'chip.classList.toggle("is-warn", Boolean(change));' in built
    assert "if (change) chip.title = change;" in built
    # the text in an element of its own: its second line goes under its first, not under the mark
    assert 'h("p", { class: "tiles-built-warn" }, h("span", null, change))' in built
    rules = dict(_css_rules((UI / "styles.css").read_text(encoding="utf-8")))
    warn = rules[".tiles-built-warn"]
    assert "display: flex;" in warn and "padding-left" not in warn
    assert "gap: var(--help-gap);" in rules[".tiles-built-item"]
    chip = rules[".chip.is-warn"]
    assert "var(--warn)" in chip and "var(--warn-soft)" in chip


def test_the_screen_shows_before_the_engine_answers() -> None:
    """Users on Windows saw the menu alone until they clicked it: the page waited for every answer
    of the engine before showing a screen, and one of them was slow (2026-09-22). The screen now
    shows first and fills in: the top bar, the Plan's source and Settings do not wait for the
    status, the map waits for the library alone, and the sizes come on their own."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    boot = _function_body(app_js, "boot")
    wait = boot.index("await Promise.allSettled([")
    assert boot.index("routeFromHash();") < wait < boot.rindex("routeFromHash();")
    first = boot.index("const early = Promise.allSettled([providers, settings, schema])")
    early = boot[first:wait]
    assert 'renderProviders(state.settings?.essential?.provider || "BI");' in early
    assert 'if (state.screen === "settings") renderSettings();' in early
    # the top bar from the engine's quick answer, not from the status
    assert 'api("GET", "/api/engine").then(renderEngine, () => {});' in boot[:wait]
    engine = _function_body(app_js, "renderEngine")
    assert "if (!e || state.status) return;" in engine and "renderEngineBanner();" in engine
    # the map waits for the library, to open on the tiles installed rather than on Europe first
    library = boot[boot.index('api("GET", "/api/library")') :]
    finished = library[library.index(".finally(() => {") :]
    assert "state.libraryKnown = true;" in finished
    assert 'if (state.screen === "plan") planMap.show();' in finished
    assert "if (state.libraryKnown) planMap?.show();" in _function_body(app_js, "showScreen")
    status = _function_body(app_js, "loadStatus")
    assert "  loadSizes();" in status  # not awaited
    sizes = _function_body(app_js, "loadSizes")
    assert 'api("GET", "/api/sizes")' in sizes and "renderDiskSizes();" in sizes
    assert "renderStatus()" not in sizes  # the checks' open fold stays open
    assert "renderDiskSizes();" in _function_body(app_js, "renderStatus")
    assert 'if (p === "/api/sizes")' in app_js and 'if (p === "/api/engine")' in app_js  # mock
    # until the status comes, X-Plane is looked for, not "not detected"
    settings_js = (UI / "settings.js").read_text(encoding="utf-8")
    assert 'let detected = t("settings.q.xplane_looking");' in settings_js
    assert 'else if (xp) detected = t("settings.q.xplane_not_detected");' in settings_js


def test_a_chosen_installed_tile_shows_both_outlines() -> None:
    """A tile both installed and chosen keeps its green outline and shows its blue one 4px inside
    it, each over a wider line of ``--map-casing`` that parts them from each other and from the
    photo: on the same line the green hid the blue, and a thin blue beside the green hardly showed
    (a user chose this drawing, 2026-09-22). Too small on the screen for both, under 16px, the
    tile shows the green: zoomed out on the world, the map shows which tiles are installed (the
    same user). The outlines are drawn on whole pixels: blended over two, the parting line faded at
    the zooms where Leaflet leaves the layer between two pixels."""
    if NODE is None:
        pytest.skip("node is not installed")
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    found = re.search(r"\n  function insetBox\(box, px\) \{.*?\n  \}\n", map_js, re.S)
    assert found is not None
    # Maps of 100, 16 and 15 pixels a degree: the inset is counted in pixels, whatever the zoom,
    # and a tile of 16px still holds both outlines (zoom 5, where tiles are chosen, gives 22px).
    fake_map = """
      const L = { point: (x, y) => ({ x, y }) };
      let scale = 100;
      const map = {
        latLngToLayerPoint: ([lat, lng]) => ({ x: lng * scale, y: -lat * scale }),
        layerPointToLatLng: (p) => ({ lat: -p.y / scale, lng: p.x / scale }),
      };
    """
    run = """
      const got = [100, 16, 15].map((s) => { scale = s; return insetBox([[43, 5], [44, 6]], 4); });
      process.stdout.write(JSON.stringify(got));
    """
    got = _run_node(fake_map + found.group(0) + run)
    assert got[0] == [
        [pytest.approx(43.04), pytest.approx(5.04)],
        [pytest.approx(43.96), pytest.approx(5.96)],
    ]
    assert got[1] == [
        [pytest.approx(43.25), pytest.approx(5.25)],
        [pytest.approx(43.75), pytest.approx(5.75)],
    ]
    assert got[2] is None

    grid = re.search(r"\n  function renderGrid\(\) \{.*?\n  \}\n", map_js, re.S)
    assert grid is not None
    body = grid.group(0)
    assert "installed.has(name) && selected.has(name) ? insetBox(box, 4) : null;" in body
    # the casings under both lines, then the green on the edge, then the blue inside it
    casing = body.index('className: "osxp-tile-casing"')
    green = body.index("className: `osxp-tile-installed${both}`")
    # the blue after the green, the route's ends in their own colour (2026-09-22)
    assert casing < green < body.index("className: `osxp-tile-selected${both}${end}`")
    assert 'const both = inner ? " is-both" : "";' in body
    assert "for (const b of [box, inner])" in body
    assert "L.rectangle(inner || box," in body
    # too small for both, the blue is left out and the green shows
    assert "if (selected.has(name) && (inner || !installed.has(name))) {" in body
    rules = dict(_css_rules((UI / "styles.css").read_text(encoding="utf-8")))
    assert "stroke-width: 3;" in rules[".plan-map .osxp-tile-installed"]
    assert "stroke-width: 3;" in rules[".plan-map .osxp-tile-selected"]
    # 5px under lines of 2.5px, 4px apart: a line of 1 to 1.5px each side of each of them, which
    # whole pixels keep at two pixels of a Retina screen wherever the layer lies
    casing_rule = rules[".plan-map .osxp-tile-casing"]
    assert "stroke: var(--map-casing);" in casing_rule and "stroke-width: 5;" in casing_rule
    both_rule = ".plan-map .osxp-tile-installed.is-both, .plan-map .osxp-tile-selected.is-both"
    assert "stroke-width: 2.5;" in rules[both_rule]
    marks = ("selected", "installed", "casing")
    crisp = ", ".join(f".plan-map .osxp-tile-{mark}" for mark in marks)
    assert "shape-rendering: crispEdges;" in rules[crisp]
    for theme in (":root", ':root:not([data-theme="light"])', ':root[data-theme="dark"]'):
        assert "--map-casing:" in rules[theme], theme


def test_a_waiting_imagery_says_what_it_waits_for() -> None:
    """Both tiles were done up to Coast, and the first Imagery said "pending" for a long while on
    Windows: it waited for its DSF, shown under Assembly, and the second waits for the first's
    images (one tile downloads at a time). A user, 2026-09-15."""
    running_dsf = {
        "assembly": {
            "status": "running",
            "nodes": {"+46+006/BI16/dsf": {"role": "dsf", "status": "running"}},
        }
    }
    first = {
        "tile": "+46+006",
        "steps": {"imagery": {"status": "pending", "nodes": {}}, **running_dsf},
    }
    job = {"status": "running", "install": True, "tiles": [first]}
    done_dsf = {
        "assembly": {
            "status": "running",
            "nodes": {"+46+007/BI16/dsf": {"role": "dsf", "status": "done"}},
        }
    }
    second = {
        "tile": "+46+007",
        "steps": {"imagery": {"status": "pending", "nodes": {}}, **done_dsf},
    }
    downloading = {
        "tile": "+46+006",
        "steps": {"imagery": {"status": "running", "fraction": 0.2, "nodes": {}}},
    }
    job2 = {"status": "running", "install": True, "tiles": [downloading, second]}
    alone = {"tile": "+46+008", "steps": {"imagery": {"status": "pending", "nodes": {}}}}
    got = _node_json(
        "app.js",
        f"[m.stepView({json.dumps(job)}, {json.dumps(first)}, 'imagery').text, "
        f"m.stepView({json.dumps(job2)}, {json.dumps(second)}, 'imagery').text, "
        f"m.stepView({json.dumps(job)}, {json.dumps(alone)}, 'imagery').text, "
        f"m.stepView({json.dumps(job)}, {json.dumps(first)}, 'assembly').text]",
    )
    assert got[0] == "waits for the DSF (Assembly)"
    assert got[1] == "waits for the images of +46+006"
    assert got[2] == "pending" and got[3] != got[0]


def test_two_squares_with_the_same_answer_are_not_called_different() -> None:
    """The Plan said "the chosen squares differ" for squares that did not (a user, 2026-09-18).

    Two choices are the same answer when their key is: the key of a named look is its name, since
    its numbers are never read, and the order of an object's keys never matters.
    """
    calls = ", ".join(
        [
            "m.photoKey(null)",
            "m.photoKey({look: null, brightness: 0, contrast: 0, saturation: 0})",
            'm.photoKey({look: "softer", brightness: 0, contrast: 0, saturation: 0})',
            'm.photoKey({saturation: -0.4, look: "softer", contrast: 0.2, brightness: 0})',
            'm.photoKey({look: "custom", brightness: 0, contrast: 0, saturation: -0.4})',
            'm.photoKey({saturation: -0.4, contrast: 0, brightness: 0, look: "custom"})',
            'm.photoKey({look: "custom", brightness: 0, contrast: 0, saturation: -0.35})',
        ]
    )
    empty, none, named, named_other_order, custom, custom_other_order, custom_other = _node_json(
        "geo.js", f"[{calls}]"
    )
    assert empty == none == ""  # nothing set: the level above applies
    assert named == named_other_order  # a named look ignores the numbers beside it
    assert custom == custom_other_order  # and the key order never matters
    assert custom != custom_other and custom != named


def test_a_folder_under_the_home_is_written_with_a_tilde() -> None:
    """The page showed "/Users/hap/X-Plane 12" in the status bar, in the Library and in Settings.

    It is longer to read than it needs to be, and it carries the name of whoever runs OrthoStudio
    XP into every picture they post with a report (2026-09-19). What the engine is asked to open or
    to save is still the path itself: only what is read is shortened.
    """
    calls = ", ".join(
        [
            'm.homely("/Users/pilot/X-Plane 12")',  # before the status says, nothing is assumed
            '(m.setUserHome("/Users/pilot"), m.homely("/Users/pilot/X-Plane 12"))',
            'm.homely("/Users/pilot")',
            'm.homely("/Users/pilotage/X-Plane 12")',  # the home is a folder, not a prefix
            'm.homely("/Volumes/Scenery/tiles")',
            '(m.setUserHome("C:\\\\Users\\\\pilot"),'
            ' m.homely("C:\\\\Users\\\\pilot\\\\X-Plane 12"))',
        ]
    )
    unknown, under, itself, alike, elsewhere, windows = _node_json("i18n.js", f"[{calls}]")
    assert unknown == "/Users/pilot/X-Plane 12"
    assert under == "~/X-Plane 12" and itself == "~"
    assert alike == "/Users/pilotage/X-Plane 12" and elsewhere == "/Volumes/Scenery/tiles"
    assert windows == "~\\X-Plane 12"


def test_the_legend_says_when_this_machine_has_no_airports() -> None:
    """A user ticked Airports on a machine without X-Plane's apt.dat, nothing came, and he had to
    ask why (2026-09-20). The request answers 503 there; the legend now says so instead of
    leaving the box ticked over an empty map."""
    js = (UI / "map.js").read_text(encoding="utf-8")
    call = js[js.index("icao_only=true") :]  # the request itself, not the comment above it
    fail = call[call.index(".catch(") : call.index(".finally(")]
    assert "missing = true" in fail and "renderLegend()" in fail
    # and it stops saying it the moment airports do come back
    ok = call[call.index(".then(") : call.index(".catch(")]
    assert "missing = false" in ok
    for key in ("map.airports_missing", "map.airports_missing_hint"):
        assert f'"{key}"' in js, key


def test_a_browser_only_system_is_told_at_the_top_of_the_page() -> None:
    """The doctor's window check said it at the foot of the page, behind a fold nobody opens (a
    user asked, 2026-09-20). It is said once at the top too, in the reader's own words, and stays
    away once put away."""
    js = (UI / "app.js").read_text(encoding="utf-8")
    note = js[js.index("function renderWindowNote") : js.index("function renderEngineBanner")]
    assert "browser_only" in note and "details.install" in note  # what the doctor found
    assert "WINDOW_NOTE_KEY" in note and "localStorage.setItem" in note  # put away, it stays away
    assert "app.window_browser_only" in note
    # put away it must still be findable, so it says where it stays before it goes
    assert "app.window_browser_only_again" in note
    # the banner of problems still has its say: this only shows when there is nothing else
    assert "renderWindowNote();" in js[js.index("function renderEngineBanner") :]
    css = (UI / "styles.css").read_text(encoding="utf-8")
    assert ".window-note {" in css and "--warn" not in css[css.index(".window-note {") :][:200]


def test_a_check_that_failed_is_said_at_the_top_of_the_page() -> None:
    """Triangle4XP missing makes every build impossible, and a red pill at the foot of the page
    was the only sign of it (a user asked, 2026-09-20). What stops you belongs where you are."""
    js = (UI / "app.js").read_text(encoding="utf-8")
    banner = js[js.index("function renderEngineBanner") :]
    banner = banner[: banner.index("\nfunction ", 10)]
    assert "failedChecks()" in banner and "app.checks_failed" in banner
    # loud, like the other things that stop you: the same banner, not the quiet note
    assert "window-note" not in banner
    # and it gives the way to what failed, rather than leaving the user to find the foot
    assert "app.checks_failed_show" in banner and "showChecks" in banner
    # it yields to what is more urgent: an engine that cannot answer says so first
    assert banner.index("app.engine_error") < banner.index("app.checks_failed")


def test_the_map_leaves_room_for_the_status_bar_under_it() -> None:
    """Sized as "100vh - 120px" since v0.1.7, the map ended one pixel past the foot of the window
    and 29px under the status bar, on any screen short enough for the 820px cap not to save it:
    the bottom of the legend was cut, in the browser exactly as in the window (measured at a
    browser, a user, 2026-09-20)."""
    css = (UI / "styles.css").read_text(encoding="utf-8")
    rule = css[css.index(".plan-map {") : css.index("@media (max-width: 960px) { .plan-map")]
    assert "var(--statusbar-h)" in rule  # the bar at the foot has its room
    assert "var(--map-top" in rule  # and so has whatever stands above the map
    assert "calc(100vh - 120px)" not in css  # the formula that counted neither

    js = (UI / "app.js").read_text(encoding="utf-8")

    def body(name: str) -> str:
        start = js.index(f"function {name}")
        return js[start : js.index("\nfunction ", start + 1)]

    measure = body("measureMapTop")
    assert ".plan-flow" in measure  # not the map: .map-col sticks to the top as the page scrolls
    assert "offsetParent === null" in measure  # a hidden screen measures zero and is not written
    # called where the page changes above the map, rather than watched: a ResizeObserver on a
    # screen still hidden never reported it being shown (measured, 2026-09-20)
    assert "measureMapTop();" in body("showScreen")
    assert "measureMapTop();" in body("renderEngineBanner")


def test_the_final_report_folds_before_it_runs_out_of_room() -> None:
    """Its three columns cannot go below 740px: 170 + 330 + 200, and two 20px gutters. Beside the
    260px job list, inside the report's own border and the page's padding, that asks for 1070px of
    window, and it folded only under 920: from 920 to 1070 it pushed the page wider than the
    window, and the decisions were cut off at the right (a user, at the smallest window the app
    allows, 2026-09-20).

    The room it needs is read back from the stylesheet rather than written down here, so that
    narrowing a column or widening the job list moves the fold with it."""
    css = (UI / "styles.css").read_text(encoding="utf-8")
    grid = css[css.index(".report-grid {") :][:220]
    columns = [int(px) for px in re.findall(r"minmax\((\d+)px", grid)]
    gap = int(re.search(r"gap: \d+px (\d+)px", grid).group(1))
    assert len(columns) == 3, columns
    beside = int(re.search(r"\.cols-works \{ grid-template-columns: (\d+)px", css).group(1))
    between = int(re.search(r"^\.cols \{[^}]*gap: (\d+)px", css, re.MULTILINE).group(1))
    page = int(re.search(r"--pad: (\d+)px", css).group(1))
    side = int(re.search(r"--card-pad: \d+px (\d+)px", css).group(1))
    report = side * 2 + 2  # .report: its padding either side, and its border

    needs = sum(columns) + gap * 2 + report + beside + between + page * 2
    folds_at = int(re.search(r"@media \(max-width: (\d+)px\) \{ \.report-grid", css).group(1))
    assert folds_at >= needs, (
        f"three columns need {needs}px of window, folded only under {folds_at}"
    )


def test_the_three_tools_at_the_top_right_are_one_height() -> None:
    """Quit took its height from .btn-small, which sets a minimum and not a height: 24px against
    the 26px of the language select and the theme button beside it, so it stood 1px inside them.
    Invisible on macOS, plain on Windows. Giving all three `height: 26px` was not enough there:
    the rule sat above `.btn`, which Quit also carries and which sets a min-height of its own, so
    a single class lost on source order; and a `<select>` is drawn by the engine, which gives it
    metrics of its own. The rule is scoped to the bar, so it wins, and holds the height on both
    sides (a user, twice, 2026-09-20)."""
    css = (UI / "styles.css").read_text(encoding="utf-8")
    start = css.index(".topbar-tools .tool-select")
    rule = css[start : css.index("}", start)]
    head = rule[: rule.index("{")]
    assert ".quit-btn" in head, "Quit is sized apart from the tools beside it"
    # two classes deep, so it beats `.btn` whatever the order of the file
    assert head.count(".topbar-tools ") == 3
    for held in ("height: 26px", "min-height: 26px", "max-height: 26px"):
        assert held in rule, held
    # and no line-height of its own: pinning the line box to the font's exact height left Quit's
    # label standing off its own icon on Windows, where the font is not the Mac's (2026-09-21)
    assert "line-height" not in rule
    assert css.index(".btn {") > start  # and the rule it has to beat really does come later
    # .btn-small keeps its minimum for the buttons whose label may wrap; only these three are fixed
    small = css[css.index(".btn-small {") :][: css[css.index(".btn-small {") :].index("}")]
    assert "min-height" in small and "height: " not in small.replace("min-height", "")


def test_a_setting_can_be_found_by_name_or_by_its_ortho4xp_name() -> None:
    """The questions are laid out in two CSS columns, so the tenth of them sits halfway down the
    right-hand one: a user with the page in English looked for "How much of the photo on lakes and
    rivers?", read the page from the top, and never reached it (2026-09-20). The window has no
    Find to jump there either. A search box filters instead of highlighting, which no Find does:
    what does not match leaves the column flow and the rest gathers at the top."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="settings-search"' in html
    assert 'data-i18n-placeholder="settings.search_ph"' in html
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        for key in ("settings.search", "settings.search_ph", "settings.search_found"):
            assert tables[lang][key], f"{lang} is missing {key}"

    code = (UI / "settings.js").read_text(encoding="utf-8")
    # drawn, then filtered, then the focus and the scroll put back: filtering moves the page
    body = _function_body(code, "renderSettingsView")
    assert body.index("renderExperts(") < body.index("applySearch(") < body.index("restoreFocus(")
    filt = _function_body(code, "searchFilter")
    assert ".question" in filt and ".gen-field" in filt  # both the questions and the experts
    assert "expert-group" in filt  # a group with nothing left in it hides its title too
    assert "searchFilter(parts, words)" in _function_body(code, "applySearch")

    got = _node_json(
        "settings.js",
        """(() => {
          const w = m.searchWords;
          const hit = (text, q) => m.matchesSearch(text, w(q));
          return {
            accents: w("Rivières"),
            plural: w("rivers"),
            short: w("gps"),
            words: w("  photo   lakes "),
            singular: hit("Simplify lake and river outlines", "rivers"),
            ortho4xp: hit("Smallest pond drawn as water min_area", "min_area"),
            accented: hit("Quelle part de la photo sur les lacs et les rivières ?", "rivieres"),
            both: hit("How much of the photo on lakes and rivers?", "photo lakes"),
            one_word_missing: hit("How much of the photo on lakes and rivers?", "photo runway"),
            empty: w(""),
          };
        })()""",
    )
    assert got == {
        "accents": ["riviere"],  # accents folded, and the plural s dropped
        "plural": ["river"],
        "short": ["gps"],  # too short to lose its s: "gps" is not "gp"
        "words": ["photo", "lake"],
        "singular": True,
        "ortho4xp": True,
        "accented": True,
        "both": True,
        "one_word_missing": False,  # every word has to be found
        "empty": [],
    }


def test_the_page_finds_its_own_text_because_the_window_has_no_find() -> None:
    """Since 0.1.8 the app opens in a window of its own: neither WKWebView nor WebView2 carries a
    Find, and pywebview's Edit menu is Cut, Copy, Paste and Select All only. A user who reached a
    setting with Cmd+F in the browser found the key dead (2026-09-20). In a browser the key is
    left alone: its own Find does more than this one."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    for el in ("find-bar", "find-input", "find-count", "find-prev", "find-next", "find-close"):
        assert f'id="{el}"' in html
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        for key in ("find.label", "find.count", "find.none", "quit.stopped_text_window"):
            assert tables[lang][key], f"{lang} is missing {key}"
    # the window has no tab to close, and said so until 0.1.8
    assert "tab" not in tables["en"]["quit.stopped_text_window"]
    assert "onglet" not in tables["fr"]["quit.stopped_text_window"]

    code = (UI / "find.js").read_text(encoding="utf-8")
    # nothing is written into the page: app.js draws its screens again and compares what it drew,
    # so matches wrapped in <mark> would be torn out from under the reader. The prose of the
    # module says all this, so only what it runs is read here.
    runs = "\n".join(
        line for line in code.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))
    )
    assert "innerHTML" not in runs and "<mark" not in runs
    assert "CSS.highlights" in code and "new Highlight(" in code
    assert "window.find?.(" in code  # where the highlight API is missing
    assert "::highlight(osxp-find)" in (UI / "styles.css").read_text(encoding="utf-8")

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "window.pywebview" in _function_body(app_js, "inOwnWindow")
    assert "bindFindKeys();" in _function_body(app_js, "boot")
    assert "findForget();" in _function_body(app_js, "showScreen")

    got = _node_json(
        "find.js",
        """(() => {
          const text = (s) => ({nodeType: 3, nodeValue: s, childNodes: []});
          const el = (tag, kids, extra = {}) =>
            ({nodeType: 1, tagName: tag, childNodes: kids, ...extra});
          globalThis.document = {
            createRange: () => ({
              setStart(node, at) { this.startContainer = node; this.from = at; },
              setEnd(node, at) { this.to = at; },
            }),
          };
          const tree = el("DIV", [
            text("The Copernicus relief"),
            el("SCRIPT", [text("copernicus in a script")]),
            el("P", [text("copernicus twice: copernicus")], {hidden: false}),
            el("P", [text("hidden copernicus")], {hidden: true}),
            el("DETAILS", [text("body copernicus")], {open: false, querySelector: () => null}),
          ]);
          const found = m.findRanges(tree, "copernicus");
          return {
            hits: found.length,
            offsets: found.map((r) => [r.from, r.to]),
            words: found.map((r) => r.startContainer.nodeValue),
            nothing: m.findRanges(tree, "").length,
          };
        })()""",
    )
    assert got == {
        "hits": 3,  # the script, the hidden paragraph and the closed details are not read
        "offsets": [[4, 14], [0, 10], [18, 28]],
        "words": ["The Copernicus relief"] + ["copernicus twice: copernicus"] * 2,
        "nothing": 0,
    }


def test_a_build_that_stopped_short_offers_to_be_built_again() -> None:
    """The engine has had POST /api/jobs/{id}/retry all along, and the page called it from one
    place only: a button inside an error card, drawn for the handful of codes that carry the
    action "retry" (network, missing textures, OSM mirror, elevation). A build that failed for
    any other reason, or was stopped, offered nothing at all (a user, 2026-09-20). The button now
    sits in the job's own header, beside Stop, and is left out only where there is nothing to
    redo: a build under way, and one that finished with everything built."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    head = _function_body(app_js, "buildJobView")
    assert "onclick: retryJob" in head and 't("works.again")' in head
    assert "v.again, v.stop" in head  # beside Stop, before it: Stop is the dangerous one
    update = _function_body(app_js, "updateJobView")
    rule = 'v.again.hidden = active || (job.status === "done" && !(job.errors || []).length);'
    assert rule in update
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        assert tables[lang]["works.again"] and tables[lang]["works.again_help"]


def test_the_usgs_relief_at_one_arc_second_is_offered_too() -> None:
    """A user on X-Plane.Org asked for the USGS NED at 1 arc-second, saying it reaches Canada.
    It does: only the 1/3" layer stops at the border (the 1" answers for the United States,
    Canada, Mexico and Alaska, probed 2026-09-20), and the engine has read `NED1` all along.
    The list offered the 1/3" one alone, and the refusal claimed the USGS is the United States."""
    from orthostudio.config import Settings
    from orthostudio.config.overrides import to_build_overrides

    chosen = Settings.model_validate({"essential": {"relief": {"source": "usgs1"}}})
    assert to_build_overrides(chosen)["custom_dem"] == "NED1"

    code = (UI / "settings.js").read_text(encoding="utf-8")
    assert '{ value: "usgs1", label: t("settings.q.relief_usgs1")' in code
    assert 'relief === "usgs1"' in code  # and the Plan says which relief a build will use
    assert 'relief === "usgs1"' in (UI / "app.js").read_text(encoding="utf-8")  # so does Works

    tables = _i18n_tables()
    for lang in ("en", "fr"):
        for key in (
            "settings.q.relief_usgs1",
            "settings.q.relief_usgs1_note",
            "plan.s.relief_usgs1",
            "works.relief_usgs1",
        ):
            assert tables[lang][key], f"{lang} is missing {key}"
    # the note warns what the measurement showed: over Canada it was drawn from contour lines
    assert "contour" in tables["en"]["settings.q.relief_usgs1_note"]
    assert "courbes de niveau" in tables["fr"]["settings.q.relief_usgs1_note"]
    # and the refusal no longer sends a Canadian user away from a layer that covers them
    i18n = (UI / "i18n.js").read_text(encoding="utf-8")
    assert "the USGS covers the United States only" not in i18n
    assert 'c.source === "NED1" ? "North America" : "the United States"' in i18n


def test_settings_are_where_the_next_plan_starts_and_nothing_more() -> None:
    """Settings hold where a plan starts; the Plan on screen is this build's and is never written
    over, in either direction.

    Saving put the settings' source and detail level back into the Plan, so a user who chose Bing
    there, changed only the relief in Settings and saved, built his tile with the source of the
    settings and was never told (2026-09-20). Narrowing that to the settings a save had really
    changed was not enough: changing the source on purpose, for the builds to come, still moved
    the plan already set up on screen (the next day).

    And the traffic went the other way too: starting a build wrote the Plan's source and level
    into the settings, so the "default" followed the last build. It no longer does. The Plan
    takes the settings when the page opens, and that is the whole of it."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    save = _function_body(app_js, "saveSettings")
    assert "renderProviders();" in save  # the list of sources, never the one the Plan shows
    assert '$("zl-select")' not in save
    assert "planChanged();" in save  # the cost is worked out again with what was saved
    # nothing of the three attempts at making the two screens talk is left
    for gone in ("sourceChanged", "planSourceChosen", "persistSettings"):
        assert gone not in app_js, gone
    assert 't("settings.left_unsaved")' in _function_body(app_js, "showScreen")

    # the Plan starts from the settings, and only there
    assert "state.settings?.essential?.provider" in _function_body(app_js, "renderSourceOptions")
    assert "state.settings?.essential?.zoom_level" in _function_body(app_js, "renderZlOptions")


def test_the_page_keeps_the_zoom_keys_and_where_it_was_left() -> None:
    """Cmd+plus, Cmd+minus and Cmd+0, which the window took away with the browser. Bound only
    once pywebview says the window is there: asked at boot, `window.pywebview` is not written
    yet, and neither the zoom nor Cmd+F was ever bound in the window itself (measured in a real
    WKWebView, 2026-09-20)."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "pywebviewready" in _function_body(app_js, "whenInOwnWindow")
    boot = _function_body(app_js, "boot")
    assert "bindFind();" in boot  # the bar's buttons work in a browser too
    assert "bindFindKeys();" in boot and "bindZoom((text) => toast(text));" in boot
    assert boot.index("whenInOwnWindow(") < boot.index("bindFindKeys();")

    got = _node_json(
        "zoom.js",
        """(() => {
          const store = {};
          globalThis.localStorage = {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = v; },
            removeItem: (k) => { delete store[k]; },
          };
          return {
            up: m.nextZoom(1, 1),
            down: m.nextZoom(1, -1),
            ceiling: m.nextZoom(2, 1),
            floor: m.nextZoom(0.67, -1),
            offStep: m.nextZoom(1.04, 1),
            normal: m.NORMAL,
            fresh: m.savedZoom(),
          };
        })()""",
    )
    assert got == {
        "up": 1.1,
        "down": 0.9,
        "ceiling": 2,  # the ends hold rather than wrapping round
        "floor": 0.67,
        "offStep": 1.1,  # a size that is not a step starts from the nearest one
        "normal": 1,
        "fresh": 1,  # nothing remembered: the page opens at its own size
    }


def test_the_final_report_says_which_tiles_were_patched() -> None:
    """A build read the hand-made patches and said so nowhere: a tile built with its patches
    looked exactly like one built without (a user, 2026-09-20). The report says which tiles had
    them, with the file names under the pointer, and says "none" rather than staying silent when
    there were none: the question is whether they were applied, and no answer is an answer."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    body = _function_body(app_js, "renderReport")
    assert "(tile.patches || []).length" in body  # tiles that had patches, whatever the engine
    assert 't("works.patches_none")' in body  # and the other half of the answer
    assert "tile.patches.join(" in body  # the names, under the pointer
    assert '[t("works.patches"), patchWords' in body
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        assert tables[lang]["works.patches"] and tables[lang]["works.patches_none"]
    # the mock builds a tile with patches, so both halves can be seen without a folder of them
    assert "patch.osm`" in app_js


def test_the_works_line_says_which_relief_was_read_not_only_which_was_chosen() -> None:
    """A user built Banff with "Canada's lidar relief", watched it through without one error,
    and got Copernicus: the lidar has not flown there, a composite relief falls back to what it
    is laid over, and this line named his choice back at him (2026-09-20). The engine already
    recorded DEM_OVERLAY_UNAVAILABLE per square; nothing read it."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    body = _function_body(app_js, "updateJobView")
    assert 'decisionCounts(job).get("DEM_OVERLAY_UNAVAILABLE")' in body
    assert 't("works.relief_gap_one")' in body and 't("works.relief_gap"' in body
    # and the code reads as words in the list of decisions, not as its own name
    assert 'DEM_OVERLAY_UNAVAILABLE: () => t("works.dec_no_overlay")' in app_js
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        for key in ("works.relief_gap", "works.relief_gap_one", "works.dec_no_overlay"):
            assert tables[lang][key], f"{lang} is missing {key}"
    assert "{n}" in tables["en"]["works.relief_gap"]
    assert "{n}" not in tables["en"]["works.relief_gap_one"]  # one square is not "1 square(s)"
    # the mock builds such a job, so the line can be seen without flying to Canada
    jobs = _mock_json("jobs")
    assert jobs[0]["relief"] == "canada"
    codes = [d.get("code") for d in _mock_json("job_done")["decisions"]]
    assert "DEM_OVERLAY_UNAVAILABLE" in codes


def test_the_import_says_it_takes_nothing_from_the_ortho4xp_folder() -> None:
    """ "Import my Ortho4XP tiles" reads and registers, and the word import suggests copying or
    moving: a user asked whether his Ortho4XP tiles would be deleted, copied, or where they
    would be put (2026-09-20). They stay exactly where they are, the library keeps their path,
    and a tile Ortho4XP built is refused by delete (`pipeline/pack.py`). The page said none of
    it."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'data-i18n="library.import_help"' in html
    assert html.index('data-i18n="library.import"') < html.index('data-i18n="library.import_help"')
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        assert tables[lang]["library.import_help"], f"{lang} is missing it"
    # the three things a user fears, answered
    words = tables["en"]["library.import_help"]
    assert "copied" in words and "moved" in words and "deletes" in words

    # and it is the truth: the import registers, and deleting an imported tile is refused
    from orthostudio.install.library import Library

    source = Path(Library.import_ortho4xp.__code__.co_filename).read_text(encoding="utf-8")
    body = source[source.index("def import_ortho4xp") :]
    body = body[: body.index("\n\n\n")]
    for takes in ("copy", "move", "rmtree", "unlink", "rename"):
        assert takes not in body, f"the import {takes}s something"


def test_an_imported_tile_can_be_taken_off_the_list() -> None:
    """Delete is drawn for tiles OrthoStudio XP built; an imported tile first had nothing in its
    place, then a "not deletable here" (2026-09-20), and a user asked for a way back from an
    import (2026-09-21). "Remove from the list" forgets the tile and touches nothing on the disk;
    not while X-Plane shows the tile, which the Library could then no longer take out."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    row = app_js[app_js.index("const deleteButton = byOsxp") :]
    row = row[: row.index("\n  let missing")]
    assert "onclick: () => forgetLibraryTile(e)" in row and 't("library.forget")' in row
    assert "disabled: busy || Boolean(e.installed)" in row
    assert 'e.installed ? t("library.forget_installed") : t("library.forget_help")' in row
    assert "delete_not_ours" not in app_js  # what stood there before
    # an imported tile still in X-Plane whose files are gone keeps its way out of X-Plane
    assert "if (e.installed && (present || !byOsxp)) {" in _function_body(app_js, "libraryRow")
    forget = _function_body(app_js, "forgetLibraryTile")
    assert 'libraryRequest(e, "forget")' in forget and "runLibraryChange(e," in forget
    assert 't("library.forgotten", { tile: e.tile })' in forget
    for code in ("SYS_PACK_IN_XPLANE", "SYS_PACK_NOT_IMPORTED"):
        assert f"  {code}: () => [" in app_js, code
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        assert "delete_not_ours" not in str(tables[lang])
        for key in ("forget", "forget_help", "forget_installed", "forgotten", "err_in_xplane",
                    "err_not_imported", "err_not_imported_remedy"):  # fmt: skip
            assert tables[lang][f"library.{key}"], (lang, key)
        assert "{tile}" in tables[lang]["library.forgotten"]
    # and it says the files stay, with the way back
    assert "Ortho4XP" in tables["en"]["library.forget_help"]
    assert "again" in tables["en"]["library.forget_help"]


def test_the_mock_takes_an_imported_tile_off_the_list_like_the_engine() -> None:
    script = """
    const before = (await call("GET", "/api/library")).ok;
    const out = {
      shown: await call("POST", "/api/library/zOrtho4XP_+44+005/forget", {}),
      built: await call("POST", "/api/library/+43+005/forget", {}),
      forgot: await call("POST", "/api/library/zOrtho4XP_+44+006/forget", {}),
      again: await call("POST", "/api/library/zOrtho4XP_+44+006/forget", {}),
    };
    const after = (await call("GET", "/api/library")).ok;
    const kept = new Set(after.map((e) => `${e.kind} ${e.path}`));
    out.gone = before.filter((e) => !kept.has(`${e.kind} ${e.path}`)).map((e) => [e.tile, e.kind]);
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(script)
    assert got["shown"] == {"status": 409, "code": "SYS_PACK_IN_XPLANE"}
    assert got["built"] == {"status": 409, "code": "SYS_PACK_NOT_IMPORTED"}
    assert got["forgot"]["ok"]["tile"] == "+44+006" and got["forgot"]["ok"]["forgotten"] == 1
    assert got["again"] == {"status": 422, "code": "SYS_WORKING_DIR_INVALID"}
    assert got["gone"] == [["+44+006", "ortho"]]


def test_the_page_says_when_a_newer_version_is_out() -> None:
    """A user who did not read the forum stayed on the version he had, with bugs fixed since, and
    nothing told him (2026-09-21). One line at the top, with a link to the release page; nothing
    is downloaded or installed, and "Not now" hides it until the next version."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    load = _function_body(app_js, "loadUpdate")
    assert 'api("GET", "/api/update")' in load
    # asked once the page is up, and never waited for: a slow GitHub must not hold the page back
    boot = _function_body(app_js, "boot")
    assert "loadUpdate();" in boot and "await loadUpdate" not in boot
    note = _function_body(app_js, "renderUpdateNote")
    # a link, not window.open: the window hands only a clicked target="_blank" link to the
    # system's browser
    assert 'h("a", {' in note and 'target: "_blank"' in note and 'rel: "noopener"' in note
    assert "window.open(" not in note  # a call; the comment above says why there is none
    assert "UPDATE_DISMISSED_KEY, u.latest" in note  # put away for this version, not for ever
    assert "measureMapTop();" in note  # it sits above the map
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        for key in (
            "app.update_available",
            "app.update_open",
            "app.update_later",
            "settings.x.group_app",
            "settings.x.check_updates",
            "settings.x.check_updates_hint",
        ):
            assert tables[lang][key], f"{lang} is missing {key}"
    # and it can be turned off, in a group of its own: it has nothing to do with the tiles
    code = (UI / "settings.js").read_text(encoding="utf-8")
    assert 'fields: ["expert.check_updates"]' in code
    assert '"expert.check_updates": [() => t("settings.x.check_updates")' in code
    # the setting says that GitHub sees the address, rather than leaving it to be found out
    assert "GitHub" in tables["en"]["settings.x.check_updates_hint"]


def test_the_library_says_what_each_tile_was_built_with() -> None:
    """A user asked where to see which data a tile was built with (2026-09-21). The "Built by" cell
    of a tile OrthoStudio XP built opens a row under it; an imported tile's settings are
    Ortho4XP's, which nothing here reads, so it has none. The relief is said as it really was: a
    lidar asked for where it never flew says so, which is how a user at Banff would have known."""
    got = _node_json(
        "app.js",
        """(() => {
          const facts = {version: "0.1.10", relief: "COP30", relief_laid: [],
                         relief_asked: ["HRDEM"], patches: ["CBH2.patch.osm"],
                         zones: [{zl: 17, provider: "BI"}, {zl: 17, provider: "BI"},
                                 {zl: 18, provider: "BI"}]};
          const row = {provider: "BI", zl: 16, photo: null, built: {at: 1790000000, facts}};
          const full = m.builtLines(row, [{code: "BI", name: "Bing Maps"}]);
          const old = m.builtLines({...row, built: {at: 1790000000, facts: {}}});
          return {
            lines: full.lines,
            head: full.head.includes("0.1.10"),
            oldHead: old.head.includes("before OrthoStudio XP 0.1.10"),
            oldLabels: old.lines.map(([k]) => k),
            unknown: m.builtLines({provider: "BI", built: null}).lines.length,
            laid: m.reliefSentence({relief: "COP30", relief_laid: ["HRDEM"],
                                    relief_asked: ["HRDEM"]}),
            own: m.reliefSentence({relief: "XP12", relief_laid: ["/Users/me/lidar"],
                                   relief_asked: ["/Users/me/lidar"]}),
          };
        })()""",
    )
    assert got["lines"] == [
        ["Imagery", "Bing Maps"],
        ["Detail", "Standard · ZL16"],
        ["Relief", "Copernicus: Canada's lidar asked, none on this square"],
        ["Colours", "as delivered"],
        ["Zones", "3: 2 at ZL17, 1 at ZL18"],
        ["Patches", "CBH2.patch.osm"],
    ]
    assert got["head"]
    # a pack written before 0.1.10 knows its imagery, detail and colours, and says no more
    assert got["oldHead"] and got["oldLabels"] == ["Imagery", "Detail", "Colours"]
    assert got["unknown"] == 0
    assert got["laid"] == "Copernicus, with Canada's lidar over it"
    assert got["own"] == "X-Plane 12, with your own files over it"

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    row = _function_body(app_js, "libraryRow")
    assert "libraryOpen.has(key)" in row  # open parts stay open across the table's redraws
    assert 'class: "library-built"' in row and "colspan: 8" in row
    assert "byOsxp ?" in row  # none for an imported tile
    assert "return detail ? [row, detail] : [row];" in row
    # focus comes back to a row by its place among the tiles, not among the parts under them
    assert 'classList.contains("library-built")' in _function_body(app_js, "libraryRows")
    assert "body.append(...libraryRow(e));" in _function_body(app_js, "renderLibrary")


def test_the_plan_says_what_a_built_tile_was_built_with() -> None:
    """Where the user decides to build again (2026-09-21): a tooltip over a built tile's green
    outline, and under the chosen squares a line for each one already built, with what a build
    now would change of the Plan's own two choices, the imagery source and the detail level."""
    got = _node_json(
        "app.js",
        """(() => {
          const facts = {version: "0.1.10", relief: "COP30", relief_laid: [],
                         relief_asked: ["HRDEM"]};
          const library = [
            {tile: "+43+005", kind: "ortho", built_by: "osxp", installed: true, provider: "BI",
             zl: 16, built: {facts, at: 1}},
            {tile: "+43+005", kind: "overlay", built_by: "osxp", installed: true},
            {tile: "+44+005", kind: "ortho", built_by: "ortho4xp", installed: true, provider: "",
             zl: 0, built: null},
          ];
          const providers = [{code: "BI", name: "Bing Maps"}];
          return {
            ours: m.builtSummary("+43+005", library, providers),
            imported: m.builtSummary("+44+005", library, providers),
            none: m.builtSummary("+40+010", library, providers),
          };
        })()""",
    )
    assert got == {
        "ours": "+43+005 · Bing Maps · ZL16 · "
        "Copernicus: Canada's lidar asked, none on this square",
        "imported": "+44+005 · Ortho4XP",
        "none": None,
    }
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    # said again whenever what it depends on changes: the squares, the library, source and level
    assert "renderTilesBuilt();" in _function_body(app_js, "renderTiles")
    assert "renderTilesBuilt();" in _function_body(app_js, "loadLibrary")
    boot = _function_body(app_js, "boot")
    assert boot.count("renderTilesBuilt();") >= 3  # library at boot, source, level
    built = _function_body(app_js, "renderTilesBuilt")
    # short by default: what it was built with is folded, and stays as the user left it
    assert 'class: "tiles-built-detail", hidden: !open }, builtBlock(e)' in built  # as the Library
    assert "tilesBuiltOpen.has(name)" in built
    # what a build would change is never folded: it has to be read before Build is pressed
    warn = built[built.index('class: "tiles-built-warn"') - 120 :]
    assert "hidden" not in warn[: warn.index("tiles-built-warn") + 40]
    assert 't("plan.built_change", { changes: changes.join(t("plan.built_and")) })' in built
    assert 'e.built_by !== "osxp"' in built  # an Ortho4XP tile is never built again here
    # the square's colours too, by the Library's own test, and said again as soon as a slider
    # moves: a user moved one and was told nothing here (2026-09-21)
    assert 'if (photoDiffers(e)) changes.push(t("plan.built_colours"));' in built
    colours = _function_body(app_js, "renderTileColours")
    assert colours.index("renderTilesBuilt();") < colours.index("return")  # before any way out

    map_js = (UI / "map.js").read_text(encoding="utf-8")

    def nested(name: str) -> str:
        """A function of the map's factory, indented two spaces, to its closing brace."""
        match = re.search(rf"\n  function {name}\(.*?\n  \}}\n", map_js, re.S)
        assert match is not None, name
        return match.group(0)

    tip = nested("showTip")
    assert "ctx.builtSummary?.(name)" in tip and "installedTiles().includes(name)" in tip
    # an element of the map's own: a Leaflet tooltip needs the outline to be interactive, and an
    # interactive outline would take the clicks that choose the squares
    assert "bindTooltip" not in tip
    assert "hideTip();" in nested("onMapMouseMove")  # not while drawing a zone
    css = (UI / "styles.css").read_text(encoding="utf-8")
    assert "pointer-events: none" in css[css.index(".map-tip {") :][:300]
    for lang in ("en", "fr"):
        tables = _i18n_tables()
        for key in (
            "plan.built_tile",
            "plan.built_tile_o4x",
            "plan.built_change",
            "plan.built_instead",
            "plan.built_and",
            "plan.built_details",
            "plan.built_colours",
        ):
            assert tables[lang][key], f"{lang} is missing {key}"


def test_importing_ortho4xp_tiles_is_one_button_that_says_where_it_looked() -> None:
    """A user pressed "Import my Ortho4XP tiles" with the field still empty: nothing happened at
    all, and he could not tell which folder was meant (2026-09-21). The button then opened the
    folder dialog when no folder was typed, and "Choose…" beside it did the same: two buttons for
    one thing (the same user). One button now asks for the folder and imports it; the field to
    type it in shows only where no dialog opens. The answer counts tiles, not the overlays pack
    beside them, names the folder, and says where it looked when it found none."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    form = html[html.index('<form id="import-form"') :]
    form = form[: form.index("</form>")]
    assert 'id="import-choose"' not in html and "library.import_choose" not in html
    assert '<div class="field field-wide" id="import-path-field" hidden>' in form
    assert form.count("<button") == 1 and 'type="submit"' in form
    # the answer right under the button, the help after it
    assert html.index('id="import-result"') < html.index('data-i18n="library.import_help"')
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert 'id="import-choose"' not in app_js and '$("import-choose")' not in app_js
    body = _function_body(app_js, "importOrtho4xp")
    assert "if (!dir) return;" in body  # a cancel changes nothing
    assert 'await chooseFolder(t("library.import_prompt"), ortho4xpStart(), showImportPath)' in body
    assert 't("library.import_type_first")' in body  # the field shown and still empty
    assert 'e.kind == null || e.kind === "ortho"' in body  # tiles, not the overlays pack
    assert 't("library.imported", { n: fmtInt(tiles), where: homely(dir) })' in body
    assert 't("library.import_none", { where })' in body and "res?.searched" in body
    assert "Array.isArray(res) ? res" in body  # an older engine answered with the list alone
    # no dialog on this system: the field to type the path in is shown
    choose = _function_body(app_js, "chooseFolder")
    assert 'if (errorDetail(err)?.code === "SYS_NO_FOLDER_DIALOG") onMissing?.();' in choose
    assert '$("import-path-field").hidden = false;' in _function_body(app_js, "showImportPath")
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        assert "library.import_choose" not in tables[lang]
        assert "library.import_choose_first" not in tables[lang]
        assert tables[lang]["library.import"].endswith("…")  # it opens a dialog
        assert "{where}" in tables[lang]["library.imported"]
        assert tables[lang]["library.import_none"] and tables[lang]["library.import_type_first"]
    # the help says which folder before anything else
    assert tables["en"]["library.import_help"].startswith("The folder holding Ortho4XP.py")


def _css_rules(css: str) -> list[tuple[str, str]]:
    """``(selector, body)`` of every rule of the page's stylesheet, comments left out; the rules
    inside an ``@media`` block are read as the rules they are."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{", "", css)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


# The only rules with a size of their own: controls and bars, not text (see the "styles" block).
OWN_SIZES = {
    ".topbar-tools .tool-select, .topbar-tools .tool-btn, .topbar-tools .quit-btn",
    ".btn-small",
    ".chip button",
    ".map-notice",
    ".map-legend",
    ".plan-step-num",
    ".map-tip",
    ".statusbar",
    ".find-input",
    ".experts-chevron::before",
}


def test_the_text_follows_one_set_of_styles() -> None:
    """A user found the page chaotic: eleven sizes of text, titles standing 0 to 14px above their
    text, and asked for it to work like a word processor's template, where every text has a style
    defined once (2026-09-21). Seven styles (three titles, text, secondary, label, small), their
    sizes and the spaces between things are tokens; no rule but a control's or a bar's sets a size
    of its own."""
    css = (UI / "styles.css").read_text(encoding="utf-8")
    for token, value in (
        ("--fs-title-1", "18px"), ("--fs-title-2", "15px"), ("--fs-title-3", "13.5px"),
        ("--fs", "13.5px"), ("--fs-2", "12.5px"), ("--fs-3", "11.5px"),
        ("--title-gap", "6px"), ("--label-gap", "4px"), ("--help-gap", "5px"), ("--gap", "8px"),
        ("--block-gap", "12px"), ("--section-gap", "20px"),
    ):  # fmt: skip
        assert re.search(rf"^  {re.escape(token)}: {re.escape(value)};", css, re.M), token
    rules = _css_rules(css)
    own = {sel for sel, body in rules if re.search(r"font-size: [\d.]+px", body)}
    assert own == OWN_SIZES, own ^ OWN_SIZES
    # a title stands the same space above what follows it, whatever its level
    base = dict(rules)
    assert "margin: 0 0 var(--title-gap);" in base["h1, h2, h3"]
    assert "uppercase" not in base["h2, .h2"]  # "DISK SPACE" and "JOBS" were a style of their own
    assert "margin-bottom: var(--title-gap);" in base[".question > legend"]  # floated: its own
    for title in (".plan-step-title", ".job-head h2", ".cost h3"):
        assert "font-size" not in base[title], title  # the style's own
    assert ".dialog-title" not in base
    # the same space everywhere from a field to the text under it (5px, measured in the page)
    for sel, rule in (
        (".field > .help", "margin-top: calc(var(--help-gap) - var(--label-gap));"),
        (".toolbar > .help", "margin: var(--help-gap) 0 0;"),
        (".disk-check + .help", "margin-top: calc(var(--help-gap) - 3px);"),
        (".gen-field > .hint", "padding: var(--help-gap) 0 var(--block-gap);"),
        (".question-help", "margin: var(--help-gap) 0 var(--gap);"),
        (".actions-build + .help", "margin-top: var(--help-gap);"),
    ):
        assert rule in base[sel], sel
    assert "gap: var(--label-gap);" in base[".field"]  # the label to its field
    # a field's name looks the same in the Plan, in Settings and under For experts
    label = next(sel for sel, _ in rules if ".sub-question-title," in sel)
    for part in (".field > label:not(.switch)", ".label-row > label", "fieldset > legend"):
        assert part in label, part


def test_what_the_macs_window_showed_wrong() -> None:
    """Four things a user saw in the Mac's window (2026-09-21): Settings' second column standing
    12px lower than the first (WebKit carried the space under the first column's last card over
    to the top of the second), *Show in Finder* reading as text, the arrows of a number field
    covering its unit, and *Decisions* standing lower than *Final report*."""
    css = (UI / "styles.css").read_text(encoding="utf-8")
    rules = dict(_css_rules(css))
    # cards of a line each: nothing is carried over a column break
    assert "display: inline-block; width: 100%; vertical-align: top;" in rules[".question"]
    # every button has a frame
    for name in (*PAGE_MODULES, "index.html", "styles.css"):
        assert "btn-quiet" not in (UI / name).read_text(encoding="utf-8"), name
    # a number and its unit in one frame, the unit after the field (and after its arrows)
    assert "border: 1px solid var(--line-2);" in rules[".with-unit"]
    assert "border: 0;" in rules[".with-unit input"]
    assert "position: absolute" not in rules[".unit"] and ".form-grid .with-unit .unit" not in rules
    # the report's two titles on one line, over their own columns
    assert 'grid-template-areas: "title title decisions-title" "summary steps decisions";' in css
    body = _function_body((UI / "app.js").read_text(encoding="utf-8"), "renderReport")
    grid = body[body.index('h("div", { class: "report-grid" },') :]
    assert grid.index("report-title") < grid.index("report-title-decisions") < grid.index("summary")


def test_the_patches_are_named_before_anything_is_built() -> None:
    """A user saw "Patches: none" in a report and took it that his patch had not been found, when
    it was for another square (2026-09-21). The Plan names the chosen tiles that have patches, and
    which files; Settings says what the folder shown holds, saved or not; the report says "none
    for these tiles"."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    order = [html.index(f'id="{name}"') for name in ("tiles-built", "tiles-patched", "tile-count")]
    assert order == sorted(order)
    plan = _function_body(app_js, "renderTilesPatched")
    assert "state.patches?.tiles" in plan and "for (const name of state.tiles)" in plan
    assert 't("plan.patched", { tile: name, files: files.join(", ") })' in plan
    assert "renderTilesPatched();" in _function_body(app_js, "renderTiles")
    # read when the page opens, when the Plan shows (a patch dropped in meanwhile), after a save
    assert 'api("GET", "/api/patches")' in _function_body(app_js, "loadPatches")
    for name in ("boot", "showScreen", "saveSettings"):
        assert "loadPatches();" in _function_body(app_js, name), name
    # Settings asks about the folder it shows, and only when it changes
    ask = _function_body(app_js, "loadSettingsPatches")
    assert "`/api/patches?dir=${encodeURIComponent(dir)}`" in ask
    assert "if (dir === settingsPatchesDir) return;" in ask
    assert "loadSettingsPatches();" in _function_body(app_js, "renderSettings")
    assert "patches: state.settingsPatches || null," in app_js
    settings_js = (UI / "settings.js").read_text(encoding="utf-8")
    assert (
        'path === PATCHES_DIR ? h("span", { class: "hint-found" }, patchesFoundText(view.patches))'
        in settings_js
    )
    words = _node_json(
        "settings.js",
        "[null, {dir: null}, {dir: '/p', exists: false, tiles: {}}, {dir: '/p', exists: true,"
        " tiles: {}}, {dir: '/p', exists: true, tiles: {'-20-044': ['SBCF.patch.osm']}},"
        " {dir: '/p', exists: true, tiles: Object.fromEntries(['+41+001', '+42+001', '+43+001',"
        " '+44+001', '+45+001', '+46+001', '+47+001', '+48+001'].map((n) => [n, ['a']]))}]"
        ".map((f) => m.patchesFoundText(f))",
    )
    assert words[:2] == ["", ""]
    assert words[2:5] == [
        "This folder does not exist.",
        "No tile has patches in this folder yet.",
        "Patches found for 1 tile(s): -20-044.",
    ]
    assert words[5] == (
        "Patches found for 8 tile(s): +41+001, +42+001, +43+001, +44+001, +45+001, +46+001"
        " and 2 more."
    )
    tables = _i18n_tables()
    for lang in ("en", "fr"):
        for key in ("plan.patched", "settings.x.patches_found", "settings.x.patches_more",
                    "settings.x.patches_none", "settings.x.patches_missing"):  # fmt: skip
            assert tables[lang][key], (lang, key)
    assert tables["en"]["works.patches_none"] == "none for these tiles"
    assert tables["fr"]["works.patches_none"] == "aucune pour ces tuiles"


def test_the_mock_has_a_folder_of_patches() -> None:
    script = """
    const out = {
      saved: await call("GET", "/api/patches"),
      empty: await call("GET", "/api/patches?dir=" + encodeURIComponent("/Users/pilot/empty")),
      missing: await call("GET", "/api/patches?dir=/nowhere/missing"),
    };
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    """
    got = _node_mock(script)
    assert got["saved"]["ok"]["tiles"]["-20-044"] == ["SBCF.patch.osm"]
    assert got["saved"]["ok"]["exists"] is True
    assert got["empty"]["ok"]["tiles"] == {}
    assert got["missing"]["ok"]["exists"] is False


def test_step_3_says_it_is_working_beside_its_title() -> None:
    """The spinner, "Working out the cost…" and *Estimate again* had a line of their own under
    the build settings, kept 24px high so the cost did not jump when it showed: most of the time
    it stood as 24px of nothing between the settings and the cost (a user, 2026-09-21). Beside
    the step's title they take no room, and nothing jumps either."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    head = html[html.index('<div class="plan-step-head">') :]
    head = head[: head.index('<p class="plan-step-lead" data-i18n="step3.lead">')]
    for part in ("step3-title", "estimate-btn", "estimate-spinner", "estimate-status"):
        assert f'id="{part}"' in head, part
    css = (UI / "styles.css").read_text(encoding="utf-8")
    rules = dict(_css_rules(css))
    assert "min-height" not in rules.get(".estimate-actions", "")
    assert "margin: 0;" in rules[".plan-step-head > .estimate-actions"]


def test_a_list_draws_the_same_arrows_in_every_engine() -> None:
    """Chromium (Windows, and Chrome) drew a list's arrow as a chevron almost touching its right
    edge, where the Mac's window drew up and down arrows clear of it: a user put the two side by
    side (2026-09-21). The page draws its own, the Mac's, in each theme's --fg-2."""
    css = (UI / "styles.css").read_text(encoding="utf-8")
    rules = dict(_css_rules(css))
    lists = rules["select"]
    for part in (
        "appearance: none;",
        "-webkit-appearance: none;",
        "padding-right: 24px;",
        "background-image: var(--select-arrow);",
        "background-position: right 8px center;",
    ):
        assert part in lists, part
    # the top bar's list keeps it: its shared rule sets the colour only, not the whole background
    shared = rules[".topbar-tools .tool-select, .topbar-tools .tool-btn, .topbar-tools .quit-btn"]
    assert "background-color: var(--bg-2);" in shared and "background:" not in shared
    assert "padding-right: 22px;" in rules[".topbar-tools .tool-select"]
    # one arrow per theme, in its --fg-2 (an image cannot read the tokens)
    arrow = r"--select-arrow: url\(\"data:image/svg\+xml,[^\"]*stroke='%23(\w{6})'"
    arrows = re.findall(arrow, css)
    fg2 = re.findall(r"--fg-2: #(\w{6});", css)
    assert arrows == fg2 and len(arrows) == 3, (arrows, fg2)


def test_a_fields_title_is_text() -> None:
    """A label hands its clicks to its field: a user who double-clicked a setting's name to copy
    it saw the caret jump into the field, or a check box tick (2026-09-21). A click on a title (a
    label naming a field outside it) is left to the text; a label around its own box ("On", a
    choice) is still that box's target."""
    got = _node_json(
        "app.js",
        "(() => { const box = {}; const at = (label) => ({ closest: () => label });"
        " return ["
        " m.isTitleClick(at({ control: box, contains: () => false })),"  # a title
        " m.isTitleClick(at({ control: box, contains: (x) => x === box })),"  # around its box
        " m.isTitleClick(at({ control: null, contains: () => false })),"  # names nothing
        " m.isTitleClick(at(null)), m.isTitleClick(null)]; })()",
    )
    assert got == [True, False, False, False, False]
    boot = _function_body((UI / "app.js").read_text(encoding="utf-8"), "boot")
    assert 'document.addEventListener("click", (ev) => {' in boot
    assert "if (isTitleClick(ev.target)) ev.preventDefault();\n  }, true);" in boot


FAKE_DOM = """
// Just enough of a DOM for app.js morphChildren: nodes, attributes, dataset, a focused element.
class Node {
  constructor(type) { this.nodeType = type; this.childNodes = []; this.parentNode = null; }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() {
    const s = this.parentNode ? this.parentNode.childNodes : [];
    return s[s.indexOf(this) + 1] || null;
  }
  removeChild(n) {
    this.childNodes.splice(this.childNodes.indexOf(n), 1);
    n.parentNode = null;
    return n;
  }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
    this.childNodes.splice(i, 0, n); n.parentNode = this; return n;
  }
  append(...ns) { for (const n of ns) this.insertBefore(n, null); return this; }
}
class Text extends Node { constructor(v) { super(3); this.nodeValue = v; } }
class Element extends Node {
  constructor(tag, attrs = {}) {
    super(1); this.tagName = tag.toUpperCase(); this.attrs = new Map(Object.entries(attrs || {}));
    const el = this;
    const name = (k) => "data-" + String(k).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
    this.dataset = new Proxy({}, { get: (_, k) => el.attrs.get(name(k)) });
  }
  get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
  get id() { return this.attrs.get("id") || ""; }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  removeAttribute(n) { this.attrs.delete(n); }
  hasAttribute(n) { return this.attrs.has(n); }
}
class Input extends Element {
  constructor(attrs, value) { super("input", attrs); this.value = value; this.checked = false; }
}
globalThis.document = { activeElement: null, getElementById: () => null };
const node = (k) => (typeof k === "string" ? new Text(k) : k);
const el = (tag, attrs, ...kids) => new Element(tag, attrs).append(...kids.map(node));
const m = await import("./app.js");
"""


def test_a_screen_drawn_again_changes_only_what_differs() -> None:
    """A user saw the Settings screen flash in the Mac's window each time a field was left, and a
    number field answer late under its own arrows, since each change drew the whole screen anew;
    and asked for one method that works in all cases, not fixes one by one (2026-09-21). The screen
    is drawn apart and only what differs is put in (app.js morphChildren)."""
    script = (
        FAKE_DOM
        + """
    const out = {};
    // unchanged: every node stays; a text that changed is set in place
    const note = new Text("ZL18");
    const live = el("div", null, el("p", { id: "a" }, "same"), el("p", { id: "b" }, note));
    const [a, b] = live.childNodes;
    const next = el("div", null, el("p", { id: "a" }, "same"), el("p", { id: "b" }, "ZL17"));
    m.morphChildren(live, next);
    out.kept = live.childNodes[0] === a && live.childNodes[1] === b && b.firstChild === note;
    out.text = note.nodeValue;
    // an element's attribute is set in place; a control whose attributes change is replaced
    const box = el("div", { class: "help" });
    const field = new Input({ type: "number", "data-focus-key": "x" }, "17");
    const host = el("div", null, box, field);
    const wanted = new Input({ type: "number", "data-focus-key": "x", min: "12" }, "17");
    m.morphChildren(host, el("div", null, el("div", { class: "help is-warn" }), wanted));
    out.attribute = host.childNodes[0] === box && box.getAttribute("class");
    out.replaced = host.childNodes[1] === wanted;
    // a value is set in place, except in the field being typed in
    const typed = new Input({ "data-focus-key": "t" }, "abc");
    const other = new Input({ "data-focus-key": "o" }, "1");
    const form = el("form", null, typed, other);
    document.activeElement = typed;
    const key = (k) => ({ "data-focus-key": k });
    const drawn = [new Input(key("t"), "ab"), new Input(key("o"), "2")];
    m.morphChildren(form, el("form", null, ...drawn));
    const [t0, o0] = form.childNodes;
    out.values = [t0 === typed && typed.value, o0 === other && other.value];
    document.activeElement = null;
    // what is drawn later (a canvas) is kept or replaced whole by what it says it shows
    const shot = el("div", { "data-version": "v1" }, el("canvas"), "drawn");
    const panel = el("div", null, shot);
    const again = el("div", { "data-version": "v1" }, el("canvas"), "waiting");
    m.morphChildren(panel, el("div", null, again));
    out.version = panel.childNodes[0] === shot && shot.childNodes[1].nodeValue;
    const newer = el("div", { "data-version": "v2" }, el("canvas"));
    m.morphChildren(panel, el("div", null, newer));
    out.versionChanged = panel.childNodes[0] === newer;
    // matched by key wherever they are: reordered, removed, added
    const x = el("p", { id: "x" }), y = el("p", { id: "y" }), z = el("p", { id: "z" });
    const list = el("div", null, x, y, z);
    const w = el("p", { id: "w" });
    m.morphChildren(list, el("div", null, el("p", { id: "z" }), el("p", { id: "x" }), w));
    out.order = list.childNodes.map((n) => n.id).join(" ");
    out.moved = list.childNodes[0] === z && list.childNodes[1] === x && list.childNodes[2] === w;
    process.stdout.write(JSON.stringify(out));
    """
    )
    got = _run_node(script)
    assert got == {
        "kept": True,
        "text": "ZL17",
        "attribute": "help is-warn",
        "replaced": True,
        "values": ["abc", "2"],
        "version": "drawn",
        "versionChanged": True,
        "order": "z x w",
        "moved": True,
    }


def test_settings_are_drawn_apart_and_morphed_in() -> None:
    """Settings draws its three parts apart, filters them as the search will show them, and puts
    in only what differs; the draft keeps its identity, since the parts left in place keep handlers
    that write into it. The cases handled one by one before it are gone."""
    settings_js = (UI / "settings.js").read_text(encoding="utf-8")
    view = _function_body(settings_js, "renderSettingsView")
    assert "box.cloneNode(false)" in view and "searchFilter(drawn," in view
    assert (
        'for (const part of ["presets", "questions", "experts"]) morph(parts[part], drawn[part]);'
        in view
    )
    for gone in ("questionsShow", "numbersPending", "view.from", "view.touched"):
        assert gone not in settings_js, gone
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "dom: { h, clear, morph: morphChildren }," in app_js
    assert "state.settingsDraft = structuredClone" not in app_js  # always setDraft
    assert app_js.count("setDraft(") >= 6
    set_draft = _function_body(app_js, "setDraft")
    assert "for (const key of Object.keys(draft)) delete draft[key];" in set_draft
    # a canvas drawn later says what it shows
    preview = (UI / "preview.js").read_text(encoding="utf-8")
    assert '"data-version": JSON.stringify([sample.url, look, size])' in preview
    # the check box is named by its title without being tied to it
    assert 'const tied = control.type !== "checkbox";' in settings_js
    assert 'control.setAttribute("aria-labelledby", `${id}-title`);' in settings_js


def test_the_import_dialog_opens_at_the_ortho4xp_folder_already_imported() -> None:
    rows = [
        {"tile": "+43+005", "kind": "ortho", "built_by": "osxp", "path": "/t/zOrthoStudio_+43+005",
         "updated_at": 9},
        {"tile": "+44+005", "kind": "ortho", "built_by": "ortho4xp", "updated_at": 1,
         "path": "/Users/pilot/Ortho4XP/Tiles/zOrtho4XP_+44+005"},
        {"tile": "+45+005", "kind": "ortho", "built_by": "ortho4xp", "updated_at": 2,
         "path": "C:\\Ortho4XP-1.40\\Tiles\\zOrtho4XP_+45+005"},
        {"tile": "+45+005", "kind": "overlay", "built_by": "ortho4xp", "updated_at": 3,
         "path": "/elsewhere/yOrtho4XP_Overlays"},
    ]  # fmt: skip
    got = _node_mock(
        f"const rows = {json.dumps(rows)};\n"
        "const got = [rows, rows.slice(0, 2), rows.slice(0, 1), []].map(m.ortho4xpStart);\n"
        "process.stdout.write(JSON.stringify(got), () => process.exit(0));"
    )
    assert got == ["C:\\Ortho4XP-1.40", "/Users/pilot/Ortho4XP", None, None]


def test_my_sources_is_a_button_that_can_be_seen() -> None:
    """A user could not find "My sources…" in the Plan at all: it was a quiet button, with no
    border, grey text and the panel's own background, so it did not read as a button
    (2026-09-21)."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    tag = html[html.index('id="sources-open"') - 120 : html.index('id="sources-open"')]
    assert "btn-quiet" not in tag and "btn btn-small" in tag


def test_the_flight_plan_of_step_1_is_in_plain_sight() -> None:
    """A route is one more way of choosing squares, beside the airport, and both are visible.

    They were under a folded "Other ways" line, which a user never opened (2026-09-19). What the
    buttons count is what the map draws: the same arithmetic answers both.
    """
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    step = re.search(r'<section class="plan-step" id="tiles-panel".*?</section>', html, re.S)
    assert step is not None
    panel = step.group(0)
    folded = re.search(r'<details class="more">.*?</details>', panel, re.S)
    assert folded is not None
    for element in ('id="icao-input"', 'id="radius-input"', 'id="route-input"', 'id="route-draw"',
                    'id="route-ends"', 'id="route-all"', 'id="route-clear"'):  # fmt: skip
        assert element in panel and element not in folded.group(0)
    # what stays folded: the two ways nobody uses to plan a flight
    assert 'id="tiles-text"' in folded.group(0) and 'id="lat-input"' in folded.group(0)


def test_shift_and_a_drag_choose_the_squares_of_a_rectangle() -> None:
    """Clicking every square of a route one by one was long (a user, 2026-09-22): Shift held, the
    mouse down and the pointer moved draws a rectangle, and every whole square in it is chosen as
    it goes; drawn back, they leave again. Started on a square already chosen, the rectangle takes
    squares out instead. A Shift+click that does not move still puts a point of a free shape, and
    the click that ends the drag chooses nothing more."""
    js = (UI / "map.js").read_text(encoding="utf-8")
    press = re.search(r"\n  function sweepPress\(ev, latlng\) \{.*?\n  \}\n", js, re.S)
    assert press is not None
    assert "map.getZoom() < GRID_MIN_ZOOM" in press.group(0)
    assert "map.dragging.disable()" in press.group(0)  # the map would follow the pointer
    assert "ctx.tiles().includes(under)" in press.group(0)  # from a chosen square: it takes out
    to = re.search(r"\n  function sweepTo\(latlng, ev\) \{.*?\n  \}\n", js, re.S)
    assert to is not None
    assert "SWEEP_MOVE_PX" in to.group(0)  # a click that does not move is still a zone's point
    assert "ctx.sweep.to(tilesInBounds(box))" in to.group(0)
    assert "drawSweepBox(sweep.from, latlng, sweep.removing)" in to.group(0)
    end_fn = re.search(r"\n  function sweepEnd\(\) \{.*?\n  \}\n", js, re.S)
    assert end_fn is not None
    assert "map?.dragging.enable()" in end_fn.group(0) and "skipClick = true" in end_fn.group(0)
    assert 'if (removing) ctx.toast(t("map.swept_out", { n }));' in end_fn.group(0)
    click = re.search(r"\n  function onMapClick\(ev\) \{.*?\n  \}\n", js, re.S)
    assert click is not None and "if (skipClick)" in click.group(0)
    assert 'el.addEventListener("mousedown"' in js
    assert 'document.addEventListener("mouseup", sweepEnd)' in js
    help_list = re.search(r"\n  function renderHelp\(\) \{.*?\n  \}\n", js, re.S)
    assert help_list is not None and 't("map.sc_sweep", { shift })' in help_list.group(0)

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    taking = _function_body(app_js, "sweepTo")
    assert "sweepBefore.filter((name) => !inside.has(name))" in taking  # the rectangle, taken out
    assert "wanted.length >= MAX_BUILD_TILES" in taking  # never more than one build takes
    assert "tilesInBuilds(activeJobs())" in taking  # a tile being built is not chosen again
    assert "sweep: { start: sweepStart, to: sweepTo, end: sweepEnd }," in app_js
    css = (UI / "styles.css").read_text(encoding="utf-8")
    assert ".plan-map .osxp-sweep-box {" in css and ".osxp-sweep-box.is-removing {" in css
    for lang in ("en", "fr"):
        texts = _node_json(
            "i18n.js",
            f'(globalThis.document = {{documentElement: {{}}}}, m.setLanguage("{lang}"),'
            ' [m.t("map.sc_sweep", {shift: "Shift"}), m.t("map.swept", {n: 7}),'
            ' m.t("map.swept_out", {n: 3}), m.t("map.swept_max", {n: 500})])',
        )
        assert "Shift" in texts[0] and "7" in texts[1] and "3" in texts[2] and "500" in texts[3]


def test_the_routes_ends_are_marked_on_the_map() -> None:
    """On a flight plan across Europe, 28 squares of one blue left nothing to tell the departure
    and the arrival from the way between them (a user, 2026-09-22): their squares take the route's
    own colour, and the legend says so while a route is drawn."""
    js = (UI / "map.js").read_text(encoding="utf-8")
    grid = re.search(r"\n  function renderGrid\(\) \{.*?\n  \}\n", js, re.S)
    assert grid is not None
    drawn = grid.group(0)
    assert "ctx.routeEnds?.()" in drawn
    assert 'routeEnds.has(name) ? " is-route-end" : ""' in drawn
    legend = re.search(r"\n  function renderLegend\(\) \{.*?\n  \}\n", js, re.S)
    assert legend is not None and 't("map.legend_route_ends")' in legend.group(0)
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "routeEnds: () => new Set(state.route ? routeEndTiles() : [])" in app_js
    css = (UI / "styles.css").read_text(encoding="utf-8")
    assert "--route: #e0572f;" in css  # map.js draws the line with the same colour
    assert ".plan-map .osxp-tile-selected.is-route-end { stroke: var(--route); }" in css
    assert ".legend-route-end { border: 2px solid var(--route); }" in css
    for lang in ("en", "fr"):
        text = _node_json(
            "i18n.js",
            f'(globalThis.document = {{documentElement: {{}}}}, m.setLanguage("{lang}"),'
            ' m.t("map.legend_route_ends"))',
        )
        assert text and "{" not in text, text


def test_the_page_stays_where_it_was_when_squares_are_added() -> None:
    """The squares chosen sit at the top of step 1, so choosing more pushes the buttons under them
    down. Chrome puts the scroll back by itself, WebKit does not, and the app's own window looked
    as if it had scrolled up under the button just pressed (a user, 2026-09-22)."""
    js = (UI / "app.js").read_text(encoding="utf-8")
    helper = re.search(r"\nfunction keepInPlace\(el, fn\) \{.*?\n\}\n", js, re.S)
    assert helper is not None
    body = helper.group(0)
    assert "getBoundingClientRect" in body and "window.scrollBy(0, moved)" in body
    for name in ("addRouteTiles", "addTilesFromIcao", "addTileFromLatLon", "addTilesFromText"):
        assert "keepInPlace(" in _function_body(js, name), name


def test_a_flight_plan_gives_its_ends_and_its_route_two_levels() -> None:
    """A pilot wanted his departure and arrival squares sharper than the squares along the route
    (2026-09-22). Each group carries its level, chosen beside its button; step 1's list shows the
    level of the chosen squares, or "Several levels" when they differ, and every chip then says
    its own. A level chosen in that list is every chosen square's again."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    for element in ('id="route-ends-zl"', 'id="route-all-zl"'):
        assert element in html and 'class="route-zl" hidden' in html
    js = (UI / "app.js").read_text(encoding="utf-8")
    # the two groups are disjoint: the ends are not built twice at the route's level
    along = re.search(r"\nfunction routeAlongTiles\(\) \{.*?\n\}\n", js, re.S)
    assert along is not None and "!ends.has(name)" in along.group(0)
    # one rule: uniform squares carry nothing of their own, and the list holds the level
    rule = re.search(r"\nfunction normalizeLevels\(\) \{.*?\n\}\n", js, re.S)
    assert rule is not None
    assert "delete state.tileZl[name]" in rule.group(0)
    options = re.search(r"\nfunction renderZlOptions\(\) \{.*?\n\}\n", js, re.S)
    assert options is not None
    assert 't("plan.zl_several")' in options.group(0) and "disabled: true" in options.group(0)
    chips = re.search(r"\nfunction renderTiles\(\) \{.*?\n\}\n", js, re.S)
    assert chips is not None and "chosenLevels().length > 1" in chips.group(0)
    request = re.search(r"\nasync function planRequest\(\) \{.*?\n\}\n", js, re.S)
    assert request is not None and "tiles_zl: own" in request.group(0)
    # the squares along the route start at 14: flown over, not landed on (a user, 2026-09-22)
    assert "const ROUTE_ALONG_ZL = 14;" in js
    assert "Math.min(ROUTE_ALONG_ZL, top)" in js  # never above what the source offers
    levels = re.search(r"\nfunction renderRouteLevels\(maxZl, lat\) \{.*?\n\}\n", js, re.S)
    assert levels is not None
    assert 'id === "route-all-zl" ? routeAlongZl(maxZl) : routeEndsZl(maxZl)' in levels.group(0)
    click = js[js.index('$("route-all").addEventListener') :][:200]
    assert "routeAlongZl()" in click and "planZl()" not in click
    # the ends take what the pilot chose in step 1, not what the chosen squares happen to share:
    # the test that runs the two buttons is below (2026-09-23)
    ends = js[js.index('$("route-ends").addEventListener') :][:200]
    assert "routeEndsZl()" in ends and "planZl()" not in ends
    listed = js.index('$("zl-select").addEventListener("change"')
    assert js.index("state.tileZl = {};", listed) < js.index("planChanged();", listed)
    for lang, several in (("en", "Several levels"), ("fr", "Plusieurs niveaux")):
        texts = _node_json(
            "i18n.js",
            f'(globalThis.document = {{documentElement: {{}}}}, m.setLanguage("{lang}"),'
            ' [m.t("plan.zl_several"), m.t("plan.route_all", {n: 9}), m.t("plan.route_ends_zl")])',
        )
        assert texts[0] == several and "9" in texts[1] and texts[2]


def test_what_went_wrong_with_a_way_is_said_under_it() -> None:
    """A user pressed Draw and saw nothing happen: an empty line or an unknown code was said in
    step 3's box, far below the button (2026-09-22). The airport and the flight plan say it right
    under them, and the field's own example works in the mock."""
    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    at = [html.index(f'id="{name}"') for name in ("route-draw", "way-error", "route-found")]
    assert at == sorted(at)
    js = (UI / "app.js").read_text(encoding="utf-8")
    for name in ("addTilesFromIcao", "drawRoute", "routeFromSimbrief", "clearRoute"):
        body = re.search(rf"\n(?:async )?function {name}\(\) \{{.*?\n\}}\n", js, re.S)
        assert body is not None, name
        assert "showWayError(" in body.group(0) and "showPlanError(" not in body.group(0), name
    airports = json.loads((UI / "mock" / "airports.json").read_text(encoding="utf-8"))
    assert {"LSGG", "LFMN"} <= {a["icao"] for a in airports}  # the placeholder, "LSGG LFMN"


def test_the_legend_says_what_the_view_is_worth_in_a_builds_terms() -> None:
    """The map's zoom is the web-mercator level, so what a pilot sees while panning is what that
    level would put on the ground. They asked for it in those words: it "helps to get an impression
    of just how a given provider's imagery will look at the desired ortho ZL" (2026-09-24).

    Below the levels a build offers the name would only repeat the number, so the ground size
    alone is said.
    """
    said = _node_json(
        "map.js",
        "[[18, 46], [16, 46], [12, 46], [8, 46]].map(([z, lat]) => m.viewLabel(z, lat))",
    )
    assert said[0] == "Very sharp, about 40 cm per pixel \u00b7 ZL18"
    assert said[1] == "Standard, about 2 m per pixel \u00b7 ZL16"
    assert said[2] == "Very coarse, about 27 m per pixel \u00b7 ZL12"
    assert said[3] == "about 425 m per pixel \u00b7 ZL8", "no name below the levels a build offers"

    # past the provider's own ceiling the map enlarges what it already has, so the line stops
    # promising a sharpness no build can deliver and says what the provider really gives
    # (a user zoomed to ZL18 on EOX, which stops at ZL14, 2026-09-25)
    over = _node_json(
        "map.js",
        '[[18, 46, 14, "EOX"], [14, 46, 14, "EOX"], [18, 46, null, ""]]'
        ".map(([z, lat, cap, name]) => m.viewLabel(z, lat, cap, name))",
    )
    assert over[0] == "ZL18, enlarged. EOX goes no further than ZL14, about 7 m per pixel"
    assert over[1] == "Low, about 7 m per pixel \u00b7 ZL14", "at the ceiling nothing is enlarged"
    assert over[2] == "Very sharp, about 40 cm per pixel \u00b7 ZL18", "no ceiling, nothing to warn"

    # and the ceiling the line measures against is the one the base layer stops downloading at,
    # read from the same expression, so the warning cannot land on a different zoom
    caps = _node_json(
        "map.js",
        "[{ max_zl: 14 }, { max_zl: 22 }, { max_zl: 19 }, {}, null].map((p) => m.nativeCeiling(p))",
    )
    assert caps == [14, 19, 19, 19, 19], "a provider claiming more than the engine serves is capped"
    code_ = (UI / "map.js").read_text(encoding="utf-8")
    assert "maxNativeZoom: nativeCeiling(p)," in code_, "the layer reads it"
    assert "const ceiling = shown ? nativeCeiling(shown) : null;" in code_, "the legend reads it"
    assert "viewLabel(...viewNow())" in code_

    # and the legend puts it on the map, redrawn when the zoom or the latitude moves under it
    code = (UI / "map.js").read_text(encoding="utf-8")
    legend = code[code.index("function renderLegend()") : code.index("function bordersToggle()")]
    assert 'h("p", { class: "legend-view" }' in legend and "viewLabel(" in legend
    assert "clear(box).append(view, list)" in legend
    for event in ("moveend", "zoomend"):
        handler = code[code.index(f'm.on("{event}"') :]
        called = handler[: handler.index("});")].splitlines()
        assert any(line.strip().startswith("viewChanged();") for line in called), event
    pair = code[code.index("function viewChanged()") : code.index("function showNotice()")]
    assert "renderLegend();" in pair and 'setNotice("");' in pair

    # The source is chosen in step 1, not on the map, and the street map replaces it altogether:
    # both go through setBaseLayer, so the line is drawn again there. A user switched to EOX at
    # ZL18 and the line went on promising 40 cm per pixel over an enlarged ZL14 tile (2026-09-25).
    swap = code[code.index("function setBaseLayer(") :]
    swap = swap[: swap.index("\n  }\n")].splitlines()
    assert any(line.strip().startswith("viewChanged();") for line in swap), "setBaseLayer"
    tables = _i18n_tables()
    for lang in ("fr", "en"):
        assert "map.view" in tables[lang] and "map.view_coarse" in tables[lang]


def test_a_source_that_does_not_cover_the_view_says_so() -> None:
    """A source of one country answers a plain white (PDOK) or black (Luxembourg) image outside
    its own, with a 200: nothing fails, so the map went blank without a word (a user, 2026-09-25).

    The question asked is the engine's own, `Provider.covers` on the square in the middle of the
    screen. Weighing the whole visible rectangle made the word come and go with the zoom, since a
    wide view over Paris still touches the Netherlands: PDOK said nothing until ZL8, over Lyon
    nothing until ZL6, and a small pan near the threshold turned it on and off. The middle of the
    screen answers the same at every zoom, which is what the user asked for.
    """
    nl = '{"extent_bounds": [3.06, 50.72, 7.26, 53.76]}'  # the registry's own, Netherlands
    said = _node_json(
        "sources.js",
        f'["+48+002", "+45+004", "+43+001", "+52+004", "+50+006"]'
        f".map((name) => m.sourceCovers({nl}, name))",
    )
    assert said == [False, False, False, True, True], "Paris, Lyon, Toulouse no; Amsterdam yes"
    # and the last one straddles the border, where a build would have imagery for part of it
    assert _node_json("sources.js", 'm.sourceCovers({"extent_bounds": null}, "+45+004")') is True

    # the map asks it about the middle of the screen, so the answer cannot change with the zoom
    code = (UI / "map.js").read_text(encoding="utf-8")
    standing = code[code.index("function standingNotice()") : code.index("let happened")]
    assert "const c = map.getCenter();" in standing
    assert "sourceCovers(p, tileName(c.lat, wrapLon(c.lng)))" in standing
    assert "getBounds" not in standing, "not the visible rectangle: that is what came and went"
    assert "ctx.mock || (zs.street.wanted && !zs.street.failed)" in standing, (
        "not a source's imagery"
    )

    # what just happened wins over what is standing, and a blank tile is not news
    shown = code[code.index("function showNotice()") : code.index("function setNotice(")]
    assert "const text = happened || standingNotice();" in shown
    layer = code[
        code.index("function providerLayer(") : code.index("// -- the colours, live on the map")
    ]
    assert 'setNotice(""); // a blank tile is a tile that came; the standing word stays' in layer
    assert "showNotice(); // in the language now chosen" in code, "a language change re-words it"
    tables = _i18n_tables()
    for lang in ("fr", "en"):
        assert "map.base_outside" in tables[lang]


def test_a_view_without_imagery_says_so_however_it_was_reached() -> None:
    """Esri Clarity serves ZL19 over New York and 404s over France: how deep a source goes is not
    one number. A user zoomed from ZL18, where it had imagery, to ZL19, where it has none there,
    and the map emptied without a word (2026-09-25).

    The tiles counted are the view's, not the layer's life, so a view that had imagery a moment
    ago does not vouch for the one on screen. One tile that does not make it still says nothing:
    the next draw usually fixes it.
    """
    said = _node_json(
        "map.js",
        """(() => {
          const out = [];
          const tally = m.tileTally();
          // a view where everything arrives
          for (let i = 0; i < 8; i++) tally.came();
          out.push([1, false]);
          // zoomed deeper: this view is a different one, and nothing comes
          tally.starting();
          out.push([2, [1, 2, 3, 4, 5, 6].map(() => tally.missed())]);
          // back to a view that has imagery: it speaks again only if that one empties
          tally.starting();
          tally.came();
          out.push([3, [1, 2, 3, 4, 5, 6, 7].map(() => tally.missed())]);
          // and one tile lost out of a full view says nothing
          tally.starting();
          out.push([4, [1, 2].map(() => tally.missed())]);
          return out;
        })()""",
    )
    empty = said[1][1]
    assert empty == [False] * 5 + [True], "a view that empties says so, once enough tiles failed"
    assert said[2][1] == [False] * 7, "a view that has imagery never says it has none"
    assert said[3][1] == [False, False], "one tile lost is not a view without imagery"

    # and the layer hands Leaflet's three moments to it, `loading` being the start of a view
    code = (UI / "map.js").read_text(encoding="utf-8")
    layer = code[
        code.index("function providerLayer(") : code.index("// -- the colours, live on the map")
    ]
    assert 'layer.on("loading", () => tally.starting());' in layer, "each view starts its own count"
    assert "tally.came();" in layer and "if (tally.missed() && layer === base) {" in layer
    assert "counts" not in layer, "one tally, not a second count beside it"


def test_the_map_goes_to_the_airport_chosen() -> None:
    """A code says nothing about where its airport is: a user chose one and the map stayed where
    it was, so the squares just added were somewhere off the screen (2026-09-24). Choosing from
    the list and typing the code in full both bring the map over it, keeping the zoom the user
    set but never leaving it so far out that the airport and its squares are not drawn."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    body = re.search(r"\n    goTo\(lat, lon\) \{.*?\n    \},\n", map_js, re.S)
    assert body is not None, "map.js offers goTo"
    go = body.group(0)
    assert "if (!map || !Number.isFinite(lat) || !Number.isFinite(lon)) return;" in go
    # a floor, never a setting: someone close over one airfield stays that close over the next
    assert "map.setView([lat, lon], Math.max(map.getZoom(), AIRPORTS_MIN_ZOOM));" in go
    js = (UI / "app.js").read_text(encoding="utf-8")
    for name in ("pickAirport", "addTilesFromIcao"):
        assert "planMap?.goTo(" in _function_body(js, name), name


def test_the_squares_a_route_crosses_and_its_length() -> None:
    """``tilesAlong`` samples every leg well under the one degree a square measures, so a square
    the line only clips is still counted; ``routeLength`` measures on the sphere."""
    calls = ", ".join(
        [
            "m.tilesAlong([{lat: 46.24, lon: 6.11}, {lat: 43.66, lon: 7.21}])",
            "Math.round(m.routeLength([{lat: 46.24, lon: 6.11}, {lat: 43.66, lon: 7.21}]))",
            # the short way round the antimeridian, as an aircraft flies it
            "m.tilesAlong([{lat: 0, lon: 179.5}, {lat: 0, lon: -179.5}])",
            "m.tilesAlong([{lat: 46.24, lon: 6.11}])",
        ]
    )
    geneva_nice, km, dateline, alone = _node_json("geo.js", f"[{calls}]")
    assert geneva_nice == ["+46+006", "+45+006", "+44+006", "+44+007", "+43+007"]
    assert 295 <= km <= 305  # 299 km on the great circle
    assert dateline == ["+00+179", "+00-180"]  # two squares, not the whole world
    assert alone == ["+46+006"]


def test_a_route_is_read_from_what_a_pilot_types() -> None:
    """Spaces, commas or arrows between the codes, and anything too short is not an airport."""
    calls = ", ".join(
        [
            'm.routeCodes("LSGG LFMN")',
            'm.routeCodes("lsgg, lfmn")',
            'm.routeCodes("LSGG -> LFMN -> LIRF")',
            'm.routeCodes("LSGG DCT MOLUS DCT LFMN")',
            'm.routeCodes("")',
        ]
    )
    plain, commas, arrows, with_fixes, empty = _node_json("app.js", f"[{calls}]")
    assert plain == commas == ["LSGG", "LFMN"]
    assert arrows == ["LSGG", "LFMN", "LIRF"]
    # a pasted route keeps its four-letter tokens; the engine leaves out what is not an airport
    assert with_fixes == ["LSGG", "DCT", "DCT", "LFMN"]
    assert empty == []


# -- the levels of a flight plan, run rather than read -----------------------------------------


def _run_in_node(script: str) -> str:
    """Run a piece of the page in node, so a rule about its state is checked and not merely read.

    The tests around it assert that the source says a thing; this one asks the source to do it,
    which is what an ordering bug needs (2026-09-23).
    """
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.mjs"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([node, str(path)], capture_output=True, text=True, check=False)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()


def test_the_two_ends_of_a_route_keep_step_ones_level_whichever_button_is_pressed_first() -> None:
    """Pressing "along the route" first put every chosen square at ZL14, so step 1's list followed
    them there, and "departure and arrival" then took ZL14 from it: the ends came out blurred and
    step 1 was quietly rewritten, all depending on the order the two buttons were pressed in
    (2026-09-23). The bug lived between two functions that are each correct on their own, which is
    why reading them could not find it."""
    app_js = (ui_dir() / "app.js").read_text(encoding="utf-8")
    pieces = [
        _function_body(app_js, name)
        for name in (
            "planZl",
            "routeAlongZl",
            "routeEndsZl",
            "tileZl",
            "chosenLevels",
            "normalizeLevels",
        )
    ]
    along = re.search(r"\nconst ROUTE_ALONG_ZL = \d+;", app_js)
    assert along is not None
    script = (
        "const state = { tiles: [], tileZl: {}, planZl: 16, zlChosen: 16, settings: null };\n"
        "const sourceMaxZl = () => 18;\n"
        + along.group(0)
        + "\n".join(pieces)
        + """
function add(names, zl) {                    // what addRouteTiles does to the levels
  for (const n of names) {
    if (!state.tiles.includes(n)) state.tiles.push(n);
    state.tileZl[n] = zl;
  }
  normalizeLevels();
}
function press(order) {
  state.tiles = []; state.tileZl = {}; state.planZl = 16; state.zlChosen = 16;
  for (const which of order) {
    if (which === "ends") add(["+46+006", "+43+007"], routeEndsZl());
    else add(["+45+006", "+44+006"], routeAlongZl());
  }
  return { ends: tileZl("+46+006"), along: tileZl("+45+006"), step1: state.planZl };
}
console.log(JSON.stringify({ a: press(["ends", "along"]), b: press(["along", "ends"]) }));
"""
    )
    got = json.loads(_run_in_node(script))
    assert got["a"]["ends"] == 16 and got["a"]["along"] == 14
    assert got["b"]["ends"] == 16, "the ends keep step 1's level whichever button came first"
    assert got["b"]["along"] == 14


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_route_over_the_pacific_is_drawn_where_the_map_can_go() -> None:
    """The line's longitude runs on past 180, which is how a leg goes the short way; drawn there,
    Tokyo sat at -220, outside the bounds the map pans to, so its ring could not be reached and
    the view could not be fitted to it (found in review, 2026-09-23). The line is cut at the
    meridian and continues on the other side, the way a chart draws it."""
    ksfo_rjtt = [{"lat": 37.6, "lon": -122.4}, {"lat": 35.8, "lon": 140.4}]
    answer = _node_json("map.js", f"m.routePieces({json.dumps(ksfo_rjtt)})")
    assert len(answer["pieces"]) == 2, "it is cut where it crosses the meridian"
    assert [round(p[1]) for p in answer["at"]] == [-122, 140]
    assert all(-180 <= lon <= 180 for _lat, lon in answer["at"])
    for piece in answer["pieces"]:
        assert all(-180 <= lon <= 180 for _lat, lon in piece)
    # it leaves by one edge of the meridian and comes back by the other, at the same latitude
    leaves, comes_back = answer["pieces"][0][-1], answer["pieces"][1][0]
    assert {leaves[1], comes_back[1]} == {-180, 180}
    assert leaves[0] == comes_back[0]

    # and the unrolled line still goes the short way, which is what the buttons count
    line = _node_json("map.js", f"m.routeLine({json.dumps(ksfo_rjtt)})")
    assert [round(p[1]) for p in line] == [-122, -220]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_route_that_goes_round_the_world_keeps_every_leg_the_short_way() -> None:
    """One correction of a turn is not enough once the longitude has run on: from Geneva east to
    Tokyo the accumulated longitude reaches 499, and the next leg was then taken the long way."""
    round_world = [
        {"lat": 46, "lon": 6},
        {"lat": 40, "lon": 116},
        {"lat": 21, "lon": -158},
        {"lat": 37, "lon": -122},
        {"lat": 51, "lon": 0},
        {"lat": 35, "lon": 139},
        {"lat": 46, "lon": 6},
    ]
    line = _node_json("map.js", f"m.routeLine({json.dumps(round_world)})")
    steps = [round(b[1] - a[1]) for a, b in itertools.pairwise(line)]
    assert all(abs(step) <= 180 for step in steps), steps
    answer = _node_json("map.js", f"m.routePieces({json.dumps(round_world)})")
    assert all(-180 <= lon <= 180 for _lat, lon in answer["at"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_selection_full_of_squares_says_so_in_both_languages() -> None:
    """The cap lived in the mouse sweep alone: a flight plan across a continent added its nine
    hundred squares, and the estimate then refused the whole selection with no way forward but
    taking four hundred out by hand (found in review, 2026-09-23)."""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert 't("plan.tiles_capped"' in _function_body(app_js, "sayTilesInBuild")
    assert 't("plan.tiles_capped"' in _function_body(app_js, "toggleTile")
    tables = _i18n_tables()
    for lang in ("fr", "en"):
        words = tables[lang]["plan.tiles_capped"]
        assert "{max}" in words and "{n}" in words, lang


def test_the_flight_plan_is_not_offered_in_this_release() -> None:
    """Choosing squares along a route is a feature of its own, and it arrived in the same
    release as three faults users are waiting on. It waits for 0.1.15: its code, its tests and
    its words stay, the page does not offer it, and one line turns it back on (2026-09-23)."""
    release_js = (UI / "release.js").read_text(encoding="utf-8")
    assert "export const FLIGHT_PLAN = false;" in release_js, "the switch is off for this release"
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert 'import { FLIGHT_PLAN } from "./release.js";' in app_js, "and there is one of it"

    # Settings asked for a SimBrief name and its help pointed at a Plan button this release does
    # not have (found in review, 2026-09-23)
    settings_js = (UI / "settings.js").read_text(encoding="utf-8")
    assert "if (FLIGHT_PLAN) box.append(simbriefQuestion(view));" in settings_js
    assert 'import { FLIGHT_PLAN } from "./release.js";' in settings_js

    wiring = _function_body(app_js, "wireFlightPlan")
    assert '$("plan-route").hidden = !FLIGHT_PLAN;' in wiring
    assert "if (!FLIGHT_PLAN) return;" in wiring
    # every listener of the route lives behind that guard, and nowhere else
    for control in (
        "route-draw",
        "route-input",
        "route-ends",
        "route-all",
        "route-simbrief",
        "route-clear",
    ):
        assert f'$("{control}")' in wiring, control
        assert app_js.count(f'$("{control}").addEventListener') == wiring.count(
            f'$("{control}").addEventListener'
        ), control
    assert "if (FLIGHT_PLAN) restoreRoute();" in app_js, "no route comes back from the last visit"

    html = (UI / INDEX_FILE).read_text(encoding="utf-8")
    assert '<div id="plan-route">' in html
    # the error line under the airport field is shared, so it stays outside the box
    assert html.index('id="plan-route"') < html.index('id="route-input"')
    assert html.index('id="way-error"') > html.index("</div>", html.index('id="route-input"'))

    notes = (UI / ".." / ".." / ".." / "docs" / "releases" / "0.1.14.md").resolve()
    assert "SimBrief" not in notes.read_text(encoding="utf-8"), "and the notes do not promise it"


def test_the_map_is_told_when_its_box_changes() -> None:
    """Leaflet works out where a click landed from the size it last measured, and it measures
    only when told. Nothing told it: `invalidateSize` was called when the map was shown and
    never again, so a window resized, a panel opened or the browser's own zoom changed put every
    click somewhere else than where the pointer was (a user drawing a shape, 2026-09-24)."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    watched = (
        "new ResizeObserver(() => m.invalidateSize({ animate: false, pan: false })).observe(el)"
    )
    assert watched in map_js, "the map's own element is watched"
    # and it is set up where the map is made, on that map's element
    assert map_js.index("function createMap") < map_js.index(watched)
    assert map_js.index(watched) < map_js.index("function refreshAirports")


def test_a_point_put_on_the_texture_grid_says_so_and_how_far() -> None:
    """Ctrl+Shift+click puts a point on the texture grid rather than under the pointer, and a
    texture is about seven kilometres a side at ZL16: a user clicked in the middle of his
    village and watched two points appear at the far corners of the map, with nothing saying
    why (2026-09-24). The shortcut list had always said it; the moment it happens had not."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    start = map_js.index("function draftPoint(")
    body = map_js[start : map_js.index("\n  function ", start + 1)]
    assert "snapToTextureCorner" in body and 't("draw.snapped"' in body
    assert "routeLength(" in body, "it says how far the point moved"

    tables = _i18n_tables()
    for lang in ("fr", "en"):
        words = tables[lang]["draw.snapped"]
        for field in ("{mod}", "{shift}", "{km}"):
            assert field in words, (lang, field)
        # and it says what to do instead, since the two ways differ only by the modifier
        assert words.count("{mod}") == 2, lang


def test_the_texture_grid_is_drawn_while_a_zone_is_drawn() -> None:
    """A zone takes every texture its outline touches, whole: a square zone drawn by hand costs
    four to eleven textures more than the same zone on the grid, at about 11 MB and 256 pieces
    each (measured 2026-09-24). Ctrl+Shift+click puts a point on that grid, and seeing it is
    what makes the shortcut worth using: a user asked what two ways of placing a point were
    for."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    start = map_js.index("function renderTextureGrid(")
    body = map_js[start : map_js.index("\n  function ", start + 1)]
    # while a zone is under way, and while the keys that snap to the grid are held: the first
    # point is the one that starts the shape, so at that moment there is no zone in progress and
    # the grid appeared only after the click that needed it (a user, 2026-09-24)
    assert "if (!zs.draft && !snapKeysHeld) return;" in body
    assert "TEXTURE_GRID_MIN_PX" in body, "and only while the squares can be aimed at"
    assert "zs.nextZl" in body, "at the level the zone will take, not the map's"
    assert "map.getBounds()" in body, "the view only, or it is thousands of lines"

    # redrawn when the view moves, and when the draft changes
    assert "renderTextureGrid();  // the view moved" in map_js
    assert map_js.index("renderTextureGrid();\n    layers.draft.clearLayers();") > 0

    keys = map_js[map_js.index("function onSnapKeys(") :]
    assert "(ev.ctrlKey || ev.metaKey) && ev.shiftKey" in keys[:400]
    for listener in ('"keydown", onSnapKeys', '"keyup", onSnapKeys', '"blur", forgetSnapKeys'):
        assert listener in map_js, listener

    css = (UI / "styles.css").read_text(encoding="utf-8")
    assert ".osxp-texture-grid" in css


def test_the_snap_shortcut_is_not_taken_for_a_sweep() -> None:
    """Shift with the mouse held sweeps squares; Ctrl (or Cmd) with Shift puts a point on the
    texture grid. The press handler looked only at Shift, so the first point of a shape was
    swallowed whenever the pointer moved four pixels while the two keys were held, and squares
    were swept instead. Only the first: from the second point on a zone is under way and the
    sweep steps aside for it (a user, 2026-09-24)."""
    map_js = (UI / "map.js").read_text(encoding="utf-8")
    start = map_js.index('el.addEventListener("mousedown"')
    press = map_js[start : map_js.index("});", start)]
    assert "if (ev.ctrlKey || ev.metaKey) return;" in press
    assert press.index("ev.ctrlKey") < press.index("sweepPress("), "before the sweep is started"

    # and the end of a sweep no longer wipes a draft it never drew
    end = map_js[map_js.index("function sweepEnd(") :]
    end = end[: end.index("\n  }")]
    assert end.index("if (!started) return;") < end.index("layers.draft.clearLayers();")
