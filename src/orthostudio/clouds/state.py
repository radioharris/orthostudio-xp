# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Every rule of the cloud mode, in one place (``clouds.md`` 1.5, R4): from two facts, the tile's
list (``cloudlist``) and what its folder shows (``tile.read_tile``), the state of the tile and of
each of its clouds; and from those states alone, everything the page shows and every action the
engine takes. The page decides nothing: it shows a :class:`View`. The routes check an action
against it and Apply is given what it asks (:attr:`View.asked`), which the view planned with
:func:`laying.plan_folder`, the very function Apply runs.

A cloud is, the first that holds, and one way only:

- its **source**: the one chosen by the user while it may be chosen there, else the best clear
  one (:func:`source_of`); never kept apart;
- its **images**: the newest got of that source when they can be laid on the tile, else those the
  tile shows of that source (images judged another way are none, ``sources.JUDGED``);
- what **Apply lays** for it: ticked, its images, else what the tile shows of it (it keeps its
  ground while new images are awaited); unticked, nothing; which of them can be laid is the
  folder's plan's to say (:func:`laying.plan_folder`), given them in that order;
- its **row**: a task of it, read only, held, another source, ready, back, waiting, found
  (:data:`ROWS`), from what the tile holds of it and what Apply lays;
- its **actions** (:func:`_actions`), of what the tile allows (:data:`TILE_STATES`).

The count, Apply's and Restore's words and what is useful come from the clouds' states and what
the folder shows alone.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from orthostudio.clouds.cloudlist import CloudList
from orthostudio.clouds.laying import FolderPlan, plan_folder
from orthostudio.clouds.picture import fine_level, map_level
from orthostudio.clouds.sources import ASK_AGAIN, JUDGED, PROPOSABLE
from orthostudio.clouds.tile import TileClouds, held_by_id, same_laid, unready
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

__all__ = [
    "DRAFT",
    "LIST_EDITS",
    "STOP",
    "TASKS",
    "TILE_STATES",
    "WRITES",
    "CloudState",
    "View",
    "as_shown",
    "best_of",
    "library_cell",
    "possible",
    "proposable",
    "reading",
    "source_of",
    "tile_state",
    "view",
]

# -- the tile ----------------------------------------------------------------------------------

DRAFT = frozenset({"tick", "untick", "choose"})
"""A cloud's tick and its source: what the next Apply does, changed even while one runs. The
names of every action are the same for the tile, its clouds, the routes, its tasks and the
page."""
LIST_EDITS = DRAFT | {"remove", "discard"}
"""The list's own changes: they wait for the next Apply. Removing an outline and Discard are
read of what the tile shows, which an Apply under way changes: never beside one."""
TASKS = frozenset({"find", "outline", "check"})
"""What reads the tile's folder (its picture, its textures)."""
WRITES = frozenset({"apply", "restore"})
"""What writes the tile's textures: Stop does not end them, they end what they began."""
STOP = "stop"
"""Stop of every task of the tile Stop ends (Stop all)."""


@dataclass(frozen=True, slots=True)
class TileState:
    """What a tile's state allows, and whether something is under way (the page reads it
    again)."""

    allows: frozenset[str]
    busy: bool


TILE_STATES: Mapping[str, TileState] = {
    "data_away": TileState(frozenset(), False),
    "disk_away": TileState(frozenset(), False),
    "in_build": TileState(frozenset(), True),
    "queued": TileState(frozenset(), True),
    "filing": TileState(frozenset(), True),
    "deleting": TileState(frozenset(), True),
    "not_built": TileState(frozenset(), False),
    "not_whole": TileState(frozenset(), False),
    "no_photo": TileState(frozenset(), False),
    "ready": TileState(LIST_EDITS | TASKS | WRITES | {STOP}, False),
}
"""Every state of a tile, the first that holds in this order (:func:`tile_state`): the data
folder's disk away; the tile's disk away; something else holding its folder, the cloud mode none
of it: being built, waiting in the queue, being filed, being deleted (no task of it starts,
``tasks.Tasks.start``, and what holds it waits for those under way to end); not built (no folder
of ours); not whole (a build cut short, files taken from it); no photo (no texture: the sea
alone, opening its mode was an internal error, a review, 2026-11-07); ready (``tile.unready``).
Its folder is read in the last four only, and kept in ``ready`` only."""


def tile_state(
    *,
    data_away: bool,
    disk_away: bool,
    taken: str | None,
    read: Callable[[], TileClouds | None],
) -> tuple[str, TileClouds | None]:
    """The tile's state (:data:`TILE_STATES`) and, ready, what its folder shows; ``taken``: the
    state something else holds its folder in (``in_build``, ``queued``, ``filing``,
    ``deleting``), else ``None``; ``read``: what its folder shows (``tile.read_tile``), read only
    when the states before do not hold."""
    if data_away:
        return "data_away", None
    if disk_away:
        return "disk_away", None
    if taken is not None:
        return taken, None
    shown = read()
    why = unready(shown)
    return ("ready", shown) if why is None else (why, None)


def possible(state: str, tasks: Collection[str] = ()) -> frozenset[str]:
    """What the tile allows in ``state`` while the tasks of the kinds ``tasks`` run: no Apply or
    Restore beside a task, nothing but the draft (:data:`DRAFT`) beside Apply or Restore."""
    can = TILE_STATES[state].allows
    if WRITES & set(tasks):
        return can & DRAFT
    return can - WRITES if tasks else can


# -- a cloud's source --------------------------------------------------------------------------


def got_of(c: Mapping[str, Any], code: str) -> Mapping[str, Any] | None:
    """The images got of the source ``code`` for the cloud ``c`` (its check), judged as they are
    now (``sources.JUDGED``): images judged another way are as if never got."""
    got = (c.get("checks") or {}).get(code)
    return got if got and got.get("judged") == JUDGED else None


def reading(c: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    """What the source ``code`` has under the cloud ``c``, the newest said, its answer and its fit
    together: its images got, else the search's or the outline's try; empty when never asked.
    What a piece said (``sources.ASK_AGAIN``: no answer, refused, a page) is not what the source
    has: the try's stands, and the cloud's line says it, offering to ask again. A source is so
    left out by what it has there alone, never by a passing answer."""
    got = got_of(c, code)
    if got and got.get("answer") and got["answer"] not in ASK_AGAIN:
        return got
    said = (c.get("screening") or {}).get(code)
    return said if said and said.get("answer") else {}


def proposable(c: Mapping[str, Any], code: str, held: Mapping[str, Any] | None = None) -> bool:
    """Whether ``code`` may be the source of ``c`` (``sources.PROPOSABLE``): always the one whose
    images the tile shows of it (``held``)."""
    if held is not None and held["source"] == code:
        return True
    answer = reading(c, code).get("answer")
    return answer is None or answer in PROPOSABLE


def _ranked(c: Mapping[str, Any], codes: Iterable[str]) -> list[str]:
    """``codes`` in the order the sources under ``c`` are offered: the clear first, then the
    cloudy, then the others, each by its fit, then by its name. The one order: the best is its
    first clear one, and the menu lists them so."""
    order = {"clear": 0, "cloudy": 1}

    def key(k: str) -> tuple[int, int, str]:
        said = reading(c, k)
        return order.get(said.get("answer"), 2), -int(said.get("fit") or 0), k

    return sorted(codes, key=key)


def best_of(c: Mapping[str, Any]) -> str | None:
    """Of the sources clear under ``c``, the one whose colours fit best (:func:`_ranked`)."""
    codes = {*(c.get("screening") or {}), *(c.get("checks") or {})}
    clear = [k for k in _ranked(c, codes) if reading(c, k).get("answer") == "clear"]
    return clear[0] if clear else None


def source_of(c: Mapping[str, Any], held: Mapping[str, Any] | None = None) -> str | None:
    """The source of ``c``: the one the user chose while it may be chosen there, else the best,
    else the one whose images the tile shows of it (``held``)."""
    chosen = c.get("chosen")
    if chosen and proposable(c, chosen, held):
        return chosen
    return best_of(c) or (held["source"] if held is not None else None)


# -- a cloud -----------------------------------------------------------------------------------

ROWS = ("check", "outline", "frozen", "held", "another", "ready", "back", "waiting", "found")
"""A cloud's row, what Apply does with it (:func:`_row`): a task of it (getting its images, trying
an outline's sources), whatever the tile's state; the list read only; held (the tile shows it as
Apply lays it);
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
    def unasked(self) -> bool:
        """Whether its source was never asked for its images (``no_images``): a check that turned
        it to this source, the one asked answering worse there, asks them in turn; each source so
        once."""
        return self.why == "no_images"

    @property
    def toggle(self) -> str | None:
        """What a click on its outline does."""
        return next((a for a in ("tick", "untick") if a in self.actions), None)

    def page(self, previews: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Its row as the page shows it, with its preview in ``previews`` when After needs one;
        on the map, a found cloud's number (``numbered``), and at its centre while a task of it
        runs, not while it waits its turn, a spinner, or its line for an outline's try, where the
        user just drew (``label``: Tick all's labels hid the map, a user, 2026-11-06)."""
        c = self.cloud
        task = self.task
        running = task is not None and task.get("phase") != "waiting"
        label = None if not running else "words" if task["kind"] == "outline" else "spinner"
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
            "tip": f"clouds.tip_{self.toggle}" if self.toggle else None,
            "task": {k: v for k, v in task.items() if k != "shows"} if task else None,
            "label": label,
            "numbered": c["kind"] == "found",
            "preview": (previews or {}).get(c["id"]),
            "menu": _menu(c, self.source, self.held),
        }


def _options(
    c: Mapping[str, Any], held: Mapping[str, Any] | None
) -> tuple[str | None, list[Mapping[str, Any]], str | None]:
    """The cloud's source, the images Apply may lay for it, the wanted first, and why it waits
    before the folder is planned: unticked, none; ticked, the newest images got of its source and
    those the tile shows of that source, then, while it waits for them, those the tile shows of
    it (its ground kept)."""
    source = source_of(c, held)
    if not c["tick"]:
        return source, [], None
    if source is None:  # no source: nothing the tile shows of it either (``source_of``)
        return source, [], "no_source"
    got = got_of(c, source) or {}
    shows = held if held is not None and held["source"] == source else None
    mine = [e for e in (got.get("entry"), shows) if e is not None]
    ground = [held] if held is not None and shows is None else []
    return source, mine + ground, None if mine else got.get("answer") or "no_images"


def _why(
    options: Sequence[Mapping[str, Any]],
    lays: Mapping[str, Any] | None,
    source: str | None,
    wait: str | None,
    plan: FolderPlan | None,
) -> str | None:
    """Why the cloud waits: nothing of it can be laid, or its source has no images of it yet
    (``wait``), or the images of its source cannot be laid, the tile keeping its ground; each as
    the folder's plan says (``photo_<why>``)."""
    if plan is None:
        return wait
    if options and lays is None:
        return f"photo_{plan.dropped[options[0]['id']]}"
    if wait is not None:
        return wait
    if lays is not None and lays["source"] != source:
        return f"photo_{plan.dropped[lays['id']]}"
    return None


def _row(c: Mapping[str, Any], held: Any, lays: Any, why: str | None) -> str:
    """What Apply does with the cloud: from what the tile shows of it and what Apply lays."""
    if held is None:
        return "ready" if lays is not None else "waiting" if c["tick"] else "found"
    if lays is None:
        return "back"
    if same_laid(lays, held):
        return "waiting" if why is not None else "held"
    return "another"


_ASKS = frozenset({"no_images"}) | ASK_AGAIN
"""The waits asking the source may end: none got yet, an answer asking again may change; and
every photo that changed since (``photo_*``)."""


def _asks(why: str | None) -> bool:
    return why is not None and (why in _ASKS or why.startswith("photo_"))


def _actions(
    c: Mapping[str, Any], source: str | None, held: Any, why: str | None
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
    if s.row in ("check", "outline"):  # where its task is, in a few words: one line in the row
        phase = (s.task or {}).get("phase", "waiting")
        return {"key": f"clouds.row.task.{phase}", "params": {}, "tone": "plain"}
    if s.why is not None:  # what it waits for, whatever Apply does meanwhile
        return {"key": f"clouds.row.waiting.{s.why}", "params": src, "tone": "warn"}
    held = s.held["source"] if s.held is not None else None
    return {"key": f"clouds.row.{s.row}", "params": {**src, "held": held}, "tone": "plain"}


def _menu(c: Mapping[str, Any], source: str | None, held: Any = None) -> list[dict[str, Any]]:
    """Every source tried under the cloud, and its own: what each has there and its fit (its
    newest reading), whether it may be chosen, the best first."""
    screening = c.get("screening") or {}
    codes = [*screening, *([source] if source and source not in screening else [])]
    items = []
    for code in _ranked(c, codes):
        said = reading(c, code)
        items.append(
            {
                "source": code,
                "answer": said.get("answer"),
                "fit": int(said.get("fit") or 0),
                "proposable": proposable(c, code, held),
            }
        )
    return items


# -- the view ----------------------------------------------------------------------------------

COUNTS = ("replace", "another", "back", "waiting", "held", "textures", "remade", "changes")


@dataclass(frozen=True, slots=True)
class View:
    """The tile as the mode shows it: its state, its clouds' states, what Apply lays."""

    tile: str
    state: str
    clouds: list[CloudState]
    allowed: frozenset[str]
    counts: Mapping[str, int]
    searching: bool
    busy: bool
    """Something under way: a build of the tile, or any task of it (the page reads it again)."""
    search: Mapping[str, Any] | None = None
    tasks: list[Mapping[str, Any]] = field(default_factory=list)
    """The tile's own tasks, each with what it allows (``actions``): a cloud's is in its row."""
    remade: frozenset[str] = frozenset()
    """The textures Apply makes again: After shows every other one as X-Plane does."""
    asked: list[list[Mapping[str, Any]]] = field(default_factory=list)
    """What Apply is given, each cloud's images in the order they are wanted: the very plan the
    view made (``plan_folder``)."""
    previews: list[tuple[Mapping[str, Any], list[str]]] = field(default_factory=list)
    """The clouds After shows by a preview, each in the textures of it Apply makes again: in every
    other texture, After shows what X-Plane does (:func:`as_shown`). A cloud whose images are
    being got keeps what After showed of it when that began (its task's ``shows``): Apply waits
    for the task, and a source changed showed the photo with its cloud again for the seconds the
    new images took (a user, 2026-11-06)."""
    fine: bool = False
    """Whether a fine search may be asked: the tile's textures are finer than the map's level,
    which the quick search reads (``picture.fine_level``)."""

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
        after = "clouds.after_pending" if self.remade else "clouds.after"
        return {"after": after, "before": "clouds.before"}

    @property
    def to_apply(self) -> int:
        """What Apply changes, said in a number: the clouds it changes, else the textures it
        makes again (one showing clouds no file says, ``TileClouds.known``)."""
        return self.counts["changes"] or self.counts["remade"]

    def texts(self) -> dict[str, Any]:
        """The count line, Apply's button, the questions of Apply and Restore, and Restore's
        button while the tile shows replaced clouds; a texture to make again showing clouds no
        file says (``TileClouds.known``) counted as a change when no cloud's is."""
        c = self.counts
        n = self.to_apply
        line = [
            _k(f"clouds.pending.{k}", n=c[k]) for k in ("replace", "another", "back", "waiting")
        ]
        if not c["changes"]:
            line.append(_k("clouds.pending.remade", n=c["remade"]))
        count = [w for w in line if w["params"]["n"] > 0] or [_k("clouds.pending_none")]
        one = "_one" if n == 1 else ""
        told = [w for w in count if w["key"] != "clouds.pending.waiting"]
        waits = [_k("clouds.apply_waiting", n=c["waiting"])] if c["waiting"] else []
        return {
            "count": count,
            "apply": _k(f"clouds.apply{one}", n=n),
            "restore": _k("clouds.restore") if c["textures"] else None,
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

    def library(self) -> dict[str, int]:
        """The Library's Clouds cell."""
        return library_cell(self.counts["held"], self.to_apply)


def library_cell(replaced: int = 0, to_apply: int = 0) -> dict[str, int]:
    """The Library's Clouds cell of a tile: the clouds it shows replaced, what Apply changes (the
    page says them in its own words, every one of them written out for the words' check)."""
    return {"replaced": replaced, "to_apply": to_apply}


def _k(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def _stops(task: Mapping[str, Any]) -> frozenset[str]:
    """What a task allows: Stop, when Stop ends it (``Task.stoppable``)."""
    return frozenset({"stop"}) if task["stoppable"] else frozenset()


def as_shown(shown: TileClouds, remade: frozenset[str] | None) -> dict[str, str]:
    """Of the textures showing clouds, those a picture draws as X-Plane shows them, with the
    clouds each shows: every one but those Apply makes again (``remade``, After); none for
    Before (``None``), every texture then drawn as the build made it."""
    if remade is None:
        return {}
    return {n: s.clouds for n, s in shown.stamps.items() if n not in remade}


def view(
    cl: CloudList,
    shown: TileClouds | None,
    tile: TileRef,
    state: str,
    tasks: Sequence[Mapping[str, Any]] = (),
    under_way: bool = False,
) -> View:
    """The tile ``tile`` in ``state``, its list ``cl`` and what its folder shows (``shown``,
    ``None`` when not read), its tasks (``Task.view``s) and whether anything of it is under way
    (``Tasks.busy``: a task, or what follows one): its view, decided here alone."""
    kinds = {t["kind"] for t in tasks}
    can = possible(state, kinds)
    of_cloud = {t["cloud"]: t for t in tasks if t.get("cloud")}
    holds = held_by_id(shown)
    wanted = []
    for c in cl.clouds:
        source, options, wait = _options(c, holds.get(c["id"]))
        listed = [{**e, "kind": c["kind"], "number": c["number"]} for e in options]
        wanted.append((c, source, holds.get(c["id"]), listed, wait))
    asked = [w[3] for w in wanted]
    plan = plan_folder(tile, shown, asked) if shown is not None else None
    kept = {e["id"]: e for e in (plan.laid if plan is not None else ())}
    clouds: list[CloudState] = []
    facts: list[str | None] = []
    actions: frozenset[str]
    remade = plan.remade if plan is not None else frozenset()
    previews = [(e, sorted(remade & set(e["parts"]))) for e in kept.values()]
    for c, source, held, options, wait in wanted:
        lays = kept.get(c["id"])
        why = _why(options, lays, source, wait, plan)
        fact = _row(c, held, lays, why) if plan is not None else None  # what Apply does
        task = of_cloud.get(c["id"])
        if task is not None:
            row, actions = task["kind"], _stops(task)
            if task.get("shows") is not None and lays is None and held is None:
                previews.append((task["shows"], sorted(task["shows"]["parts"])))
        elif fact is None:  # the folder not read: the list read only
            row, actions = "frozen", frozenset()
        else:
            row, actions = fact, _actions(c, source, held, why) & can
        clouds.append(CloudState(c, source, held, lays, row, why, actions, task))
        facts.append(fact)
    counts = _counts(facts, shown, remade)
    searching = "find" in kinds
    fine = shown is not None and fine_level(map_level(tile, shown.textures), shown.textures)
    idle = {
        "apply": not remade,
        "restore": counts["textures"] == 0,
        "discard": counts["changes"] == 0 and counts["waiting"] == 0,
        "find": searching,
        "choose": True,  # a cloud's alone
        "remove": True,
        # of every cloud: those each allows (Tick all, Untick all, their images)
        "tick": not any("tick" in s.actions for s in clouds),
        "untick": not any("untick" in s.actions for s in clouds),
        "check": not any("check" in s.actions for s in clouds),
        # every task Stop ends, while a cloud's runs (after Tick all, one by one was two hundred
        # clicks)
        STOP: not any(_stops(t) for t in tasks if t.get("cloud")),
    }
    allowed = frozenset(a for a in can if not idle.get(a, False))
    return View(
        tile.name,
        state,
        clouds,
        allowed,
        counts,
        searching,
        TILE_STATES[state].busy or bool(tasks) or under_way,
        cl.search,
        [{**t, "actions": sorted(_stops(t))} for t in tasks if not t.get("cloud")],
        remade,
        asked,
        [(e, names) for e, names in previews if names],
        bool(fine),
    )


_COUNTED = {"ready": "replace", "another": "another", "back": "back", "waiting": "waiting"}
"""The rows the count counts, each under its name (``clouds.pending.<name>``)."""


def _counts(
    facts: Sequence[str | None], shown: TileClouds | None, remade: frozenset[str]
) -> dict[str, int]:
    """What Apply changes, the clouds' rows counted (``facts``: what Apply does with each, a task
    of it or not, :func:`_row`); and what the folder shows, the clouds and the textures, and the
    textures Apply makes again, from the folder's plan."""
    out = dict.fromkeys(COUNTS, 0)
    for fact in facts:
        if fact in _COUNTED:
            out[_COUNTED[fact]] += 1
    out["held"] = len(shown.held) if shown is not None else 0
    out["textures"] = len(shown.stamps) if shown is not None else 0
    out["remade"] = len(remade)
    out["changes"] = out["replace"] + out["another"] + out["back"]
    return out
