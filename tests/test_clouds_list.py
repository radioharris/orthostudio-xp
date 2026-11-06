# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's list of a tile's clouds (``clouds.md`` 2.4, R3, R4): picks kept apart from what the
tile holds, searches merged without losing a tick or a name, what Apply lays."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orthostudio.clouds import cloudlist as cls
from orthostudio.clouds.tile import Stamp, TileClouds
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
NAME = "1488_2096_T12"
PLAIN, OTHER = "p" * 40, "o" * 40
"""The texture as built the tile shows, and another one."""


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
        "plain": {NAME: PLAIN},
    }


def _held(*entries: dict[str, Any]) -> TileClouds:
    """The tile showing ``entries`` in its one texture, built as ``PLAIN``."""
    return TileClouds(
        Path("/nowhere"), "k", "BI", True, (NAME,), {NAME: Stamp(PLAIN, "x" * 40)}, entries
    )


def _hold(*entries: dict[str, Any]) -> TileClouds:
    """The tile showing ``entries``, applied from the list: their rows in it, ticked."""
    with cls.update(TILE) as cl:
        for e in entries:
            row = cl.add("found", e["rings"], tick=True, source=e["source"])
            cl.clouds[-1] = {**row, "id": e["id"]}
        cl.laid = list(entries)
    return _held(*entries)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    return tmp_path


def test_a_tick_and_a_source_are_kept_apart_and_survive_a_restart() -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
        cid = cl.clouds[0]["id"]
        cls.pick(cl, cid, "choose", "Arc")
        assert cl.get(cid)["tick"] is True  # choosing a source ticks
        cls.pick(cl, cid, "untick")
    again = cls.load(TILE)
    assert again.get(cid)["tick"] is False and again.get(cid)["source"] == "Arc"


def test_a_search_keeps_ticks_names_and_ids_and_never_gives_one_again() -> None:
    # R3
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A), _found(B)], None)
        a, b = cl.clouds
        cls.pick(cl, a["id"], "tick")
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
    held = _hold(_entry("held0001", A))
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(_ring(43.42, 5.12, 43.48, 5.28)), _found(B)], held)
        assert cl.clouds[0]["id"] == "held0001" and len(cl.clouds) == 2


def test_an_outline_takes_the_found_clouds_it_covers_and_keeps_a_held_one() -> None:
    # R3: covering half of a found cloud or more, it is that cloud; touching one, it is another
    held = _hold(_entry("held0001", A))
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(B), _found(D)], held)
        b, d = cl.clouds[1:]
        c = cls.add_outline(cl, _ring(43.59, 5.09, 43.71, 5.31), held)  # over B
        cls.add_outline(cl, _ring(43.85, 5.25, 43.95, 5.40), held)  # touches D's corner
        ids = [x["id"] for x in cl.clouds]
        assert b["id"] not in ids and d["id"] in ids and "held0001" in ids and c["id"] in ids
        cls.remove_outline(cl, c["id"], held)
        assert c["id"] not in [x["id"] for x in cl.clouds]


def test_a_held_outline_leaves_the_list_only_once_the_tile_no_longer_holds_it() -> None:
    # R3: unticked it comes back at Apply
    with cls.update(TILE) as cl:
        o = cls.add_outline(cl, A, None)
        cls.pick(cl, o["id"], "choose", "EOX")
        cl.get(o["id"])["checks"]["EOX"] = {"answer": "clear", "entry": _entry(o["id"], A)}
        (laid,) = cls.plan(cl, None, TILE).laid
    held = _held(laid)
    with pytest.raises(OsxpError), cls.update(TILE) as cl:
        cls.remove_outline(cl, o["id"], held)
    with cls.update(TILE) as cl:
        cls.remove_outline(cl, o["id"], _held())  # no longer held: it may leave


def test_a_tick_on_images_got_on_another_photo_gets_them_again() -> None:
    # R6: images that no longer stand are images not in
    held = _held()
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], held)
        cid = cl.clouds[0]["id"]
        e = _entry(cid, A)
        e["plain"] = {NAME: OTHER}
        cl.get(cid)["checks"]["EOX"] = {"answer": "clear", "entry": e}
        cls.pick(cl, cid, "tick")
        (ch,) = cls.plan(cl, held, TILE).changes
    assert cls.needs_images(ch)
    assert not cls.needs_images(cls.Change(cid, "replace", "EOX", None, "same_photo"))


def test_a_source_showing_the_tiles_own_photo_cannot_be_chosen() -> None:
    with cls.update(TILE) as cl:
        f = _found(A)
        f["screening"]["Arc"] = {"answer": "same_photo", "fit": 0}
        cls.merge_search(cl, [f], None)
        cid = cl.clouds[0]["id"]
        with pytest.raises(OsxpError):
            cls.pick(cl, cid, "choose", "Arc")
        cls.pick(cl, cid, "choose", "EOX")


def test_the_changes_apply_makes_against_what_the_tile_holds() -> None:
    # R4: replaced, another source, back as photographed, waiting for its images
    held = _hold(_entry("held0001", A), _entry("held0002", B))
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(C), _found(D)], held)  # C is the held ones, D new
        assert len(cl.clouds) == 3
        new = cl.clouds[-1]["id"]
        cls.pick(cl, "held0001", "untick")
        cls.pick(cl, "held0002", "choose", "Arc")
        cls.pick(cl, new, "tick")
        cl.get(new)["checks"]["EOX"] = {"answer": "clear", "entry": _entry(new, D)}
        got = {ch.cloud: (ch.kind, ch.waiting) for ch in cls.plan(cl, held, TILE).changes}
        assert got == {
            "held0001": ("back", None),
            "held0002": ("another", "no_images"),
            new: ("replace", None),
        }
        laid = [e["id"] for e in cls.plan(cl, held, TILE).laid]
        # held0001 comes back; held0002 keeps its ground until Arc's images are in
        assert laid == ["held0002", new]


def test_images_got_on_another_photo_wait_to_be_got_again() -> None:
    # R6: a cloud's images stand on the texture as built they were got on
    held = _held()
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], held)
        cid = cl.clouds[0]["id"]
        cls.pick(cl, cid, "tick")
        e = _entry(cid, A)
        e["plain"] = {NAME: OTHER}
        cl.get(cid)["checks"]["EOX"] = {"answer": "clear", "entry": e}
        (ch,) = cls.plan(cl, held, TILE).changes
        assert ch.entry is None and ch.waiting == "photo_images"


def test_discard_and_restore() -> None:
    held = _hold(_entry("held0001", A))
    with cls.update(TILE) as cl:
        cls.pick(cl, "held0001", "untick")
        cls.discard(cl, held)
        assert cl.get("held0001")["tick"] is True
        cls.untick_all(cl)
        assert cls.plan(cl, held, TILE).laid == []


def test_a_broken_list_is_put_aside_at_the_next_write_each_one_kept(home: Path) -> None:
    # a read changes nothing; the write puts the broken file aside, each one kept
    path = cls.list_path(TILE)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert cls.load(TILE).clouds == []
    assert path.read_text() == "{not json"  # read only
    with cls.update(TILE):
        pass
    path.write_text("[]")
    with cls.update(TILE):
        pass
    aside = sorted(p.name for p in path.parent.glob(path.name + ".broken*"))
    assert aside == [path.name + ".broken-1", path.name + ".broken-2"]
    assert path.with_name(path.name + ".broken-1").read_text() == "{not json"


def test_a_task_begun_before_the_tile_was_forgotten_writes_nothing(home: Path) -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
    since = cls.generation(TILE)
    cls.forget(TILE)
    with pytest.raises(OsxpError) as got, cls.update(TILE, since=since) as cl:
        cls.merge_search(cl, [_found(B)], None)
    assert got.value.code == "SYS_CANCELLED" and not cls.list_path(TILE).exists()


def test_the_list_is_written_whole(home: Path) -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
    doc = json.loads(cls.list_path(TILE).read_text())
    assert doc["format"] == "osxp-cloud-list-2" and len(doc["clouds"]) == 1
    assert doc["laid"] == []  # the clouds last laid in the tile: none yet


def test_every_answer_of_a_source_has_its_words_in_both_languages() -> None:
    # one table of what a source can answer (sources.ANSWERS), the page's words for each
    from orthostudio.clouds.sources import ANSWERS, ASK_AGAIN, PROPOSABLE

    text = (Path(__file__).parent.parent / "src/orthostudio/ui/i18n.js").read_text("utf-8")
    fr, en = text.split("\n  en: {", 1)
    for answer in ANSWERS:
        assert f'"clouds.answer.{answer}":' in fr and f'"clouds.answer.{answer}":' in en, answer
    assert set(ANSWERS) >= PROPOSABLE >= ASK_AGAIN


def test_a_held_cloud_given_a_source_without_images_keeps_its_ground_and_is_no_change() -> None:
    # R4: Apply lays the newest images that stand, else what the tile holds; the count says so
    from orthostudio.clouds import mode

    held = _hold(_entry("held0001", A))
    with cls.update(TILE) as cl:
        cls.pick(cl, "held0001", "choose", "Arc")
        plan = cls.plan(cl, held, TILE)
        laid = plan.laid
        counts = mode.summary(cl, held, plan)
    assert [e["source"] for e in laid] == ["EOX"]
    assert counts["changes"] == 0 and counts["waiting"] == 0 and counts["held"] == 1


def test_a_source_whose_images_showed_the_same_photo_cannot_be_chosen() -> None:
    # one answer of a source under a cloud: its images got, else its screening
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
        cid = cl.clouds[0]["id"]
        cl.get(cid)["screening"]["Arc"] = {"answer": "clear", "fit": 90}
        cl.get(cid)["checks"]["Arc"] = {"answer": "same_photo", "fit": 0, "entry": None}
        assert not cls.proposable(cl.get(cid), "Arc") and cls.best_of(cl.get(cid)) == "EOX"
        with pytest.raises(OsxpError):
            cls.pick(cl, cid, "choose", "Arc")


def test_a_list_put_aside_never_takes_the_name_of_another(home: Path) -> None:
    path = cls.list_path(TILE)
    path.parent.mkdir(parents=True)
    path.with_name(path.name + ".broken-2").write_text("old")
    path.write_text("{not json")
    with cls.update(TILE):
        pass
    assert path.with_name(path.name + ".broken-2").read_text() == "old"
    assert path.with_name(path.name + ".broken-3").read_text() == "{not json"
