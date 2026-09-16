"""Typed errors raised by the artefact graph."""

from __future__ import annotations


class GraphError(Exception):
    """Base class of every error raised by :mod:`orthostudio.graph`."""


class CanonError(GraphError, ValueError):
    """A value cannot be serialised to canonical JSON (NaN, non-string key, unknown type)."""


class InvalidRuleError(GraphError, ValueError):
    """A rule declaration violates an invariant (name, version, inputs, params model)."""


class InvalidNodeError(GraphError, ValueError):
    """A node does not match its rule (wrong params type, missing or unknown input)."""


class InvalidDigestError(GraphError, ValueError):
    """A digest or key is not 64 lowercase hexadecimal characters."""


class StoreError(GraphError):
    """Base class of store errors."""


class IncompatibleIndexError(StoreError):
    """The sqlite index was written by another schema version."""


class MissingArtifactError(StoreError, KeyError):
    """The key is not in the store (or its files vanished)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        return f"artefact {self.key} is not in the store"


class ArtifactInUseError(StoreError):
    """Deleting an artefact that is pinned or referenced by another artefact."""


class CommitError(StoreError):
    """The rule finished but left no valid output at the expected path."""


class RuleFailedError(GraphError):
    """A rule raised while building an artefact; the cause is chained."""

    def __init__(self, rule: str, key: str, cause: BaseException) -> None:
        super().__init__(f"rule {rule!r} failed for key {key[:16]}: {cause!r}")
        self.rule = rule
        self.key = key
        self.__cause__ = cause


class BudgetExceededError(GraphError):
    """A rule declares more RAM than the executor is allowed to spend."""
