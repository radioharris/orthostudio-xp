# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Artefact keys: blake3 of the canonical recipe (rule, version, params, input digests)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import blake3

from orthostudio.graph.canon import canonical_json
from orthostudio.graph.digest import check_digest
from orthostudio.graph.rule import Rule, RuleParams

__all__ = ["KEY_FORMAT", "artifact_key", "key_document", "key_for", "parse_recipe"]

KEY_FORMAT = "osxp-key-1"


def key_document(
    rule_name: str,
    version: int,
    params: Mapping[str, Any],
    inputs: Mapping[str, str | None],
) -> dict[str, Any]:
    """The recipe object whose canonical JSON is hashed into the key."""
    for name, digest in inputs.items():
        if digest is not None:
            check_digest(digest, f"input {name!r}")
    return {
        "format": KEY_FORMAT,
        "rule": rule_name,
        "version": int(version),
        "params": dict(params),
        "inputs": dict(inputs),
    }


def artifact_key(
    rule_name: str,
    version: int,
    params: Mapping[str, Any],
    inputs: Mapping[str, str | None],
) -> tuple[str, str]:
    """Return ``(key, recipe)``: the blake3 hex key and the canonical recipe it hashes."""
    recipe = canonical_json(key_document(rule_name, version, params, inputs))
    return blake3.blake3(recipe.encode("ascii")).hexdigest(), recipe


def key_for(rule: Rule, params: RuleParams, inputs: Mapping[str, str | None]) -> tuple[str, str]:
    """Key of ``rule`` applied to ``params`` with the given input digests (None = absent)."""
    if not isinstance(params, rule.params):
        raise TypeError(f"rule {rule.name!r} expects {rule.params.__name__}, got {type(params)}")
    if set(inputs) != set(rule.inputs):
        raise ValueError(f"rule {rule.name!r} inputs are {list(rule.inputs)}, got {sorted(inputs)}")
    return artifact_key(rule.name, rule.version, params.canonical(), inputs)


def parse_recipe(recipe: str) -> dict[str, Any]:
    """Decode a stored recipe (canonical JSON text) back into its document."""
    import json

    doc = json.loads(recipe)
    if not isinstance(doc, dict) or doc.get("format") != KEY_FORMAT:
        raise ValueError(f"not an {KEY_FORMAT} recipe")
    return doc
