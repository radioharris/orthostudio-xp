# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Typed settings of OrthoStudio XP: 8 essential / 14 advanced / 20
expert (``docs/specs/settings.md``).

- :class:`Settings` and its levels (frozen pydantic models, Ortho4XP defaults and hints);
- :func:`load_settings` / :func:`save_settings` (``~/.orthostudio/config.toml``, TOML, atomic);
- :func:`settings_schema` (JSON schema with ``unit`` / ``hint`` / ``level`` / ``ortho4xp``);
- :func:`to_build_overrides` (``BuildSpec.config`` under the Ortho4XP names).
"""

from __future__ import annotations

from orthostudio.config.models import (
    LEVELS,
    Advanced,
    AirportCoverage,
    CoastTransition,
    Essential,
    Expert,
    Relief,
    Settings,
)
from orthostudio.config.overrides import to_build_overrides
from orthostudio.config.schema import leaf_properties, settings_schema
from orthostudio.config.store import (
    CONFIG_FILE,
    default_config_path,
    load_settings,
    save_settings,
    settings_from_dict,
    toml_dumps,
)

__all__ = [
    "CONFIG_FILE",
    "LEVELS",
    "Advanced",
    "AirportCoverage",
    "CoastTransition",
    "Essential",
    "Expert",
    "Relief",
    "Settings",
    "default_config_path",
    "leaf_properties",
    "load_settings",
    "save_settings",
    "settings_from_dict",
    "settings_schema",
    "to_build_overrides",
    "toml_dumps",
]
