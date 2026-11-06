# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's list of a tile's clouds (``clouds.md`` 2.4, R3, R4): picks kept apart from what the
tile holds, searches merged without losing a tick or a name, what Apply builds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orthostudio.clouds import cloudlist as cls
from orthostudio.clouds.tile import TileClouds, colours_near
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
NAME = "1488_2096_T12"


def _ring(lat0: float, lon0: float, lat1: float, lon1: float) -> list[list[float]]:
    return [[lat0, lon0, lat0, lon1, lat1, lon1, lat1, lon0, lat0, lon0]]


A, B, C = (
    _ring(43.40, 5.10, 43.50, 5.30),
    _ring(43.60, 5.10, 43.70, 5.30),
    _ring(43.45, 5.20, 43.65, 5.25),
)
D = _ring(43.80, 5.10, 43.90, 5.30)


def _found(rings: list[list[float]], best: str = "EOX") -> dict[str, Any]:
    return {
        "rings": rings,
        "ha": 10.0,
        "screening": {best: {"answer": "clear", "fit": 70}},
        "best": best,
    }


def _entry(cid: str, rings: list[list[float]], source: str = "EOX") -> dict[str, Any]:
    return {
        "id": cid,
        "source": source,
        "match": [[1, 1, 1], [0, 0, 0]],
        "rings": rings,
        "parts": {NAME: [12, "d" * 64]},
        "images": {NAME: "chunks-1"},
        "colours": colours_near(rings, TILE, (0.0, 0.0, 0.0), ()),
    }


def _held(*entries: dict[str, Any]) -> TileClouds:
    return TileClouds(
        Path("/nowhere"),
        "k",
        tuple(entries),
        {},
        {NAME: "chunks-1"},
        {"tile": [0, 0, 0], "zones": []},
    )


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    return tmp_path


def test_a_tick_and_a_source_are_kept_apart_and_survive_a_restart() -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
        cid = cl.clouds[0]["id"]
        cls.pick(cl, cid, source="Arc")
        assert cl.get(cid)["tick"] is True  # choosing a source ticks
        cls.pick(cl, cid, tick=False)
    again = cls.load(TILE)
    assert again.get(cid)["tick"] is False and again.get(cid)["source"] == "Arc"


def test_a_search_keeps_ticks_names_and_ids_and_never_gives_one_again() -> None:
    # R3
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A), _found(B)], None)
        a, b = cl.clouds
        cls.pick(cl, a["id"], tick=True)
    with cls.update(TILE) as cl:
        cls.merge_search(
            cl, [_found(_ring(43.41, 5.11, 43.51, 5.31))], None
        )  # A moved a little, B gone
        assert [c["id"] for c in cl.clouds] == [a["id"]]  # ticked and kept as it was
        assert cl.get(a["id"])["rings"] == A
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(B)], None)
        new = cl.clouds[-1]
        assert new["id"] not in (a["id"], b["id"]) and new["number"] == 3


def test_a_new_cloud_over_a_held_or_ticked_one_is_that_one() -> None:
    held = _held(_entry("held0001", A))
    with cls.update(TILE, held) as cl:
        cls.merge_search(cl, [_found(_ring(43.42, 5.12, 43.48, 5.28)), _found(B)], held)
        assert cl.clouds[0]["id"] == "held0001" and len(cl.clouds) == 2


def test_an_outline_takes_the_found_clouds_it_covers_and_keeps_a_held_one() -> None:
    held = _held(_entry("held0001", A))
    with cls.update(TILE, held) as cl:
        cls.merge_search(cl, [_found(B)], held)
        c = cls.add_outline(cl, C, 3.0, held)
        ids = [x["id"] for x in cl.clouds]
        assert "held0001" in ids and c["id"] in ids and len(ids) == 2  # B was covered: it is C now
        cls.remove_outline(cl, c["id"])
        assert [x["id"] for x in cl.clouds] == ["held0001"]


def test_the_changes_apply_makes_against_what_the_tile_holds() -> None:
    # R4: replaced, another source, back as photographed, waiting for its images
    held = _held(_entry("held0001", A), _entry("held0002", B))
    with cls.update(TILE, held) as cl:
        cls.merge_search(cl, [_found(C), _found(D)], held)  # C is the held ones, D new
        assert len(cl.clouds) == 3
        new = cl.clouds[-1]["id"]
        cls.pick(cl, "held0001", tick=False)
        cls.pick(cl, "held0002", source="Arc")
        cls.pick(cl, new, tick=True)
        cl.get(new)["checks"]["EOX"] = {"answer": "clear", "entry": _entry(new, D)}
        got = {ch.cloud: (ch.kind, ch.waiting) for ch in cls.changes(cl, held, TILE)}
        assert got == {
            "held0001": ("back", None),
            "held0002": ("another", "no_images"),
            new: ("replace", None),
        }
        laid = [e["id"] for e in cls.laid_after(cl, held, TILE)]
        assert laid == [new]  # held0002 waits for its images, held0001 comes back


def test_images_got_on_another_photo_wait_to_be_got_again() -> None:
    # R6: a cloud's images stand on the pieces they were got on
    held = _held()
    with cls.update(TILE, held) as cl:
        cls.merge_search(cl, [_found(A)], held)
        cid = cl.clouds[0]["id"]
        cls.pick(cl, cid, tick=True)
        e = _entry(cid, A)
        e["images"] = {NAME: "chunks-0"}
        cl.get(cid)["checks"]["EOX"] = {"answer": "clear", "entry": e}
        (ch,) = cls.changes(cl, held, TILE)
        assert ch.entry is None and ch.waiting == "photo_images"


def test_discard_and_restore() -> None:
    held = _held(_entry("held0001", A))
    with cls.update(TILE, held) as cl:
        cls.pick(cl, "held0001", tick=False)
        cls.discard(cl, held)
        assert cl.get("held0001")["tick"] is True
        cls.restore(cl)
        assert cls.laid_after(cl, held, TILE) == []


def test_a_broken_list_is_put_aside_and_made_again_from_what_the_tile_holds(home: Path) -> None:
    path = cls.list_path(TILE)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    cl = cls.load(TILE, _held(_entry("held0001", A)))
    assert [c["id"] for c in cl.clouds] == ["held0001"] and cl.get("held0001")["tick"]
    assert path.with_name(path.name + ".broken").read_text() == "{not json"


def test_the_list_is_written_whole(home: Path) -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
    doc = json.loads(cls.list_path(TILE).read_text())
    assert doc["format"] == "osxp-cloud-list-1" and doc["version"] == 1 and len(doc["clouds"]) == 1
