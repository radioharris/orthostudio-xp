"""Which polygon and road types stay out of the overlay (spec ``overlays.md`` section 4).

The grammar is Ortho4XP's (``O4_Overlay_Utils.py:133-149`` for polygons, ``159-165`` for networks):
an ``int`` is a definition index or a road type, a ``str`` is a substring of a ``POLYGON_DEF`` name,
optionally negated with a leading ``!``; ``""`` or ``"*"`` in the network list drops every segment.
The OrthoStudio XP default excludes the beaches **by name** where Ortho4XP excluded index 0.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import Field, field_validator

from orthostudio.graph.rule import RuleParams

__all__ = [
    "ALWAYS_EXCLUDED_POLYGONS",
    "DEFAULT_EXCLUDED_POLYGONS",
    "OverlayExclusions",
    "exclusions_by_name",
    "networks_all_excluded",
    "resolve_network_exclusions",
    "resolve_polygon_exclusions",
]

DEFAULT_EXCLUDED_POLYGONS: tuple[str, ...] = ("lib/g12/beaches.bch", "lib/g8/beaches.bch")
"""The beach polygon definitions of X-Plane 12 (``1200 beaches``) and XP10/11 (``900 beaches``).

Both are what ``ovl_exclude_pol = [0]`` meant in Ortho4XP on a Global Scenery source (verified:
``POLYGON_DEF`` 0 of the sampled XP12 tiles is ``lib/g12/beaches.bch``).
"""

ALWAYS_EXCLUDED_POLYGONS: tuple[str, ...] = ("lib/g10/terrain10/apt_border",)
"""The line X-Plane 12 draws around an airport, dropped whatever the settings say.

``apt_border_<climate>.lin`` feathers the edge of the airport grass into the terrain around it,
and Laminar keeps it in the base mesh, not in the autogen. An overlay that copies it lays it over
a photograph that already shows the ground: what the pilot sees is a painted outline around every
airfield, sand-coloured in the desert, that follows no airport boundary he can edit. Reported for
Ortho4XP on the X-Plane.Org forum (topic 349619, 2026-07), and measured in our own overlay of
+46+006: fifteen of them, one per airfield, Geneva's 99 points included.

It is not a setting: the line exists to blend a terrain OrthoStudio XP does not draw, so nothing
of value is lost, and a user cannot be expected to find it. ``ovl_exclude_pol`` stays what it was
for everything else.
"""

_ALL_NETWORKS = ("", "*")


def _default_polygons() -> list[str | int]:
    return list(DEFAULT_EXCLUDED_POLYGONS)


def _items(value: object) -> list[object]:
    """The raw items of a list-like value (a validator in ``before`` mode sees ``True`` as
    ``True``, not as the ``1`` pydantic would coerce it to)."""
    if isinstance(value, list | tuple):
        return list(value)
    raise ValueError(f"expected a list, got {type(value).__name__}")


class OverlayExclusions(RuleParams):
    """Parameters of the overlay extraction (frozen, closed; Ortho4XP names kept)."""

    ovl_exclude_pol: list[str | int] = Field(default_factory=_default_polygons)
    """Polygon types to drop: definition indices, or substrings of definition names
    (``!`` prefix inverts the match)."""

    ovl_exclude_net: list[str | int] = Field(default_factory=list)
    """Road types (the subtype number of ``BEGIN_SEGMENT``) to drop; ``"*"`` or ``""`` drops
    every segment."""

    keep_objects: bool = True
    """Keep ``OBJECT_DEF`` and ``OBJECT`` lines (Ortho4XP dropped them by omission)."""

    @field_validator("ovl_exclude_pol", mode="before")
    @classmethod
    def _check_pol(cls, items: object) -> object:
        for item in _items(items):
            if isinstance(item, bool) or not isinstance(item, int | str):
                raise ValueError(f"ovl_exclude_pol: expected int or str, got {item!r}")
            if isinstance(item, int) and item < 0:
                raise ValueError(f"ovl_exclude_pol: a definition index is >= 0, got {item}")
        return items

    @field_validator("ovl_exclude_net", mode="before")
    @classmethod
    def _check_net(cls, items: object) -> object:
        for item in _items(items):
            if isinstance(item, bool) or not isinstance(item, int | str):
                raise ValueError(f"ovl_exclude_net: expected int, '*' or '', got {item!r}")
            if isinstance(item, str) and item not in _ALL_NETWORKS:
                raise ValueError(
                    f"ovl_exclude_net: road types are numbers (see roads.net); {item!r} is not "
                    "one, and '*' drops every segment"
                )
            if isinstance(item, int) and item < 0:
                raise ValueError(f"ovl_exclude_net: a road type is >= 0, got {item}")
        return items


def resolve_polygon_exclusions(defs: Sequence[str], items: Iterable[str | int]) -> frozenset[int]:
    """Indices of ``defs`` excluded by ``items`` (``O4_Overlay_Utils.py:133-149``).

    ``int``: that index (an index beyond ``defs`` is kept in the set, harmless: no polygon
    uses it). ``str``: every index whose name contains it; with a leading ``!``, every index
    whose name does **not** contain the rest. Items are united, and
    :data:`ALWAYS_EXCLUDED_POLYGONS` joins them whatever was asked for.
    """
    out = {
        k
        for k, name in enumerate(defs)
        if any(always in name for always in ALWAYS_EXCLUDED_POLYGONS)
    }
    for item in items:
        if isinstance(item, int):
            out.add(item)
            continue
        if item.startswith("!"):
            needle = item[1:]
            out.update(k for k, name in enumerate(defs) if needle not in name)
        else:
            out.update(k for k, name in enumerate(defs) if item in name)
    return frozenset(out)


def networks_all_excluded(items: Iterable[str | int]) -> bool:
    """True when ``""`` or ``"*"`` is in the list (``O4_Overlay_Utils.py:163-164``)."""
    return any(isinstance(item, str) and item in _ALL_NETWORKS for item in items)


def resolve_network_exclusions(items: Iterable[str | int]) -> frozenset[int]:
    """Road types (``int`` items) excluded by ``items``; strings are handled by
    :func:`networks_all_excluded`."""
    return frozenset(item for item in items if isinstance(item, int))


def exclusions_by_name(exclusions: OverlayExclusions, defs: Sequence[str]) -> OverlayExclusions:
    """Rewrite the polygon indices of an Ortho4XP configuration into the exact names of ``defs``.

    ``[0]`` on an XP12 Global Scenery source becomes ``["lib/g12/beaches.bch"]``. String
    items are kept as they are; an index with no definition in ``defs`` is dropped (it
    excluded nothing). Duplicates are removed, order of first appearance kept.
    """
    names: list[str | int] = []
    for item in exclusions.ovl_exclude_pol:
        resolved: str | int = defs[item] if isinstance(item, int) and item < len(defs) else item
        if isinstance(item, int) and item >= len(defs):
            continue
        if resolved not in names:
            names.append(resolved)
    return exclusions.model_copy(update={"ovl_exclude_pol": names})
