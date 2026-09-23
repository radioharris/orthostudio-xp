"""Node ids -> the six user stages; error codes -> the page's action.

Spec: ``docs/specs/api.md`` sections 5.2 and 5.4. Node ids come from
``docs/specs/pipeline-build.md`` section 2: ``<tile>/<role>`` or
``<tile>/<provider><zl>/<role>``, with an optional ``#n`` suffix.

The three P3 roles that acquire data -- ``osm``, ``coastline`` and ``dem`` -- belong to the
``data`` stage, next to ``vectors``: without them the page showed no progress at all for a
node that takes 24 s on a cold tile (integration blocker B4, confirmed by review 4).
"""

from __future__ import annotations

import re
from typing import Literal

__all__ = [
    "ROLE_STAGE",
    "STAGES",
    "Action",
    "Stage",
    "action_for",
    "parse_node_id",
    "stage_of",
]

Stage = Literal["data", "terrain", "coast", "imagery", "assembly", "install"]
Action = Literal["retry", "settings", "none"]

STAGES: tuple[Stage, ...] = ("data", "terrain", "coast", "imagery", "assembly", "install")

ROLE_STAGE: dict[str, Stage] = {
    "osm": "data",
    "coastline": "data",
    "dem": "data",
    "vectors": "data",
    "mesh": "terrain",
    "masks": "coast",
    "textures": "imagery",
    "xp12": "assembly",
    "dsf": "assembly",
    "overlay": "assembly",
    "pack": "assembly",
    "install": "install",
}

_SUFFIX = re.compile(r"#\d+$")
# OSM_ too: what fails there is a server having a bad day, and pressing the button asks
# every source and every server afresh (a user read "could not be obtained" with nothing
# to click, 2026-09-23)
_RETRY_PREFIXES = ("IMG_", "NET_", "OSM_")
_RETRY_CODES = frozenset({"TEX_MISSING"})
# The X-Plane 12 folder is chosen in Settings: its absence, or its scenery's, sends there too.
_SETTINGS_PREFIXES = ("CFG_", "XP_DIR_", "XP_GLOBAL_SCENERY_", "DSF_GLOBAL_SCENERY_")


def parse_node_id(node_id: str) -> tuple[str, str]:
    """``'+43+005/BI14/dsf#2'`` -> ``('+43+005', 'dsf')``."""
    parts = node_id.split("/")
    tile = parts[0]
    role = _SUFFIX.sub("", parts[-1]) if len(parts) > 1 else ""
    return tile, role


def stage_of(role: str) -> Stage | None:
    return ROLE_STAGE.get(role)


def action_for(code: str) -> Action:
    """What the page offers next to an error: retry, open the settings, nothing."""
    if code in _RETRY_CODES or code.startswith(_RETRY_PREFIXES):
        return "retry"
    if code.startswith(_SETTINGS_PREFIXES):
        return "settings"
    return "none"
