# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Local HTTP API and job manager of the page (spec ``docs/specs/api.md``).

``create_app`` needs the ``server`` extra (fastapi, uvicorn); ``JobManager`` does not.
"""

from orthostudio.api.jobs import (
    CancelRequested,
    Job,
    JobBusyError,
    JobManager,
    TileInBuildError,
    error_json,
)
from orthostudio.api.stages import ROLE_STAGE, STAGES, action_for, parse_node_id, stage_of

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


def create_app(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Lazy import of ``orthostudio.api.app.create_app`` (fastapi is an optional extra)."""
    from orthostudio.api.app import create_app as _create_app

    return _create_app(*args, **kwargs)
