# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Local HTTP API and job manager of the page (spec ``docs/specs/api.md``).

``create_app`` needs the ``server`` extra (fastapi, uvicorn); ``JobManager`` does not. The job
manager's names load it, and the whole tile engine with it, only when first asked for: a module of
the package that needs neither (the presence, the guard, SimBrief's routes) is imported without
them, as OrthoStudio XP Live's window does (``orthostudio.api.guard``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orthostudio.api.stages import (
    ROLE_STAGE,
    STAGES,
    action_for,
    error_json,
    parse_node_id,
    stage_of,
)

if TYPE_CHECKING:
    from orthostudio.api.jobs import (
        CancelRequested,
        Job,
        JobBusyError,
        JobManager,
        TileInBuildError,
    )

__all__ = [
    "ROLE_STAGE",
    "STAGES",
    "CancelRequested",
    "Job",
    "JobBusyError",
    "JobManager",
    "TileInBuildError",
    "action_for",
    "create_app",
    "error_json",
    "parse_node_id",
    "stage_of",
]


_FROM_JOBS = frozenset({"CancelRequested", "Job", "JobBusyError", "JobManager", "TileInBuildError"})


def __getattr__(name: str) -> Any:
    """The job manager's names, imported when first asked for (PEP 562)."""
    if name in _FROM_JOBS:
        from orthostudio.api import jobs

        return getattr(jobs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_app(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Lazy import of ``orthostudio.api.app.create_app`` (fastapi is an optional extra)."""
    from orthostudio.api.app import create_app as _create_app

    return _create_app(*args, **kwargs)
