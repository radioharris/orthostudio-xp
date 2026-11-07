# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's list of a tile's clouds, kept and changed here (``clouds.md`` 2.4): what was found
or outlined, what is ticked, the source the user chose. The list's own rules are here: how a
search's clouds join it (R3), what an outline takes over, what Discard brings back, that it knows
every cloud the tile shows replaced (:func:`knowing`). What each cloud is, and what Apply does with
it, is ``clouds.state``'s alone.

``$OSXP_HOME/clouds/<tile>.json``: every cloud with its id (eight characters, never given again
on the tile), its number (``Cloud 7``, ``Outline 2``), its outline, what each source has under it
(the search's screening), its tick, the source the user chose (``chosen``, ``None`` for the
best), and the images got of each source tried (its checks). The list is the draft: each change
is saved as it is made and survives closing, quitting and every build; only Apply changes what
X-Plane shows. Its order is the order clouds are laid in. It keeps the clouds last laid too
(``laid``), which a build of the tile lays again (``laying.lay_again``).
"""

from __future__ import annotations

import json
import math
import secrets
import shutil
import string
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from orthostudio.clouds.sources import JUDGED
from orthostudio.clouds.tile import (
    TileClouds,
    entry_problem,
    held_by_id,
    laid_fields,
    list_path,
    listed,
    part_path,
    previews_dir,
    read_checked,
    ring_problem,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text
from orthostudio.model import TileRef

__all__ = ["CloudList", "erase", "knowing", "load", "pick", "update"]

FORMAT = "osxp-cloud-list-3"
_ID = string.ascii_lowercase + string.digits
_LOCKS: dict[str, threading.Lock] = {}
_DEGREE_M = 111_320.0  # a degree of latitude, in metres
_GUARD = threading.Lock()


@dataclass
class CloudList:
    """The list as data: ``clouds`` in laying order, each a JSON object (module doc), and the
    clouds last applied (``laid``: entries, in laying order)."""

    tile: str
    clouds: list[dict[str, Any]]
    number: int = 0
    search: dict[str, Any] | None = None
    laid: list[dict[str, Any]] = field(default_factory=list)

    def get(self, cid: str) -> dict[str, Any]:
        for c in self.clouds:
            if c["id"] == cid:
                return c
        raise OsxpError("CLOUD_UNKNOWN", context={"tile": self.tile, "cloud": cid})

    def new_id(self, taken: set[str] = frozenset()) -> str:  # type: ignore[assignment]
        mine = {c["id"] for c in self.clouds} | set(taken)
        while (cid := "".join(secrets.choice(_ID) for _ in range(8))) in mine:
            continue
        return cid

    def add(self, kind: str, rings: list[list[float]], **more: Any) -> dict[str, Any]:
        """A new cloud of ``kind`` outlined by ``rings``, its area its outline's."""
        self.number += 1
        c = {
            "id": self.new_id(),
            "kind": kind,
            "number": self.number,
            "rings": rings,
            "ha": rings_ha(rings),
            "tick": False,
            "chosen": None,
            "screening": {},
            "checks": {},
            **more,
        }
        self.clouds.append(c)
        return c


def rings_ha(rings: Sequence[Sequence[float]]) -> float:
    """The area inside rings of the zones' format (``[lat0, lon0, ...]``), in hectares."""
    total = 0.0
    for r in rings:
        lat, lon = [float(v) for v in r[0::2]], [float(v) for v in r[1::2]]
        n = len(lat)
        if n < 3:
            continue
        twice = sum(lon[i] * lat[(i + 1) % n] - lon[(i + 1) % n] * lat[i] for i in range(n))
        total += abs(twice) / 2 * math.cos(math.radians(sum(lat) / n)) * _DEGREE_M**2
    return round(total / 10_000, 1)


def _lock(tile: TileRef) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(tile.name, threading.Lock())


def load(tile: TileRef) -> CloudList:
    """The tile's list, read and nothing else; a new one when it has none or it cannot be read as
    a list (:func:`update` puts such a file aside before writing)."""
    return _read(tile) or CloudList(tile.name, [])


def _read(tile: TileRef) -> CloudList | None:
    """The list in the tile's file, ``None`` when there is none or it is not one, every cloud of
    it whole (:func:`_cloud_problem`; a passing read error is raised: a draft is never dropped for
    it)."""
    try:
        doc, whole = read_checked(list_path(tile), f"list {tile.name}", lambda d: _whole(d, tile))
        if whole is None:
            return None
        laid = [e for e, good in zip(doc.get("laid") or (), whole, strict=True) if good]
        return CloudList(
            tile.name, list(doc["clouds"]), int(doc.get("number", 0)), doc.get("search"), laid
        )
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError):
        return None


def _whole(doc: Any, tile: TileRef) -> tuple[bool, ...] | None:
    """Whether the list ``doc`` of ``tile`` is one, every cloud of it whole: ``None`` when not,
    else which of its clouds last laid are whole (the others are left out)."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("tile") != tile.name:
        return None
    clouds = doc["clouds"]
    if not isinstance(clouds, list) or any(_cloud_problem(c) is not None for c in clouds):
        return None
    search = doc.get("search")
    if search is not None and not isinstance(search, dict):
        return None
    return tuple(entry_problem(e) is None for e in doc.get("laid") or ())


def _cloud_problem(c: Any) -> str | None:
    """Why ``c`` is not a cloud of a list (the module's doc), or ``None``: each field of its
    kind, the images got of each source whole."""
    if not isinstance(c, dict):
        return "not an object"
    if not (isinstance(c.get("id"), str) and c["id"] and c.get("kind") in ("found", "drawn")):
        return "no id or kind"
    if not (isinstance(c.get("number"), int) and isinstance(c.get("tick"), bool)):
        return "no number or tick"
    if not (c.get("chosen") is None or isinstance(c["chosen"], str)):
        return "a source is named"
    rings = ring_problem(c.get("rings"))
    if rings is not None:
        return rings
    for said in ("screening", "checks"):
        what = c.get(said)
        if not (isinstance(what, dict) and all(isinstance(v, dict) for v in what.values())):
            return f"no {said}"
    for got in c["checks"].values():
        if got.get("entry") is not None and entry_problem(got["entry"]) is not None:
            return "images not whole"
    return None


def save(cl: CloudList) -> None:
    doc = {
        "format": FORMAT,
        "tile": cl.tile,
        "number": cl.number,
        "search": cl.search,
        "clouds": cl.clouds,
        "laid": cl.laid,
    }
    path = list_path(TileRef.parse(cl.tile))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, indent=1) + "\n", fsync=True)


def knowing(tile: TileRef, shown: TileClouds | None) -> CloudList:
    """The tile's list, knowing every cloud the tile shows replaced (``shown``): one it does not
    (its list put aside, or written on another computer) is added and kept as Apply would have
    left it: ticked, its source and its images those the tile shows (its check), and among the
    clouds last laid, which a build lays again."""
    cl = load(tile)
    if shown is not None and _adopt(cl, shown):
        with update(tile) as cl:
            _adopt(cl, shown)
    return cl


def _adopt(cl: CloudList, shown: TileClouds) -> bool:
    """The clouds ``shown`` holds that ``cl`` does not know, added (:func:`knowing`); whether
    one was."""
    known = {c["id"] for c in cl.clouds}
    new = [e for e in shown.held if e["id"] not in known]
    for e in new:
        c = cl.add("found", [list(r) for r in e["rings"]], tick=True, chosen=e["source"])
        c["id"] = e["id"]
        got = {"answer": "clear", "fit": 0, "entry": laid_fields(e), "judged": JUDGED}
        c["checks"] = {e["source"]: got}  # its images laid: they were judged fit to lay
        cl.laid = [x for x in cl.laid if x["id"] != e["id"]] + [laid_fields(e)]
    return bool(new)


def erase(tile: TileRef) -> None:
    """The tile's clouds forgotten, once its folder shows none (Forget its clouds, after its
    Restore): its list, its pictures and previews, the frozen parts its clouds' images stand on
    but those another tile's list stands on too: named by their bytes alone, a small cloud across
    the line of two tiles reads the same ones from both (a review, 2026-11-07). A search starts
    from nothing then."""
    with _lock(tile):
        mine = _parts(load(tile))
        list_path(tile).unlink(missing_ok=True)
        shutil.rmtree(previews_dir(tile), ignore_errors=True)
        for name in listed():
            mine -= _parts(load(TileRef.parse(name)))
        for digest in mine:
            part_path(digest).unlink(missing_ok=True)


def _parts(cl: CloudList) -> set[str]:
    """The frozen parts the images of the list ``cl`` stand on, those laid and those got."""
    got = [g["entry"] for c in cl.clouds for g in c["checks"].values() if g.get("entry")]
    return {str(p[1]) for e in [*cl.laid, *got] for p in e["parts"].values()}


@contextmanager
def update(tile: TileRef) -> Iterator[CloudList]:
    """The tile's list to change, written back on leaving, one writer at a time per tile; a
    file that is not a list put aside first (``.broken-<n>``, each kept)."""
    with _lock(tile):
        path = list_path(tile)
        if path.exists() and _read(tile) is None:
            taken = [p.name.rpartition("-")[2] for p in path.parent.glob(path.name + ".broken-*")]
            n = max((int(x) for x in taken if x.isdigit()), default=0)
            path.replace(path.with_name(f"{path.name}.broken-{n + 1}"))
        cl = load(tile)
        yield cl
        save(cl)


# -- edits -------------------------------------------------------------------------------------


def pick(cl: CloudList, cid: str, action: str, source: str | None = None) -> None:
    """One cloud's pick, the one way a cloud is ticked or unticked (its state allowed it, the
    caller asked ``state.View.allows``): ``choose`` makes ``source`` its source and ticks it,
    ``tick`` ticks it, ``untick`` unticks it."""
    c = cl.get(cid)
    if action == "choose":
        c["chosen"] = source
    c["tick"] = action != "untick"


def _shape(rings: Sequence[Sequence[float]]) -> Any:
    parts = [Polygon([(r[i + 1], r[i]) for i in range(0, len(r) - 1, 2)]) for r in rings]
    shape = unary_union([p if p.is_valid else make_valid(p) for p in parts])
    return shape


def _overlap(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> bool:
    return bool(_shape(a).intersection(_shape(b)).area > 0)


def _covers(outline: Sequence[Sequence[float]], cloud: Sequence[Sequence[float]]) -> bool:
    """Whether ``outline`` holds half of ``cloud`` or more: it is that cloud."""
    shape = _shape(cloud)
    return bool(_shape(outline).intersection(shape).area >= 0.5 * shape.area > 0)


def merge_search(
    cl: CloudList,
    found: Sequence[Mapping[str, Any]],
    shown: TileClouds | None,
    search: Mapping[str, Any] | None = None,
) -> None:
    """A search's clouds into the list (R3): what X-Plane shows replaced, an outline and a
    ticked cloud stay as they are, a new cloud over one of them is that one; every other cloud
    found before and found again keeps its id and number and takes the new outline and the new
    search's judging of its sources, the images got and the source chosen on the old one gone
    (a source chosen on an old search's fits stayed: Esri cloudy at 27 % over Esri clear at 86 %,
    a user, 2026-11-07); one not found again leaves; a new one is added. ``search``: what the
    search was, kept with the list."""
    if search is not None:
        cl.search = dict(search)
    holds = held_by_id(shown)
    fixed = [c for c in cl.clouds if _kept(c, holds)]
    loose = [c for c in cl.clouds if c not in fixed]
    kept: list[dict[str, Any]] = list(fixed)
    for f in found:
        if any(_overlap(f["rings"], c["rings"]) for c in fixed):
            continue
        old = next((c for c in loose if _overlap(f["rings"], c["rings"])), None)
        if old is not None:
            loose.remove(old)
            old.update(
                rings=f["rings"],
                ha=rings_ha(f["rings"]),
                screening=f["screening"],
                checks={},
                chosen=None,
            )
            kept.append(old)
        else:
            c = cl.add("found", f["rings"], screening=f["screening"])
            cl.clouds.remove(c)
            kept.append(c)
    order = {c["id"]: i for i, c in enumerate(cl.clouds)}
    cl.clouds = sorted(kept, key=lambda c: order.get(c["id"], len(order) + c["number"]))


def _kept(c: Mapping[str, Any], holds: Mapping[str, Any]) -> bool:
    """Whether a search or an outline leaves ``c`` as it is: held, outlined, or ticked."""
    return c["id"] in holds or c["kind"] == "drawn" or bool(c["tick"])


def outline_problem(rings: Any, tile: TileRef) -> str | None:
    """Why ``rings`` drawn by hand are no outline on ``tile``, or ``None``: rings of places on
    Earth, around some ground, on the tile."""
    why = ring_problem(rings)
    if why is not None:
        return why
    if rings_ha(rings) <= 0:
        return "an outline needs three points"
    lat, lon = tile.lat, tile.lon
    box = Polygon([(lon, lat), (lon + 1, lat), (lon + 1, lat + 1), (lon, lat + 1)])
    if not _shape(rings).intersects(box):
        return "an outline is drawn on the tile"
    return None


def add_outline(cl: CloudList, rings: list[list[float]]) -> dict[str, Any]:
    """A cloud outlined by hand, its sources to try (:func:`record_screening`): it comes ticked
    once they are (drawing it is asking to replace)."""
    return cl.add("drawn", rings)


def remove_outline(cl: CloudList, cid: str) -> None:
    """The outline ``cid`` out of the list, as its state allows it (``remove``), or its try
    stopped; nothing when it left already."""
    cl.clouds = [c for c in cl.clouds if c["id"] != cid]


def screening_of(verdicts: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What each source answered under a cloud (``find.Judgement``), as the list keeps it."""
    return {code: {"answer": j.answer, "fit": j.fit} for code, j in verdicts.items()}


def record_screening(
    cl: CloudList, cid: str, verdicts: Mapping[str, Any], shown: TileClouds | None
) -> None:
    """An outline's sources tried, kept with it, unless it left the list meanwhile; the found
    clouds it covers that it does not leave as they are (:func:`_kept`) leave then: it is them.
    Its try stopped or failed, it leaves, and they stay (``remove_outline``)."""
    c = next((x for x in cl.clouds if x["id"] == cid), None)
    if c is None:
        return
    c["screening"] = screening_of(verdicts)
    holds = held_by_id(shown)
    cl.clouds = [
        x for x in cl.clouds if x is c or _kept(x, holds) or not _covers(c["rings"], x["rings"])
    ]


def record_check(cl: CloudList, cid: str, code: str, rings: Any, got: Any) -> None:
    """The images got of ``code`` for the cloud ``cid`` (``find.Check``), kept under that source,
    unless the cloud left the list or changed outline meanwhile (another search)."""
    c = next((x for x in cl.clouds if x["id"] == cid), None)
    if c is None or c["rings"] != rings:
        return
    c.setdefault("checks", {})[code] = {
        "answer": got.judgement.answer,
        "fit": got.judgement.fit,
        "entry": got.entry,
        "judged": JUDGED,
    }


def discard(cl: CloudList, shown: TileClouds | None) -> None:
    """Every tick and source back to what the tile holds."""
    holds = held_by_id(shown)
    for c in cl.clouds:
        c["tick"] = c["id"] in holds
        if c["id"] in holds:
            c["chosen"] = holds[c["id"]]["source"]
