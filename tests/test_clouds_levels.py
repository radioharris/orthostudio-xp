# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A cloud's source at a coarser level (``clouds.md`` 4.1, 4.3): some sources show another photo
at ZL11, blurred but with no clouds, where ZL12 shows cumulus (Esri World Imagery over tropical
forest, a user, 2026-11-14). The search tries each source at every coarser level under the clouds
no source clears and keeps those showing another photo; a cloud's level may be chosen, every
source then judged there."""

from __future__ import annotations

import io
from collections.abc import Iterable
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image

from orthostudio.clouds import cloudlist, find, layer, sources, state
from orthostudio.clouds.cloudlist import CloudList
from orthostudio.clouds.find import Judgement, lonlat, screen
from orthostudio.clouds.model import THICK
from orthostudio.clouds.picture import Picture
from orthostudio.imagery.grid import TextureId
from orthostudio.imagery.providers import Provider, load_registry

W = H = 128
ZL = 13
TOPS = {"A": 16, "O": 16, "B": 16}


def _noise(seed: int, *, cloudy: bool = False) -> np.ndarray:
    """A photo no other one resembles: its own pattern of smooth patches; ``cloudy``, the test's
    detector sees cloud all over it (its blue channel full, which no clear one has)."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 200, (H // 4, W // 4, 3), dtype=np.uint8)
    if cloudy:
        small[..., 2] = 255
    return np.asarray(Image.fromarray(small).resize((W, H), Image.Resampling.BILINEAR))


def _picture(photo: np.ndarray) -> Picture:
    blank = np.full((H, W), 255, np.uint8)
    return Picture(photo, photo, blank, np.zeros((H, W), np.int16), ("t",), ZL, 4096, 3968)


def _ring(p: Picture) -> list[list[float]]:
    flat: list[float] = []
    for x, y in [(32, 32), (96, 32), (96, 96), (32, 96), (32, 32)]:
        lon, lat = lonlat(p, x, y)
        flat += [lat, lon]
    return [flat]


def _provider(code: str) -> Provider:
    """A source of 19 levels named ``code`` before its level: ``A:12`` is it at ZL12."""
    base, zl = sources.level_of(code)
    arc = load_registry()["Arc"].model_copy(update={"code": base})
    return arc if zl is None else arc.model_copy(update={"max_zl": zl})


class _Pieces:
    """Sources whose pieces are all there; what each shows at a level is the test's."""

    def __init__(self, codes: Iterable[str]) -> None:
        self.providers = {c: _provider(c) for c in codes}

    def fetch(self, todo: Any, **_k: Any) -> None:
        pass

    def kind(self, _code: str, _key: Any) -> str:
        return "picture"


def _screened(
    shows: dict[tuple[str, int], np.ndarray | None],
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: Any,
) -> tuple[dict[str, Judgement], list[tuple[str, int]]]:
    """``screen`` of one cloud over the tile's own photo, each source showing at each level what
    ``shows`` says (``None``: no picture there); with the levels each was read at."""
    p = _picture(_noise(0))
    read: list[tuple[str, int]] = []

    def source_at(_s: Any, code: str, zl: int, _p: Picture, box: Any, *_c: Any) -> Any:
        read.append((code, zl))
        x0, y0, x1, y1 = box
        shown = shows[(code, zl)]
        if shown is None:
            return np.zeros((y1 - y0, x1 - x0, 3), np.uint8), np.zeros((y1 - y0, x1 - x0), bool)
        return shown[y0:y1, x0:x1], np.ones((y1 - y0, x1 - x0), bool)

    def seen(imgs: list[np.ndarray], _haves: Any, **_k: Any) -> list[np.ndarray]:
        return [np.where(i[..., 2] >= 250, THICK, 0).astype(np.uint8) for i in imgs]

    monkeypatch.setattr(find, "_source_at", source_at)
    monkeypatch.setattr(find, "classify_many", seen)
    pieces = _Pieces({c for c, _zl in shows})
    (out,) = screen(p, [_ring(p)], np.zeros((H, W), np.uint8), pieces, [TOPS], **kwargs)  # type: ignore[arg-type]
    return out, read


# -- one code per reading ------------------------------------------------------------------------


def test_one_reading_has_one_code() -> None:
    # two codes of one reading had the same images got twice, a change counted where there was
    # none, and the sharp one ranked as blurred (a review, 2026-11-14)
    tops = {"BI": 16, "EOX": 14}
    assert sources.code_of("BI", 16, tops) == "BI"  # its sharpest on the tile: itself
    assert sources.code_of("EOX", 16, tops) == "EOX"  # none finer than ZL14: its finest
    assert sources.code_of("BI", 12, tops) == "BI:12"
    assert sources.code_of("BI", None, tops) == "BI"
    assert sources.read_at("BI", tops) == 16 and sources.read_at("BI:12", tops) == 12
    assert sources.read_at("EOX", tops) == 14 and sources.read_at("Gone", tops) is None
    assert sources.level_of("Arc@:12") == ("Arc@", 12)
    assert sources.level_of("Arc@") == ("Arc@", None)  # Esri Clarity's own code keeps its @
    assert sources.level_of("Arc:²") == ("Arc:²", None)  # a list's text never raises
    assert sources.level_of("Arc:123") == ("Arc:123", None)
    registry = load_registry()
    coarse = sources.resolve("Arc:11", registry)
    assert coarse is not None and coarse.max_zl == 11 and coarse.code == "Arc"  # its pieces
    assert sources.resolve("EOX:14", registry) is None  # its finest is EOX itself
    assert sources.resolve("Arc:10", registry) is None
    assert sources.resolve("Nope:11", registry) is None
    tex = [TextureId(1, 1, 16, "Arc"), TextureId(2, 2, 11, "Arc")]
    assert sources.tops(tex, registry)["Arc"] == 16 and sources.tops(tex, registry)["EOX"] == 14


# -- reading a source ---------------------------------------------------------------------------


def _png(rgb: np.ndarray, alpha: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.dstack([rgb, alpha])).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("finer", [1, 3])
def test_a_finer_source_is_reduced_piece_by_piece_as_the_whole_was(finer: int) -> None:
    # read at ZL16, a large cloud's window was hundreds of MB: each piece is reduced where it
    # lies, the same averages as the whole reduced
    p = _picture(np.zeros((H, W, 3), np.uint8))
    zl = ZL + finer
    rng = np.random.default_rng(finer)
    bodies: dict[tuple[int, int, int], bytes] = {}

    class Pieces(_Pieces):
        def body(self, _code: str, key: tuple[int, int, int]) -> bytes:
            if key not in bodies:
                rgb = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
                alpha = np.where(rng.random((256, 256)) < 0.1, 0, 255).astype(np.uint8)
                bodies[key] = _png(rgb, alpha)
            return bodies[key]

    box = (5, 9, 117, 101)
    rgb, have = find._source_at(Pieces(["A"]), "A", zl, p, box)  # type: ignore[arg-type]
    # the whole, as it was reduced before
    gx0, gy0, gx1, gy1 = find._world_box(p, box, zl)
    whole = np.zeros((gy1 - gy0, gx1 - gx0, 3), np.uint8)
    shows = np.zeros(whole.shape[:2], np.uint8)
    for (tx, ty, _z), body in bodies.items():
        piece = np.asarray(Image.open(io.BytesIO(body)).convert("RGBA"))
        ox, oy = tx * 256 - gx0, ty * 256 - gy0
        sx0, sy0, dx0, dy0 = max(0, -ox), max(0, -oy), max(0, ox), max(0, oy)
        cw, ch = min(256 - sx0, whole.shape[1] - dx0), min(256 - sy0, whole.shape[0] - dy0)
        whole[dy0 : dy0 + ch, dx0 : dx0 + cw] = piece[sy0 : sy0 + ch, sx0 : sx0 + cw, :3]
        shows[dy0 : dy0 + ch, dx0 : dx0 + cw] = piece[sy0 : sy0 + ch, sx0 : sx0 + cw, 3] > 0
    size = (box[2] - box[0], box[3] - box[1])
    assert np.array_equal(rgb, cv2.resize(whole, size, interpolation=cv2.INTER_AREA))
    assert np.array_equal(have, cv2.resize(shows * 255, size, interpolation=cv2.INTER_AREA) >= 128)


def test_a_source_read_coarser_than_the_picture_is_enlarged_in_its_place() -> None:
    p = _picture(np.zeros((H, W, 3), np.uint8))
    colours: dict[tuple[int, int, int], tuple[int, int, int]] = {}

    class Pieces(_Pieces):
        def body(self, _code: str, key: tuple[int, int, int]) -> bytes:
            colour = colours.setdefault(key, (len(colours) * 60 + 30, 90, 160))
            return _png(
                np.full((256, 256, 3), colour, np.uint8), np.full((256, 256), 255, np.uint8)
            )

    box = (0, 0, W, H)
    rgb, have = find._source_at(Pieces(["A"]), "A", 11, p, box)  # type: ignore[arg-type]
    assert rgb.shape == (H, W, 3) and have.all()
    for x in (0, 60, 127):
        assert tuple(rgb[5, x]) == colours[((p.x0 + x) // 4 // 256, (p.y0 + 5) // 4 // 256, 11)]
    assert find._keys_of(p, [box], 11) == set(colours)


# -- the search ---------------------------------------------------------------------------------


def test_the_search_judges_a_coarser_level_only_where_it_shows_another_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sharp, green = _noise(1, cloudy=True), _noise(2)
    # read at ZL14 (one finer than the picture's 13); the same photo at 13 and 12, another at 11
    shows = {("A", 14): sharp, ("A", 13): sharp, ("A", 12): sharp, ("A", 11): green}
    out, _read = _screened(shows, monkeypatch)
    assert set(out) == {"A", "A:11"} and out["A"].answer == "cloudy"
    assert out["A:11"].answer == "clear"
    assert set(_screened(shows, monkeypatch, coarser=False)[0]) == {"A"}


def test_a_cloud_a_sharp_source_clears_is_not_looked_at_coarser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a blurred photo is chosen only where no sharp one is clear: looked for there alone (B13)
    sharp, green = _noise(1), _noise(2)
    shows = {("A", 14): sharp, ("A", 13): sharp, ("A", 12): sharp, ("A", 11): green}
    out, _read = _screened(shows, monkeypatch)
    assert set(out) == {"A"} and out["A"].answer == "clear"


def test_the_tiles_own_source_is_judged_at_its_coarser_levels_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own, other = _noise(0), _noise(3)
    shows = {("O", 14): own, ("O", 13): own, ("O", 12): other, ("O", 11): other}
    out, _read = _screened(shows, monkeypatch, own="O")
    assert set(out) == {"O:12"} and out["O:12"].answer == "clear"


def test_a_coarser_level_showing_the_tiles_own_photo_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sharp, own = _noise(1, cloudy=True), _noise(0)
    shows = {("A", 14): sharp, ("A", 13): sharp, ("A", 12): own, ("A", 11): own}
    out, _read = _screened(shows, monkeypatch)
    assert set(out) == {"A"}  # the same photo would bring the cloud back


def test_a_source_with_no_picture_at_its_level_is_judged_at_the_first_coarser_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # none at ZL14: the coarser readings stopped there, where any picture is another photo
    green = _noise(2)
    shows = {("A", 14): None, ("A", 13): green, ("A", 12): green, ("A", 11): green}
    out, _read = _screened(shows, monkeypatch)
    assert out["A"].answer == "no_picture" and out["A:13"].answer == "clear"
    assert "A:12" not in out and "A:11" not in out  # the same photo as ZL13, blurrier


def test_a_clouds_level_reads_every_source_at_that_level(monkeypatch: pytest.MonkeyPatch) -> None:
    # ZL16, ZL15 and ZL14 were all judged on the search's ZL14 and said the same (a review)
    shows = {("B", 16): _noise(4), ("A:12", 12): _noise(5)}
    out, read = _screened(shows, monkeypatch, coarser=False, finest=16)
    assert set(read) == {("B", 16), ("A:12", 12)} and set(out) == {"B", "A:12"}


def test_the_same_photo_is_told_at_the_coarser_sources_sharpness() -> None:
    # enlarged, the tile's own photo at ZL11 could slip under 0.9 and be laid over itself
    here = _noise(0)
    small = cv2.resize(here, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    blurred = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
    core = np.zeros((H, W), bool)
    core[16:112, 16:112] = True
    have = np.ones((H, W), bool)
    assert find._same(here, blurred, core, have, 4)
    assert not find._same(here, _noise(9), core, have, 4)
    assert find._same(here, here, core, have, 1)
    assert not find._same(here, _noise(9), core, have, 1)
    # a small cloud: too few of its core's pixels at that sharpness, its whole window told
    small = np.zeros((H, W), bool)
    small[60:68, 60:68] = True
    assert find._same(here, blurred, small, have, 4)
    assert not find._same(here, _noise(9), small, have, 4)


def test_sharpening_a_coarse_source_blurs_no_wider_than_before_its_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[float] = []
    real = cv2.GaussianBlur

    def blur(src: np.ndarray, size: Any, sigmaX: float) -> np.ndarray:  # noqa: N803
        asked.append(sigmaX)
        return real(src, size, sigmaX=sigmaX)

    monkeypatch.setattr(layer.cv2, "GaussianBlur", blur)
    src = np.full((8, 8, 3), 100, np.float32)
    layer.sharpened(src, 2)  # less than four times coarser: as it is
    layer.sharpened(src, 8)
    layer.sharpened(src, 256)  # ZL11 on ZL19: 15 s a window
    assert asked == [8, layer.SHARPEN_SIGMA_MAX]


# -- the rules of a cloud's source and level -----------------------------------------------------

CHOICE = {"BI": 16, "EOX": 14, "Arc": 16, "Arc@": 16}


def test_auto_ranks_the_sources_by_their_fits_as_before_a_coarser_photo_after() -> None:
    c: dict[str, Any] = {
        "screening": {
            "BI": {"answer": "clear", "fit": 40},
            "EOX": {"answer": "clear", "fit": 90},  # coarser than Bing, but its own sharpest
            "Arc:11": {"answer": "clear", "fit": 95},
            "Arc@:12": {"answer": "clear", "fit": 80},
            "Arc@": {"answer": "cloudy", "fit": 99},
        }
    }
    assert state._ranked(c, c["screening"], CHOICE) == ["EOX", "BI", "Arc@:12", "Arc:11", "Arc@"]
    assert state.best_of(c, CHOICE) == "EOX"
    del c["screening"]["EOX"], c["screening"]["BI"]
    assert state.best_of(c, CHOICE) == "Arc@:12"  # no sharp one clear: the sharpest blurred one


def test_a_clouds_level_offers_every_source_at_it_the_best_there_taken() -> None:
    c = {
        "level": 12,
        "screening": {
            "EOX": {"answer": "clear", "fit": 90},  # Auto's: not at ZL12
            "BI:12": {"answer": "clear", "fit": 40},
            "EOX:12": {"answer": "cloudy", "fit": 60},
            "Arc@:12": {"answer": "clear", "fit": 55},
        },
    }
    assert state.tried(c, CHOICE) == ["BI:12", "EOX:12", "Arc@:12"]
    assert state.best_of(c, CHOICE) == "Arc@:12"
    assert state.source_of({**c, "chosen": "EOX:12"}, None, CHOICE) == "EOX:12"  # EOX at ZL12
    at16 = {
        "level": 16,
        "screening": {"BI": {"answer": "clear", "fit": 40}, "EOX": {"answer": "clear", "fit": 60}},
    }
    assert state.best_of(at16, CHOICE) == "EOX"  # each as sharp as it can be at ZL16: by fit


def test_the_menu_keeps_each_sources_lines_together_and_its_own_beside_a_level() -> None:
    # EOX at ZL12 and at ZL11 tried by hand stayed hidden behind EOX's best line; and with the
    # tile holding EOX at a level where nothing is clear, EOX at that level could not be chosen
    auto = {
        "screening": {
            "BI": {"answer": "cloudy", "fit": 80},
            "EOX": {"answer": "clear", "fit": 60},
            "BI:12": {"answer": "clear", "fit": 30},
            "EOX:11": {"answer": "cloudy", "fit": 20},
        }
    }
    menu = state._menu(auto, None, None, CHOICE)
    assert [(m["source"], m["base"], m["level"]) for m in menu] == [
        ("EOX", "EOX", 14),
        ("EOX:11", "EOX", 11),
        ("BI", "BI", 16),
        ("BI:12", "BI", 12),
    ]
    at12 = {"level": 12, "screening": {"EOX:12": {"answer": "cloudy", "fit": 50}}}
    lines = state._menu(at12, "EOX", None, CHOICE)  # the tile holds EOX, nothing clear at ZL12
    assert {m["source"] for m in lines} == {"EOX", "EOX:12"}
    assert all(m["proposable"] for m in lines)


def test_a_clouds_levels_go_from_the_tiles_finest_down_to_the_coarsest() -> None:
    assert state.levels_of({"Arc": 16, "EOX": 14}) == (16, 15, 14, 13, 12, 11)
    assert state.levels_of({"X": 11}) == () and state.levels_of({}) == ()


# -- the level in the list ------------------------------------------------------------------------


def _list() -> tuple[CloudList, dict[str, Any]]:
    cl = CloudList("+08-078", [])
    c = cl.add("found", [[8.1, -77.9, 8.1, -77.8, 8.2, -77.8, 8.1, -77.9]])
    return cl, c


def test_a_level_is_written_with_its_try_and_auto_leaves_no_trace() -> None:
    cl, c = _list()
    c["chosen"] = "BI"
    cloudlist.set_level(cl, c["id"], c["rings"], 12, {"EOX:12": Judgement("clear", 50)})
    assert c["level"] == 12 and c["chosen"] is None
    assert c["tries"]["EOX:12"]["answer"] == "clear" and "EOX:12" not in c["screening"]
    moved = [[0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0]]
    cloudlist.set_level(cl, c["id"], moved, 13)  # its outline changed meanwhile: nothing
    assert c["level"] == 12
    cloudlist.set_level(cl, c["id"], c["rings"], None)
    assert "level" not in c  # a list where no level is chosen is written as before


def test_a_try_where_no_source_answered_writes_nothing() -> None:
    """Offline, every source answers « did not answer »: the level written had left a ticked
    cloud with no source (a review, 2026-11-15)."""
    cl, c = _list()
    c["chosen"] = "BI"
    silent = {"EOX:12": Judgement("did_not_answer", 0), "BI:12": Judgement("refused", 0)}
    assert not cloudlist.set_level(cl, c["id"], c["rings"], 12, silent)
    assert not cloudlist.set_level(cl, c["id"], c["rings"], 12, {})
    assert "level" not in c and c["chosen"] == "BI"
    assert cloudlist.set_level(cl, c["id"], c["rings"], 12, {"EOX:12": Judgement("clear", 50)})


def test_a_new_search_and_discard_bring_a_cloud_back_to_auto() -> None:
    # a level kept by a new search, its tries dropped, left the cloud with no source
    cl, c = _list()
    cloudlist.set_level(cl, c["id"], c["rings"], 12, {"EOX:12": Judgement("clear", 50)})
    found = [{"rings": c["rings"], "screening": {"EOX": {"answer": "clear"}}}]
    cloudlist.merge_search(cl, found, None)
    (again,) = cl.clouds
    assert "level" not in again and again["chosen"] is None and "tries" not in again
    cloudlist.set_level(cl, again["id"], again["rings"], 12, {})
    cloudlist.discard(cl, None)
    assert "level" not in again


@pytest.mark.parametrize("level", [10, 21, "12", True, 12.0])
def test_a_list_with_a_level_that_is_none_is_set_aside(level: Any) -> None:
    _cl, c = _list()
    assert cloudlist._cloud_problem({**c, "level": level}) == "no level"
    assert cloudlist._cloud_problem({**c, "level": 12}) is None
    assert cloudlist._cloud_problem({**c, "tries": []}) == "no tries"


def test_auto_shows_what_the_search_found_and_what_was_taken_a_levels_tries_at_that_level() -> None:
    # ZL12 tried: every source's ZL12 came into Auto, taken or not (a user, 2026-11-14)
    c: dict[str, Any] = {
        "screening": {"EOX": {"answer": "clear", "fit": 40}, "BI": {"answer": "cloudy", "fit": 30}},
        "tries": {
            "EOX:12": {"answer": "clear", "fit": 90},
            "BI:12": {"answer": "clear", "fit": 80},
            "BI": {"answer": "clear", "fit": 70},  # Bing at ZL16 tried at ZL16
        },
        "checks": {"BI:12": {"answer": "clear", "fit": 80, "judged": sources.JUDGED}},
    }
    auto = [m["source"] for m in state._menu(c, None, None, CHOICE)]
    assert auto == ["BI", "BI:12", "EOX"]  # EOX:12, never taken, waits for ZL12
    # one verdict for a line whatever the level: the newest, Bing at ZL16 tried at ZL16, clear at
    # 70 % where the search had it cloudy, now the best
    assert state.reading(c, "BI")["answer"] == "clear"
    assert state.best_of(c, CHOICE) == "BI"
    at12 = {**c, "level": 12}
    assert {m["source"] for m in state._menu(at12, None, None, CHOICE)} == {"EOX:12", "BI:12"}
    at16 = {**c, "level": 16}
    assert state.reading(at16, "BI")["answer"] == "clear"  # at ZL16, ZL16's try


def test_a_level_finer_than_the_tile_now_has_shows_as_its_finest() -> None:
    # built again coarser, the level list showed nothing for a level it no longer has
    assert state._level({"level": 16}, {"BI": 14}) == 14
    assert state._level({"level": 12}, {"BI": 14}) == 12 and state._level({}, {"BI": 14}) is None


def test_a_clouds_level_waits_for_apply_as_an_outline_removed_does() -> None:
    # its try reads the tile's picture, which Apply changes
    assert "level" not in state.possible("ready", {"apply"})
    assert "level" in state.possible("ready", {"find"}) and "level" in state.possible("ready")


def test_each_clouds_levels_are_those_of_the_textures_under_it() -> None:
    # a cloud in a ZL16 square of a tile with an airport at ZL18 was offered ZL18 and ZL17, read
    # as ZL16, and its ZL16 photo ranked as blurred (two reviews, 2026-11-14)
    from orthostudio.imagery.grid import texture_bbox, wgs84_to_tile

    def tex(lat: float, lon: float, zl: int) -> TextureId:
        x, y = wgs84_to_tile(lat, lon, zl)
        return TextureId(int(x) // 16 * 16, int(y) // 16 * 16, zl, "BI")

    airport = tex(8.5, -77.5, 18)
    textures = [tex(8.2, -77.8, 16), tex(8.5, -77.5, 16), airport]
    under = sources.tops_under(textures, {"BI": 18, "EOX": 14})
    lat_max, lon_min, lat_min, lon_max = texture_bbox(airport)
    over = [[lat_min, lon_min, lat_min, lon_max, lat_max, lon_max, lat_min, lon_min]]
    assert under(over) == {"BI": 18, "EOX": 14}
    away = [[8.20, -77.81, 8.20, -77.80, 8.21, -77.80, 8.20, -77.81]]
    assert under(away) == {"BI": 16, "EOX": 14}
    assert state.levels_of(under(away))[0] == 16


def test_judging_the_sources_says_how_far_it_is_from_start_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the detector counts its patches, several windows to one: the bar stopped near 5 % of each
    # source and the phase ended far short of 100 (a review, 2026-11-14)
    said: list[tuple[str, int, int]] = []
    p = _picture(_noise(0))

    def source_at(_s: Any, code: str, zl: int, _p: Picture, box: Any, *_c: Any) -> Any:
        x0, y0, x1, y1 = box
        return _noise(1)[y0:y1, x0:x1], np.ones((y1 - y0, x1 - x0), bool)

    def two_patches(imgs: list[np.ndarray], _h: Any, progress: Any, **_k: Any) -> Any:
        progress(1, 2)
        progress(2, 2)
        return [np.zeros(i.shape[:2], np.uint8) for i in imgs]

    monkeypatch.setattr(find, "_source_at", source_at)
    monkeypatch.setattr(find, "classify_many", two_patches)
    rings = [_ring(p)] * 4
    screen(p, rings, np.zeros((H, W), np.uint8), _Pieces(["A", "B"]), [TOPS] * 4,  # type: ignore[arg-type]
           phase=lambda step, d, t: said.append((step, d, t)))  # fmt: skip
    judged = [(d, t) for step, d, t in said if step == "sources"]
    assert judged == [(50, 200), (100, 200), (150, 200), (200, 200)]


def test_discard_keeps_a_clouds_source_as_before() -> None:
    cl, c = _list()
    c["screening"] = {"BI": {"answer": "clear", "fit": 30}}
    cloudlist.pick(cl, c["id"], "choose", "BI")
    cloudlist.discard(cl, None)
    assert c["chosen"] == "BI" and not c["tick"]  # an unheld cloud keeps its source, as before
    cloudlist.set_level(cl, c["id"], c["rings"], 12, {"EOX:12": Judgement("clear", 50)})
    cloudlist.pick(cl, c["id"], "choose", "EOX:12")
    cloudlist.discard(cl, None)
    assert "level" not in c and c["chosen"] == "EOX:12"  # taken: kept, with what it said
    assert state.reading(c, "EOX:12")["answer"] == "clear"


def test_a_passing_answer_of_a_levels_try_leaves_the_searchs_verdict() -> None:
    # EOX refused under load in a level's try showed refused in Auto over the search's clear
    c = {
        "screening": {"EOX": {"answer": "clear", "fit": 80}},
        "tries": {"EOX": {"answer": "refused", "fit": 0}},
        "checks": {},
    }
    assert state.reading(c, "EOX")["answer"] == "clear"
    assert state.best_of(c, CHOICE) == "EOX"
    alone = {"screening": {}, "tries": {"EOX": {"answer": "refused", "fit": 0}}, "checks": {}}
    assert state.reading(alone, "EOX")["answer"] == "refused"  # said, offering to ask again


def test_the_level_asked_is_shown_from_its_tries_task_the_list_as_it_was() -> None:
    # during the try the list lost its source and showed the old level (two reviews)
    trying = {"kind": "outline", "was": {"row": "found", "lays": None, "level": 12}}
    assert state._asked(trying) == 12
    assert state._asked({"kind": "check", "was": {"row": "found", "lays": None}}) is None
    assert state._asked(None) is None
