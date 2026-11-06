# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Every rule of the cloud mode, in one place (``clouds.md`` 1.5, R4): from two facts, the tile's
list (``cloudlist``) and what its folder shows (``tile.read_tile``), the state of the tile and of
each of its clouds; and from those states alone, everything the page shows and every action the
engine takes. The page decides nothing: it shows a :class:`View`. The routes check an action
against it and Apply lays what it says (:attr:`View.laid`), which :func:`laying.plan_folder`
computed, the very function Apply runs.

A cloud is, the first that holds, and one way only:

- its **source**: the one chosen by the user while it may be chosen there, else the best clear
  one (:func:`source_of`); never kept apart;
- its **images**: the newest got of that source when they stand on the tile, else those the tile
  shows of that source;
- what **Apply lays** for it: ticked, its images, else what the tile shows of it (it keeps its
  ground while new images are awaited); unticked, nothing;
- its **row**: read only, a task of it, held, another source, ready, back, waiting, found
  (:data:`ROWS`), from what the tile holds of it and what Apply lays;
- its **actions** (:func:`_actions`), of what the tile allows (:data:`TILE_STATES`).

The count, Apply's and Restore's words and what is useful come from the clouds' states alone.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from orthostudio.clouds.cloudlist import CloudList
from orthostudio.clouds.laying import plan_folder
from orthostudio.clouds.sources import ASK_AGAIN, PROPOSABLE
from orthostudio.clouds.tile import TileClouds, held_by_id, laid_fields, lays_not
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

__all__ = [
    "LIST_EDITS",
    "TASKS",
    "TILE_STATES",
    "WRITES",
    "CloudState",
    "View",
    "best_of",
    "possible",
    "proposable",
    "reading",
    "source_of",
    "tile_state",
    "view",
]

# -- the tile ----------------------------------------------------------------------------------

LIST_EDITS = frozenset({"tick", "untick", "choose", "remove", "discard"})
"""The list's own changes: they wait for the next Apply. The names of every action are the same
for the tile, its clouds, the routes, its tasks and the page."""
TASKS = frozenset({"find", "outline", "check"})
"""What reads the tile's folder (its picture, its textures)."""
WRITES = frozenset({"apply", "restore"})
"""What writes the tile's textures."""


@dataclass(frozen=True, slots=True)
class TileState:
    """What a tile's state allows, whether its folder is read in it, whether something is under
    way (the page reads it again)."""

    allows: frozenset[str]
    reads: bool
    busy: bool


TILE_STATES: Mapping[str, TileState] = {
    "data_away": TileState(frozenset(), False, False),
    "disk_away": TileState(frozenset(), False, False),
    "in_build": TileState(frozenset(), False, True),
    "queued": TileState(frozenset(), False, True),
    "not_built": TileState(frozenset(), False, False),
    "not_whole": TileState(frozenset(), False, False),
    "ready": TileState(LIST_EDITS | TASKS | WRITES, True, False),
}
"""Every state of a tile, the first that holds in this order (:func:`tile_state`): the data
folder's disk away; the tile's disk away; being built; waiting in the queue (its folder not read
while a build writes it or waits to); not built (no folder of ours); not whole (a build cut short,
files taken from it); ready."""


def tile_state(
    *, data_away: bool, disk_away: bool, building: str | None, shown: TileClouds | None
) -> str:
    """The tile's state (:data:`TILE_STATES`); ``building``: ``running``, ``queued`` or ``None``."""
    if data_away:
        return "data_away"
    if disk_away:
        return "disk_away"
    if building == "running":
        return "in_build"
    if building is not None:
        return "queued"
    if shown is None:
        return "not_built"
    return "ready" if shown.whole else "not_whole"


def possible(state: str, tasks: Collection[str] = ()) -> frozenset[str]:
    """What the tile allows in ``state`` while the tasks of the kinds ``tasks`` run: no Apply or
    Restore beside a task, nothing but the list's edits beside Apply or Restore."""
    can = TILE_STATES[state].allows
    if WRITES & set(tasks):
        return can & LIST_EDITS
    return can - WRITES if tasks else can


# -- a cloud's source --------------------------------------------------------------------------


def reading(c: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    """What the source ``code`` has under the cloud ``c``, the newest said, its answer and its fit
    together: its images got, else the search's or the outline's try; empty when never asked."""
    for said in ((c.get("checks") or {}).get(code), (c.get("screening") or {}).get(code)):
        if said and said.get("answer"):
            return said
    return {}


def proposable(c: Mapping[str, Any], code: str, held: Mapping[str, Any] | None = None) -> bool:
    """Whether ``code`` may be the source of ``c`` (``sources.PROPOSABLE``): always the one whose
    images the tile shows of it (``held``)."""
    if held is not None and held["source"] == code:
        return True
    answer = reading(c, code).get("answer")
    return answer is None or answer in PROPOSABLE


def best_of(c: Mapping[str, Any]) -> str | None:
    """Of the sources clear under ``c``, the one whose colours fit best."""
    codes = {*(c.get("screening") or {}), *(c.get("checks") or {})}
    clear = [(int(reading(c, k).get("fit") or 0), k) for k in codes]
    clear = [(fit, k) for fit, k in clear if reading(c, k).get("answer") == "clear"]
    return max(clear)[1] if clear else None


def source_of(c: Mapping[str, Any], held: Mapping[str, Any] | None = None) -> str | None:
    """The source of ``c``: the one the user chose while it may be chosen there, else the best,
    else the one whose images the tile shows of it (``held``)."""
    chosen = c.get("chosen")
    if chosen and proposable(c, chosen, held):
        return chosen
    return best_of(c) or (held["source"] if held is not None else None)


# -- a cloud -----------------------------------------------------------------------------------

ROWS = ("frozen", "check", "outline", "held", "another", "ready", "back", "waiting", "found")
"""A cloud's row, what Apply does with it (:func:`_row`): the list read only; a task of it
(getting its images, trying an outline's sources); held (the tile shows it as Apply lays it);
another (held, Apply lays other images); ready (not held, Apply lays it); back (held, Apply lays
nothing: as photographed); waiting (ticked, nothing new laid: it keeps what the tile shows of it);
found (unticked). Why it waits is said on any row it has one (``why``)."""


@dataclass(frozen=True, slots=True)
class CloudState:
    """One cloud as the mode shows it and Apply takes it."""

    cloud: Mapping[str, Any]
    source: str | None
    held: Mapping[str, Any] | None
    """What the tile shows of it."""
    lays: Mapping[str, Any] | None
    """What Apply lays for it, the list's kind and number with it."""
    row: str
    why: str | None
    """Why it waits (``waiting``): the key of its line."""
    actions: frozenset[str]
    task: Mapping[str, Any] | None = None

    @property
    def toggle(self) -> str | None:
        """What a click on its outline does."""
        return next((a for a in ("tick", "untick") if a in self.actions), None)

    def page(self, names: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Its row as the page shows it."""
        c = self.cloud
        return {
            "id": c["id"],
            "kind": c["kind"],
            "number": c["number"],
            "rings": c["rings"],
            "ha": c.get("ha", 0.0),
            "tick": bool(c["tick"]),
            "source": self.source,
            "state": self.row,
            "line": _line(self),
            "actions": sorted(self.actions),
            "toggle": self.toggle,
            "task": self.task,
            "preview": (names or {}).get(c["id"]),
            "menu": _menu(c, self.source, self.held),
        }


def _same(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    return a is not None and b is not None and laid_fields(a) == laid_fields(b)


def _wanted(
    c: Mapping[str, Any], shown: TileClouds, tile: TileRef
) -> tuple[str | None, Mapping[str, Any] | None, Mapping[str, Any] | None, str | None]:
    """The cloud's source, what the tile holds of it, what Apply would lay for it before the
    folder is planned, and why it waits: ticked, the newest images got of its source when they
    stand on the tile, else those the tile shows of that source, else it waits and keeps what the
    tile shows of it."""
    held = held_by_id(shown).get(c["id"])
    source = source_of(c, held)
    if not c["tick"]:
        return source, held, None, None
    if source is None:
        return source, held, held, "no_source"
    got = (c.get("checks") or {}).get(source) or {}
    shows = held if held is not None and held["source"] == source else None
    if got.get("entry") is None and shows is None:
        return source, held, held, got.get("answer") or "no_images"
    for images in (got.get("entry"), shows):
        if images is not None and (_same(images, held) or not lays_not(images, tile, shown)):
            return source, held, images, None
    return source, held, held, f"photo_{lays_not(got['entry'], tile, shown)}"


def _row(c: Mapping[str, Any], held: Any, lays: Any, why: str | None) -> str:
    """What Apply does with the cloud: from what the tile shows of it and what Apply lays."""
    if held is None:
        return "ready" if lays is not None else "waiting" if c["tick"] else "found"
    if lays is None:
        return "back"
    if _same(lays, held):
        return "waiting" if why is not None else "held"
    return "another"


_ASKS = frozenset({"no_images"}) | ASK_AGAIN
"""The waits asking the source may end: none got yet, an answer asking again may change; and
every photo that changed since (``photo_*``)."""


def _asks(why: str | None) -> bool:
    return why is not None and (why in _ASKS or why.startswith("photo_"))


def _actions(
    c: Mapping[str, Any], source: str | None, held: Any, row: str, why: str | None
) -> frozenset[str]:
    """What the cloud allows, before what the tile allows."""
    out = {"choose", "untick" if c["tick"] else "tick"}
    if not c["tick"] and source is None:
        out.discard("tick")
    if _asks(why):
        out.add("check")
    if c["kind"] == "drawn" and held is None:
        out.add("remove")
    return frozenset(out)


def _line(s: CloudState) -> dict[str, Any] | None:
    """The words of the cloud's line, a key and its parameters, with its tone."""
    src = {"source": s.source}
    if s.row == "frozen" or (s.row == "found" and s.source is not None):
        return None
    if s.row == "found":
        return {"key": "clouds.row.no_source", "params": {}, "tone": "plain"}
    if s.row in ("check", "outline"):
        return {"key": f"clouds.row.{s.row}", "params": src, "tone": "plain"}
    if s.why is not None:  # what it waits for, whatever Apply does meanwhile
        return {"key": f"clouds.row.waiting.{s.why}", "params": src, "tone": "warn"}
    held = s.held["source"] if s.held is not None else None
    key = {"held": "held", "another": "another", "ready": "ready", "back": "back"}[s.row]
    return {"key": f"clouds.row.{key}", "params": {**src, "held": held}, "tone": "plain"}


def _menu(c: Mapping[str, Any], source: str | None, held: Any = None) -> list[dict[str, Any]]:
    """Every source tried under the cloud, and its own: what each has there and its fit (its
    newest reading), whether it may be chosen, the best first."""
    screening = c.get("screening") or {}
    codes = [*screening, *([source] if source and source not in screening else [])]
    items = []
    for code in codes:
        said = reading(c, code)
        items.append(
            {
                "source": code,
                "answer": said.get("answer"),
                "fit": int(said.get("fit") or 0),
                "proposable": proposable(c, code, held),
            }
        )
    order = {"clear": 0, "cloudy": 1}
    return sorted(items, key=lambda i: (order.get(i["answer"], 2), -i["fit"], i["source"]))


# -- the view ----------------------------------------------------------------------------------

COUNTS = ("replace", "another", "back", "waiting", "held", "textures", "changes")


@dataclass(frozen=True, slots=True)
class View:
    """The tile as the mode shows it: its state, its clouds' states, what Apply lays."""

    tile: str
    state: str
    clouds: list[CloudState]
    laid: list[Mapping[str, Any]]
    """What Apply lays, in the list's order: what :func:`laying.plan_folder` keeps."""
    allowed: frozenset[str]
    counts: Mapping[str, int]
    searching: bool
    busy: bool
    """Something under way: a build of the tile, or any task of it (the page reads it again)."""
    search: Mapping[str, Any] | None = None
    tasks: list[Mapping[str, Any]] = field(default_factory=list)
    """The tile's own tasks: a cloud's is in its row."""

    def cloud(self, cid: str) -> CloudState:
        for s in self.clouds:
            if s.cloud["id"] == cid:
                return s
        raise OsxpError("CLOUD_UNKNOWN", context={"tile": self.tile, "cloud": cid})

    def allows(self, action: str, cid: str | None = None, source: str | None = None) -> bool:
        """Whether ``action`` may be done now, of the tile or of the cloud ``cid`` (one gone
        allows nothing); ``choose`` of ``source``, one its menu offers."""
        if cid is None:
            return action in self.allowed
        s = next((x for x in self.clouds if x.cloud["id"] == cid), None)
        if s is None or action not in s.actions:
            return False
        offered = {i["source"] for i in _menu(s.cloud, s.source, s.held) if i["proposable"]}
        return action != "choose" or source in offered

    @property
    def line(self) -> dict[str, Any] | None:
        """The tile's line: its state's, else that a search found none or none was made."""
        if self.state != "ready":
            return {"key": f"clouds.tile.{self.state}", "params": {}}
        if self.clouds or self.searching:
            return None
        return {"key": "clouds.none_found" if self.search else "clouds.not_looked", "params": {}}

    def views(self, picture: bool) -> dict[str, Any]:
        """The words of After and Before; none without a picture."""
        if not picture:
            return {"after": None, "before": None}
        after = "clouds.after_pending" if self.counts["changes"] else "clouds.after"
        return {"after": after, "before": "clouds.before"}

    def texts(self) -> dict[str, Any]:
        """The count line, Apply's button, the questions of Apply and Restore."""
        n, c = self.counts["changes"], self.counts
        line = [
            _k(f"clouds.pending.{k}", n=c[k]) for k in ("replace", "another", "back", "waiting")
        ]
        count = [w for w in line if w["params"]["n"] > 0] or [_k("clouds.pending_none")]
        one = "_one" if n == 1 else ""
        told = [w for w in count if w["key"] != "clouds.pending.waiting"]
        waits = [_k("clouds.apply_waiting", n=c["waiting"])] if c["waiting"] else []
        return {
            "count": count,
            "apply": _k(f"clouds.apply{one}", n=n),
            "apply_question": {
                "title": _k(f"clouds.apply_title{one}", tile=self.tile, n=n),
                "lines": [*told, *waits, _k("clouds.apply_text")],
                "go": _k("clouds.apply_go"),
            },
            "restore_question": {
                "title": _k("clouds.restore_title", tile=self.tile),
                "lines": [_k("clouds.restore_text", n=c["held"])],
                "go": _k("clouds.restore_go"),
            },
        }

    def library(self) -> dict[str, Any]:
        """The Library's Clouds cell."""
        c = self.counts
        return {"replaced": c["held"], "to_apply": c["changes"]}


def _k(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def view(
    cl: CloudList,
    shown: TileClouds | None,
    tile: TileRef,
    state: str,
    tasks: Sequence[Mapping[str, Any]] = (),
) -> View:
    """The tile ``tile`` in ``state``, its list ``cl`` and what its folder shows (``shown``,
    ``None`` when not read), its tasks (``Task.view``s): its view, decided here alone."""
    kinds = {t["kind"] for t in tasks}
    can = possible(state, kinds)
    of_cloud = {t["cloud"]: t for t in tasks if t.get("cloud")}
    frozen = not can & LIST_EDITS or shown is None
    wanted: list[tuple[Mapping[str, Any], Any, Any, Any, str | None]] = []
    for one in cl.clouds:
        if shown is None:
            wanted.append((one, source_of(one), None, None, None))
        else:
            wanted.append((one, *_wanted(one, shown, tile)))
    asked = [
        {**lays, "kind": c["kind"], "number": c["number"]} for c, _s, _h, lays, _w in wanted if lays
    ]
    plan = plan_folder(tile, shown, asked) if shown is not None else None
    kept = {e["id"]: e for e in (plan.laid if plan is not None else ())}
    lost = plan.dropped if plan is not None else {}
    clouds: list[CloudState] = []
    actions: frozenset[str]
    for c, source, held, lays, why in wanted:
        if lays is not None and c["id"] not in kept:  # its textures made again, its images gone
            lays, why = None, f"photo_{lost.get(c['id'], 'parts')}"
        task = of_cloud.get(c["id"])
        if frozen:
            row, actions = "frozen", frozenset()
        elif task is not None:
            row, actions = task["kind"], frozenset({"stop"})
        else:
            row = _row(c, held, kept.get(c["id"]), why)
            actions = _actions(c, source, held, row, why) & can
        clouds.append(CloudState(c, source, held, kept.get(c["id"]), row, why, actions, task))
    counts = _counts(clouds, shown)
    searching = "find" in kinds
    idle = {
        "apply": counts["changes"] == 0,
        "restore": counts["textures"] == 0,
        "discard": not any(s.row in ("another", "ready", "back", "waiting") for s in clouds),
        "find": searching,
    }
    allowed = frozenset(a for a in can if not idle.get(a, False))
    return View(
        tile.name,
        state,
        clouds,
        list(kept.values()),
        allowed,
        counts,
        searching,
        TILE_STATES[state].busy or bool(tasks),
        cl.search,
        [t for t in tasks if not t.get("cloud")],
    )


def _counts(clouds: Sequence[CloudState], shown: TileClouds | None) -> dict[str, int]:
    """What Apply changes, from each cloud's state: what the tile holds of it against what
    Apply lays."""
    out = dict.fromkeys(COUNTS, 0)
    for s in clouds:
        if s.row == "waiting":
            out["waiting"] += 1
        if s.held is not None:
            out["held"] += 1
        if s.lays is not None and s.held is None:
            out["replace"] += 1
        elif s.lays is not None and not _same(s.lays, s.held):
            out["another"] += 1
        elif s.lays is None and s.held is not None:
            out["back"] += 1
    out["textures"] = len(shown.stamps) if shown is not None else 0
    out["changes"] = out["replace"] + out["another"] + out["back"]
    return out
