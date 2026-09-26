# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Read and write ``config.toml`` (``docs/specs/settings.md`` section 4.2).

``tomllib`` reads; :func:`toml_dumps` is a small emitter for what the settings need (scalars,
lists of scalars, nested tables) so no dependency is added. Writes are atomic and keep the
previous file as ``.bak``.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from orthostudio.config.models import Settings
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text
from orthostudio.pipeline.home import osxp_home

__all__ = [
    "CONFIG_FILE",
    "default_config_path",
    "load_settings",
    "save_settings",
    "settings_from_dict",
    "toml_dumps",
    "validation_error",
]

log = logging.getLogger("orthostudio.config")

CONFIG_FILE = "config.toml"
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def default_config_path() -> Path:
    """``<osxp_home>/config.toml``."""
    return osxp_home() / CONFIG_FILE


# -- emitter -------------------------------------------------------------------------------


def _quote(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(name: str) -> str:
    return name if _BARE_KEY.match(name) else _quote(name)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    raise TypeError(f"toml_dumps: unsupported value {value!r} ({type(value).__name__})")


def toml_dumps(data: Mapping[str, Any]) -> str:
    """Serialise ``data`` as TOML: scalars and lists first, then one ``[table]`` per nested
    mapping, depth first, in insertion order. ``None`` values are omitted."""
    lines: list[str] = []
    _emit_table(data, (), lines)
    return "\n".join(lines).rstrip("\n") + "\n"


def _emit_table(data: Mapping[str, Any], path: tuple[str, ...], lines: list[str]) -> None:
    scalars = [(k, v) for k, v in data.items() if v is not None and not isinstance(v, Mapping)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, Mapping)]
    if path and (scalars or not tables):
        if lines:
            lines.append("")
        lines.append("[" + ".".join(_key(p) for p in path) + "]")
    for k, v in scalars:
        lines.append(f"{_key(k)} = {_scalar(v)}")
    for k, v in tables:
        _emit_table(v, (*path, k), lines)


# -- validation errors -> OsxpError -----------------------------------------------------------


def validation_error(exc: ValidationError, *, prefix: str = "") -> OsxpError:
    """The first pydantic error as ``CFG_VALUE_INVALID`` (dotted ``name``, value, type, range)."""
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first.get("loc", ()) if p != "__root__")
    name = f"{prefix}{loc}" if loc else (prefix.rstrip(".") or "settings")
    kind = first.get("type", "-")
    ctx = first.get("ctx") or {}
    rng = ", ".join(f"{k} {v}" for k, v in ctx.items() if k != "error") or "-"
    value = first.get("input", "-")
    return OsxpError(
        "CFG_VALUE_INVALID",
        context={"name": name, "value": value, "type": kind, "range": rng},
        message=f"{name} = {value!r}: {first.get('msg', 'invalid value')}.",
    )


# -- load / save -----------------------------------------------------------------------------


def _prune_unknown(
    data: Mapping[str, Any], model: type[BaseModel], path: str, warnings: list[str]
) -> dict[str, Any]:
    """Drop keys the model does not declare (warned), recursing into sub-models."""
    out: dict[str, Any] = {}
    fields = model.model_fields
    for key, value in data.items():
        dotted = f"{path}{key}"
        if key not in fields:
            warnings.append(f"unknown setting {dotted!r} ignored")
            continue
        sub = fields[key].annotation
        if isinstance(value, Mapping) and isinstance(sub, type) and issubclass(sub, BaseModel):
            out[key] = _prune_unknown(value, sub, f"{dotted}.", warnings)
        else:
            out[key] = value
    return out


MAX_DROPPED_SETTINGS = 32
"""How many values a file may have that this version cannot read before it is refused outright:
past that it is not a setting or two from another version, it is not our file."""


def settings_from_dict(data: Mapping[str, Any], *, source: str = "") -> Settings:
    """Validate a TOML-shaped mapping; unknown keys are ignored with a warning.

    Strict: a value it cannot read raises. What a page sends must be told, where a file already
    on disk is read as far as it can be (:func:`settings_and_problems`).
    """
    warnings: list[str] = []
    pruned = _prune_unknown(data, Settings, "", warnings)
    for w in warnings:
        log.warning("%s%s", f"{source}: " if source else "", w)
    try:
        return Settings.model_validate(pruned)
    except ValidationError as exc:
        raise validation_error(exc) from None


def _without(data: Mapping[str, Any], loc: Sequence[Any]) -> dict[str, Any]:
    """``data`` with the leaf at ``loc`` taken out (the deepest key that exists)."""
    out = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in data.items()}
    here: Any = out
    for key in list(loc)[:-1]:
        if not isinstance(here, dict) or key not in here or not isinstance(here[key], dict):
            return out
        here[key] = dict(here[key])
        here = here[key]
    if isinstance(here, dict):
        here.pop(loc[-1], None)
    return out


def settings_and_problems(
    data: Mapping[str, Any], *, source: str = ""
) -> tuple[Settings, list[str]]:
    """The settings of a document, the values this version cannot read left out.

    A single value it does not know used to refuse the whole file, and every screen of the page
    then stayed empty: a user who had chosen a relief in a newer version, then opened an older
    one, was told ``essential.relief.source = 'south_america'`` and nothing worked any more
    (2026-09-20). A value that cannot be read is now dropped, its default is used, and the page
    says which one, so that the app runs and the user can choose again. What is written on disk
    is left alone until he saves.
    """
    warnings: list[str] = []
    pruned = _prune_unknown(data, Settings, "", warnings)
    for w in warnings:
        log.warning("%s%s", f"{source}: " if source else "", w)
    problems: list[str] = []
    for _ in range(MAX_DROPPED_SETTINGS):
        try:
            return Settings.model_validate(pruned), problems
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = [p for p in first.get("loc", ()) if p != "__root__"]
            if not loc:
                raise validation_error(exc) from None
            name = ".".join(str(p) for p in loc)
            value = first.get("input", "-")
            problems.append(f"{name} = {value!r}: {first.get('msg', 'invalid value')}")
            log.warning("%s%s ignored: %s", f"{source}: " if source else "", name, problems[-1])
            pruned = _without(pruned, loc)
    raise validation_error(ValidationError.from_exception_data("Settings", []))


def load_settings(path: Path | None = None, problems: list[str] | None = None) -> Settings:
    """``Settings`` from ``path`` (default ``<osxp_home>/config.toml``); absent = defaults.

    ``problems`` collects the values this version could not read and left at their default
    (:func:`settings_and_problems`), for a page that wants to say so.
    """
    target = Path(path) if path is not None else default_config_path()
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return Settings()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        m = re.search(r"line (\d+)", str(exc))
        raise OsxpError(
            "CFG_LINE_INVALID",
            context={"path": str(target), "line": int(m.group(1)) if m else 0, "reason": str(exc)},
        ) from None
    settings, found = settings_and_problems(data, source=str(target))
    if problems is not None:
        problems.extend(found)
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Write ``settings`` as TOML atomically; the previous file becomes ``<name>.bak``."""
    target = Path(path) if path is not None else default_config_path()
    text = toml_dumps(settings.model_dump(mode="json"))
    try:
        if target.is_file():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        atomic_write_text(target, text)
    except OSError as exc:
        raise OsxpError(
            "CFG_TILE_WRITE_FAILED", context={"path": str(target), "reason": str(exc)}
        ) from exc
