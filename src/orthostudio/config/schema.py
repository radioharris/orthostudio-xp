"""The enriched JSON schema of the settings for the UI (``docs/specs/settings.md`` 4.3).

Pydantic's schema with every ``$ref`` inlined, and ``unit`` / ``hint`` / ``level`` / ``ortho4xp``
/ ``default`` on every leaf property (``enum`` where the field is a ``Literal``).
"""

from __future__ import annotations

import copy
from typing import Any

from orthostudio.config.models import Settings

__all__ = ["leaf_properties", "settings_schema"]

_META_KEYS = ("unit", "hint", "level", "ortho4xp")


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            merged = copy.deepcopy(defs[name])
            merged.update({k: v for k, v in node.items() if k != "$ref"})
            return _inline(merged, defs)
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def _lift_enum(prop: dict[str, Any]) -> None:
    """``enum`` at the property level, also when it sits inside an ``anyOf`` (``X | None``)."""
    if "enum" in prop:
        return
    for alt in prop.get("anyOf", ()):
        if isinstance(alt, dict) and "enum" in alt:
            prop["enum"] = list(alt["enum"])
            return


def _propagate(props: dict[str, Any], level: str | None) -> None:
    for prop in props.values():
        if not isinstance(prop, dict):
            continue
        lvl = prop.get("level", level)
        if lvl is not None:
            prop["level"] = lvl
        if "properties" in prop:
            _propagate(prop["properties"], lvl)
        else:
            _lift_enum(prop)


def settings_schema() -> dict[str, Any]:
    """JSON schema of :class:`Settings`, ``$ref``-free, with the UI metadata on each leaf."""
    raw = Settings.model_json_schema()
    defs = raw.pop("$defs", {})
    schema: dict[str, Any] = _inline(raw, defs)
    for level, prop in schema["properties"].items():
        prop["level"] = level
        _propagate(prop["properties"], level)
    schema["x-levels"] = list(schema["properties"])
    return schema


def leaf_properties(schema: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Flat ``{"advanced.road_level": {...}}`` view of the leaves of ``schema``."""
    schema = settings_schema() if schema is None else schema
    out: dict[str, dict[str, Any]] = {}

    def walk(props: dict[str, Any], prefix: str) -> None:
        for name, prop in props.items():
            if "properties" in prop:
                walk(prop["properties"], f"{prefix}{name}.")
            else:
                out[f"{prefix}{name}"] = prop

    walk(schema["properties"], "")
    return out
