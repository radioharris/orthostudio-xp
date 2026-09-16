"""Per-layer OSM tag sets, transcribed from Ortho4XP.

Specification: ``docs/specs/vectors-osm-layers.md`` section 3.

``OSM_queries_to_OSM_layer`` (``O4_OSM_Utils.py:392-419``) derives two dictionaries from the
Overpass selectors of a layer and from its ``tags_of_interest``:

* ``input_tags``: what makes an element a *first catch* (an element the query asked for, as
  opposed to a child pulled in by the ``(._;>>;)`` recursion);
* ``target_tags``: what tags are kept at all.

The selectors themselves are not re-typed here: they live once, verbatim and with their
origin, in :data:`orthostudio.sources.osm.LAYERS` (``docs/specs/osm-source.md`` section 3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from orthostudio.sources.osm import LAYERS, LayerSpec

__all__ = [
    "LAKE_NAME_TAG",
    "LAYER_TAGS",
    "OSM_TYPES",
    "ROAD_EXCLUSION_TAGS",
    "LayerTags",
    "OsmType",
    "TagPair",
    "layer_tags",
    "tags_from_selectors",
]

OsmType = Literal["n", "w", "r"]
"""The three OSM element types, spelled as Ortho4XP keys its dictionaries."""

OSM_TYPES: tuple[OsmType, ...] = ("n", "w", "r")

TagPair = tuple[str, str]
"""``(key, value)``; an empty value means "any value of that key" (``way["aeroway"]``)."""

ROAD_EXCLUSION_TAGS: frozenset[str] = frozenset({"bridge", "tunnel"})
"""``O4_Vector_Map.py:258`` ``tags_for_exclusion``: a road carrying either is not levelled."""

LAKE_NAME_TAG = "name"
"""``O4_Vector_Map.py:459-482``: a lake named in ``good_imagery_list`` escapes the sea."""


@dataclass(frozen=True, slots=True)
class LayerTags:
    """The two tag filters of one layer, keyed by OSM type (``n``, ``w``, ``r``)."""

    name: str
    input_tags: Mapping[OsmType, tuple[TagPair, ...]]
    target_tags: Mapping[OsmType, tuple[TagPair, ...]]

    def keeps(self, kind: OsmType, key: str, value: str) -> bool:
        """Is this tag stored? ``O4_OSM_Utils.py:164-170``."""
        target = self.target_tags[kind]
        return ("all", "") in target or (key, "") in target or (key, value) in target

    def is_first(self, kind: OsmType, key: str, value: str) -> bool:
        """Does this tag make its element a first catch? ``O4_OSM_Utils.py:177-181``."""
        source = self.input_tags[kind]
        return (key, "") in source or (key, value) in source


def tags_from_selectors(
    selectors: Sequence[str], tags_of_interest: Iterable[str | TagPair] = ()
) -> tuple[dict[OsmType, tuple[TagPair, ...]], dict[OsmType, tuple[TagPair, ...]]]:
    """``(input_tags, target_tags)`` as ``OSM_queries_to_OSM_layer:395-419`` builds them.

    One pair per selector, ``("key", "")`` when the selector names a key only; then, **for the
    OSM type of that selector only**, one ``(tag, "")`` per entry of ``tags_of_interest``.
    That nesting (``:409-419``) is why the ``water`` layer keeps ``name`` on ways and
    relations but not on nodes.
    """
    interest = tuple(tags_of_interest)
    input_tags: dict[OsmType, list[TagPair]] = {"n": [], "w": [], "r": []}
    target_tags: dict[OsmType, list[TagPair]] = {"n": [], "w": [], "r": []}
    for selector in selectors:
        items = selector.split('"')
        kind = _osm_type(items[0][0])
        pair: TagPair = (items[1], items[3]) if len(items) > 3 else (items[1], "")
        input_tags[kind].append(pair)
        target_tags[kind].append(pair)
        for tag in interest:
            extra: TagPair = (tag, "") if isinstance(tag, str) else tag
            if extra not in target_tags[kind]:
                target_tags[kind].append(extra)
    return (
        {k: tuple(v) for k, v in input_tags.items()},
        {k: tuple(v) for k, v in target_tags.items()},
    )


def _osm_type(letter: str) -> OsmType:
    if letter not in ("n", "w", "r"):  # pragma: no cover - a malformed selector
        raise ValueError(f"selector does not start with node/way/rel: {letter!r}")
    return letter  # type: ignore[return-value]


def layer_tags(layer: str | LayerSpec) -> LayerTags:
    """The tag filters of a layer, by name or from a :class:`LayerSpec`.

    Pass the spec (from ``layers_for(road_level)``) rather than the name for ``small_roads``:
    its selector list grows with ``road_level`` (``O4_Vector_Map.py:288-299``).
    """
    spec = LAYERS[layer] if isinstance(layer, str) else layer
    input_tags, target_tags = tags_from_selectors(spec.selectors, spec.tags_of_interest)
    return LayerTags(
        name=spec.name,
        input_tags=MappingProxyType(input_tags),
        target_tags=MappingProxyType(target_tags),
    )


LAYER_TAGS: Mapping[str, LayerTags] = MappingProxyType(
    {name: layer_tags(spec) for name, spec in LAYERS.items()}
)
"""The five layers at the Ortho4XP default ``road_level``; ``small_roads`` at its widest."""
