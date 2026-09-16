"""Content-keyed artefact graph: rules, keys, store, synchronous executor.

Spec: docs/specs/graph-keys.md.
"""

from __future__ import annotations

from orthostudio.graph.canon import canonical_bytes, canonical_json
from orthostudio.graph.digest import digest_bytes, digest_dir, digest_file, digest_path
from orthostudio.graph.errors import (
    ArtifactInUseError,
    BudgetExceededError,
    CanonError,
    CommitError,
    GraphError,
    IncompatibleIndexError,
    InvalidDigestError,
    InvalidNodeError,
    InvalidRuleError,
    MissingArtifactError,
    RuleFailedError,
    StoreError,
)
from orthostudio.graph.executor import (
    Event,
    Executor,
    Node,
    NodeFinished,
    NodeResult,
    NodeStarted,
    PlanEntry,
    RunReport,
    Source,
)
from orthostudio.graph.keys import KEY_FORMAT, artifact_key, key_for
from orthostudio.graph.rule import Kind, ResolvedInput, Rule, RuleParams, RunContext, rule
from orthostudio.graph.store import (
    SCHEMA_VERSION,
    ArtifactInfo,
    Build,
    FsckReport,
    GcReport,
    InputRef,
    Provenance,
    Store,
)

__all__ = [
    "KEY_FORMAT",
    "SCHEMA_VERSION",
    "ArtifactInUseError",
    "ArtifactInfo",
    "BudgetExceededError",
    "Build",
    "CanonError",
    "CommitError",
    "Event",
    "Executor",
    "FsckReport",
    "GcReport",
    "GraphError",
    "IncompatibleIndexError",
    "InputRef",
    "InvalidDigestError",
    "InvalidNodeError",
    "InvalidRuleError",
    "Kind",
    "MissingArtifactError",
    "Node",
    "NodeFinished",
    "NodeResult",
    "NodeStarted",
    "PlanEntry",
    "Provenance",
    "ResolvedInput",
    "Rule",
    "RuleFailedError",
    "RuleParams",
    "RunContext",
    "RunReport",
    "Source",
    "Store",
    "StoreError",
    "artifact_key",
    "canonical_bytes",
    "canonical_json",
    "digest_bytes",
    "digest_dir",
    "digest_file",
    "digest_path",
    "key_for",
    "rule",
]
