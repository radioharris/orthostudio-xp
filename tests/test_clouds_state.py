# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The one state of the cloud mode (``clouds.md`` 1.5, R4, R6; ``clouds/state.py``): from the list
and what the tile's folder shows, each cloud's row, its words, its actions, the count, what Apply
lays and what Discard and Apply leave. Every combination is walked, and they must all agree."""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

import pytest

from orthostudio.clouds import cloudlist as cls
from orthostudio.clouds import state
from orthostudio.clouds.cloudlist import CloudList
from orthostudio.clouds.laying import plan_folder
from orthostudio.clouds.tile import (
    Stamp,
    TileClouds,
    clouds_digest,
    laid_fields,
    part_path,
    texture_digest,
)
from orthostudio.graph.digest import digest_bytes
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
NAME = "1488_2096_T12"
PART_BYTES = b"a cloud's frozen part"
PART = digest_bytes(PART_BYTES)
GONE = "f" * 64
"""A frozen part found nowhere."""
OTHER = "o" * 40
"""A texture as built the tile does not show."""


def _ring(lat0: float, lon0: float, lat1: float, lon1: float) -> list[list[float]]:
    return [[lat0, lon0, lat0, lon1, lat1, lon1, lat1, lon0, lat0, lon0]]


A, B = _ring(43.40, 5.10, 43.50, 5.30), _ring(43.60, 5.10, 43.70, 5.30)

_I18N = (Path(__file__).parent.parent / "src/orthostudio/ui/i18n.js").read_text("utf-8")
FR, EN = _I18N.split("\n  en: {", 1)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home whose data folder holds the clouds' frozen part (:data:`PART`)."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    monkeypatch.delenv("OSXP_DATA_DIR", raising=False)
    part_path(PART).parent.mkdir(parents=True)
    part_path(PART).write_bytes(PART_BYTES)
    return tmp_path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A tile's folder with its one texture as built."""
    f = tmp_path / "tile"
    (f / "textures").mkdir(parents=True)
    (f / "textures" / f"{NAME}.dds").write_bytes(b"the texture as built")
    return f


def _plain(folder: Path) -> str:
    return texture_digest(folder / "textures" / f"{NAME}.dds")


def _shows(folder: Path, entries: list[dict[str, Any]]) -> TileClouds:
    """The folder showing ``entries`` laid in its texture, as Apply leaves it."""
    stamps = {NAME: Stamp(_plain(folder), clouds_digest(entries))} if entries else {}
    held = tuple({**e} for e in entries)
    return TileClouds(folder, "k", "BI", True, (NAME,), stamps, {NAME: (0, 0, 0)}, held)


def _entry(folder: Path, cid: str, rings: Any, source: str, how: str = "stands") -> dict[str, Any]:
    """The images of ``source`` for ``cid``: ``stands`` on the tile, got on another photo
    (``other_photo``), or their frozen part gone (``part_gone``)."""
    return {
        "id": cid,
        "source": source,
        "match": [[1, 1, 1], [0, 0, 0]],
        "rings": rings,
        "parts": {NAME: [12, GONE if how == "part_gone" else PART]},
        "plain": {NAME: OTHER if how == "other_photo" else _plain(folder)},
    }


def _cloud(cl: CloudList, cid: str, rings: Any, kind: str = "found") -> dict[str, Any]:
    c = cl.add(kind, rings)
    c["id"] = cid
    c["screening"] = {
        "EOX": {"answer": "clear", "fit": 70},
        "Arc": {"answer": "same_photo", "fit": 0},
    }
    return c


def _view(cl: CloudList, shown: TileClouds, tasks: Any = ()) -> state.View:
    return state.view(cl, shown, TILE, "ready", list(tasks))


def _words(key: str) -> bool:
    return f'"{key}":' in FR and f'"{key}":' in EN


# -- every combination -------------------------------------------------------------------------

TICKS = (False, True)
CHOSEN = (None, "EOX", "Arc", "Esri")
"""None (the best), the clear one, one showing the tile's own photo, the held one's."""
HELD = (None, "same", "other")
"""What the tile shows of the cloud: nothing, the images of its source, those of another."""
IMAGES = ("none", "stands", "other_photo", "part_gone", "did_not_answer", "no_picture")
"""What was got of its source."""
NEIGHBOUR = ("held", "unticked", "ticked")
"""The cloud beside it in the same texture: held and kept, held and unticked, new and ticked."""


def _case(folder: Path, tick: bool, chosen: Any, held: Any, images: str, neighbour: str) -> Any:
    cl = CloudList(TILE.name, [])
    c = _cloud(cl, "cloud001", A)
    n = _cloud(cl, "cloud002", B)
    c["tick"], c["chosen"] = tick, chosen
    shown_entries = []
    if held == "same":
        shown_entries.append(_entry(folder, "cloud001", A, "EOX"))
    elif held == "other":
        shown_entries.append(_entry(folder, "cloud001", A, "Esri"))
    src = state.source_of(c)
    if src is not None and images != "none":
        if images in ("did_not_answer", "no_picture"):
            c["checks"][src] = {"answer": images, "fit": 0, "entry": None}
        else:
            got = _entry(folder, "cloud001", A, src, images)
            c["checks"][src] = {"answer": "clear", "fit": 70, "entry": got}
    held_n = _entry(folder, "cloud002", B, "EOX")
    n["checks"]["EOX"] = {"answer": "clear", "fit": 70, "entry": held_n}
    n["tick"] = neighbour != "unticked"
    if neighbour != "ticked":
        shown_entries.append(held_n)
    return cl, _shows(folder, shown_entries)


CASES = list(itertools.product(TICKS, CHOSEN, HELD, IMAGES, NEIGHBOUR))


@pytest.mark.parametrize("tick,chosen,held,images,neighbour", CASES)
def test_every_state_agrees_with_what_apply_and_discard_do(
    folder: Path, tick: bool, chosen: Any, held: Any, images: str, neighbour: str
) -> None:
    cl, shown = _case(folder, tick, chosen, held, images, neighbour)
    v = _view(cl, shown)
    rows = {s.cloud["id"]: s for s in v.clouds}

    # each row from what the tile holds against what Apply lays
    for s in v.clouds:
        assert s.row in state.ROWS
        if s.row == "waiting":
            assert s.cloud["tick"] and s.why is not None
            assert (s.lays is None) == (s.held is None)  # it keeps what the tile shows of it
            assert s.held is None or laid_fields(s.lays) == laid_fields(s.held)
        elif s.row == "held":
            assert s.held is not None and s.lays is not None
        elif s.row == "another":
            assert s.held is not None and s.lays is not None
            assert laid_fields(s.lays) != laid_fields(s.held)
        elif s.row == "ready":
            assert s.held is None and s.lays is not None
        elif s.row == "back":
            assert s.held is not None and s.lays is None
        elif s.row == "found":
            assert not s.cloud["tick"] and s.held is None
        line = s.page()["line"]
        if line is not None:
            assert _words(line["key"]), line["key"]  # every line has its words, in both

    # the count is the rows', Apply's and Discard's buttons the count's
    changes = sum(s.row in ("another", "ready", "back") for s in v.clouds)
    assert v.counts["changes"] == changes
    assert v.counts["waiting"] == sum(s.row == "waiting" for s in v.clouds)
    assert v.allows("apply") == (changes > 0)
    open_rows = any(s.row in ("another", "ready", "back", "waiting") for s in v.clouds)
    assert v.allows("discard") == open_rows

    # Apply lays what the rows say, what the folder's plan keeps
    assert [e["id"] for e in v.laid] == [s.cloud["id"] for s in v.clouds if s.lays is not None]
    asked = [e for e in v.laid]
    assert [e["id"] for e in plan_folder(TILE, shown, asked).laid] == [e["id"] for e in v.laid]

    # the actions, from the row
    for s in v.clouds:
        assert ("tick" in s.actions) <= (s.source is not None)
        assert ("check" in s.actions) <= (s.why is not None)
        assert ("remove" in s.actions) == (s.cloud["kind"] == "drawn" and s.held is None)
        assert s.toggle == ("untick" if s.cloud["tick"] else "tick" if s.source else None)
        if s.why in ("did_not_answer", "no_images") or (s.why or "").startswith("photo_"):
            assert "check" in s.actions, s.why  # what asking again may end is offered

    # after Apply, nothing is left to change; what waits still waits
    after = _view(cl, _shows(folder, [dict(e) for e in v.laid]))
    assert after.counts["changes"] == 0, [(s.cloud["id"], s.row, s.why) for s in after.clouds]
    for cid, s in rows.items():
        if s.row == "waiting":
            assert after.cloud(cid).row == "waiting"

    # after Discard, nothing ticked differs from what the tile holds
    cls.discard(cl, shown)
    back = _view(cl, shown)
    assert all(s.row not in ("ready", "back", "waiting") for s in back.clouds), [
        (s.cloud["id"], s.row, s.why) for s in back.clouds
    ]


def test_a_task_of_a_cloud_takes_its_row_and_the_tile_gives_apply_up(folder: Path) -> None:
    cl, shown = _case(folder, True, None, None, "stands", "ticked")
    task = {"id": "t1", "kind": "check", "cloud": "cloud001", "stoppable": True}
    v = _view(cl, shown, [task])
    s = v.cloud("cloud001")
    assert s.row == "check" and s.actions == {"stop"} and s.task == task
    assert not v.allows("apply") and not v.allows("restore") and v.tasks == []


def test_a_tile_in_a_build_reads_nothing_and_allows_nothing(folder: Path) -> None:
    cl, _shown = _case(folder, True, None, "same", "stands", "held")
    for st in ("in_build", "queued", "not_built", "not_whole", "disk_away", "data_away"):
        v = state.view(cl, None, TILE, st)
        assert not v.allowed and v.laid == [] and all(s.row == "frozen" for s in v.clouds)
        assert v.line is not None and _words(v.line["key"]), st
    assert state.view(cl, None, TILE, "in_build").busy


def test_a_held_cloud_needs_its_part_only_where_its_texture_is_made_again(folder: Path) -> None:
    # R6: the folder shows it; its part counts only when a neighbour's change makes it again
    cl = CloudList(TILE.name, [])
    c, n = _cloud(cl, "cloud001", A), _cloud(cl, "cloud002", B)
    gone = _entry(folder, "cloud001", A, "EOX", "part_gone")
    c["tick"], c["checks"]["EOX"] = True, {"answer": "clear", "fit": 70, "entry": gone}
    kept = _entry(folder, "cloud002", B, "EOX")
    n["tick"], n["checks"]["EOX"] = True, {"answer": "clear", "fit": 70, "entry": kept}
    shown = _shows(folder, [gone, kept])
    v = _view(cl, shown)
    assert [s.row for s in v.clouds] == ["held", "held"] and v.counts["changes"] == 0
    n["tick"] = False  # its texture made again: the cloud whose part is gone cannot stay laid
    v = _view(cl, shown)
    lost = v.cloud("cloud001")
    assert lost.row == "back" and lost.why == "photo_parts" and "check" in lost.actions
    assert v.cloud("cloud002").row == "back" and v.laid == [] and v.counts["back"] == 2


def test_a_source_that_may_not_be_chosen_is_refused(folder: Path) -> None:
    cl, shown = _case(folder, False, None, None, "none", "held")
    v = _view(cl, shown)
    assert v.allows("choose", "cloud001", "EOX")
    assert not v.allows("choose", "cloud001", "Arc")  # the tile's own photo
    assert not v.allows("choose", "cloud001", "Nowhere")  # not tried there
    assert not v.allows("tick", "gone0001")  # a cloud gone allows nothing


def test_a_held_outline_may_not_be_removed(folder: Path) -> None:
    cl = CloudList(TILE.name, [])
    o = _cloud(cl, "outl0001", A, kind="drawn")
    e = _entry(folder, "outl0001", A, "EOX")
    o["tick"], o["checks"]["EOX"] = True, {"answer": "clear", "fit": 70, "entry": e}
    assert "remove" not in _view(cl, _shows(folder, [e])).cloud("outl0001").actions
    assert "remove" in _view(cl, _shows(folder, [])).cloud("outl0001").actions


def test_every_word_the_view_sends_has_its_words(folder: Path) -> None:
    cl, shown = _case(folder, True, None, "other", "stands", "unticked")
    texts = _view(cl, shown).texts()
    keys = [w["key"] for w in texts["count"]] + [texts["apply"]["key"]]
    for q in ("apply_question", "restore_question"):
        keys += [texts[q]["title"]["key"], texts[q]["go"]["key"]]
        keys += [w["key"] for w in texts[q]["lines"]]
    for k in keys:
        assert _words(k), k
    for st in state.TILE_STATES:
        assert st == "ready" or _words(f"clouds.tile.{st}"), st
    # every wait the page has words for is one a row can say, in both languages
    said = {s.why for case in CASES for s in _view(*_case(folder, *case)).clouds if s.why}
    for why in re.findall(r'"clouds\.row\.waiting\.(\w+)":', FR):
        assert _words(f"clouds.row.waiting.{why}")
        # refused and not_a_picture as did_not_answer; level, source, textures: another build's
        others = ("refused", "not_a_picture", "photo_level", "photo_source", "photo_textures")
        assert why in said or why in others, why
