# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A cloud's source at a coarser level (``clouds.md`` 4.1, 4.3): some sources show another photo
at ZL11, blurred but with no clouds, where ZL12 shows cumulus (Esri World Imagery over tropical
forest, a user, 2026-11-14). The search tries each source at every coarser level and keeps those
showing another photo; a cloud's level may be chosen by hand."""

from __future__ import annotations

import io
from collections.abc import Iterable
from typing import Any

import numpy as np
import pytest
from PIL import Image

from orthostudio.clouds import find, sources, state
from orthostudio.clouds.find import Judgement, lonlat, screen
from orthostudio.clouds.model import THICK
from orthostudio.clouds.picture import Picture
from orthostudio.imagery.grid import TextureId
from orthostudio.imagery.providers import load_registry

W = H = 128
ZL = 13


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
    pts = [(32, 32), (96, 32), (96, 96), (32, 96), (32, 32)]
    flat: list[float] = []
    for x, y in pts:
        lon, lat = lonlat(p, x, y)
        flat += [lat, lon]
    return [flat]


class _Pieces:
    """Sources whose pieces are all there; what each shows at a level is the test's."""

    def __init__(self, codes: Iterable[str]) -> None:
        registry = load_registry()
        self.providers = {c: registry["Arc"] for c in codes}

    def fetch(self, todo: Any, **_k: Any) -> None:
        pass

    def kind(self, _code: str, _key: Any) -> str:
        return "picture"


def test_a_source_at_a_coarser_level_is_a_code_of_its_own() -> None:
    registry = load_registry()
    assert sources.at_level("Arc", 11) == "Arc:11"
    assert sources.level_of("Arc:11") == ("Arc", 11)
    assert sources.level_of("Arc@") == ("Arc@", None)  # Esri Clarity's own code keeps its @
    assert sources.level_of("Arc@:12") == ("Arc@", 12)
    coarse = sources.resolve("Arc:11", registry)
    assert coarse is not None and coarse.max_zl == 11 and coarse.code == "Arc"  # its pieces
    assert sources.resolve("Arc", registry) is registry["Arc"]
    assert sources.resolve("EOX:14", registry) is None  # its finest: the source itself
    assert sources.resolve("Arc:10", registry) is None  # below the coarsest
    assert sources.resolve("Nope:11", registry) is None
    tex = [TextureId(1, 1, 16, "Arc"), TextureId(2, 2, 11, "Arc")]
    assert sources.tops(tex, registry)["Arc"] == 16 and sources.tops(tex, registry)["EOX"] == 14


def test_a_source_read_coarser_than_the_picture_is_enlarged_in_its_place() -> None:
    # two pieces of ZL11 side by side, each one colour: the picture at ZL13 shows each where it is
    p = _picture(np.zeros((H, W, 3), np.uint8))
    colours = {}

    class Pieces(_Pieces):
        def body(self, _code: str, key: tuple[int, int, int]) -> bytes:
            colour = colours.setdefault(key, (len(colours) * 60 + 30, 90, 160))
            buf = io.BytesIO()
            Image.new("RGB", (256, 256), colour).save(buf, "PNG")
            return buf.getvalue()

    box = (0, 0, W, H)
    rgb, have = find._source_at(Pieces(["A"]), "A", 11, p, box)  # type: ignore[arg-type]
    assert rgb.shape == (H, W, 3) and have.all()
    for x in (0, 60, 127):
        key = ((p.x0 + x) // 4 // 256, (p.y0 + 5) // 4 // 256, 11)
        assert tuple(rgb[5, x]) == colours[key]
    assert find._keys_of(p, [box], 11) == set(colours)


def _screened(
    shows: dict[tuple[str, int], np.ndarray],
    monkeypatch: pytest.MonkeyPatch,
    *,
    own: str | None = None,
    coarser: bool = True,
) -> dict[str, Judgement]:
    photo = _noise(0)
    p = _picture(photo)

    def source_at(_s: Any, code: str, zl: int, _p: Picture, box: Any) -> Any:
        x0, y0, x1, y1 = box
        return shows[(code, zl)][y0:y1, x0:x1], np.ones((y1 - y0, x1 - x0), bool)

    monkeypatch.setattr(find, "_source_at", source_at)

    def seen(imgs: list[np.ndarray], _haves: Any, **_k: Any) -> list[np.ndarray]:
        return [np.where(i[..., 2] >= 250, THICK, 0).astype(np.uint8) for i in imgs]

    monkeypatch.setattr(find, "classify_many", seen)
    codes = {c for c, _zl in shows}
    (out,) = screen(
        p,
        [_ring(p)],
        np.zeros((H, W), np.uint8),
        _Pieces(codes),  # type: ignore[arg-type]
        own=own,
        coarser=coarser,
    )
    return out


def test_the_search_judges_a_coarser_level_only_where_it_shows_another_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sharp, green = _noise(1, cloudy=True), _noise(2)
    # read at ZL14 (one finer than the picture's 13); the same photo at 13 and 12, another at 11
    shows = {("A", 14): sharp, ("A", 13): sharp, ("A", 12): sharp, ("A", 11): green}
    out = _screened(shows, monkeypatch)
    assert set(out) == {"A", "A:11"} and out["A"].answer == "cloudy"
    assert out["A:11"].answer == "clear"
    assert set(_screened(shows, monkeypatch, coarser=False)) == {"A"}


def test_a_cloud_a_sharp_source_clears_is_not_looked_at_coarser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a blurred photo is chosen only where no sharp one is clear: looked for there alone (B13)
    sharp, green = _noise(1), _noise(2)
    shows = {("A", 14): sharp, ("A", 13): sharp, ("A", 12): sharp, ("A", 11): green}
    out = _screened(shows, monkeypatch)
    assert set(out) == {"A"} and out["A"].answer == "clear"


def test_the_tiles_own_source_is_judged_at_its_coarser_levels_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the tile's own photo at ZL14 and 13, another at ZL12 and below: that one is offered
    own = _noise(0)
    other = _noise(3)
    shows = {("O", 14): own, ("O", 13): own, ("O", 12): other, ("O", 11): other}
    out = _screened(shows, monkeypatch, own="O")
    assert set(out) == {"O:12"} and out["O:12"].answer == "clear"


def test_a_coarser_level_showing_the_tiles_own_photo_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sharp, own = _noise(1, cloudy=True), _noise(0)
    shows = {("A", 14): sharp, ("A", 13): sharp, ("A", 12): own, ("A", 11): own}
    assert set(_screened(shows, monkeypatch)) == {"A"}  # the same photo would bring the cloud back


def test_a_clear_sharp_source_comes_before_a_clear_blurred_one_whatever_their_fits() -> None:
    c = {
        "screening": {
            "Arc:11": {"answer": "clear", "fit": 90},
            "BI": {"answer": "clear", "fit": 40},
            "EOX": {"answer": "cloudy", "fit": 95},
            "Arc:12": {"answer": "clear", "fit": 95},
        }
    }
    assert state._ranked(c, c["screening"]) == ["BI", "Arc:12", "Arc:11", "EOX"]
    assert state.best_of(c) == "BI"
    del c["screening"]["BI"]
    assert state.best_of(c) == "Arc:12"  # no sharp one clear: the sharpest blurred one


def test_a_clouds_levels_go_from_the_sharpest_of_its_source_down_to_the_coarsest() -> None:
    tops = {"Arc": 16, "EOX": 14, "X": 11}
    assert state._levels("Arc", tops) == (
        (16, "Arc"),
        (15, "Arc:15"),
        (14, "Arc:14"),
        (13, "Arc:13"),
        (12, "Arc:12"),
        (11, "Arc:11"),
    )
    assert state._levels("EOX:12", tops)[0] == (14, "EOX")  # the same list, whatever is chosen
    assert state._level("EOX:12", state._levels("EOX:12", tops)) == 12
    assert state._level("EOX", state._levels("EOX", tops)) == 14
    assert state._levels("X", tops) == () and state._levels(None, tops) == ()  # nothing to choose
    assert state._levels("Gone", tops) == ()


def test_a_level_chosen_by_hand_stays_in_the_menu_once_another_source_is_taken() -> None:
    # ZL11 chosen, its images got, then a ZL16 source taken: the menu went from four lines back
    # to three, the ZL11 one gone with its verdict (a user, 2026-11-14)
    c = {
        "screening": {"EOX": {"answer": "clear", "fit": 70}, "BI": {"answer": "cloudy", "fit": 50}},
        "checks": {"EOX:11": {"answer": "clear", "fit": 40, "judged": sources.JUDGED}},
    }
    menu = state._menu(c, "BI", None, {"EOX": 14, "BI": 16})
    assert [(m["source"], m["level"], m["answer"]) for m in menu] == [
        ("EOX", 14, "clear"),
        ("EOX:11", 11, "clear"),
        ("BI", 16, "cloudy"),
    ]


def test_a_clouds_menu_keeps_each_sources_lines_together_the_best_source_first() -> None:
    c = {
        "screening": {
            "BI": {"answer": "cloudy", "fit": 80},
            "EOX": {"answer": "clear", "fit": 60},
            "BI:12": {"answer": "clear", "fit": 30},
            "Arc:11": {"answer": "clear", "fit": 90},
            "EOX:11": {"answer": "cloudy", "fit": 20},
        }
    }
    menu = [m["source"] for m in state._menu(c, None, None, {"BI": 16, "EOX": 14, "Arc": 16})]
    # EOX's best line is the best of all, then Bing's (clear at ZL12), then Esri's (ZL11)
    assert menu == ["EOX", "EOX:11", "BI", "BI:12", "Arc:11"]
    assert state.best_of(c) == "EOX"  # the choice is the same rule as ever
