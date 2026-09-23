"""Where a tile's map data comes from, in order: prepared libraries first, Overpass last.

Specification: ``docs/specs/osm-prepared.md``. The night of 2026-09-22 stopped every build on
every continent because the one thing a tile cannot do without was asked of three public machines
and two of them became unusable at once. The data is public and downloadable in bulk, so the same
four questions can be answered from files, in milliseconds, with no quota.

This module holds the chain and its refusals; the readers live in ``prepared.py`` and the live
client in ``osm.py``.

Two rules carry the whole thing:

* **all or nothing per tile.** A source that holds three layers of four gives nothing. Mixing
  sources inside one tile is how an incoherent tile is made: a coastline truncated at a national
  border under roads that are complete, and nothing to show for it.
* **a source that breaks is set aside.** Two failures in one build and it is not asked again:
  a library exists to save seconds, and one that hangs would cost them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import orjson
import zstandard

from orthostudio.model import TileRef
from orthostudio.sources.osm import LayerSpec, OsmSnapshot
from orthostudio.sources.prepared import snapshot_from_xml

__all__ = [
    "Chain",
    "ChainResult",
    "FolderSource",
    "PreparedSource",
    "sources_from_settings",
]

log = logging.getLogger("orthostudio.sources.chain")

GIVE_UP_AFTER = 2
"""Failures of one source in a single build before it is asked nothing more."""
EMPTY_BYTES = 200
"""A layer file smaller than this holds no element at all (an empty document is about 100 bytes).

Not an answer, whoever it comes from: 22 % of the tiles the one public library lists hold no road
at all, and a build that took them would lay scenery with no roads and say nothing (2026-09-19,
measured again unchanged on 2026-09-23).
"""


class PreparedSource(Protocol):
    """One place a tile's layers may be read from, before the live servers are asked."""

    name: str

    def layers(
        self, tile: TileRef, specs: Sequence[LayerSpec]
    ) -> dict[str, OsmSnapshot] | None: ...


@dataclass(frozen=True, slots=True)
class ChainResult:
    """What the chain did: the snapshots, the source that gave them, what the others said."""

    snapshots: dict[str, OsmSnapshot] | None
    source: str
    notes: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.snapshots is not None


# -- a folder on the user's own disk ----------------------------------------------------------


def _our_name(tile: TileRef, layer: str) -> str:
    return f"{tile.name}_{layer}.osm.json.zst"


def _their_name(tile: TileRef, layer: str) -> str:
    return f"{tile.name}_{layer}.osm.bz2"


def _candidates(root: Path, tile: TileRef, layer: str) -> list[Path]:
    """Where a layer's file may sit, ours and Ortho4XP's layouts, deepest first."""
    cell, name = tile.folder, tile.name
    out: list[Path] = []
    for leaf in (_our_name(tile, layer), _their_name(tile, layer)):
        out.extend(
            (
                root / "osm" / cell / name / leaf,
                root / cell / name / leaf,
                root / name / leaf,
                root / leaf,
            )
        )
    return out


class FolderSource:
    """A folder of prepared layers on disk, in our format or in Ortho4XP's.

    A user asked for what Ortho4XP's OSM folder gives him: a place to drop files he already has,
    so that a build reads them instead of queueing behind a public server (2026-09-23). Both
    shapes are read, since the library he downloads publishes Ortho4XP's.
    """

    def __init__(self, root: Path | str, *, name: str = "folder") -> None:
        self.root = Path(root).expanduser()
        self.name = name

    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot] | None:
        out: dict[str, OsmSnapshot] = {}
        for spec in specs:
            snap = self._one(tile, spec)
            if snap is None:
                return None  # all or nothing: a folder short of one layer gives none
            out[spec.name] = snap
        return out

    def _one(self, tile: TileRef, spec: LayerSpec) -> OsmSnapshot | None:
        for path in _candidates(self.root, tile, spec.name):
            if not path.is_file():
                continue
            if path.stat().st_size < EMPTY_BYTES and spec.name != "coastline":
                # an empty layer is an answer only where emptiness is the truth: an inland tile
                # has no coastline, no tile at all has no roads
                log.info("%s: %s is empty in %s", tile.name, spec.name, self.name)
                return None
            try:
                raw = path.read_bytes()
                if path.suffix == ".zst":
                    return OsmSnapshot.from_json(zstandard.ZstdDecompressor().decompress(raw))
                return snapshot_from_xml(raw, tile, spec, mirror=self.name)
            except (OSError, ValueError, KeyError, orjson.JSONDecodeError, zstandard.ZstdError):
                log.warning("%s: %s unreadable in %s", tile.name, spec.name, self.name)
                return None
        return None


# -- the chain --------------------------------------------------------------------------------


class Chain:
    """The prepared sources of a build, asked in order until one holds the whole tile."""

    def __init__(
        self, sources: Sequence[PreparedSource], *, give_up_after: int = GIVE_UP_AFTER
    ) -> None:
        self.sources = list(sources)
        self.give_up_after = give_up_after
        self.failures: dict[str, int] = {}

    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> ChainResult:
        """The layers of ``tile`` from the first source holding them all, or an empty result."""
        notes: list[str] = []
        for source in self.sources:
            if self.failures.get(source.name, 0) >= self.give_up_after:
                continue  # set aside for the rest of this build
            try:
                got = source.layers(tile, specs)
            except Exception as exc:  # a library must never stop a build
                self.failures[source.name] = self.failures.get(source.name, 0) + 1
                notes.append(f"{source.name}: {type(exc).__name__}: {exc}")
                log.warning("%s: %s failed (%s)", tile.name, source.name, exc)
                continue
            if got is None:
                notes.append(f"{source.name}: not held")
                continue
            missing = [s.name for s in specs if s.name not in got]
            if missing:  # a source that says yes must hold the whole tile
                notes.append(f"{source.name}: missing {', '.join(missing)}")
                continue
            return ChainResult(got, source.name, tuple(notes))
        return ChainResult(None, "", tuple(notes))


def sources_from_settings(
    settings: Mapping[str, object], *, cache_dir: Path | None = None
) -> list[PreparedSource]:
    """The sources a build asks, in order, read from the settings (``osm-prepared.md`` 6).

    The whitelist of the public library is taken from our own manifest, and lazily: our library
    is asked first, so its manifest is read by the time the public one is reached. No manifest,
    no whitelist, and that source stays inert.
    """
    from orthostudio.sources.library import LibrarySource
    from orthostudio.sources.prepared import PublicSource

    out: list[PreparedSource] = []
    folder = str(settings.get("osm_folder", "") or "").strip()
    if folder:
        out.append(FolderSource(folder))
    url = str(settings.get("osm_library", "") or "").strip()
    library: LibrarySource | None = None
    if url:
        token = str(settings.get("osm_library_token", "") or "")
        library = LibrarySource(url, token, cache_dir=cache_dir)
        out.append(library)
    if settings.get("osm_prepared_public", True):
        out.append(PublicSource(whitelist_of(library), cache_dir=cache_dir))
    return out


def whitelist_of(library: object) -> Callable[[], frozenset[str]]:
    """The tiles of the public library our own manifest says we verified, read when asked."""

    def tiles() -> frozenset[str]:
        index = getattr(library, "index", None)
        return frozenset(getattr(index, "verified_elsewhere", ()) or ())

    return tiles
