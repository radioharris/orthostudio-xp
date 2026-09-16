"""Tests for the network benchmark helpers in tools/bench/network (pure parts only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "bench" / "network"
sys.path.insert(0, str(TOOLS))

import common  # noqa: E402
import curl_sustained  # noqa: E402
import overpass_health  # noqa: E402
import quadkeys  # noqa: E402

# --- quadkeys ---------------------------------------------------------------------------------


def test_quadkey_known_values() -> None:
    # Bing documentation example: tile (3, 5) at level 3 -> "213"
    assert quadkeys.gtile_to_quadkey(3, 5, 3) == "213"
    assert quadkeys.gtile_to_quadkey(0, 0, 1) == "0"
    assert quadkeys.gtile_to_quadkey(1, 1, 1) == "3"


def test_quadkey_roundtrip() -> None:
    for x, y, zl in [(33700, 24000, 16), (0, 0, 16), (65535, 65535, 16), (5, 9, 4)]:
        assert quadkeys.quadkey_to_gtile(quadkeys.gtile_to_quadkey(x, y, zl)) == (x, y, zl)


def test_quadkey_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        quadkeys.gtile_to_quadkey(1 << 16, 0, 16)


def test_cell_ranges_marseille_zl16() -> None:
    xs, ys = quadkeys.cell_tile_range(43, 5, 16)
    assert (xs.start, xs.stop - 1) == (33678, 33859)
    assert (ys.start, ys.stop - 1) == (23830, 24080)
    assert len(xs) * len(ys) == 45_682
    ax, ay = quadkeys.texture_aligned_range(xs, ys)
    assert ax.start % 16 == 0 and ay.start % 16 == 0
    assert ax.stop % 16 == 0 and ay.stop % 16 == 0
    assert len(ax) * len(ay) == 56_576  # 13 x 17 textures of 256 tiles


def test_cell_quadkeys_unique_and_texture_grouped() -> None:
    keys = quadkeys.cell_quadkeys(43, 5, 16, texture_aligned=True)
    assert len(keys) == len(set(keys)) == 56_576
    # first 256 keys belong to one texture: same x // 16 and y // 16
    first = [quadkeys.quadkey_to_gtile(k) for k in keys[:256]]
    assert len({(x // 16, y // 16) for x, y, _ in first}) == 1
    assert len(quadkeys.cell_quadkeys(43, 5, 16)) == 45_682


def test_gtile_wgs84_inverse() -> None:
    lat, lon = quadkeys.gtile_to_wgs84(33700, 24000, 16)
    assert quadkeys.wgs84_to_gtile(lat - 1e-9, lon + 1e-9, 16) == (33700, 24000)


# --- common -----------------------------------------------------------------------------------


def test_placeholder_detection() -> None:
    assert common.is_bing_placeholder(200, 1033, {"X-VE-Tile-Info": "no-tile"})
    assert common.is_bing_placeholder(200, 1033, {"Content-Type": "image/png"})
    assert common.is_bing_placeholder(200, 9999, {"x-ve-tile-info": "No-Tile "})
    assert not common.is_bing_placeholder(200, 1033, {"Content-Type": "image/jpeg"})
    assert not common.is_bing_placeholder(404, 1033, {"X-VE-Tile-Info": "no-tile"})
    assert not common.is_bing_placeholder(200, 16000, {"Content-Type": "image/jpeg"})


def test_percentiles_nearest_rank() -> None:
    sample = [float(i) for i in range(1, 101)]
    p = common.percentiles(sample, (50, 90, 99))
    assert p == {"p50": 50.0, "p90": 90.0, "p99": 99.0}
    assert common.percentiles([]) == {}
    assert common.percentiles([7.0], (50, 99)) == {"p50": 7.0, "p99": 7.0}


def test_evenly_spaced() -> None:
    items = [str(i) for i in range(100)]
    picked = common.evenly_spaced(items, 10)
    assert picked == ["0", "10", "20", "30", "40", "50", "60", "70", "80", "90"]
    assert common.evenly_spaced(items, 1000) == items


def test_bing_url_shards() -> None:
    t = common.BING_HOSTS["plan_ecn"]
    assert common.bing_url(t, "1202", 5) == (
        "https://ecn.t1.tiles.virtualearth.net/tiles/a1202.jpeg?g=15312"
    )


# --- curl_sustained ---------------------------------------------------------------------------


def test_parse_write_out_line() -> None:
    line = "\t".join(
        ["200", "12396", "0.108", "0.054", "2", "0", "0", "1.2.3.4", "", "TCP_MISS x", "https://u"]
    )
    s = curl_sustained.parse_line(line + "\n", 1.5)
    assert s is not None and s.ok and not s.placeholder
    assert (s.code, s.bytes, s.http_version, s.remote_ip) == (200, 12396, "2", "1.2.3.4")
    ph = curl_sustained.parse_line(line.replace("12396", "1033"), 1.5)
    assert ph is not None and ph.placeholder
    assert curl_sustained.parse_line("garbage\n", 0.0) is None


def test_guard_aborts_on_pushback_and_bursts() -> None:
    def sample(code: int) -> curl_sustained.Sample:
        return curl_sustained.Sample(code, 10, 0.1, 0.05, "2", 0, 0, "", "", "", "u", 0.0)

    g = curl_sustained.Guard(min_samples=100)
    for _ in range(100):
        g.observe(sample(200))
    assert g.reason is None
    for _ in range(2):
        g.observe(sample(429))
    assert g.reason is not None and "429/403" in g.reason

    g = curl_sustained.Guard(max_consecutive_failures=5)
    for _ in range(4):
        g.observe(sample(500))
    assert g.reason is None
    g.observe(sample(200))
    for _ in range(5):
        g.observe(sample(500))
    assert g.reason == "5 consecutive failures"


def test_curl_config_and_command(tmp_path: Path) -> None:
    cfg = tmp_path / "c.cfg"
    curl_sustained.write_curl_config(["https://a/1", "https://a/2"], cfg)
    assert cfg.read_text().count('output = "/dev/null"') == 2
    cmd = curl_sustained.curl_command(cfg, 64, 60.0, http2=True)
    assert "--http2" in cmd and cmd[cmd.index("--parallel-max") + 1] == "64"


# --- overpass_health --------------------------------------------------------------------------


def test_overpass_query_shapes() -> None:
    q = overpass_health.build_query(overpass_health.LAYERS["coastline"], 43, 5, 120)
    assert q == (
        '[out:json][timeout:120];(way["natural"="coastline"](43,5,44,6););(._;>>;);out body qt;'
    )
    query = overpass_health.ortho4xp_query(overpass_health.LAYERS["coastline"], 43, 5)
    assert query == '(way["natural"="coastline"](43, 5, 44, 6););(._;>>;);out meta;'
    assert set(overpass_health.LAYERS) == {"airports", "big_roads", "coastline", "water"}
    assert len(overpass_health.MIRRORS) == 8
