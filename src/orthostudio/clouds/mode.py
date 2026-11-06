# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the cloud mode shows of a tile: its state, each cloud's row, what Apply would change
(``clouds.md`` 1.3, 1.5). One function computes it all from the tile's record and the list, so the
page, the Library and Works say the same; the words are the page's, by key (``ui/i18n.js``).

The tile's state, the first that holds: the data folder away; the tile's disk away; being built;
waiting in the queue; not built (no folder of ours); not whole (a build cut short, or files taken
from it); built by an earlier version (its textures do not say what a cloud is chosen on, or no
settings of that build kept for Apply); its own source gone from the sources; ready.

A cloud's state, the first that holds: read only (the tile not ready); a task under way for it
(getting its images, trying an outline's sources); held (replaced, ticked with its source); back
(replaced, unticked: it comes back as photographed at Apply); ready (ticked, its images got:
replaced at Apply); waiting (ticked, its images not got, with why); found (not ticked).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from orthostudio.clouds.cloudlist import (
    Change,
    CloudList,
    answer_of,
    laid_after,
    needs_images,
    proposable,
    removable,
    tickable,
)
from orthostudio.clouds.tile import TileClouds
from orthostudio.errors import OsxpError

__all__ = [
    "TileFacts",
    "library_row",
    "possible",
    "require",
    "rows",
    "summary",
    "texts",
    "tile_state",
    "useful",
    "views",
    "words",
]


@dataclass(frozen=True, slots=True)
class TileFacts:
    """What the tile's state is decided from."""

    data_away: bool = False
    disk_away: bool = False
    in_build: bool = False
    queued: bool = False
    folder: bool = True
    whole: bool = True
    held: TileClouds | None = None
    settings: bool = True
    own_source: str | None = None
    registry: frozenset[str] = frozenset()


def tile_state(f: TileFacts) -> str:
    """The tile's state, the first that holds (module doc): ``data_away``, ``disk_away``,
    ``in_build``, ``queued``, ``not_built``, ``not_whole``, ``earlier_version``,
    ``own_source_gone``, ``ready``."""
    if f.data_away:
        return "data_away"
    if f.disk_away:
        return "disk_away"
    if f.in_build:
        return "in_build"
    if f.queued:
        return "queued"
    if not f.folder:
        return "not_built"
    if not f.whole:
        return "not_whole"
    if f.held is None or not f.held.described or not f.settings:
        return "earlier_version"
    if f.own_source is not None and f.own_source not in f.registry:
        return "own_source_gone"
    return "ready"


def rows(
    cl: CloudList,
    held: TileClouds | None,
    changes: Sequence[Change],
    possible: frozenset[str],
    tasks: Mapping[str, Mapping[str, Any]],
    previews: Mapping[str, Mapping[str, Any] | None],
) -> list[dict[str, Any]]:
    """Every cloud's row, in laying order: its state, the words of its line (``line``: a key and
    its parameters), what After lays for it (``preview``: :func:`cloudlist.laid_after`'s), the
    actions it allows, of what the tile allows (``possible``, :func:`possible`). ``changes`` what
    Apply would change (``cloudlist.changes``), ``tasks`` and ``previews`` by cloud id."""
    holds = {c["id"]: c for c in (held.applied if held is not None else ())}
    pending = {ch.cloud: ch for ch in changes}
    out: list[dict[str, Any]] = []
    frozen = not possible & LIST_EDITS  # the list read only: no edit of it allowed now
    for c in cl.clouds:
        cid = c["id"]
        was, ch, task = holds.get(cid), pending.get(cid), tasks.get(cid)
        actions: list[str] = []
        if frozen:
            row, line = "frozen", None
        elif task is not None:
            row, line = (
                task["kind"],
                {"key": f"clouds.row.{task['kind']}", "params": {"source": c["source"]}},
            )
            actions = ["stop"]
        elif was is not None and ch is None:
            row, line = "held", {"key": "clouds.row.held", "params": {"source": was["source"]}}
        elif was is not None and ch is not None and ch.kind == "back":
            row, line = "back", {"key": "clouds.row.back", "params": {}}
        elif ch is not None and ch.entry is not None:
            row = "ready"
            line = {
                "key": "clouds.row.another" if was else "clouds.row.ready",
                "params": {"source": c["source"], "held": was["source"] if was else None},
            }
        elif ch is not None:
            row, line = (
                "waiting",
                {"key": f"clouds.row.waiting.{ch.waiting}", "params": {"source": c["source"]}},
            )
            actions = ["check"] if "check" in possible and needs_images(ch) else []
        else:
            row = "found"
            line = None if c.get("best") else {"key": "clouds.row.no_source", "params": {}}
        if not frozen and task is None:
            actions += ["untick"] if c["tick"] else (["tick"] if tickable(c) else [])
            actions += ["choose"]
            if "remove" in possible and removable(c, held):
                actions.append("remove")
        out.append(
            {
                "id": cid,
                "kind": c["kind"],
                "number": c["number"],
                "rings": c["rings"],
                "ha": c.get("ha", 0.0),
                "tick": c["tick"],
                "source": c["source"],
                "state": row,
                "line": line,
                "actions": actions,
                "preview": previews.get(cid),
                "menu": _menu(c),
            }
        )
    return out


def _menu(c: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Its source and every source tried under the cloud: what each has there
    (:func:`cloudlist.answer_of`), its fit, whether it may be chosen
    (:func:`cloudlist.proposable`), the best first."""
    screening = c.get("screening") or {}
    own = [c["source"]] if c.get("source") and c["source"] not in screening else []
    codes = [*screening, *own]
    items = [
        {
            "source": code,
            "answer": answer_of(c, code),
            "fit": (screening.get(code) or {}).get("fit", 0),
            "best": code == c.get("best"),
            "proposable": proposable(c, code),
        }
        for code in codes
    ]
    order = {"clear": 0, "cloudy": 1}
    return sorted(
        items, key=lambda i: (order.get(i["answer"], 2), -int(i["fit"] or 0), i["source"])
    )


def summary(cl: CloudList, held: TileClouds | None, changes: Sequence[Change]) -> dict[str, Any]:
    """What Apply would change, counted from what it lays (:func:`cloudlist.laid_after`):
    ``replace`` (laid, not held), ``another`` (laid with another source than held), ``back``
    (held, not laid), ``waiting`` (ticked, nothing to lay yet: left out); ``held``, the clouds
    the tile holds replaced."""
    holds = {c["id"]: c["source"] for c in (held.applied if held is not None else ())}
    laid = {e["id"]: e["source"] for e in laid_after(cl, held, changes)}
    counts = {
        "replace": sum(1 for i in laid if i not in holds),
        "another": sum(1 for i, s in laid.items() if i in holds and s != holds[i]),
        "back": sum(1 for i in holds if i not in laid),
        "waiting": sum(1 for c in cl.clouds if c["tick"] and c["id"] not in laid),
        "held": len(holds),
    }
    counts["changes"] = counts["replace"] + counts["another"] + counts["back"]
    return counts


LIST_EDITS = frozenset({"tick", "untick", "choose", "remove", "discard"})
"""The list's own changes: they wait for the next Apply, whatever the tile does meanwhile. The
names of every action are the same for the tile, its rows, the routes and the page."""
TASKS = frozenset({"find", "outline", "check"})
"""What reads the tile's folder (its picture, its textures): not while a build writes it."""
BUILDS = frozenset({"apply", "restore"})


def possible(state: str, tasks: bool = False) -> frozenset[str]:
    """What the tile allows in ``state`` (``clouds.md`` 1.5): everything when it is ready, the
    list's edits while a build writes it or waits to, no build while its own source is gone,
    nothing else; and no build while a task of the tile reads its folder (``tasks``). The routes
    refuse the rest (:func:`require`), the page greys it."""
    can = {
        "ready": LIST_EDITS | TASKS | BUILDS,
        "in_build": LIST_EDITS,
        "queued": LIST_EDITS,
        "own_source_gone": LIST_EDITS | TASKS,
    }.get(state, frozenset())
    return can - BUILDS if tasks else can


def readable(state: str) -> bool:
    """Whether the tile's folder may be read now (its picture, its textures): whole, and no build
    writing it."""
    return bool(possible(state) & TASKS)


def useful(can: frozenset[str], counts: Mapping[str, int], searching: bool) -> frozenset[str]:
    """Of ``can``, what would do something: Apply a change, Restore a replaced cloud, Discard a
    change or a wait, Find when no search runs."""
    idle = {
        "apply": counts["changes"] == 0,
        "restore": counts["held"] == 0,
        "discard": counts["changes"] == 0 and counts["waiting"] == 0,
        "find": searching,
    }
    return frozenset(a for a in can if not idle.get(a, False))


def require(tile: str, state: str, action: str, tasks: bool = False) -> None:
    """``CLOUD_TILE_NOT_READY`` unless the tile allows ``action`` now (:func:`possible`)."""
    if action not in possible(state, tasks):
        why = "tasks" if action in possible(state) else state
        raise OsxpError("CLOUD_TILE_NOT_READY", context={"tile": tile, "state": why})


def views(counts: Mapping[str, int], picture: bool) -> dict[str, Any]:
    """The words of the map's two views: After (what X-Plane shows, or will once Apply builds the
    changes) and Before; none without a picture of the tile (the tile's line says why)."""
    if not picture:
        return {"after": None, "before": None}
    after = "clouds.after_pending" if counts["changes"] > 0 else "clouds.after"
    return {"after": after, "before": "clouds.before"}


def words(state: str, cl: CloudList, searching: bool) -> dict[str, Any] | None:
    """The tile's line: its state's, else when it has no cloud listed and no search runs, whether
    one found none or none was made; ``None`` otherwise."""
    if state != "ready":
        return {"key": f"clouds.tile.{state}", "params": {}}
    if cl.clouds or searching:
        return None
    return {"key": "clouds.none_found" if cl.search else "clouds.not_looked", "params": {}}


def texts(tile: str, counts: Mapping[str, int]) -> dict[str, Any]:
    """The words of the count line, of Apply's button and of the questions of Apply and Restore,
    each a key and its parameters: what they say is what Apply would build (:func:`summary`)."""
    n = counts["changes"]
    line = [
        _k(f"clouds.pending.{k}", n=counts[k]) for k in ("replace", "another", "back", "waiting")
    ]
    count = [w for w in line if w["params"]["n"] > 0] or [_k("clouds.pending_none")]
    one = "_one" if n == 1 else ""
    told = [w for w in count if w["key"] != "clouds.pending.waiting"]
    waits = [_k("clouds.apply_waiting", n=counts["waiting"])] if counts["waiting"] else []
    return {
        "count": count,
        "apply": _k(f"clouds.apply{one}", n=n),
        "apply_question": {
            "title": _k(f"clouds.apply_title{one}", tile=tile, n=n),
            "lines": [*told, *waits, _k("clouds.apply_text")],
            "go": _k("clouds.apply_go"),
        },
        "restore_question": {
            "title": _k("clouds.restore_title", tile=tile),
            "lines": [_k("clouds.restore_text", n=counts["held"])],
            "go": _k("clouds.restore_go"),
        },
    }


def _k(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def library_row(state: str, counts: Mapping[str, int]) -> dict[str, Any]:
    """The Library's Clouds column: the tile's state and its counts."""
    return {"state": state, "replaced": counts.get("held", 0), "to_apply": counts.get("changes", 0)}
