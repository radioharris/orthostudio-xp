# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The bodies of the new version's banner, which Build's engine and Live's server both read: here
apart, so that Live's server loads nothing of the tile engine for them."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UpdateInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    """Stop a build or Live at work too, which the app's quitting stops (the page asks first)."""


class UpdateLaterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    """The version the banner offered, which *Not now* hides until a newer one is out."""
