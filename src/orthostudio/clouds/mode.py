# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the cloud mode shows of a tile: its state, each cloud's row, what Apply would change
(``clouds.md`` 1.3, 1.5). One function computes it all from the tile's record and the list, so the
page, the Library and Works say the same; the words are the page's, by key (``ui/i18n.js``).

The tile's state, the first that holds: the data folder away; the tile's disk away; being built;
waiting in the queue; not built (no folder of ours); built by an earlier version (its textures do
not say what a cloud is chosen on, or no settings of that build kept for Apply); its own
source gone from the sources; ready.

A cloud's state, the first that holds: read only (the tile not ready); a task under way for it
(getting its images, trying an outline's sources); held (replaced, ticked with its source); back
(replaced, unticked: it comes back as photographed at Apply); ready (ticked, its images got:
replaced at Apply); waiting (ticked, its images not got, with why); found (not ticked).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from orthostudio.clouds.cloudlist import CloudList, changes
from orthostudio.clouds.tile import TileClouds
from orthostudio.model import TileRef

__all__ = ["TILE_STATES", "TileFacts", "library_row", "rows", "summary", "tile_state", "words"]

TILE_STATES = (
    "data_away",
    "disk_away",
    "in_build",
    "queued",
    "not_built",
    "earlier_version",
    "own_source_gone",
    "ready",
)


@dataclass(frozen=True, slots=True)
class TileFacts:
    """What the tile's state is decided from."""

    data_away: bool = False
    disk_away: bool = False
    in_build: bool = False
    queued: bool = False
    folder: bool = True
    held: TileClouds | None = None
    settings: bool = True
    own_source: str | None = None
    registry: frozenset[str] = frozenset()


def tile_state(f: TileFacts) -> str:
    """The tile's state, the first of :data:`TILE_STATES` that holds."""
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
    if f.held is None or not f.held.described or not f.settings:
        return "earlier_version"
    if f.own_source is not None and f.own_source not in f.registry:
        return "own_source_gone"
    return "ready"


def rows(
    cl: CloudList,
    held: TileClouds | None,
    tile: TileRef,
    state: str,
    tasks: Mapping[str, Mapping[str, Any]],
    previews: Mapping[str, Mapping[str, Any] | None],
) -> list[dict[str, Any]]:
    """Every cloud's row, in laying order: its state, the words of its line (``line``: a key and
    its parameters), what After lays for it (``preview``), the actions it allows. ``tasks`` by
    cloud id, ``previews`` by cloud id: the preview of what After lays (held or new)."""
    holds = {c["id"]: c for c in (held.applied if held is not None else ())}
    pending = {ch.cloud: ch for ch in changes(cl, held, tile)} if held is not None else {}
    undone = dict(held.undone) if held is not None else {}
    out: list[dict[str, Any]] = []
    frozen = state not in ("ready", "in_build", "queued")
    for c in cl.clouds:
        cid = c["id"]
        was, ch, task = holds.get(cid), pending.get(cid), tasks.get(cid)
        actions: list[str] = []
        if frozen:
            row, line = "frozen", None
        elif task is not None:
            row, line = (
                task["kind"],
                {
                    "key": f"clouds.row.{task['kind']}",
                    "params": {"source": c["source"], "progress": task.get("progress")},
                },
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
            actions = ["get_images"]
        else:
            row = "found"
            line = None if c.get("best") else {"key": "clouds.row.no_source", "params": {}}
        if not frozen and task is None:
            actions += ["untick"] if c["tick"] else ["tick"]
            actions += ["choose"]
            if c["kind"] == "drawn":
                actions.append("remove")
        lays = row in ("held", "ready") or (row in ("waiting", "getting") and was is not None)
        note = (
            {"key": f"clouds.row.came_back.{undone[cid]}", "params": {}}
            if cid in undone and c["tick"]
            else None
        )
        out.append(
            {
                "id": cid,
                "kind": c["kind"],
                "number": c["number"],
                "rings": c["rings"],
                "ha": c.get("ha", 0.0),
                "shadow": c.get("shadow", False),
                "tick": c["tick"],
                "source": c["source"],
                "state": row,
                "line": line,
                "note": note,
                "actions": actions,
                "preview": previews.get(cid) if lays else None,
                "menu": _menu(c),
            }
        )
    return out


def _menu(c: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The sources under a cloud, what each has there and its fit, the best first."""
    got = c.get("checks") or {}
    items = []
    for code, s in (c.get("screening") or {}).items():
        answer = (got.get(code) or {}).get("answer") or s.get("answer")
        items.append(
            {
                "source": code,
                "answer": answer,
                "fit": s.get("fit", 0),
                "best": code == c.get("best"),
            }
        )
    if c.get("source") and not any(i["source"] == c["source"] for i in items):
        # its source, never screened here (a cloud the tile holds, its list lost)
        answer = (got.get(c["source"]) or {}).get("answer")
        items.append({"source": c["source"], "answer": answer, "fit": 0, "best": False})
    order = {"clear": 0, "cloudy": 1}
    return sorted(
        items, key=lambda i: (order.get(i["answer"], 2), -int(i["fit"] or 0), i["source"])
    )


def summary(cl: CloudList, held: TileClouds | None, tile: TileRef) -> dict[str, Any]:
    """What Apply would change, counted: ``replace``, ``another``, ``back``, and those ``waiting``
    for their images; ``held``, the clouds the tile holds replaced."""
    counts = {"replace": 0, "another": 0, "back": 0, "waiting": 0}
    for ch in changes(cl, held, tile) if held is not None else ():
        counts["waiting" if ch.entry is None and ch.kind != "back" else ch.kind] += 1
    counts["held"] = len(held.applied) if held is not None else 0
    counts["changes"] = counts["replace"] + counts["another"] + counts["back"]
    return counts


def words(state: str) -> dict[str, Any] | None:
    """The tile's line for its state, or ``None`` when ready."""
    return None if state == "ready" else {"key": f"clouds.tile.{state}", "params": {}}


def library_row(state: str, counts: Mapping[str, int]) -> dict[str, Any]:
    """The Library's Clouds column: the tile's state and its counts."""
    return {"state": state, "replaced": counts.get("held", 0), "to_apply": counts.get("changes", 0)}
