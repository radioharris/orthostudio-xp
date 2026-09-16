"""External data sources OrthoStudio XP talks to directly (OpenStreetMap through Overpass, for now).

Spec: ``docs/specs/osm-source.md``.
"""

from orthostudio.sources.osm import (
    LAYERS,
    MIRRORS,
    SNAPSHOT_FORMAT,
    LayerSpec,
    Mirror,
    MirrorHealth,
    OsmNode,
    OsmRelation,
    OsmSnapshot,
    OsmWay,
    OverpassClient,
    SnapshotStore,
    Transport,
    layers_for,
    overpass_query,
    snapshot_from_overpass,
    snapshot_label,
)

__all__ = [
    "LAYERS",
    "MIRRORS",
    "SNAPSHOT_FORMAT",
    "LayerSpec",
    "Mirror",
    "MirrorHealth",
    "OsmNode",
    "OsmRelation",
    "OsmSnapshot",
    "OsmWay",
    "OverpassClient",
    "SnapshotStore",
    "Transport",
    "layers_for",
    "overpass_query",
    "snapshot_from_overpass",
    "snapshot_label",
]
