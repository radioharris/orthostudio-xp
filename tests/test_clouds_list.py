# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's list of a tile's clouds (``clouds.md`` 2.4, R3): picks kept, searches and outlines
merged without losing a tick or a name, the file kept whole. What each cloud is and what Apply
lays is ``test_clouds_state``'s."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orthostudio.clouds import cloudlist as cls
from orthostudio.clouds.sources import JUDGED
from orthostudio.clouds.tile import Stamp, TileClouds, part_path
from orthostudio.graph.digest import digest_bytes
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
NAME = "1488_2096_T12"
PLAIN, OTHER = "p" * 40, "o" * 40
"""The texture as built the tile shows, and another one."""
PART_BYTES = b"a cloud's frozen part"
PART = digest_bytes(PART_BYTES)


def _ring(lat0: float, lon0: float, lat1: float, lon1: float) -> list[list[float]]:
    return [[lat0, lon0, lat0, lon1, lat1, lon1, lat1, lon0, lat0, lon0]]


A, B, C = (
    _ring(43.40, 5.10, 43.50, 5.30),
    _ring(43.60, 5.10, 43.70, 5.30),
    _ring(43.45, 5.20, 43.65, 5.25),
)
D = _ring(43.80, 5.10, 43.90, 5.30)


def _found(rings: list[list[float]], best: str = "EOX") -> dict[str, Any]:
    return {"rings": rings, "screening": {best: {"answer": "clear", "fit": 70}}}


def _entry(cid: str, rings: list[list[float]], source: str = "EOX") -> dict[str, Any]:
    return {
        "id": cid,
        "source": source,
        "match": [[1, 1, 1], [0, 0, 0]],
        "rings": rings,
        "parts": {NAME: [12, PART]},
        "plain": {NAME: PLAIN},
    }


def _held(*entries: dict[str, Any]) -> TileClouds:
    """The tile showing ``entries`` in its one texture, built as ``PLAIN``."""
    stamps, files = {NAME: Stamp(PLAIN, "x" * 40)}, {NAME: (0, 0, 0)}
    known = frozenset(stamps)
    return TileClouds(Path("/nowhere"), "k", "BI", True, (NAME,), stamps, files, entries, known)


def _hold(*entries: dict[str, Any]) -> TileClouds:
    """The tile showing ``entries``, applied from the list: their rows in it, ticked."""
    with cls.update(TILE) as cl:
        for e in entries:
            row = cl.add("found", e["rings"], tick=True, chosen=e["source"])
            cl.clouds[-1] = {**row, "id": e["id"]}
        cl.laid = list(entries)
    return _held(*entries)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home whose data folder holds the clouds' frozen part (:data:`PART`)."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    monkeypatch.delenv("OSXP_DATA_DIR", raising=False)
    part_path(PART).parent.mkdir(parents=True)
    part_path(PART).write_bytes(PART_BYTES)
    return tmp_path


def test_a_tick_and_a_source_are_kept_apart_and_survive_a_restart() -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
        cid = cl.clouds[0]["id"]
        cls.pick(cl, cid, "choose", "Arc")
        assert cl.get(cid)["tick"] is True  # choosing a source ticks
        cls.pick(cl, cid, "untick")
    again = cls.load(TILE)
    assert again.get(cid)["tick"] is False and again.get(cid)["chosen"] == "Arc"


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


def test_an_outline_takes_the_found_clouds_it_covers_once_its_sources_are_tried() -> None:
    # R3: covering half of a found cloud or more, it is that cloud; touching one, it is another;
    # its try stopped, it leaves and they stay (a review, 2026-11-06: they had gone already)
    held = _hold(_entry("held0001", A))
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(B), _found(D)], held)
        b, d = cl.clouds[1:]
        c = cls.add_outline(cl, _ring(43.59, 5.09, 43.71, 5.31))  # over B
        e = cls.add_outline(cl, _ring(43.85, 5.25, 43.95, 5.40))  # touches D's corner
        assert b in cl.clouds  # its sources not tried yet: B stays
        cls.remove_outline(cl, c["id"])  # its try stopped
        assert c not in cl.clouds and b in cl.clouds
        c = cls.add_outline(cl, _ring(43.59, 5.09, 43.71, 5.31))
        cls.record_screening(cl, c["id"], {}, held)
        cls.record_screening(cl, e["id"], {}, held)
        ids = [x["id"] for x in cl.clouds]
        assert b["id"] not in ids and d["id"] in ids and "held0001" in ids and c["id"] in ids


def test_discard_brings_every_tick_and_source_back_to_what_the_tile_holds() -> None:
    held = _hold(_entry("held0001", A))
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(B)], held)
        new = cl.clouds[-1]["id"]
        cls.pick(cl, "held0001", "choose", "Arc")
        cls.pick(cl, new, "tick")
        cls.discard(cl, held)
        assert cl.get("held0001")["tick"] is True and cl.get("held0001")["chosen"] == "EOX"
        assert cl.get(new)["tick"] is False


def test_a_check_is_kept_only_while_its_cloud_keeps_its_outline() -> None:
    from orthostudio.clouds.find import Check, Judgement

    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
        cid = cl.clouds[0]["id"]
        cls.record_check(cl, cid, "EOX", B, Check(Judgement("clear", 70), _entry(cid, B)))
        assert cl.get(cid)["checks"] == {}  # got for another outline: a search moved it
        cls.record_check(cl, cid, "EOX", A, Check(Judgement("clear", 70), _entry(cid, A)))
        assert cl.get(cid)["checks"]["EOX"]["entry"]["id"] == cid
        assert cl.get(cid)["checks"]["EOX"]["judged"] == JUDGED  # how they were judged


def test_a_broken_list_is_put_aside_at_the_next_write_each_one_kept(home: Path) -> None:
    # a read changes nothing; the write puts the broken file aside, each one kept
    path = cls.list_path(TILE)
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_the_list_is_written_whole(home: Path) -> None:
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A)], None)
    doc = json.loads(cls.list_path(TILE).read_text())
    assert doc["format"] == "osxp-cloud-list-3" and len(doc["clouds"]) == 1
    assert doc["laid"] == []  # the clouds last laid in the tile: none yet


def test_every_answer_of_a_source_has_its_words_in_both_languages() -> None:
    # one table of what a source can answer (sources.ANSWERS), the page's words for each
    from orthostudio.clouds.sources import ANSWERS, ASK_AGAIN, PROPOSABLE

    text = (Path(__file__).parent.parent / "src/orthostudio/ui/i18n.js").read_text("utf-8")
    fr, en = text.split("\n  en: {", 1)
    for answer in ANSWERS:
        assert f'"clouds.answer.{answer}":' in fr and f'"clouds.answer.{answer}":' in en, answer
    assert set(ANSWERS) >= PROPOSABLE >= ASK_AGAIN


def test_a_list_put_aside_never_takes_the_name_of_another(home: Path) -> None:
    path = cls.list_path(TILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(path.name + ".broken-2").write_text("old")
    path.write_text("{not json")
    with cls.update(TILE):
        pass
    assert path.with_name(path.name + ".broken-2").read_text() == "old"
    assert path.with_name(path.name + ".broken-3").read_text() == "{not json"


def test_a_file_whose_clouds_are_not_whole_is_no_list(home: Path) -> None:
    from orthostudio.clouds.tile import list_path

    with cls.update(TILE) as cl:
        cl.add("found", [[43.1, 5.1, 43.1, 5.2, 43.2, 5.2, 43.1, 5.1]])
    doc = json.loads(list_path(TILE).read_text())
    doc["clouds"][0]["checks"] = {"EOX": {"entry": {"id": 1}}}
    list_path(TILE).write_text(json.dumps(doc))
    assert cls.load(TILE).clouds == []  # read as none, put aside before a write
    with cls.update(TILE):
        pass
    assert list(list_path(TILE).parent.glob("*.broken-*"))


def test_an_outline_is_rings_of_places_around_ground_on_the_tile() -> None:
    on = [43.1, 5.1, 43.1, 5.2, 43.2, 5.2, 43.1, 5.1]
    assert cls.outline_problem([on], TILE) is None
    nan = [43.1, 5.1, float("nan"), 5.2, 43.2, 5.2, 43.1, 5.1]
    assert cls.outline_problem([nan], TILE) == "a ring is of numbers"
    away = [10.1, 5.1, 10.1, 5.2, 10.2, 5.2, 10.1, 5.1]
    assert cls.outline_problem([away], TILE) == "an outline is drawn on the tile"
    assert cls.outline_problem([[43.1, 5.1, 43.1, 5.1, 43.1, 5.1]], TILE) is not None


def test_an_unticked_cloud_found_again_takes_the_new_judging_its_old_choice_gone() -> None:
    # a source chosen on an old search's fits stayed: Esri cloudy at 27 % over Esri clear at 86 %
    # (a user, 2026-11-07); a ticked one keeps its choice
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A), _found(B)], None)
        a, b = cl.clouds
        cls.pick(cl, a["id"], "choose", "Arc")
        cls.pick(cl, a["id"], "untick")
        cls.pick(cl, b["id"], "choose", "Arc")  # ticked
    with cls.update(TILE) as cl:
        cls.merge_search(cl, [_found(A), _found(B)], None)
        assert cl.get(a["id"])["chosen"] is None and cl.get(a["id"])["checks"] == {}
        assert cl.get(b["id"])["chosen"] == "Arc" and cl.get(b["id"])["tick"] is True


def test_forgetting_a_tile_keeps_the_parts_another_tiles_list_stands_on(home: Path) -> None:
    # named by their bytes alone, a small cloud across the line of two tiles reads the same parts
    # from both: forgetting one erased the other's (a review, 2026-11-07)
    own_bytes = b"this tile's own part"
    own = digest_bytes(own_bytes)
    part_path(own).write_bytes(own_bytes)
    with cls.update(TILE) as cl:
        cl.laid = [_entry("aaaaaaaa", A), {**_entry("bbbbbbbb", B), "parts": {NAME: [12, own]}}]
    with cls.update(TileRef(43, 6)) as cl:
        cl.laid = [_entry("cccccccc", A)]  # its own cloud, on the same part
    cls.list_path(TILE).with_name("notes.json").write_text("{}")  # no tile's list
    cls.erase(TILE)
    assert not cls.list_path(TILE).exists()
    assert part_path(PART).exists() and not part_path(own).exists()


def test_a_file_of_the_clouds_is_checked_once_while_it_stays_the_same(home: Path) -> None:
    # checked at each reading, the sidecars and the list of a user's 200 clouds took 0.9 s of
    # every reading of the tile (-02-050, 2026-11-07)
    from orthostudio.clouds.tile import read_checked

    path = home / "a.json"
    path.write_text('{"n": 1}')
    checked: list[Any] = []

    def check(doc: Any) -> bool:
        checked.append(doc)
        return doc["n"] == 1

    doc, ok = read_checked(path, "test", check)
    doc["n"] = 5  # what is read is the reader's: the next reading reads the file again
    again, ok_again = read_checked(path, "test", check)
    assert ok and ok_again and again == {"n": 1} and len(checked) == 1
    assert read_checked(path, "another check", check)[1] and len(checked) == 2
    path.write_text('{"n": 22}')  # written again: checked again
    assert read_checked(path, "test", check) == ({"n": 22}, False) and len(checked) == 3


def test_a_list_is_checked_once_and_again_once_written(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.clouds.tile import list_path

    with cls.update(TILE) as cl:
        cl.add("found", A)
    checked: list[Any] = []
    problem = cls._cloud_problem
    monkeypatch.setattr(cls, "_cloud_problem", lambda c: (checked.append(c), problem(c))[1])
    first = cls.load(TILE)
    assert len(first.clouds) == 1 and len(checked) == 1
    assert cls.load(TILE).clouds == first.clouds and len(checked) == 1  # the same file
    with cls.update(TILE) as cl:  # read (the same file), then written
        cl.add("found", D)
    assert len(cls.load(TILE).clouds) == 2 and len(checked) == 3  # the new file, its two clouds
    doc = json.loads(list_path(TILE).read_text())
    doc["clouds"][0]["rings"] = [[1, 2]]
    list_path(TILE).write_text(json.dumps(doc))
    assert cls.load(TILE).clouds == []  # a cloud not whole: no list, as before
