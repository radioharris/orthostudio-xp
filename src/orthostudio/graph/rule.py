# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Rules: the typed, versioned computations whose outputs are artefacts.

A rule declares a name, an integer version (bumped whenever its output may change for the
same inputs), the *subset* of configuration parameters it consumes (a pydantic model), its
named inputs, its declared RAM cost and whether it writes a file or a directory.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict

from orthostudio.graph.errors import InvalidRuleError

__all__ = ["Kind", "ResolvedInput", "Rule", "RuleParams", "RunContext", "rule"]

Kind = Literal["file", "dir"]

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_INPUT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RuleParams(BaseModel):
    """Base class of the parameter subset a rule consumes.

    Frozen and closed: unknown fields are an error when constructed directly (typos), and
    :meth:`subset_of` picks the consumed fields out of a larger configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def subset_of(cls, config: Mapping[str, Any] | BaseModel) -> Self:
        """Build the params from a larger configuration, ignoring fields this rule ignores."""
        if isinstance(config, BaseModel):
            config = config.model_dump()
        picked = {name: config[name] for name in cls.model_fields if name in config}
        return cls.model_validate(picked)

    def canonical(self) -> dict[str, Any]:
        """JSON-mode dump, the object that is canonicalised into the key."""
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class ResolvedInput:
    """What a rule sees of one of its inputs at run time."""

    name: str
    digest: str | None
    path: Path | None
    key: str | None = None

    @property
    def present(self) -> bool:
        return self.digest is not None


@dataclass(frozen=True, slots=True)
class RunContext:
    """Everything a rule function receives.

    ``out`` is where the output must be written: a not-yet-existing file path when the rule
    kind is ``file``, an existing empty directory when it is ``dir``. ``scratch`` is an empty
    directory removed after the run whatever happens.
    """

    rule: Rule
    key: str
    params: RuleParams
    inputs: Mapping[str, ResolvedInput]
    out: Path
    scratch: Path

    def input_path(self, name: str) -> Path:
        """Path of a present input, or raise ``LookupError``."""
        inp = self.inputs[name]
        if inp.path is None:
            raise LookupError(f"input {name!r} of rule {self.rule.name!r} has no path")
        return inp.path


RuleFn = Callable[[RunContext], None]


@dataclass(frozen=True, slots=True)
class Rule:
    """A declared computation. See module docstring."""

    name: str
    version: int
    fn: RuleFn
    params: type[RuleParams] = RuleParams
    inputs: tuple[str, ...] = ()
    ram_mb: int = 0
    kind: Kind = "file"
    doc: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise InvalidRuleError(f"rule name {self.name!r} must match {_NAME.pattern}")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 0:
            raise InvalidRuleError(f"rule {self.name!r}: version must be a non-negative int")
        if not (isinstance(self.params, type) and issubclass(self.params, RuleParams)):
            raise InvalidRuleError(f"rule {self.name!r}: params must be a RuleParams subclass")
        names = tuple(self.inputs)
        if len(set(names)) != len(names):
            raise InvalidRuleError(f"rule {self.name!r}: duplicate input names {names}")
        for n in names:
            if not _INPUT.match(n):
                raise InvalidRuleError(
                    f"rule {self.name!r}: input name {n!r} must match {_INPUT.pattern}"
                )
        object.__setattr__(self, "inputs", tuple(sorted(names)))
        if self.ram_mb < 0:
            raise InvalidRuleError(f"rule {self.name!r}: ram_mb must be >= 0")
        if self.kind not in ("file", "dir"):
            raise InvalidRuleError(f"rule {self.name!r}: kind must be 'file' or 'dir'")

    @property
    def consumed(self) -> tuple[str, ...]:
        """Names of the configuration parameters this rule consumes."""
        return tuple(self.params.model_fields)

    def bind(self, config: Mapping[str, Any] | BaseModel) -> RuleParams:
        """Freeze the consumed subset of ``config`` into this rule's params model."""
        return self.params.subset_of(config)

    def __repr__(self) -> str:
        return f"Rule({self.name}@{self.version}, inputs={list(self.inputs)}, kind={self.kind})"


def rule(
    *,
    name: str,
    version: int,
    params: type[RuleParams] = RuleParams,
    inputs: Iterable[str] = (),
    ram_mb: int = 0,
    kind: Kind = "file",
) -> Callable[[RuleFn], Rule]:
    """Decorator turning a ``fn(ctx: RunContext) -> None`` into a :class:`Rule`."""

    def wrap(fn: RuleFn) -> Rule:
        return Rule(
            name=name,
            version=version,
            fn=fn,
            params=params,
            inputs=tuple(inputs),
            ram_mb=ram_mb,
            kind=kind,
            doc=(fn.__doc__ or "").strip(),
        )

    return wrap
