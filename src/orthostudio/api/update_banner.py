# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What Build's engine and Live's server share for the new version's banner: its bodies, *Not
now* and the refusal of an install. Here apart, so that Live's server loads nothing of the tile
engine for them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from orthostudio import __version__, update
from orthostudio.api.guard import _plain_error
from orthostudio.home import osxp_home


class UpdateInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    """Stop a build or Live at work too, which the app's quitting stops (the page asks first)."""


class UpdateLaterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    """The version the banner offered, which *Not now* hides until a newer one is out."""


def later_router() -> APIRouter:
    """``POST /api/update/later``: the banner's *Not now*, kept in the home for every page of the
    app (:func:`orthostudio.update.say_later`)."""
    router = APIRouter()

    @router.post("/api/update/later")
    def later(req: UpdateLaterRequest) -> dict[str, Any]:
        update.say_later(osxp_home() / update.CACHE_NAME, req.version)
        return {"later": req.version}

    return router


def install_refused(installable: bool, latest: str | None) -> Any:
    """Why *Install and relaunch* cannot be done here, as an answer; None when it can.
    ``latest``: the newer version known, None when there is none."""
    if not installable:
        return _plain_error(
            "SYS_UPDATE_NOT_HERE",
            "This OrthoStudio XP cannot install a new version by itself here.",
            "Download the new version from its release page and install it.",
            status=409,
        )
    if latest is None:
        return _plain_error(
            "SYS_UPDATE_NONE",
            f"No version newer than {__version__} is known.",
            "Nothing to install.",
            status=409,
        )
    return None
