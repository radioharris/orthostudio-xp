# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The season of the photos (orthostudio.live.look): the colours, given to the encoded blocks as
close as to the pixels; the astronomical seasons, the other way round in the south; X-Plane's date
read from Freeflight.prf; the window's arithmetic equal to the engine's; the settings; and the
streamer building again, from the imagery kept, what was built in another season."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from live_helpers import NAMES, FakeDownloader, make_source, make_squares
from orthostudio.live.cache import ChunkCache
from orthostudio.live.gate import read_flight_time
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.imaging import encode_bc1
from orthostudio.live.look import (
    DEFAULT_LOOKS,
    Look,
    apply_look,
    look_blocks,
    sample_photo,
    season_on,
)
from orthostudio.live.region import setup_region
from orthostudio.live.settings import LiveSettings
from orthostudio.live.streamer import Fix, Streamer
from orthostudio.textures.bcdecode import decode_blocks

UI = Path(__file__).resolve().parents[1] / "src" / "orthostudio" / "live" / "ui"
NODE = shutil.which("node")


def quiet(_: str) -> None:
    pass


def ground(seed: int = 3, side: int = 256) -> np.ndarray:
    """A photo-like image: fields, forest, water, roofs, in patches."""
    rng = np.random.default_rng(seed)
    colours = np.array(
        [[70, 95, 50], [40, 60, 35], [150, 140, 100], [30, 50, 70], [170, 80, 60], [110, 120, 80]],
        dtype=np.int16,
    )
    patches = rng.integers(0, len(colours), size=(side // 16, side // 16))
    img = colours[np.kron(patches, np.ones((16, 16), dtype=int))]
    img = img + rng.integers(-12, 13, size=img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


# --- the colours --------------------------------------------------------------------------------


def test_summer_is_the_photo_as_delivered() -> None:
    img = ground()
    assert apply_look(img, DEFAULT_LOOKS["summer"]) is img
    blocks = encode_bc1(img)
    assert look_blocks(blocks, DEFAULT_LOOKS["summer"]) is blocks
    assert look_blocks(blocks, None) is blocks


def test_vegetation_turns_only_what_is_green() -> None:
    img = np.array([[[60, 100, 50], [150, 120, 90], [30, 50, 80], [120, 120, 120]]], np.uint8)
    out = apply_look(img, Look(vegetation=1.0)).astype(int)
    assert out[0, 0, 0] > img[0, 0, 0] + 20  # the green field turns russet: red up
    assert out[0, 0, 1] < img[0, 0, 1]  # and green down
    assert (out[0, 1:] == img[0, 1:]).all()  # a brown field, water, a grey roof: as they were


@pytest.mark.parametrize("season", ["spring", "autumn", "winter"])
def test_the_blocks_take_the_colours_as_the_pixels_would(season: str) -> None:
    """Changing a block's two colours, not its 16 pixels, gives what encoding the changed pixels
    gives, to within the encoding's own error: 3.0 against 2.5 steps on real photos."""
    img = ground(side=512)
    look = DEFAULT_LOOKS[season]
    exact = apply_look(img, look).astype(int)
    by_pixel = decode_blocks(encode_bc1(apply_look(img, look)), 512, 512, "bc1")[..., :3]
    by_block = decode_blocks(look_blocks(encode_bc1(img), look), 512, 512, "bc1")[..., :3]
    err_pixel = np.abs(by_pixel.astype(int) - exact).mean()
    err_block = np.abs(by_block.astype(int) - exact).mean()
    assert err_block < err_pixel + 1.5


def test_the_blocks_stay_in_four_colour_mode() -> None:
    """A change that swaps a block's two colours in their order is put back: BC1's three-colour
    mode would show black where the block meant its third colour."""
    out = look_blocks(encode_bc1(ground()), Look(temperature=1.0, vegetation=1.0, saturation=-1.0))
    a = np.frombuffer(out, dtype=np.uint8).reshape(-1, 8)
    c0 = a[:, 0].astype(int) | a[:, 1].astype(int) << 8
    c1 = a[:, 2].astype(int) | a[:, 3].astype(int) << 8
    flat = c0 == c1
    assert (c0[~flat] > c1[~flat]).all()
    assert (a[flat, 4:8] == 0).all()


# --- the season of a date -----------------------------------------------------------------------


def day(month: int, dom: int, year: int = 2026) -> int:
    import datetime

    return datetime.date(year, month, dom).timetuple().tm_yday


@pytest.mark.parametrize(
    ("month", "dom", "season"),
    [
        ((3, 19, "winter")),
        ((3, 20, "spring")),
        ((6, 20, "spring")),
        ((6, 21, "summer")),
        ((9, 22, "summer")),
        ((9, 23, "autumn")),
        ((12, 20, "autumn")),
        ((12, 21, "winter")),
        ((1, 1, "winter")),
    ],
)
def test_the_astronomical_seasons(month: int, dom: int, season: str) -> None:
    assert season_on(day(month, dom), 46.5, year=2026) == season


def test_the_seasons_the_other_way_round_south_of_the_equator() -> None:
    assert season_on(day(7, 14), -33.9, year=2026) == "winter"  # Sydney in July
    assert season_on(day(10, 13), -37.8, year=2026) == "spring"
    assert season_on(day(1, 10), -41.3, year=2026) == "summer"


def test_no_season_between_the_tropics() -> None:
    assert season_on(day(12, 25), 18.5, year=2026) == "summer"
    assert season_on(day(7, 1), -10.0, year=2026) == "summer"


def test_x_planes_date_from_its_preferences(tmp_path: Path) -> None:
    prefs = tmp_path / "Freeflight.prf"
    prefs.write_text(
        'I\n1100 Version\n_last_start {"runway_start":{"airport_id":"LGMK","runway":"16"}}\n'
        '_time {"local_time":{"day_of_year":259,"latitude":37.4351,"longitude":25.34,'
        '"time_in_24_hours":11.02}}\n'
    )
    assert read_flight_time(prefs) == (260, pytest.approx(37.4351))  # X-Plane counts from 0
    prefs.write_text('_time {"other":{"day_of_year":0}}\n')
    assert read_flight_time(prefs) == (1, None)
    prefs.write_text("_time not json\n")
    assert read_flight_time(prefs) is None
    assert read_flight_time(tmp_path / "none.prf") is None


# --- the window ---------------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_window_computes_what_the_engine_encodes() -> None:
    rng = np.random.default_rng(7)
    pixels = [*rng.integers(0, 256, size=(400, 3)).tolist(), [60, 100, 50], [0, 0, 0], [255] * 3]
    looks = [*DEFAULT_LOOKS.values(), Look(0.5, -0.5, 1.0, 1.0, -1.0), Look(-0.5, 0.5, -1, -1, 1)]
    payload = json.dumps(
        {
            "pixels": pixels,
            "looks": [asdict(lk) for lk in looks],
        }
    )
    script = (
        "import('./look.js').then(m => { const d = " + payload + ";"
        "process.stdout.write(JSON.stringify({defaults: m.DEFAULT_LOOKS, out: d.looks.map(l => {"
        "const f = m.pixelInLook(l); return d.pixels.map(p => f(...p)); })})); })"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        cwd=UI,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    got = json.loads(out.stdout)
    for look, theirs in zip(looks, got["out"], strict=True):
        ours = apply_look(np.array([pixels], dtype=np.uint8), look)[0].astype(int)
        assert np.abs(ours - np.array(theirs)).max() <= 1, look
    for season, look in DEFAULT_LOOKS.items():
        assert got["defaults"][season] == pytest.approx(asdict(look))


def test_the_settings_keep_each_season(monkeypatch, tmp_path: Path) -> None:
    from orthostudio.live import settings as live_settings

    monkeypatch.setattr(live_settings, "settings_file", lambda: tmp_path / "live.toml")
    s = LiveSettings()
    assert s.season == "auto"
    assert s.seasons.looks() == DEFAULT_LOOKS
    winter = {**s.seasons.winter.model_dump(), "vegetation": 0.55}
    changed = LiveSettings.model_validate(
        {
            **s.model_dump(),
            "season": "winter",
            "seasons": {**s.seasons.model_dump(), "winter": winter},
        }
    )
    live_settings.save_settings(changed)
    back = live_settings.load_settings()
    assert back.season == "winter"
    assert back.seasons.winter.vegetation == 0.55
    assert back.seasons.autumn == s.seasons.autumn
    with pytest.raises(ValueError):
        LiveSettings.model_validate({**s.model_dump(), "season": "monsoon"})
    with pytest.raises(ValueError):
        LiveSettings.model_validate({"seasons": {"winter": {"saturation": 3.0}}})


def test_the_preview_shows_a_place_live_keeps(tmp_path: Path) -> None:
    path = tmp_path / "chunks.sqlite"
    cache = ChunkCache(path)
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (70, 95, 50)).save(buf, "JPEG")
    cache.put_many(
        "BI", [((16, 34000 + i, 23000 + j), 1, buf.getvalue()) for i in range(3) for j in range(2)]
    )
    cache.close()
    body = sample_photo(path, "BI", 0)
    with Image.open(io.BytesIO(body)) as image:
        assert image.size == (512, 512)
    assert sample_photo(path, "BI", 5) is not None  # any index: one of the places kept
    assert sample_photo(path, "EOX", 0) is None  # nothing kept of that source
    db = sqlite3.connect(path)
    db.execute("DELETE FROM chunks")
    db.commit()
    db.close()
    assert sample_photo(path, "BI", 0) is None
    assert sample_photo(tmp_path / "missing.sqlite", "BI", 0) is None


# --- the streamer -------------------------------------------------------------------------------


def region(root: Path) -> Path:
    pack = root / "pack"
    source = make_source(root, tile=(45, 6), textures=NAMES)
    setup_region(source, pack, log=quiet)
    make_squares(pack)
    return pack


def streamer(pack: Path, cache: ChunkCache, season: str, logs: list[str] | None = None) -> Streamer:
    return Streamer(
        pack,
        cache,
        FakeDownloader,
        workers=2,
        reach_km=0.0,
        log=(logs.append if logs is not None else quiet),
        season=season,
    )


def build(s: Streamer, name: str) -> None:
    lat, lon = parse_texture_name(name).center
    s.plan(Fix(lat, lon), x_plane_there=False, starting=True)
    s.start()
    deadline = time.time() + 120
    while s.built.get(name) != 0 and time.time() < deadline:
        s.plan(Fix(lat, lon), x_plane_there=False, starting=True)
        time.sleep(0.1)
    s.close()
    assert s.built.get(name) == 0


def top_colour(path: Path) -> tuple[int, int, int]:
    """The 4096 px level's first block's first colour."""
    data = path.read_bytes()
    c0 = int.from_bytes(data[128:130], "little")
    return (c0 >> 11) & 31, (c0 >> 5) & 63, c0 & 31


def test_a_new_season_builds_again(tmp_path: Path) -> None:
    """Built in summer, then a start in autumn: the texture is forgotten as built, built again in
    autumn's colours, its far levels from the imagery kept and its 4096 px photos downloaded again
    (UNCACHED_FROM: they are not kept); the record says which season."""
    pack = region(tmp_path)
    cache = ChunkCache(tmp_path / "c.sqlite")
    name = f"{NAMES[0]}.dds"
    s = streamer(pack, cache, "summer")
    build(s, name)
    summer = top_colour(pack / "textures" / name)
    meta = json.loads((pack / "_live" / "ortho.json").read_text())
    assert meta["look"] == [0.0] * 5 and meta["season"] == "summer"

    logs: list[str] = []
    s = streamer(pack, cache, "autumn", logs)
    assert name not in s.built and name in s.stale  # its file the old season's until built again
    assert any("another season" in line for line in logs)
    build(s, name)
    assert {z for z, _, _ in s.downloader.asked} == {16}  # the 4096 px level's photos alone
    assert top_colour(pack / "textures" / name) != summer
    meta = json.loads((pack / "_live" / "ortho.json").read_text())
    assert meta["season"] == "autumn" and meta["look"] == list(DEFAULT_LOOKS["autumn"].values())
    assert name not in meta["stale"]
    cache.close()


def test_a_start_in_another_season_forgets_what_was_built(tmp_path: Path) -> None:
    pack = region(tmp_path)
    cache = ChunkCache(tmp_path / "c.sqlite")
    s = streamer(pack, cache, "summer")
    s.built.update({f"{NAMES[0]}.dds": 0, f"{NAMES[1]}.dds": 2})
    assert not s.choose_season("summer", "test")  # the same look: nothing changes
    assert s.choose_season("winter", "X-Plane's date")
    assert s.built == {} and s.stale == {f"{NAMES[0]}.dds": 0, f"{NAMES[1]}.dds": 2}
    assert s.season == "winter" and s.look == DEFAULT_LOOKS["winter"]
    cache.close()


def test_a_texture_encoded_in_the_season_before_is_not_counted_as_built(tmp_path: Path) -> None:
    from concurrent.futures import Future

    pack = region(tmp_path)
    cache = ChunkCache(tmp_path / "c.sqlite")
    s = streamer(pack, cache, "summer")
    name = f"{NAMES[0]}.dds"
    gen = s.look_gen
    s.choose_season("autumn", "test")  # while it was being encoded
    done: Future = Future()
    done.set_result(None)
    s.in_flight[name] = (0.0, 0)
    s._finished(done, name, 0, gen=gen)
    assert name not in s.built and s.stale[name] == 0 and name not in s.in_flight
    s._finished(done, name, 0, gen=s.look_gen)
    assert s.built[name] == 0 and name not in s.stale
    cache.close()


def test_textures_of_another_season_are_made_neutral_again(tmp_path: Path) -> None:
    """Not one the planner wants: its new file is on its way."""
    pack = region(tmp_path)
    cache = ChunkCache(tmp_path / "c.sqlite")
    name, wanted = f"{NAMES[0]}.dds", f"{NAMES[1]}.dds"
    s = streamer(pack, cache, "summer")
    build(s, name)
    s = streamer(pack, cache, "spring")
    s.stale[wanted] = 0
    s.queue[wanted] = (0.0, 0)
    assert s.drop_stale() == 1
    assert s.stale == {wanted: 0}
    path = pack / "textures" / name
    assert not path.exists() or s.names.leads_to_neutral(name)
    cache.close()
