# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Canonical JSON: the unique byte form of a parameter set, hashed into artefact keys.

Rules (see docs/specs/graph-keys.md, "Canonical JSON"):

* objects: keys must be ``str``, emitted sorted by Unicode code point, no whitespace;
* arrays: lists and tuples, in order;
* strings: JSON with ``ensure_ascii`` (pure ASCII output);
* integers: decimal, arbitrary size; booleans are ``true``/``false`` (never 1/0);
* floats: Python's shortest round-trip ``repr``; ``-0.0`` becomes ``0.0``;
  NaN and infinities are rejected; ``1`` and ``1.0`` are distinct;
* ``None`` is ``null``; any other type is rejected.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from orthostudio.graph.errors import CanonError

__all__ = ["canonical_bytes", "canonical_json"]


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text of ``value``."""
    parts: list[str] = []
    _emit(value, parts, "$")
    return "".join(parts)


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON of ``value`` as ASCII bytes."""
    return canonical_json(value).encode("ascii")


def _emit(value: Any, out: list[str], where: str) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(int(value)))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise CanonError(f"{where}: non-finite float {value!r} is not representable")
        if value == 0.0:
            value = 0.0  # folds -0.0
        out.append(repr(float(value)))
    elif isinstance(value, str):
        out.append(json.dumps(str(value), ensure_ascii=True))
    elif isinstance(value, Mapping):
        keys = list(value.keys())
        for k in keys:
            if not isinstance(k, str):
                raise CanonError(f"{where}: object key {k!r} is not a string")
        out.append("{")
        for i, k in enumerate(sorted(keys)):
            if i:
                out.append(",")
            out.append(json.dumps(str(k), ensure_ascii=True))
            out.append(":")
            _emit(value[k], out, f"{where}.{k}")
        out.append("}")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _emit(item, out, f"{where}[{i}]")
        out.append("]")
    else:
        raise CanonError(f"{where}: type {type(value).__name__} is not canonical JSON")
