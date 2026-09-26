# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Per-rule cost estimates (EWMA of observed wall seconds), persisted in the store's meta table.

Spec: ``docs/specs/scheduler.md`` section 2.7. Rows are ``sched.cost.<rule>@<version>`` with
a JSON value ``{"ewma": seconds, "n": observations}``. The P0 store has no public meta
accessor yet, so this module uses its connection under its lock; replace by
``Store.meta_get`` / ``meta_set`` when ``orthostudio.graph`` grows them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from orthostudio.graph.rule import Rule
from orthostudio.graph.store import Store

__all__ = ["DEFAULT_ALPHA", "DEFAULT_COST_S", "CostEntry", "CostModel", "cost_name"]

log = logging.getLogger("orthostudio.sched.costs")

DEFAULT_COST_S = 1.0
DEFAULT_ALPHA = 0.3
_PREFIX = "sched.cost."


def cost_name(rule: Rule) -> str:
    """``"<rule>@<version>"``: the identity of an estimate."""
    return f"{rule.name}@{rule.version}"


@dataclass(slots=True)
class CostEntry:
    ewma: float
    n: int


class CostModel:
    """Estimated seconds per rule; ``observe`` learns, ``flush`` persists."""

    def __init__(
        self,
        store: Store | None = None,
        *,
        default_s: float = DEFAULT_COST_S,
        alpha: float = DEFAULT_ALPHA,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.store = store
        self.default_s = float(default_s)
        self.alpha = float(alpha)
        self._entries: dict[str, CostEntry] = {}
        self._dirty: set[str] = set()
        if store is not None:
            self.load()

    # -- estimates -------------------------------------------------------------------------

    def estimate(self, rule: Rule) -> float:
        return self.estimate_name(cost_name(rule))

    def estimate_name(self, name: str) -> float:
        entry = self._entries.get(name)
        return self.default_s if entry is None else entry.ewma

    def entry(self, name: str) -> CostEntry | None:
        return self._entries.get(name)

    def as_dict(self) -> dict[str, CostEntry]:
        return dict(self._entries)

    # -- learning --------------------------------------------------------------------------

    def observe(self, rule: Rule, wall_s: float) -> float:
        """Blend one measured build into the estimate; returns the new estimate."""
        name = cost_name(rule)
        wall = max(0.0, float(wall_s))
        entry = self._entries.get(name)
        if entry is None:
            entry = CostEntry(wall, 1)
            self._entries[name] = entry
        else:
            entry.ewma = self.alpha * wall + (1.0 - self.alpha) * entry.ewma
            entry.n += 1
        self._dirty.add(name)
        return entry.ewma

    def set(self, name: str, seconds: float, n: int = 1) -> None:
        """Force an estimate (tests, or a configured hint)."""
        self._entries[name] = CostEntry(float(seconds), int(n))
        self._dirty.add(name)

    # -- persistence -----------------------------------------------------------------------

    def load(self) -> int:
        """Read every persisted estimate; returns how many were found."""
        if self.store is None:
            return 0
        store = self.store
        with store._lock:
            rows = store._db.execute(
                "SELECT k, v FROM meta WHERE k LIKE ?", (_PREFIX + "%",)
            ).fetchall()
        count = 0
        for row in rows:
            name = str(row["k"])[len(_PREFIX) :]
            try:
                doc = json.loads(str(row["v"]))
                self._entries[name] = CostEntry(float(doc["ewma"]), int(doc["n"]))
                count += 1
            except (ValueError, KeyError, TypeError):
                log.warning("ignoring malformed cost row %s", row["k"])
        return count

    def flush(self) -> int:
        """Write the estimates changed since the last flush; returns how many."""
        if self.store is None or not self._dirty:
            self._dirty.clear()
            return 0
        rows = [
            (_PREFIX + name, json.dumps({"ewma": e.ewma, "n": e.n}))
            for name in sorted(self._dirty)
            if (e := self._entries.get(name)) is not None
        ]
        store = self.store
        with store._tx() as db:
            db.executemany("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", rows)
        self._dirty.clear()
        return len(rows)
