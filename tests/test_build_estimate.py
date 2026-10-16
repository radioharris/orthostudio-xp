# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``osxp plan`` offline (spec ``pipeline-build.md`` 5): node statuses, texture upper bound,
requests from the chunk store, costs, disk, text rendering. No network, no Ortho4XP."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from shapely import geometry

from orthostudio.airports import AirportIndex
from orthostudio.airports_vec.artefact import write_airports
from orthostudio.airports_vec.model import Airport as VectorAirport
from orthostudio.airports_vec.model import AirportSet
from orthostudio.dsf.zones import M_TO_LAT, m_to_lon
from orthostudio.estimate import (
    BC1_BYTES,
    DEFAULT_KB_PER_REQUEST,
    DEFAULT_REQ_PER_S,
    RUNWAY_MARGIN_M,
    ProbeResult,
    _airport_zones,
    estimate,
    render_text,
)
from orthostudio.graph import Store
from orthostudio.imagery.chunks import ChunkContainer, ChunkEntry, ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId, textures_covering
from orthostudio.imagery.providers import Provider, cache_name, load_registry
from orthostudio.model import TileRef
from orthostudio.pipeline.build import BuildEnv, BuildSpec, declare, make_scheduler
from orthostudio.sched import PlanEntry

T = TileRef(43, 5)


@pytest.fixture
def env(tmp_path: Path) -> BuildEnv:
    gs = tmp_path / "Global Scenery"
    p = gs / T.dsf_relpath
    p.parent.mkdir(parents=True)
    p.write_bytes(b"XPLNEDSF")
    return BuildEnv(
        store=Store(tmp_path / "store", fsync=False),
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=gs,
        dsftool=None,
        registry=load_registry(),
        workers=4,
        library_path=None,
    )


def _spec(tmp_path: Path, zl: int = 14) -> BuildSpec:
    # one output directory per level: a batch refuses two levels of a tile in one --out
    return BuildSpec(
        tile=T, provider="BI", zl=zl, out_dir=tmp_path / f"out{zl}", store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks", workdir=tmp_path / "work",
    )  # fmt: skip


def test_offline_estimate_on_an_empty_store(tmp_path: Path, env: BuildEnv) -> None:
    est = estimate([_spec(tmp_path)], env=env)
    (t,) = est.tiles
    all_textures = textures_covering(44, 5, 43, 6, 14, "BI")
    assert t.textures_total == len(all_textures) and not t.textures_exact
    assert t.textures_masked is None
    assert t.requests == 256 * len(all_textures)
    assert t.download_mb == pytest.approx(t.requests * DEFAULT_KB_PER_REQUEST / 1000)
    assert t.network_s == pytest.approx(t.requests / DEFAULT_REQ_PER_S)
    assert t.dds_gb == pytest.approx(len(all_textures) * BC1_BYTES / 1e9)
    statuses = {n.role: n.status for n in t.nodes}
    assert statuses["dem"] == "build"  # no inputs: its key is known
    assert statuses["vectors"] == "unknown"  # below the elevation, still to build
    assert statuses["xp12"] == "build" and statuses["overlay"] == "build"
    assert statuses["mesh"] == "unknown" and statuses["dsf"] == "unknown"
    assert t.builds == len(t.nodes) and t.hits == 0
    assert t.compute_s > 0 and est.disk_needed_gb > t.dds_gb and est.workers == 4
    assert est.probe is None and est.to_dict()["tiles"][0]["textures"]["exact"] is False
    text = render_text(est)
    assert "+43+005 BI14" in text and "upper bound" in text and "network: your line" in text
    assert "compute: your computer" in text and "disk:" in text


def test_requests_count_only_what_the_chunk_store_lacks(tmp_path: Path, env: BuildEnv) -> None:
    store = ChunkStore(tmp_path / "chunks", fsync=False)
    textures = textures_covering(44, 5, 43, 6, 14, "BI")
    complete = ChunkContainer(
        ChunkEntry(ChunkStatus.OK, b"jpeg", "image/jpeg", 0) for _ in range(256)
    )
    store.write(textures[0], complete)
    partial = ChunkContainer(
        ChunkEntry(ChunkStatus.OK if i >= 10 else ChunkStatus.ERROR, b"", "image/jpeg", 0)
        for i in range(256)
    )
    store.write(textures[1], partial)
    est = estimate([_spec(tmp_path)], env=env)
    (t,) = est.tiles
    assert t.requests == 256 * (len(textures) - 2) + 10


def test_the_estimate_looks_where_a_build_would_for_a_source_of_the_users(
    tmp_path: Path, env: BuildEnv
) -> None:
    """A source of the user's is kept under its address as well as its name. The estimate reads
    the folder a build would, so images of an address it no longer has are not counted as there,
    and the ones of its present address are."""
    mine = Provider(
        code="Mine", url_template="https://mine.example/{zoom}/{x}/{y}.jpg", max_zl=19, custom=True
    )
    env = dataclasses.replace(env, registry={**env.registry, "Mine": mine})
    spec = dataclasses.replace(_spec(tmp_path), provider="Mine")
    textures = textures_covering(44, 5, 43, 6, 14, "Mine")
    complete = ChunkContainer(
        ChunkEntry(ChunkStatus.OK, b"jpeg", "image/jpeg", 0) for _ in range(256)
    )
    ChunkStore(tmp_path / "chunks", fsync=False).write(textures[0], complete)  # its name alone
    (t,) = estimate([spec], env=env).tiles
    assert t.requests == 256 * len(textures), "an address it no longer has is not counted"
    here = ChunkStore(tmp_path / "chunks", fsync=False, folders={"Mine": cache_name(mine)})
    here.write(textures[0], complete)
    (t,) = estimate([spec], env=env).tiles
    assert t.requests == 256 * (len(textures) - 1)


def test_probe_result_and_network_time(tmp_path: Path, env: BuildEnv) -> None:
    probe = ProbeResult(requests=20, seconds=0.5, bytes=20 * 15_000, errors=0, in_flight=100)
    assert probe.req_per_s == 40 and probe.kb_per_request == 15 and probe.mb_per_s == 0.6
    assert probe.latency_s == 0.5 and probe.throughput == 200  # 100 in flight / 0.5 s
    est = estimate([_spec(tmp_path)], env=env, probe=probe)
    (t,) = est.tiles
    assert est.req_per_s == 200 and est.kb_per_request == 15
    assert t.network_s == pytest.approx(t.requests / 200)
    assert t.download_mb == pytest.approx(t.requests * 15 / 1000)
    assert est.to_dict()["probe"]["throughput_req_per_s"] == 200.0
    assert "probe: 500 ms latency" in render_text(est)
    slow = ProbeResult(requests=20, seconds=4.0, bytes=1, errors=0, in_flight=10)
    assert slow.throughput == 5  # never below the probe's own rate
    # a server that limits itself: never above its own measured rate (Esri Clarity, 2026-09-15)
    clarity = ProbeResult(requests=20, seconds=0.3, bytes=1, errors=0, in_flight=192,
                          server_req_per_s=522.0)  # fmt: skip
    assert clarity.throughput == 522.0


def test_two_specs_share_the_scheduler_plan(tmp_path: Path, env: BuildEnv) -> None:
    est = estimate([_spec(tmp_path, 14), _spec(tmp_path, 15)], env=env)
    assert [t.zl for t in est.tiles] == [14, 15]
    assert est.tiles[1].textures_total == len(textures_covering(44, 5, 43, 6, 15, "BI"))
    assert est.compute_s == pytest.approx(sum(t.compute_s for t in est.tiles))


# -- the airports a build makes sharper --------------------------------------------------------

_APT = (
    "A\n1200 by hand\n\n"
    "1     74 0 0 LFML Marseille Provence\n"
    "1302 icao_code LFML\n1302 datum_lat 43.436666667\n1302 datum_lon 5.215\n"
    "100 45.11 1 0 0.00 0 0 0 13L 43.4300 5.2100 0 0 1 0 0 0 31R 43.4500 5.2300 0 0 1 0 0 0\n\n"
    "1    500 0 0 XLF001 A strip without a code\n"
    "100 20.00 3 0 0.00 0 0 0 09 43.7000 5.7000 0 0 1 0 0 0 27 43.7000 5.7100 0 0 1 0 0 0\n\n"
    "99\n"
)


@pytest.fixture
def xplane_list(tmp_path: Path) -> AirportIndex:
    apt = tmp_path / "apt.dat"
    apt.write_text(_APT, encoding="utf-8")
    index = AirportIndex(tmp_path / "airports.sqlite")
    index.build(apt)
    return index


def _sharper(tmp_path: Path, mode: str, zl: int = 15) -> BuildSpec:
    return dataclasses.replace(
        _spec(tmp_path, zl),
        config={"cover_airports_with_highres": mode, "cover_zl": 18, "cover_extent": 1.0},
    )


def _around(box: tuple[float, float, float, float], metres: float) -> set[TextureId]:
    """The ZL18 textures of a ``(south, west, north, east)`` box widened by ``metres``."""
    south, west, north, east = box
    dx, dy = metres * m_to_lon(T.lat), metres * M_TO_LAT
    return set(textures_covering(north + dy, west - dx, south - dy, east + dx, 18, "BI"))


def test_the_airports_made_sharper_count_before_the_first_build(
    tmp_path: Path, env: BuildEnv, xplane_list: AirportIndex
) -> None:
    """A tile never built planned its own textures alone: 54 announced, 114 built with its
    airports at ZL18 (2026-10-15). X-Plane's list places them by their runways, widened by the
    margin and by ``cover_extent``, as the build widens their outline."""
    plain = estimate([_spec(tmp_path, 15)], env=env, airports=xplane_list).tiles[0]
    est = estimate([_sharper(tmp_path, "ICAO")], env=env, airports=xplane_list)
    (t,) = est.tiles
    lfml = _around((43.43, 5.21, 43.45, 5.23), RUNWAY_MARGIN_M + 1000)
    assert t.textures_airports == len(lfml) > 0 and plain.textures_airports == 0
    assert t.textures_total == plain.textures_total + len(lfml)
    assert t.requests == plain.requests + 256 * len(lfml)  # downloads and disk follow
    assert any("X-Plane's airport list" in note for note in t.notes)
    assert f"{len(lfml)} for the airports" in render_text(est)
    # every airport when the setting takes them all, the strip without a code too
    (every,) = estimate([_sharper(tmp_path, "True")], env=env, airports=xplane_list).tiles
    strip = _around((43.70, 5.70, 43.70, 5.71), RUNWAY_MARGIN_M + 1000)
    assert every.textures_airports == len(lfml | strip)
    # none when the setting is off, or when neither the map data nor X-Plane's list is there
    (off,) = estimate([_sharper(tmp_path, "False")], env=env, airports=xplane_list).tiles
    (blind,) = estimate([_sharper(tmp_path, "ICAO")], env=env).tiles
    assert off.textures_airports == blind.textures_airports == 0
    assert blind.textures_total == plain.textures_total


def test_the_map_data_in_the_store_gives_the_airports_outline(
    tmp_path: Path, env: BuildEnv, xplane_list: AirportIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile whose map data is in the store, built before at another level, is planned from its
    airports' own outline (what the build reads), not from X-Plane's runways."""
    spec = _sharper(tmp_path, "ICAO", zl=16)
    (nodes,) = declare([spec], make_scheduler(env.store, env, cpu_in_threads=True), env,
                       planning=True)  # fmt: skip
    record = tmp_path / "vectors"
    record.mkdir()
    airports = AirportSet()
    outline = geometry.MultiPolygon([geometry.box(0.1, 0.1, 0.2, 0.2)])
    airports.add(VectorAirport("LFXX", "icao", "Test field", (5.15, 43.15), boundary=outline))
    write_airports(record, airports, T)
    monkeypatch.setattr(env.store, "path", lambda key: record)
    entries = {nodes.vectors.id: PlanEntry(nodes.vectors.id, "k", "hit", 0.0)}
    zones, source = _airport_zones(env, nodes, entries, xplane_list)
    assert source == "map" and len(zones) == 1
    ring, level, provider = zones[0]
    dx, dy = 1000 * m_to_lon(T.lat), 1000 * M_TO_LAT
    assert (level, provider) == (18, "BI")
    assert ring == pytest.approx([43.1 - dy, 5.1 - dx, 43.1 - dy, 5.2 + dx,
                                  43.2 + dy, 5.2 + dx, 43.2 + dy, 5.1 - dx])  # fmt: skip
    # map data holding no airport: none, X-Plane's list is not asked
    monkeypatch.setattr(env.store, "path", lambda key: tmp_path / "no-airport")
    assert _airport_zones(env, nodes, entries, xplane_list) == ([], "map")
    # a record that cannot be read: X-Plane's list answers instead
    (record / "airports.npz").write_bytes(b"broken")
    monkeypatch.setattr(env.store, "path", lambda key: record)
    zones, source = _airport_zones(env, nodes, entries, xplane_list)
    assert source == "list" and len(zones) == 1  # LFML; the strip carries no ICAO code


def test_a_dsf_written_again_by_an_update_keeps_the_images_already_prepared(
    tmp_path: Path,
) -> None:
    """Rules 1.1.0 changed write every tile's DSF again (its rule's version), and the Plan of
    Build again counted every image as to prepare again, 53.5 GB for fifteen tiles whose images
    were all in the cache, refusing the build (2026-10-16). The DSF an earlier version of its rule
    made of the same params and inputs gives the exact texture list, and the images the textures
    step prepared from it, the same params and masks, whose DDS the store holds, are taken as
    they are; an image whose DDS is gone, or a step with other params, is to prepare."""
    import json
    from types import SimpleNamespace

    from orthostudio.estimate import _earlier_build, _textures_kept
    from orthostudio.graph import artifact_key
    from orthostudio.model import ArtifactRef
    from orthostudio.pipeline.build import (
        TILE_DSF,
        TILE_TEXTURES,
        TileDsfParams,
        TileTexturesParams,
    )

    def ref(n: int) -> ArtifactRef:
        return ArtifactRef(f"{n:064x}", f"{n + 100:064x}", tmp_path, "x", "dir")

    def put(rule: str, key: str, recipe: str, files: dict[str, str]) -> str:
        with store.begin(rule, key, "dir") as b:
            for name, text in files.items():
                (b.out / name).write_text(text, encoding="utf-8")
            info, _ = b.commit(version=1, recipe=recipe, inputs=[])
        return info.digest

    with Store(tmp_path / "store", fsync=False) as store:
        env = SimpleNamespace(store=store)
        dsf_in = {"mesh": ref(1), "masks": ref(2), "rasters": ref(3), "vectors": ref(4)}
        dsf = SimpleNamespace(
            id="+43+005/dsf", rule=TILE_DSF, params=TileDsfParams(tile="+43+005"), inputs=dsf_in
        )
        params = TileTexturesParams(tile="+43+005", encoder="ispc", encoder_version="1")
        tex = SimpleNamespace(
            id="+43+005/textures",
            rule=TILE_TEXTURES,
            params=params,
            inputs={"dsf": dsf, "masks": ref(2)},
        )
        entries: dict[str, PlanEntry] = {}
        nodes = SimpleNamespace(dsf=dsf, textures=tex)
        assert _earlier_build(env, dsf, entries) is None
        assert _textures_kept(env, nodes, entries) == set()

        # what the version before made of the same params and inputs
        digests = {name: r.digest for name, r in dsf_in.items()}
        old_key, old_recipe = artifact_key(
            TILE_DSF.name, TILE_DSF.version - 1, dsf.params.canonical(), digests
        )
        old_digest = put(TILE_DSF.name, old_key, old_recipe, {"textures.json": "[]"})
        assert _earlier_build(env, dsf, entries) == old_key
        dds = {}
        for name in ("a", "b"):
            k, r = artifact_key("texture.dds", 1, {"n": name}, {})
            with store.begin("texture.dds", k, "file") as b:
                b.out.write_bytes(b"D")
                b.commit(version=1, recipe=r, inputs=[])
            dds[name] = k
        manifest = {
            "format": "osxp-textures-1",
            "tile": "+43+005",
            "textures": [
                {"name": "a", "key": dds["a"], "status": "built"},
                {"name": "b", "key": dds["b"], "status": "hit"},
                {"name": "c", "key": "e" * 64, "status": "built"},  # its DDS is gone
                {"name": "d", "key": dds["a"], "status": "failed"},
            ],
        }
        tex_key, tex_recipe = artifact_key(
            TILE_TEXTURES.name,
            TILE_TEXTURES.version,
            params.canonical(),
            {"dsf": old_digest, "masks": ref(2).digest},
        )
        assert _textures_kept(env, nodes, entries) == set()  # no textures step yet
        put(TILE_TEXTURES.name, tex_key, tex_recipe, {"manifest.json": json.dumps(manifest)})
        assert _textures_kept(env, nodes, entries) == {"a", "b"}
        # other colours: another textures step, nothing kept
        tex.params = TileTexturesParams(
            tile="+43+005", encoder="ispc", encoder_version="1", photo_brightness=0.1
        )
        assert _textures_kept(env, nodes, entries) == set()


def test_the_steps_to_run_count_what_they_wrote_the_last_time(tmp_path: Path) -> None:
    """A step to run counts the size of what it made the last time from the same params and
    inputs, the store knowing it; a step taken from the store, or one whose artefacts are links
    (the images, the pack), counts nothing; a tile with a step never run here counts the flat
    allowance once. Build again of fifteen tiles after an update counted 3.75 GB this way for
    0.9 GB written (2026-10-16)."""
    from types import SimpleNamespace

    from orthostudio.estimate import OTHER_ARTEFACTS_GB, _other_gb
    from orthostudio.graph import artifact_key
    from orthostudio.model import ArtifactRef
    from orthostudio.pipeline.build import TILE_DSF, TileDsfParams

    def ref(n: int) -> ArtifactRef:
        return ArtifactRef(f"{n:064x}", f"{n + 100:064x}", tmp_path, "x", "dir")

    with Store(tmp_path / "store", fsync=False) as store:
        env = SimpleNamespace(store=store)
        dsf_in = {"mesh": ref(1), "masks": ref(2), "rasters": ref(3), "vectors": ref(4)}
        dsf = SimpleNamespace(
            id="t/dsf", rule=TILE_DSF, params=TileDsfParams(tile="+43+005"), inputs=dsf_in
        )
        linked = SimpleNamespace(id="t/textures", rule=TILE_DSF, params=dsf.params, inputs={})
        nodes = SimpleNamespace(by_role={"dsf": dsf, "textures": linked})
        entries = {
            "t/dsf": PlanEntry("t/dsf", "a" * 64, "build", 1.0),
            "t/textures": PlanEntry("t/textures", None, "unknown", 1.0),
        }
        # never run here: the flat allowance, once
        assert _other_gb(env, nodes, entries) == OTHER_ARTEFACTS_GB
        digests = {name: r.digest for name, r in dsf_in.items()}
        key, recipe = artifact_key(
            TILE_DSF.name, TILE_DSF.version - 1, dsf.params.canonical(), digests
        )
        with store.begin(TILE_DSF.name, key, "dir") as b:
            (b.out / "+43+005.dsf").write_bytes(b"D" * 20_000_000)
            b.commit(version=1, recipe=recipe, inputs=[])
        # what the earlier version wrote; the images step is counted elsewhere
        assert _other_gb(env, nodes, entries) == pytest.approx(0.02, abs=1e-3)
        entries["t/dsf"] = PlanEntry("t/dsf", key, "hit", 0.0)
        assert _other_gb(env, nodes, entries) == 0.0
