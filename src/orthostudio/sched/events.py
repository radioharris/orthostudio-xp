"""Typed events emitted by the scheduler, always in the event-loop thread.

Spec: ``docs/specs/scheduler.md`` section 2.5. Per node the order is ``Started`` then any
number of ``Progress`` then exactly one of ``Done`` / ``Failed``. ``Stats`` is emitted after
every completion and periodically while nodes run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from orthostudio.errors import OsxpError
from orthostudio.model import ArtifactRef

__all__ = ["Done", "Event", "Failed", "NodeKind", "Progress", "Started", "Stats"]

NodeKind = Literal["cpu", "net", "subprocess", "io"]


@dataclass(frozen=True, slots=True)
class Started:
    node_id: str
    kind: NodeKind
    key: str


@dataclass(frozen=True, slots=True)
class Progress:
    node_id: str
    fraction: float
    """0.0 - 1.0, clamped."""
    message: str


@dataclass(frozen=True, slots=True)
class Done:
    node_id: str
    key: str
    hit: bool
    """True when the artefact was already in the store (or built by another node of the run)."""
    wall_s: float
    ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class Failed:
    node_id: str
    error: OsxpError
    cause: str | None = None
    """Id of the upstream node whose failure skipped this one (``error.code`` is then
    ``SYS_CANCELLED``); ``None`` when the node itself raised or the run was cancelled."""


@dataclass(frozen=True, slots=True)
class Stats:
    running: int
    pending: int
    done: int
    """Built plus hits."""
    failed: int
    hits: int
    elapsed_s: float
    eta_s: float


Event = Started | Progress | Done | Failed | Stats
