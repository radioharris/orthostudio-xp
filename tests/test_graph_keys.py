"""Artefact keys: format, stability across processes, sensitivity to what matters only."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from orthostudio.graph import (
    KEY_FORMAT,
    InvalidDigestError,
    InvalidRuleError,
    Rule,
    RuleParams,
    RunContext,
    artifact_key,
    key_for,
    rule,
)

GOLDEN_KEY = "d03932ed4b65b02bed14b81d358f79e830c6bb6c147dda1c238437af2c6d782f"
GOLDEN_RECIPE = (
    '{"format":"osxp-key-1","inputs":{"dem":null,"osm":"' + "00" * 32 + '"},'
    '"params":{"curvature_tol":2.0,"name":"Marseille \\u00e9","road_level":1},'
    '"rule":"vectors","version":1}'
)
_PARAMS = {"road_level": 1, "curvature_tol": 2.0, "name": "Marseille é"}
_INPUTS = {"osm": "00" * 32, "dem": None}


class VecParams(RuleParams):
    road_level: int = 1
    curvature_tol: float = 2.0


def _noop(ctx: RunContext) -> None:
    ctx.out.write_bytes(b"")


@rule(name="vectors", version=1, params=VecParams, inputs=("osm", "dem"), ram_mb=800)
def vectors(ctx: RunContext) -> None:
    """Test rule."""
    _noop(ctx)


def test_golden_key_and_recipe() -> None:
    key, recipe = artifact_key("vectors", 1, _PARAMS, _INPUTS)
    assert recipe == GOLDEN_RECIPE
    assert key == GOLDEN_KEY
    assert KEY_FORMAT in recipe


def test_key_is_stable_across_processes_and_hash_seeds() -> None:
    code = (
        "from orthostudio.graph import artifact_key;"
        f"print(artifact_key('vectors', 1, {_PARAMS!r}, {_INPUTS!r})[0])"
    )
    for seed in ("0", "12345", "random"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run(
            [sys.executable, "-c", code], env=env, check=True, capture_output=True, text=True
        )
        assert out.stdout.strip() == GOLDEN_KEY


def test_param_order_does_not_matter() -> None:
    a = artifact_key("r", 1, {"x": 1, "y": 2}, {})[0]
    b = artifact_key("r", 1, {"y": 2, "x": 1}, {})[0]
    assert a == b


def test_every_ingredient_changes_the_key() -> None:
    base = artifact_key("vectors", 1, _PARAMS, _INPUTS)[0]
    assert artifact_key("vectors2", 1, _PARAMS, _INPUTS)[0] != base
    assert artifact_key("vectors", 2, _PARAMS, _INPUTS)[0] != base
    assert artifact_key("vectors", 1, {**_PARAMS, "road_level": 2}, _INPUTS)[0] != base
    assert artifact_key("vectors", 1, _PARAMS, {**_INPUTS, "dem": "11" * 32})[0] != base
    assert artifact_key("vectors", 1, _PARAMS, {**_INPUTS, "osm": "ff" * 32})[0] != base


def test_input_digests_are_validated() -> None:
    with pytest.raises(InvalidDigestError):
        artifact_key("vectors", 1, {}, {"osm": "not-a-digest"})
    with pytest.raises(InvalidDigestError):
        artifact_key("vectors", 1, {}, {"osm": "AB" * 32})


def test_key_for_checks_params_type_and_input_names() -> None:
    params = VecParams(road_level=2)
    key, _ = key_for(vectors, params, {"osm": "00" * 32, "dem": None})
    assert len(key) == 64
    with pytest.raises(TypeError):
        key_for(vectors, RuleParams(), {"osm": "00" * 32, "dem": None})
    with pytest.raises(ValueError, match="inputs are"):
        key_for(vectors, params, {"osm": "00" * 32})


def test_bind_freezes_only_the_consumed_subset() -> None:
    cfg = {"road_level": 3, "curvature_tol": 1.5, "masks_width": 100, "default_zl": 16}
    p = vectors.bind(cfg)
    assert p == VecParams(road_level=3, curvature_tol=1.5)
    assert vectors.consumed == ("road_level", "curvature_tol")
    assert p.canonical() == {"road_level": 3, "curvature_tol": 1.5}


def test_params_are_frozen_and_closed() -> None:
    p = VecParams()
    with pytest.raises(ValidationError):
        p.road_level = 5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        VecParams(road_lvl=1)  # type: ignore[call-arg]


def test_rule_declaration_invariants() -> None:
    with pytest.raises(InvalidRuleError):
        Rule(name="Bad Name", version=1, fn=_noop)
    with pytest.raises(InvalidRuleError):
        Rule(name="ok", version=-1, fn=_noop)
    with pytest.raises(InvalidRuleError):
        Rule(name="ok", version=1, fn=_noop, inputs=("a", "a"))
    with pytest.raises(InvalidRuleError):
        Rule(name="ok", version=1, fn=_noop, inputs=("Not-Ok",))
    with pytest.raises(InvalidRuleError):
        Rule(name="ok", version=1, fn=_noop, params=dict)  # type: ignore[arg-type]
    r = Rule(name="ok", version=1, fn=_noop, inputs=("b", "a"))
    assert r.inputs == ("a", "b")
    assert vectors.ram_mb == 800
    assert vectors.doc == "Test rule."
    assert Path(__file__).exists()
