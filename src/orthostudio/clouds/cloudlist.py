# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's list of a tile's clouds: what was found or outlined, and what is ticked with which
source (``clouds.md`` 2.4, R3, R4).

``$OSXP_HOME/clouds/<tile>.json``: every cloud with its id (eight characters, never given again
on the tile), its number (``Cloud 7``, ``Outline 2``), its outline, what each source has under it
(the search's screening), its tick, its source, and the images got of each source tried (its
checks). The list is the draft: each tick or source is saved as it is made and survives closing,
quitting and every build; only Apply changes what X-Plane shows, from the list's ticks against
what the tile holds (:func:`changes`). Its order is the order clouds are laid in.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from orthostudio.clouds.laying import standing
from orthostudio.clouds.sources import PROPOSABLE
from orthostudio.clouds.tile import TileClouds, list_path, own_folder, previews_dir
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text
from orthostudio.imagery.grid import parse_texture_name
from orthostudio.model import TileRef

__all__ = ["Change", "CloudList", "changes", "forgetting", "laid_after", "load", "update"]

FORMAT = "osxp-cloud-list-1"
_ID = string.ascii_lowercase + string.digits
_LOCKS: dict[str, threading.Lock] = {}
_DEGREE_M = 111_320.0  # a degree of latitude, in metres
_GUARD = threading.Lock()


@dataclass
class CloudList:
    """The list as data: ``clouds`` in laying order, each a JSON object (module doc)."""

    tile: str
    clouds: list[dict[str, Any]]
    number: int = 0
    search: dict[str, Any] | None = None

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

    def add(self, kind: str, rings: list[list[float]], ha: float, **more: Any) -> dict[str, Any]:
        self.number += 1
        c = {
            "id": self.new_id(),
            "kind": kind,
            "number": self.number,
            "rings": rings,
            "ha": ha,
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


def load(tile: TileRef, held: TileClouds | None = None) -> CloudList:
    """The tile's list; a new one when it has none, a broken one moved aside and made again.
    Every cloud the tile holds is in it, ticked with its source (a list lost or a pack brought
    from another computer)."""
    path = list_path(tile)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") != FORMAT or doc.get("tile") != tile.name:
            raise ValueError("not this tile's list")
        cl = CloudList(tile.name, list(doc["clouds"]), int(doc.get("number", 0)), doc.get("search"))
    except FileNotFoundError:
        cl = CloudList(tile.name, [])
    except (OSError, ValueError, KeyError, TypeError):
        path.replace(path.with_name(path.name + ".broken"))
        cl = CloudList(tile.name, [])
    for entry in held.applied if held is not None else ():
        if not any(c["id"] == entry["id"] for c in cl.clouds):
            number = int(entry.get("number") or cl.number + 1)
            cl.number = max(cl.number, number)
            cl.clouds.append(
                {
                    "id": entry["id"],
                    "kind": entry.get("kind", "found"),
                    "number": number,
                    "rings": entry["rings"],
                    "ha": rings_ha(entry["rings"]),
                    "tick": True,
                    "source": entry["source"],
                    "screening": {},
                    "best": None,
                    "checks": {},
                }
            )
    return cl


def save(cl: CloudList) -> None:
    doc = {
        "format": FORMAT,
        "tile": cl.tile,
        "number": cl.number,
        "search": cl.search,
        "clouds": cl.clouds,
    }
    path = list_path(TileRef.parse(cl.tile))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, indent=1) + "\n")


def forget(tile: TileRef) -> None:
    """The tile's list and its previews gone, with the tile."""
    with _lock(tile):
        list_path(tile).unlink(missing_ok=True)
        shutil.rmtree(previews_dir(tile), ignore_errors=True)


@contextmanager
def forgetting(tile: TileRef, folder: Path, *, custom_scenery: Path | None) -> Iterator[bool]:
    """Around the deletion of ``folder``: whether it is the tile's own folder, its clouds' (asked
    before the Library forgets it), and once it is deleted, those clouds forgotten."""
    ours = Path(folder).resolve() == own_folder(tile, custom_scenery=custom_scenery).resolve()
    yield ours
    if ours:
        forget(tile)


@contextmanager
def update(tile: TileRef, held: TileClouds | None = None) -> Iterator[CloudList]:
    """The tile's list to change, written back on leaving, one writer at a time per tile."""
    with _lock(tile):
        cl = load(tile, held)
        yield cl
        save(cl)


# -- edits -------------------------------------------------------------------------------------


def pick(cl: CloudList, cid: str, *, tick: bool | None = None, source: str | None = None) -> None:
    """A tick or a source of one cloud: choosing a source ticks it, unticking keeps it; a source
    whose answer there it is no use asking again cannot be chosen (``sources.PROPOSABLE``)."""
    c = cl.get(cid)
    said = ((c.get("screening") or {}).get(source) or {}).get("answer") if source else None
    if said is not None and said not in PROPOSABLE:
        raise OsxpError(
            "CLOUD_INVALID",
            context={"tile": cl.tile, "cloud": cid, "reason": f"{source} answered {said} there"},
        )
    if source is not None:
        c["source"], c["tick"] = source, True if tick is None else tick
    elif tick is not None:
        if tick and c["source"] is None:
            c["source"] = c.get("best")
        c["tick"] = bool(tick) and c["source"] is not None


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
    held: TileClouds | None,
    search: Mapping[str, Any] | None = None,
) -> None:
    """A search's clouds into the list (R3): what X-Plane shows replaced, an outline and a
    ticked cloud stay as they are, a new cloud over one of them is that one; every other cloud
    found before and found again keeps its id and number and takes the new outline; one not found
    again leaves; a new one is added. ``search``: what the search was, kept with the list."""
    if search is not None:
        cl.search = dict(search)
    holds = {c["id"] for c in (held.applied if held is not None else ())}
    fixed = [c for c in cl.clouds if c["id"] in holds or c["kind"] == "drawn" or c["tick"]]
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
                ha=f["ha"],
                shadow=f.get("shadow", False),
                screening=f["screening"],
                best=f["best"],
                checks={},
            )
            old["source"] = old["source"] or f["best"]
            kept.append(old)
        else:
            c = cl.add(
                "found",
                f["rings"],
                f["ha"],
                shadow=f.get("shadow", False),
                screening=f["screening"],
                best=f["best"],
            )
            c["source"] = f["best"]
            cl.clouds.remove(c)
            kept.append(c)
    order = {c["id"]: i for i, c in enumerate(cl.clouds)}
    cl.clouds = sorted(kept, key=lambda c: order.get(c["id"], len(order) + c["number"]))


def add_outline(
    cl: CloudList, rings: list[list[float]], ha: float, held: TileClouds | None
) -> dict[str, Any]:
    """A cloud outlined by hand: the found clouds it covers that the tile does not hold leave
    (it is them); it comes ticked once its sources are tried (drawing it is asking to replace)."""
    holds = {c["id"] for c in (held.applied if held is not None else ())}
    cl.clouds = [
        c
        for c in cl.clouds
        if not (
            c["kind"] == "found"
            and not c["tick"]
            and c["id"] not in holds
            and _covers(rings, c["rings"])
        )
    ]
    return cl.add("drawn", rings, ha)


def removable(c: Mapping[str, Any], held: TileClouds | None) -> bool:
    """Whether ``c`` may leave the list: an outline the tile does not hold (a cloud leaves the
    list only when X-Plane no longer shows it replaced; unticked, it comes back at Apply)."""
    return c["kind"] == "drawn" and (held is None or held.held(c["id"]) is None)


def remove_outline(cl: CloudList, cid: str, held: TileClouds | None) -> None:
    c = cl.get(cid)
    if not removable(c, held):
        raise OsxpError(
            "CLOUD_INVALID",
            context={"tile": cl.tile, "cloud": cid, "reason": "only an outline not replaced"},
        )
    cl.clouds.remove(c)


def screening_of(verdicts: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What each source answered under a cloud (``find.Judgement``), as the list keeps it."""
    return {code: {"answer": j.answer, "fit": j.fit} for code, j in verdicts.items()}


def best_of(screening: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The source proposed for a cloud: the clear one whose colours fit best."""
    clear = [(s.get("fit", 0), code) for code, s in screening.items() if s["answer"] == "clear"]
    return max(clear)[1] if clear else None


def record_screening(cl: CloudList, cid: str, verdicts: Mapping[str, Any]) -> None:
    """An outline's sources tried: their answers, and its best ticked (drawing it is asking to
    replace it)."""
    c = cl.get(cid)
    c["screening"] = screening_of(verdicts)
    c["best"] = best_of(c["screening"])
    if c["best"] is not None:
        pick(cl, cid, source=c["best"])


def record_check(cl: CloudList, cid: str, code: str, rings: Any, got: Any) -> None:
    """The images got of ``code`` for the cloud ``cid`` (``find.Check``), kept under that source,
    unless the cloud changed outline meanwhile (another search)."""
    c = cl.get(cid)
    if c["rings"] != rings:
        return
    c.setdefault("checks", {})[code] = {
        "answer": got.judgement.answer,
        "fit": got.judgement.fit,
        "entry": got.entry,
    }


def restore(cl: CloudList) -> None:
    for c in cl.clouds:
        c["tick"] = False


def discard(cl: CloudList, held: TileClouds | None) -> None:
    """Every tick and source back to what the tile holds."""
    holds = {c["id"]: c["source"] for c in (held.applied if held is not None else ())}
    for c in cl.clouds:
        c["tick"] = c["id"] in holds
        if c["id"] in holds:
            c["source"] = holds[c["id"]]


# -- what Apply builds -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Change:
    """One cloud whose tick or source differs from what the tile holds: ``replace``, ``another``
    (source) or ``back`` (as photographed); ``entry`` the cloud as the build lays it, ``None``
    for ``back`` or while its images are not got (``waiting``: why)."""

    cloud: str
    kind: str
    source: str | None
    entry: dict[str, Any] | None
    waiting: str | None = None


def changes(cl: CloudList, held: TileClouds | None, tile: TileRef) -> list[Change]:
    """What Apply would change: each ticked cloud with another source than the tile holds or not
    held at all, and each held cloud unticked."""
    holds = {c["id"]: c for c in (held.applied if held is not None else ())}
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
        elif held is not None and (why := _stands_not(entry, held, tile)) is not None:
            waiting, entry = f"photo_{why}", None
        out.append(
            Change(
                c["id"], "another" if was is not None else "replace", c["source"], entry, waiting
            )
        )
    return out


def needs_images(ch: Change | None) -> bool:
    """Whether a change waits for images that asking may bring: none got yet, a photo that
    changed since, or a source that did not answer or refused for now."""
    if ch is None or ch.kind == "back" or ch.entry is not None or ch.waiting is None:
        return False
    return ch.waiting == "no_images" or ch.waiting.startswith("photo_") or ch.waiting in PROPOSABLE


def _stands_not(entry: Mapping[str, Any], held: TileClouds, tile: TileRef) -> str | None:
    """Why the images got for a cloud no longer stand on the photo the tile holds: the build's
    own rule, on what the tile's folder says of its textures."""
    textures = [parse_texture_name(n) for n in held.chunks]
    return standing(entry, tile, textures, colours=held.colours, chunks=held.chunks)[0]


def laid_after(
    cl: CloudList, held: TileClouds | None, pending: Sequence[Change]
) -> list[dict[str, Any]]:
    """The clouds Apply builds the tile with, and After shows, in the list's order: each ticked
    cloud with the newest images that stand, its change's when they are in, else what the tile
    holds of it; a cloud with neither waits, left out (the question said so). ``pending``: what
    :func:`changes` gives."""
    holds = {c["id"]: c for c in (held.applied if held is not None else ())}
    got = {ch.cloud: ch.entry for ch in pending if ch.entry is not None}
    out: list[dict[str, Any]] = []
    for c in cl.clouds:
        entry = got.get(c["id"]) or holds.get(c["id"]) if c["tick"] else None
        if entry is not None:
            out.append({**entry, "kind": c["kind"], "number": c["number"]})
    return out
