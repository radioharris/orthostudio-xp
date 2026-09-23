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

import contextlib
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.sources.library import (
    LibraryIndex,
    LibrarySource,
    parse_manifest,
    shipped_library,
    unpack,
)
from orthostudio.sources.osm import LayerSpec, OsmSnapshot
from orthostudio.sources.prepared import EMPTY_LAYER_BYTES, PublicSource, snapshot_from_xml

__all__ = [
    "Chain",
    "ChainResult",
    "FolderSource",
    "PreparedSource",
    "settings_trouble",
    "sources_from_settings",
]

log = logging.getLogger("orthostudio.sources.chain")

GIVE_UP_AFTER = 2
"""Failures of one source in a single build before it is asked nothing more."""
EMPTY_BYTES = EMPTY_LAYER_BYTES
"""A layer file smaller than this holds no element at all, whoever publishes it
(``prepared.EMPTY_LAYER_BYTES``)."""


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
        self._index: LibraryIndex | bool | None = False
        """The manifest of a copied library, when the folder is one: read once, on the first
        tile asked for. ``False`` means not looked for yet."""

    def index(self) -> LibraryIndex | None:
        """The manifest beside the files, when the folder is a copy of a baked library.

        Whoever copies a library to disk gets what the library gets: a square that really holds
        no road is taken rather than sent to the public servers, because the manifest says what
        each file's content must hash to. A folder without one, an Ortho4XP folder, keeps the
        strict rule, which is the only honest thing to do with files that say nothing about
        themselves (2026-09-23).
        """
        if self._index is False:
            path = self.root / "manifest.json"
            self._index = parse_manifest(path.read_bytes()) if path.is_file() else None
        return self._index or None

    def _vouched(self, tile: TileRef, spec: LayerSpec, snap: OsmSnapshot) -> bool:
        """Whether a manifest beside the files announces this very document."""
        index = self.index()
        entry = index.entry(tile, spec.name) if index is not None else None
        return bool(entry) and str(entry.get("digest", "")) == snap.digest  # type: ignore[union-attr]

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
                # under this there is no document at all, not even the wrapper of an empty
                # layer. An empty coastline is the exception: Ortho4XP's format writes one in
                # 26 bytes, and an inland tile has no coast, which is not a failure of anything
                log.info("%s: %s is a truncated file in %s", tile.name, spec.name, self.name)
                return None
            try:
                raw = path.read_bytes()
                if path.suffix == ".zst":
                    snap = OsmSnapshot.from_json(unpack(raw))
                    if snap.layer != spec.name or snap.tile.name != tile.name:
                        # a file mis-filed by hand: its path says one square, the document says
                        # another, and another square's geometry would be recorded as this one
                        log.warning("%s: %s holds %s of %s", self.name, path.name,
                                    snap.layer, snap.tile.name)  # fmt: skip
                        return None
                    if (
                        snap.is_empty
                        and spec.name != "coastline"
                        and not self._vouched(tile, spec, snap)
                    ):
                        log.info("%s: %s of %s holds nothing and says so on nobody's word",
                                 self.name, spec.name, tile.name)  # fmt: skip
                        return None
                    if tuple(snap.selectors) != tuple(spec.selectors):
                        # the same layer name, a different question (``library.py``)
                        log.info("%s: %s of %s was prepared for other selectors", self.name,
                                 spec.name, tile.name)  # fmt: skip
                        return None
                    return snap
                return snapshot_from_xml(raw, tile, spec, mirror=self.name)
            except Exception:  # unreadable is unreadable: XML raises SyntaxError, zstd its own
                log.warning("%s: %s unreadable in %s", tile.name, spec.name, self.name)
                return None
        return None


# -- the chain --------------------------------------------------------------------------------


class Chain:
    """The prepared sources of a build, asked in order until one holds the whole tile."""

    def __init__(
        self,
        sources: Sequence[PreparedSource],
        *,
        give_up_after: int = GIVE_UP_AFTER,
        say: Callable[[str], None] | None = None,
    ) -> None:
        self.sources = list(sources)
        self.give_up_after = give_up_after
        self.say = say
        """Where a thing the user should know goes. A source set aside is the difference between
        a build that reads prepared tiles and one that queues behind the public servers for an
        hour, and it used to happen in the log alone (review M4)."""
        self.failures: dict[str, int] = {}
        self._lock = threading.Lock()
        """A batch runs two network slots and they share this chain: without it, two failures of
        one source can be counted as one and a source set aside in one slot is still asked in the
        other (review F5)."""

    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> ChainResult:
        """The layers of ``tile`` from the first source holding them all, or an empty result."""
        notes: list[str] = []
        for source in self.sources:
            with self._lock:
                spent = self.failures.get(source.name, 0)
            if spent >= self.give_up_after:
                continue  # set aside for the rest of this build
            try:
                got = source.layers(tile, specs)
            except Exception as exc:  # a library must never stop a build
                with self._lock:
                    spent = self.failures.get(source.name, 0) + 1
                    self.failures[source.name] = spent
                notes.append(f"{source.name}: {type(exc).__name__}: {exc}")
                log.warning("%s: %s failed (%s)", tile.name, source.name, exc)
                if spent == self.give_up_after:
                    self._tell(
                        OsxpError(
                            "OSM_PREPARED_SET_ASIDE",
                            context={"source": source.name, "reason": str(exc)},
                        )
                    )
                continue
            if got is None:
                notes.append(f"{source.name}: not held")
                continue
            missing = [s.name for s in specs if s.name not in got]
            if missing:  # a source that says yes must hold the whole tile
                notes.append(f"{source.name}: missing {', '.join(missing)}")
                continue
            stamp = str(getattr(source, "stamp", "") or "")
            name = f"{source.name} ({stamp})" if stamp else source.name
            return ChainResult(got, name, tuple(notes))
        return ChainResult(None, "", tuple(notes))

    def _tell(self, trouble: OsxpError) -> None:
        log.warning("%s: %s", trouble.code, trouble.message)
        if self.say is not None:
            with contextlib.suppress(Exception):  # telling must never stop a build
                self.say(f"{trouble.message} {trouble.remedy}")


def settings_trouble(settings: Mapping[str, object]) -> list[OsxpError]:
    """What is wrong with the prepared settings, before a build starts.

    A folder that does not exist, an address without a key, a key without an address: each one
    makes the setting do nothing at all, and each one used to look exactly like a tile outside
    the library's coverage (review M4). Returned rather than raised: none of them is a reason
    to refuse to build.
    """
    out: list[OsxpError] = []
    folder = str(settings.get("osm_folder", "") or "").strip()
    if folder and not Path(folder).expanduser().is_dir():
        out.append(OsxpError("OSM_PREPARED_FOLDER_MISSING", context={"path": folder}))
    url = str(settings.get("osm_library", "") or "").strip()
    token = str(settings.get("osm_library_token", "") or "").strip()
    if url and not token and not shipped_library()[1]:
        out.append(
            OsxpError(
                "OSM_LIBRARY_KEY_REFUSED",
                context={"url": url, "status": "no key given"},
                message=f"The prepared map library at {url} was given no key, and answers nothing.",
                remedy="Fill in the key beside the address in Settings, or clear both: without a "
                "key the address does nothing and every tile is downloaded from the public "
                "servers.",
            )
        )
    if token and not url and not shipped_library()[0]:
        out.append(
            OsxpError(
                "OSM_LIBRARY_UNREACHABLE",
                context={"url": "(none)", "reason": "a key was given, but no address"},
            )
        )
    return out


def sources_from_settings(
    settings: Mapping[str, object], *, cache_dir: Path | None = None
) -> list[PreparedSource]:
    """The sources a build asks, in order, read from the settings (``osm-prepared.md`` 6).

    The whitelist of the public library is taken from our own manifest, and lazily: our library
    is asked first, so its manifest is read by the time the public one is reached. No manifest,
    no whitelist, and that source stays inert.
    """
    out: list[PreparedSource] = []
    folder = str(settings.get("osm_folder", "") or "").strip()
    if folder:
        out.append(FolderSource(folder))
    # what the user set wins; what the build carries answers when they set nothing; and a build
    # from source carries nothing, so it downloads every tile live as every version did before
    shipped_url, shipped_token = shipped_library()
    url = str(settings.get("osm_library", "") or "").strip() or shipped_url
    library: LibrarySource | None = None
    if url:
        token = str(settings.get("osm_library_token", "") or "") or shipped_token
        library = LibrarySource(url, token, cache_dir=cache_dir)
        out.append(library)
    if settings.get("osm_prepared_public", True):
        out.append(
            PublicSource(
                whitelist_of(library), version=verified_version_of(library), cache_dir=cache_dir
            )
        )
    return out


def whitelist_of(library: object) -> Callable[[], frozenset[str]]:
    """The tiles of the public library our own manifest says we verified, read when asked."""

    def tiles() -> frozenset[str]:
        index = getattr(library, "index", None)
        return frozenset(getattr(index, "verified_elsewhere", ()) or ())

    return tiles


def verified_version_of(library: object) -> Callable[[], str]:
    """Which of their bakes the whitelist was made against, read when asked."""

    def version() -> str:
        index = getattr(library, "index", None)
        return str(getattr(index, "verified_elsewhere_version", "") or "")

    return version
