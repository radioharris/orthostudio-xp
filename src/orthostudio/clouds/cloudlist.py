# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's list of a tile's clouds: what was found or outlined, and what is ticked with which
source (``clouds.md`` 2.4, R3, R4).

``$OSXP_HOME/clouds/<tile>.json``: every cloud with its id (eight characters, never given again
on the tile), its number (``Cloud 7``, ``Outline 2``), its outline, what each source has under it
(the search's screening), its tick, its source, and the images got of each source tried (its
checks). The list is the draft: each tick or source is saved as it is made and survives closing,
quitting and every build; only Apply changes what X-Plane shows, from the list's ticks against
what the tile holds (:func:`changes`). Its order is the order clouds are laid in. It keeps the
clouds last applied too (``laid``), which a build of the tile lays again (``laying.lay_again``).
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
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from orthostudio.clouds.sources import ASK_AGAIN, PROPOSABLE
from orthostudio.clouds.tile import (
    TileClouds,
    entry_problem,
    held_by_id,
    is_own_folder,
    list_path,
    previews_dir,
    stands_not,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text
from orthostudio.model import TileRef

__all__ = ["ApplyPlan", "Change", "CloudList", "forgetting", "load", "plan", "update"]

FORMAT = "osxp-cloud-list-2"
_ID = string.ascii_lowercase + string.digits
_LOCKS: dict[str, threading.Lock] = {}
_GENERATIONS: dict[str, int] = {}
"""Each tile's list's generation: forgetting the tile moves it on, and a task's write begun on an
earlier one is dropped (:func:`update`)."""
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
            "source": None,
            "screening": {},
            "best": None,
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


def generation(tile: TileRef) -> int:
    """The tile's list's generation now (:data:`_GENERATIONS`)."""
    with _GUARD:
        return _GENERATIONS.get(tile.name, 0)


def load(tile: TileRef) -> CloudList:
    """The tile's list, read and nothing else; a new one when it has none or it cannot be read as
    a list (:func:`update` puts such a file aside before writing)."""
    return _read(tile) or CloudList(tile.name, [])


def _read(tile: TileRef) -> CloudList | None:
    """The list in the tile's file, ``None`` when there is none or it is not one (a passing read
    error is raised: a draft is never dropped for it)."""
    try:
        doc = json.loads(list_path(tile).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("tile") != tile.name:
            return None
        laid = [e for e in doc.get("laid") or () if entry_problem(e) is None]
        return CloudList(
            tile.name, list(doc["clouds"]), int(doc.get("number", 0)), doc.get("search"), laid
        )
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError):
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
    atomic_write_text(path, json.dumps(doc, indent=1) + "\n")


def forget(tile: TileRef) -> None:
    """The tile's list and its previews gone, with the tile; a task's write begun before dropped."""
    with _lock(tile):
        with _GUARD:
            _GENERATIONS[tile.name] = _GENERATIONS.get(tile.name, 0) + 1
        list_path(tile).unlink(missing_ok=True)
        shutil.rmtree(previews_dir(tile), ignore_errors=True)


@contextmanager
def forgetting(tile: TileRef, folder: Path, *, custom_scenery: Path | None) -> Iterator[bool]:
    """Around the deletion of ``folder``: whether it is the tile's own folder, its clouds' (asked
    before the Library forgets it), and once it is deleted, those clouds forgotten."""
    ours = is_own_folder(tile, folder, custom_scenery=custom_scenery)
    yield ours
    if ours:
        forget(tile)


@contextmanager
def update(tile: TileRef, *, since: int | None = None) -> Iterator[CloudList]:
    """The tile's list to change, written back on leaving, one writer at a time per tile; a
    file that is not a list put aside first (``.broken-<n>``, each kept). ``since``: the
    generation a task began on; the tile forgotten since, nothing is written."""
    with _lock(tile):
        if since is not None and since != generation(tile):
            raise OsxpError("SYS_CANCELLED")
        path = list_path(tile)
        if path.exists() and _read(tile) is None:
            taken = [p.name.rpartition("-")[2] for p in path.parent.glob(path.name + ".broken-*")]
            n = max((int(x) for x in taken if x.isdigit()), default=0)
            path.replace(path.with_name(f"{path.name}.broken-{n + 1}"))
        cl = load(tile)
        yield cl
        save(cl)


# -- edits -------------------------------------------------------------------------------------


def reading(c: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    """What the source ``code`` has under the cloud ``c``, the newest said, its answer and its
    fit together: its images got, else the search's or the outline's try; empty when it was
    never asked there."""
    for said in ((c.get("checks") or {}).get(code), (c.get("screening") or {}).get(code)):
        if said and said.get("answer"):
            return said
    return {}


def answer_of(c: Mapping[str, Any], code: str) -> str | None:
    """What the source ``code`` answered under the cloud ``c`` (:func:`reading`)."""
    answer = reading(c, code).get("answer")
    return None if answer is None else str(answer)


def proposable(c: Mapping[str, Any], code: str) -> bool:
    """Whether the source ``code`` may be chosen for ``c`` (``sources.PROPOSABLE``)."""
    answer = answer_of(c, code)
    return answer is None or answer in PROPOSABLE


def source_of(c: Mapping[str, Any]) -> str | None:
    """The source ``c`` is replaced with: its own, else its best."""
    return c.get("source") or c.get("best")


def tickable(c: Mapping[str, Any]) -> bool:
    """Whether ``c`` may be ticked: it has a source to be replaced with (:func:`source_of`)."""
    return source_of(c) is not None


def pick_action(tick: bool | None, source: str | None) -> str:
    """What a pick is: ``choose`` a source, else ``untick`` or ``tick``."""
    if source is not None:
        return "choose"
    return "untick" if tick is False else "tick"


def pick(cl: CloudList, cid: str, action: str, source: str | None = None) -> None:
    """One cloud's pick (:func:`pick_action`): ``choose`` gives it ``source`` and ticks it, a
    source that may not be chosen there (:func:`proposable`) refused; ``tick`` ticks it with
    its source; ``untick`` unticks it, its source kept."""
    c = cl.get(cid)
    if action == "choose":
        if source is None or not proposable(c, source):
            said = None if source is None else answer_of(c, source)
            raise OsxpError(
                "CLOUD_INVALID",
                context={"tile": cl.tile, "cloud": cid, "reason": f"{source} answered {said}"},
            )
        c["source"] = source
    elif action == "tick":
        c["source"] = source_of(c)
    c["tick"] = action != "untick" and tickable(c)


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
    found before and found again keeps its id and number and takes the new outline; one not found
    again leaves; a new one is added. ``search``: what the search was, kept with the list."""
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
                best=f["best"],
                checks={},
            )
            old["source"] = source_of(old)
            kept.append(old)
        else:
            c = cl.add("found", f["rings"], screening=f["screening"], best=f["best"])
            c["source"] = source_of(c)
            cl.clouds.remove(c)
            kept.append(c)
    order = {c["id"]: i for i, c in enumerate(cl.clouds)}
    cl.clouds = sorted(kept, key=lambda c: order.get(c["id"], len(order) + c["number"]))


def _kept(c: Mapping[str, Any], holds: Mapping[str, Any]) -> bool:
    """Whether a search or an outline leaves ``c`` as it is: held, outlined, or ticked."""
    return c["id"] in holds or c["kind"] == "drawn" or bool(c["tick"])


def add_outline(
    cl: CloudList, rings: list[list[float]], shown: TileClouds | None
) -> dict[str, Any]:
    """A cloud outlined by hand: the found clouds it covers that it does not leave as they are
    (:func:`_kept`) leave (it is them); it comes ticked once its sources are tried (drawing it is
    asking to replace)."""
    holds = held_by_id(shown)
    cl.clouds = [c for c in cl.clouds if _kept(c, holds) or not _covers(rings, c["rings"])]
    return cl.add("drawn", rings)


def removable(c: Mapping[str, Any], shown: TileClouds | None) -> bool:
    """Whether ``c`` may leave the list: an outline the tile does not hold (a cloud leaves the
    list only when X-Plane no longer shows it replaced; unticked, it comes back at Apply)."""
    return c["kind"] == "drawn" and c["id"] not in held_by_id(shown)


def remove_outline(cl: CloudList, cid: str, shown: TileClouds | None) -> None:
    c = cl.get(cid)
    if not removable(c, shown):
        raise OsxpError(
            "CLOUD_INVALID",
            context={"tile": cl.tile, "cloud": cid, "reason": "only an outline not replaced"},
        )
    cl.clouds.remove(c)


def screening_of(verdicts: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What each source answered under a cloud (``find.Judgement``), as the list keeps it."""
    return {code: {"answer": j.answer, "fit": j.fit} for code, j in verdicts.items()}


def best_of(c: Mapping[str, Any]) -> str | None:
    """The source proposed for the cloud ``c``: of those clear under it, the one whose colours
    fit best, each as its newest reading says (:func:`reading`)."""
    codes = {*(c.get("screening") or {}), *(c.get("checks") or {})}
    said = [(reading(c, k), k) for k in codes]
    clear = [(int(r.get("fit") or 0), k) for r, k in said if r.get("answer") == "clear"]
    return max(clear)[1] if clear else None


def record_screening(cl: CloudList, cid: str, verdicts: Mapping[str, Any]) -> None:
    """An outline's sources tried: their answers, and its best ticked (drawing it is asking to
    replace it); nothing when it left the list meanwhile."""
    c = next((x for x in cl.clouds if x["id"] == cid), None)
    if c is None:
        return
    c["screening"] = screening_of(verdicts)
    c["best"] = best_of(c)
    if c["best"] is not None:
        pick(cl, cid, "choose", c["best"])


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
    }


def untick_all(cl: CloudList) -> None:
    """Every cloud of the list unticked, its source kept (Restore)."""
    for c in cl.clouds:
        c["tick"] = False


def discard(cl: CloudList, shown: TileClouds | None) -> None:
    """Every tick and source back to what the tile holds."""
    holds = held_by_id(shown)
    for c in cl.clouds:
        c["tick"] = c["id"] in holds
        if c["id"] in holds:
            c["source"] = holds[c["id"]]["source"]


# -- what Apply lays -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Change:
    """One cloud whose tick or source differs from what the tile holds: ``replace``, ``another``
    (source) or ``back`` (as photographed); ``entry`` the cloud as Apply lays it, ``None``
    for ``back`` or while its images are not got (``waiting``: why)."""

    cloud: str
    kind: str
    source: str | None
    entry: dict[str, Any] | None
    waiting: str | None = None


def _changes(cl: CloudList, shown: TileClouds | None, tile: TileRef) -> list[Change]:
    """What Apply would change: each ticked cloud with another source than the tile holds or not
    held at all, and each held cloud unticked."""
    holds = held_by_id(shown)
    out: list[Change] = []
    for c in cl.clouds:
        was = holds.get(c["id"])
        if not c["tick"]:
            if was is not None:
                out.append(Change(c["id"], "back", None, None))
            continue
        if was is not None and was["source"] == c["source"]:
            continue
        got = (c.get("checks") or {}).get(c["source"]) or {}
        entry = got.get("entry")
        waiting = None
        if entry is None:
            waiting = got.get("answer") or "no_images"
        elif shown is not None and (why := stands_not(entry, tile, shown)) is not None:
            waiting, entry = f"photo_{why}", None
        out.append(
            Change(
                c["id"], "another" if was is not None else "replace", c["source"], entry, waiting
            )
        )
    return out


def needs_images(ch: Change | None) -> bool:
    """Whether a change waits for images that asking may bring: none got yet, a photo that
    changed since, or an answer that asking again may change (``sources.ASK_AGAIN``)."""
    if ch is None or ch.kind == "back" or ch.entry is not None or ch.waiting is None:
        return False
    return ch.waiting == "no_images" or ch.waiting.startswith("photo_") or ch.waiting in ASK_AGAIN


def _laid_after(
    cl: CloudList, shown: TileClouds | None, pending: Sequence[Change]
) -> list[dict[str, Any]]:
    """The clouds Apply lays in the tile, and After shows, in the list's order: each ticked
    cloud with the newest images that stand, its change's when they are in, else what the tile
    holds of it; a cloud with neither waits, left out (the question said so). ``pending``: what
    :func:`_changes` gives."""
    holds = held_by_id(shown)
    got = {ch.cloud: ch.entry for ch in pending if ch.entry is not None}
    out: list[dict[str, Any]] = []
    for c in cl.clouds:
        entry = got.get(c["id"]) or holds.get(c["id"]) if c["tick"] else None
        if entry is not None:
            out.append({**entry, "kind": c["kind"], "number": c["number"]})
    return out


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    """What Apply would do, said once for the count, the rows, After and Apply itself: the
    ``changes`` against what the tile holds, and the clouds it lays (``laid``, in order)."""

    changes: list[Change]
    laid: list[dict[str, Any]]

    def change_of(self, cid: str) -> Change | None:
        return next((ch for ch in self.changes if ch.cloud == cid), None)


def plan(cl: CloudList, shown: TileClouds | None, tile: TileRef) -> ApplyPlan:
    """What Apply would do with the list ``cl`` on the tile as ``shown``."""
    pending = _changes(cl, shown, tile)
    return ApplyPlan(pending, _laid_after(cl, shown, pending))
