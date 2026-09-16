"""Properties of the canonical JSON that artefact keys hash."""

from __future__ import annotations

import json
import math
import random
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orthostudio.graph import CanonError, canonical_bytes, canonical_json

scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text()
)
json_values = st.recursive(
    scalars,
    lambda children: (
        st.lists(children, max_size=6) | st.dictionaries(st.text(max_size=8), children, max_size=6)
    ),
    max_leaves=40,
)


def _shuffled(value: Any, rng: random.Random) -> Any:
    """Same value, dict insertion order randomised at every level."""
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {k: _shuffled(v, rng) for k, v in items}
    if isinstance(value, list):
        return [_shuffled(v, rng) for v in value]
    return value


@given(json_values, st.integers())
@settings(max_examples=300)
def test_key_order_does_not_matter(value: Any, seed: int) -> None:
    assert canonical_json(value) == canonical_json(_shuffled(value, random.Random(seed)))


@given(json_values)
@settings(max_examples=300)
def test_round_trips_through_json(value: Any) -> None:
    text = canonical_json(value)
    assert json.loads(text) == value
    assert canonical_json(json.loads(text)) == text


@given(json_values)
def test_output_is_ascii_without_whitespace_outside_strings(value: Any) -> None:
    raw = canonical_bytes(value)
    assert raw.isascii()
    stripped = json.dumps(json.loads(raw), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    assert raw.decode() == stripped


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_float_repr_is_shortest_round_trip(x: float) -> None:
    text = canonical_json(x)
    assert float(text) == x
    assert text == repr(0.0 if x == 0 else x)


def test_negative_zero_folds_to_zero() -> None:
    assert canonical_json(-0.0) == canonical_json(0.0) == "0.0"
    assert canonical_json([-0.0]) == "[0.0]"


def test_int_and_float_are_distinct() -> None:
    assert canonical_json(1) == "1"
    assert canonical_json(1.0) == "1.0"
    assert canonical_json(True) == "true"
    assert canonical_json(1) != canonical_json(True)


def test_tuple_is_a_list() -> None:
    assert canonical_json((1, "a")) == canonical_json([1, "a"]) == '[1,"a"]'


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_floats_are_rejected(bad: float) -> None:
    with pytest.raises(CanonError):
        canonical_json({"x": bad})


@pytest.mark.parametrize("bad", [{1: "a"}, {"a": b"bytes"}, {"a": object()}, {"a": {1.5: 2}}])
def test_non_string_keys_and_unknown_types_are_rejected(bad: Any) -> None:
    with pytest.raises(CanonError):
        canonical_json(bad)


def test_worked_example() -> None:
    value = {"b": [1, 2.0, -0.0, 1e-9, 1e16, True, None, "x"], "a": {"z": 1, "y": {}}}
    assert canonical_json(value) == '{"a":{"y":{},"z":1},"b":[1,2.0,0.0,1e-09,1e+16,true,null,"x"]}'
    assert canonical_json("Marseille é") == '"Marseille \\u00e9"'
