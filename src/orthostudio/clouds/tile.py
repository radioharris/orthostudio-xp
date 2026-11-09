# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a tile's folder shows of its replaced clouds (``clouds.md`` 2.1).

The clouds are no part of the build. A texture showing clouds is the texture the build placed, its
blocks under the clouds made again (``laying``), every other block its own. Its header says so
itself, in the 44 bytes DDS leaves to tools and X-Plane does not read (:class:`Stamp`): the digest
of the texture as the build made it, kept beside in the folder's ``clouds/`` under the same name,
and the digest of the clouds it shows. A texture without a stamp is the build's own and shows no
cloud. So what the folder shows is read from the folder alone, whatever stopped a write: a texture
is whole, old or new, and says which.

Beside the textures, the clouds each stamp names are written (:func:`sidecar_path`): the tile shows
the clouds every texture they reach names, the same in each (:func:`read_tile`); nothing else, not
the list, says what it shows. A cloud's images stand on the tile as long as every texture it
reaches is the one they were got on, as built (:func:`stands_not`).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from orthostudio.clouds.layer import REACH_PX, CloudPatch, ring_pixels, tile_box, window
from orthostudio.errors import OsxpError
from orthostudio.graph.digest import digest_bytes, digest_file
from orthostudio.graph.store import Store
from orthostudio.home import default_chunks_root, default_store_root, default_tiles_root, osxp_home
from orthostudio.imagery.grid import TextureId, parse_texture_name, texture_name
from orthostudio.install.library import default_library_path
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import tile_home
from orthostudio.pipeline.pack import MANIFEST_NAME, PackManifest, pack_dir_name, pack_is_intact
from orthostudio.textures.assemble import CHUNK, TEXTURE

__all__ = [
    "DIGEST_HEX",
    "LAID_KEYS",
    "Stamp",
    "TileClouds",
    "beside",
    "clouds_digest",
    "clouds_dir",
    "entry_patches",
    "entry_problem",
    "file_digest",
    "find_part",
    "held_by_id",
    "identity",
    "is_own_folder",
    "kept_files",
    "laid_fields",
    "leftovers",
    "list_path",
    "listed",
    "own_folder",
    "part_path",
    "parts_dir",
    "patches_of",
    "picture_path",
    "preview_path",
    "previews_dir",
    "reaching",
    "read_checked",
    "read_head",
    "read_tile",
    "ring_problem",
    "same_laid",
    "served_path",
    "sidecar_path",
    "sidecars",
    "stamped",
    "stands_not",
    "texture_digest",
    "unready",
]

CLOUDS_DIR = "clouds"
TEXTURES_DIR = "textures"
"""The pack's folder of textures (``pack.write_pack``)."""

# -- where the cloud files are: every path of them, and its inverse ----------------------------


def lists_dir() -> Path:
    """The users' lists of clouds, one per tile (``$OSXP_HOME/clouds``)."""
    return osxp_home() / CLOUDS_DIR


def list_path(tile: TileRef) -> Path:
    """The user's list of the tile's clouds (``$OSXP_HOME/clouds/<tile>.json``)."""
    return lists_dir() / f"{tile.name}.json"


def listed() -> set[str]:
    """The names of the tiles that have a list (:func:`list_path`); no other file there is one."""
    out = set()
    for p in lists_dir().glob("*.json"):
        with contextlib.suppress(ValueError):
            out.add(TileRef.parse(p.stem).name)
    return out


def parts_dir() -> Path:
    """The frozen parts of the clouds whose images were got, beside the image pieces
    (``<data>/clouds/parts``)."""
    return default_chunks_root().parent / CLOUDS_DIR / "parts"


def part_path(digest: str) -> Path:
    """The file of the part ``digest``."""
    return parts_dir() / f"{digest}.part"


def find_part(digest: str) -> Path | None:
    """The file of the part ``digest`` when it is there whole; ``None`` when it is not there or
    is another's; ``CLOUD_TEXTURE_UNREADABLE`` when it cannot be read now (never taken for gone)."""
    path = part_path(digest)
    try:
        return path if file_digest(path) == digest else None
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OsxpError(
            "CLOUD_TEXTURE_UNREADABLE", context={"tile": "", "path": str(path), "reason": str(exc)}
        ) from exc


def textures_dir(folder: Path) -> Path:
    """A tile's textures, those X-Plane shows."""
    return Path(folder) / TEXTURES_DIR


def clouds_dir(folder: Path) -> Path:
    """Beside a tile's textures, what its replaced clouds are made of: each texture as built, kept
    while it shows clouds (:meth:`TileClouds.kept_path`), and the clouds each shows
    (:func:`sidecar_path`)."""
    return Path(folder) / CLOUDS_DIR


def sidecar_path(folder: Path, clouds: str) -> Path:
    """Where the clouds a texture stamped ``clouds`` shows are written, beside the textures."""
    return clouds_dir(folder) / f"{clouds}.json"


_DIGEST = re.compile(r"[0-9a-f]{40}")


def sidecars(folder: Path) -> dict[str, Path]:
    """The clouds written beside the textures (:func:`sidecar_path`), by their digest; another
    file there is not one."""
    d = clouds_dir(folder)
    found = d.glob("*.json") if d.is_dir() else ()
    return {p.stem: p for p in found if _DIGEST.fullmatch(p.stem)}


def kept_files(folder: Path) -> dict[str, Path]:
    """The textures kept as built beside them (:meth:`TileClouds.kept_path`), by name."""
    d = clouds_dir(folder)
    return {p.stem: p for p in (d.glob("*.dds") if d.is_dir() else ())}


_OURS = ".osxp-clouds-"


def beside(path: Path, what: str) -> Path:
    """A file laying makes beside ``path`` for a while, this program's
    (``<name>.osxp-clouds-<what>-<pid>``, :func:`leftovers`)."""
    return path.with_name(f"{path.name}{_OURS}{what}-{os.getpid()}")


def leftovers(folder: Path) -> list[tuple[Path, int]]:
    """The files laying made beside a tile's textures (:func:`beside`), each with the id of the
    program that made it."""
    out = []
    for d in (textures_dir(folder), clouds_dir(folder)):
        for left in d.glob(f"*{_OURS}*") if d.is_dir() else ():
            pid = left.name.rsplit("-", 1)[-1].split(".", 1)[0]
            if pid.isdigit():
                out.append((left, int(pid)))
    return out


def previews_dir(tile: TileRef) -> Path:
    """The tile's pictures and its clouds' previews (``<data>/clouds/<tile>``), made at will."""
    return default_chunks_root().parent / CLOUDS_DIR / tile.name


def picture_path(tile: TileRef, what: str) -> Path:
    """The picture of the tile drawn from ``what`` (what it is made of, said in a line)."""
    return previews_dir(tile) / f"{digest_bytes(what.encode())[:32]}.jpg"


def preview_path(tile: TileRef, what: str) -> Path:
    """The preview of a cloud made of ``what``."""
    return previews_dir(tile) / f"{digest_bytes(what.encode())[:32]}.png"


_SERVED = re.compile(r"[0-9a-f]{32}\.(png|jpg)")


def served_path(tile: TileRef, name: str) -> Path | None:
    """The picture or preview of the tile named ``name`` (:func:`picture_path`,
    :func:`preview_path`); ``None`` when that is no such name, or it is not there."""
    path = previews_dir(tile) / name
    return path if _SERVED.fullmatch(name) and path.is_file() else None


def own_folder(tile: TileRef, *, custom_scenery: Path | None) -> Path:
    """The tile's folder its clouds are laid in: the one the app's next build of it writes, where
    it is filed (``filing.tile_home``), else the workshop's."""
    home, _gone = tile_home(
        tile,
        atelier=default_tiles_root(),
        custom_scenery=custom_scenery,
        library_path=default_library_path(),
        mend=False,  # a read: a put back under way is the build's to finish
    )
    return Path(home) if home is not None else default_tiles_root() / pack_dir_name(tile)


def is_own_folder(tile: TileRef, folder: Path, *, custom_scenery: Path | None) -> bool:
    """Whether ``folder`` is the tile's own (:func:`own_folder`)."""
    return Path(folder).resolve() == own_folder(tile, custom_scenery=custom_scenery).resolve()


# -- a texture's stamp -----------------------------------------------------------------------

_STAMP = b"OSXC"
_AT, _END = 32, 76
"""``dwReserved1`` of the DDS header, the magic counted: eleven words no reader of ours, nor
X-Plane, reads (nvtt writes its own tags there)."""
DIGEST_HEX = 40


@dataclass(frozen=True, slots=True)
class Stamp:
    """What a texture showing clouds is made of: the digest of the texture as built (``plain``),
    and of the clouds laid in it (``clouds``, :func:`clouds_digest`)."""

    plain: str
    clouds: str


Identity = tuple[int, int, int]
"""A file as the system knows it: ``(inode, size, mtime_ns)``; another file placed there has
another."""


def _identity(f: Any) -> Identity:
    st = os.fstat(f.fileno())
    return st.st_ino, st.st_size, st.st_mtime_ns


def identity(path: Path) -> Identity:
    """The identity of the file at ``path``, taken from the file opened, as :func:`read_head`
    takes it."""
    with open(path, "rb") as f:
        return _identity(f)


def read_head(path: Path) -> tuple[Stamp | None, Identity]:
    """The stamp of the DDS at ``path`` (``None`` for a texture as built) and the identity of the
    very file it was read from."""
    with open(path, "rb") as f:
        known = _identity(f)
        head = f.read(_END)
    if len(head) < _END or head[:4] != b"DDS " or head[_AT : _AT + 4] != _STAMP:
        return None, known
    return Stamp(head[_AT + 4 : _AT + 24].hex(), head[_AT + 24 : _END].hex()), known


def stamped(header: bytes, stamp: Stamp) -> bytes:
    """The DDS ``header`` (its 128 bytes) with ``stamp`` in it."""
    mark = _STAMP + bytes.fromhex(stamp.plain) + bytes.fromhex(stamp.clouds)
    return header[:_AT] + mark + header[_END:]


_DIGESTS: dict[tuple[str, int, int, int], str] = {}
_DIGESTS_LOCK = threading.Lock()


def file_digest(path: Path) -> str:
    """The digest of the file at ``path``, read again only when the file changed."""
    st = os.stat(path)
    key = (str(path), st.st_ino, st.st_size, st.st_mtime_ns)
    with _DIGESTS_LOCK:
        hit = _DIGESTS.get(key)
    if hit is None:
        hit = digest_file(path)
        with _DIGESTS_LOCK:
            if len(_DIGESTS) > 2048:
                _DIGESTS.clear()
            _DIGESTS[key] = hit
    return hit


def texture_digest(path: Path) -> str:
    """The digest a stamp names a texture by (:func:`file_digest`, shortened)."""
    return file_digest(path)[:DIGEST_HEX]


LAID_KEYS = ("id", "source", "match", "rings", "parts", "plain")
"""What of a cloud's entry makes what it lays; the rest is the list's (its kind, its number)."""


def laid_fields(e: Mapping[str, Any]) -> dict[str, Any]:
    """Of the entry ``e``, what lays it (:data:`LAID_KEYS`)."""
    return {k: e[k] for k in LAID_KEYS}


def same_laid(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether the entries ``a`` and ``b`` lay the same: the same cloud, the same images."""
    return a is not None and b is not None and laid_fields(a) == laid_fields(b)


def clouds_digest(
    entries: Sequence[Mapping[str, Any]],
    said: dict[int, tuple[Mapping[str, Any], str]] | None = None,
) -> str:
    """The digest of the clouds a texture shows, in their order: of the JSON of their laid fields
    (as ``json.dumps`` writes the list). Each cloud's written once in ``said`` (by the entry,
    kept with it) when given, for the textures of one plan: a cloud over 49 textures was written
    49 times, 0.17 s of each reading of a tile of 200 clouds (-02-050, 2026-11-07)."""
    texts = []
    for e in entries:
        hit = said.get(id(e)) if said is not None else None
        if hit is not None and hit[0] is e:
            texts.append(hit[1])
            continue
        text = json.dumps(laid_fields(e), sort_keys=True)
        if said is not None:
            said[id(e)] = (e, text)
        texts.append(text)
    return digest_bytes(("[" + ", ".join(texts) + "]").encode())[:DIGEST_HEX]


_CHECKED: dict[tuple[str, int, int, int, str], Any] = {}
_CHECKED_LOCK = threading.Lock()


def read_checked(path: Path, what: str, check: Callable[[Any], Any]) -> tuple[Any, Any]:
    """The JSON of the file at ``path``, read afresh, and what ``check`` (named ``what``) says
    of it, worked out once while the file stays the same: its inode, size and time (every file
    of the clouds is written whole beside it and moved in, never written in place). Checked at
    each reading, the 124 sidecars and the list of a user's 200 clouds, a million numbers of
    outlines, took 0.9 s of every reading of the tile and four of a tick (-02-050, 2026-11-07).
    ``FileNotFoundError``, any other ``OSError`` and ``ValueError`` are the reading's."""
    with open(path, "rb") as f:
        st = os.fstat(f.fileno())
        data = f.read()
    doc = json.loads(data)
    key = (str(path), st.st_ino, st.st_size, st.st_mtime_ns, what)
    with _CHECKED_LOCK:
        if key in _CHECKED:
            return doc, _CHECKED[key]
    said = check(doc)
    with _CHECKED_LOCK:
        if len(_CHECKED) >= 4096:  # a few lists and sidecars a tile: never so many but in a test
            _CHECKED.clear()
        _CHECKED[key] = said
    return doc, said


# -- the folder ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class TileClouds:
    """What the tile's folder shows of its clouds, read once (:func:`read_tile`)."""

    folder: Path
    built: str
    """What the build placed there, one digest (its manifest's): the picture and the previews
    are named by it, so another build makes them again."""
    provider: str
    """The tile's own source."""
    whole: bool
    """Whether the folder holds the whole pack its manifest names (``pack.pack_is_intact``)."""
    names: tuple[str, ...]
    """Its textures' names."""
    stamps: Mapping[str, Stamp]
    """The textures showing clouds, and what each is made of."""
    files: Mapping[str, Identity]
    """Each texture's identity when its stamp was read: Apply replaces only that very file."""
    held: tuple[dict[str, Any], ...] = ()
    """The clouds it shows replaced: those every texture they reach names beside it
    (:func:`sidecar_path`)."""
    known: frozenset[str] = frozenset()
    """The textures showing clouds whose clouds are written beside them, whole: a texture of
    :attr:`stamps` not here shows clouds no file says (Apply makes it again)."""
    store_key: str | None = None
    """The store's artefact of the textures the build placed (its manifest's), the build's own
    of each when the one kept beside is gone."""
    tile: str = ""
    """Its name, its manifest's."""

    @property
    def textures(self) -> list[TextureId]:
        return [parse_texture_name(n) for n in self.names]

    def texture_path(self, name: str) -> Path:
        """The texture ``name`` X-Plane shows."""
        return textures_dir(self.folder) / f"{name}.dds"

    def kept_path(self, name: str) -> Path:
        """Where the texture ``name`` is kept as the build made it while it shows clouds."""
        return clouds_dir(self.folder) / f"{name}.dds"

    def stored_path(self, name: str) -> Path | None:
        """The texture ``name`` in the store, as the build made it; ``None`` when the store has
        it no more."""
        if self.store_key is None:
            return None
        with contextlib.suppress(Exception), Store(default_store_root(), sweep=False) as store:
            info = store.info(self.store_key)
            if info is not None:
                return Path(info.path) / TEXTURES_DIR / f"{name}.dds"
        return None

    def plain_path(self, name: str) -> Path:
        """The texture ``name`` as the build made it: itself when it shows no cloud, else the one
        kept beside, else the store's (the kept one taken away), each only when it is that very
        texture; ``CLOUD_TEXTURE_LOST`` when none is. The one way it is found: for the map's
        picture, a search, a cloud's images and Apply alike."""
        stamp = self.stamps.get(name)
        if stamp is None:
            return self.texture_path(name)
        kept = self.kept_path(name)
        for path in (kept, self.stored_path(name)):
            if path is not None and path.is_file() and texture_digest(path) == stamp.plain:
                return path
        raise OsxpError("CLOUD_TEXTURE_LOST", context={"tile": self.tile, "path": str(kept)})

    def plain(self, name: str) -> str | None:
        """The digest of the texture ``name`` as the build made it; ``None`` when it has none."""
        stamp = self.stamps.get(name)
        if stamp is not None:
            return stamp.plain
        return texture_digest(self.texture_path(name)) if name in self.names else None


def held_by_id(shown: TileClouds | None) -> dict[str, dict[str, Any]]:
    """The clouds ``shown`` holds replaced, by id; none without a tile."""
    return {c["id"]: c for c in (shown.held if shown is not None else ())}


def reaching(entries: Iterable[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    """Of ``entries``, in their order, those laid in the texture ``name``."""
    return [e for e in entries if name in e["parts"]]


def _names(textures: Path) -> tuple[str, ...]:
    out = []
    for p in sorted(textures.glob("*.dds")):
        try:
            parse_texture_name(p.stem)
        except ValueError:
            continue
        out.append(p.stem)
    return tuple(out)


def read_tile(folder: Path) -> TileClouds | None:
    """What ``folder`` shows of its clouds, read from it alone; ``None`` when it holds no tile of
    ours: no manifest of ours, or its textures or what is kept beside them a link to another
    folder (a build makes neither one; laying never writes into another folder)."""
    folder = Path(folder)
    if textures_dir(folder).is_symlink() or clouds_dir(folder).is_symlink():
        return None
    try:
        text = (folder / MANIFEST_NAME).read_bytes()
        manifest = PackManifest.from_toml(text.decode("utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    names = _names(textures_dir(folder))
    stamps, files = {}, {}
    for n in names:
        path = textures_dir(folder) / f"{n}.dds"
        try:
            stamp, files[n] = read_head(path)
        except OSError as exc:  # never taken for a texture as built: it may show clouds
            raise OsxpError(
                "CLOUD_TEXTURE_UNREADABLE",
                context={"tile": manifest.tile, "path": str(path), "reason": str(exc)},
            ) from exc
        if stamp is not None:
            stamps[n] = stamp
    held, known = _held(folder, stamps)
    store = manifest.artefacts.get("textures")
    return TileClouds(
        folder=folder,
        built=digest_bytes(text)[:32],
        provider=manifest.provider,
        whole=pack_is_intact(folder, manifest, with_overlay=False),
        names=names,
        stamps=stamps,
        files=files,
        held=held,
        known=known,
        store_key=store.key if store is not None else None,
        tile=manifest.tile,
    )


def unready(shown: TileClouds | None) -> str | None:
    """Why a folder read (:func:`read_tile`) is no tile the cloud mode works on: ``not_built``
    (no tile of ours), ``not_whole`` (a build cut short, files taken from it), ``no_photo`` (no
    texture at all: the sea alone, which X-Plane draws without a photo); ``None`` when it is one.
    The one rule, for the tile's state (``state.tile_state``), Apply and the map's picture."""
    if shown is None:
        return "not_built"
    if not shown.whole:
        return "not_whole"
    return None if shown.names else "no_photo"


def _held(
    folder: Path, stamps: Mapping[str, Stamp]
) -> tuple[tuple[dict[str, Any], ...], frozenset[str]]:
    """The clouds every texture they reach shows, named beside each texture's stamp, the same
    entry in all of them; and the textures whose clouds are written so, whole (a file that is
    not a list of whole entries, or not the one its stamp names, says nothing)."""
    seen: dict[str, tuple[dict[str, Any], set[str]]] = {}
    known = set()
    for name, stamp in stamps.items():
        try:
            entries, whole = read_checked(
                sidecar_path(folder, stamp.clouds),
                f"clouds {stamp.clouds}",
                lambda doc, digest=stamp.clouds: _whole_sidecar(doc, digest),
            )
        except (OSError, ValueError):
            continue  # its clouds unknown: none of them shown whole
        if not whole:
            continue
        known.add(name)
        for e in entries:
            entry, where = seen.setdefault(e["id"], (dict(e), set()))
            if same_laid(entry, e):
                where.add(name)
    held = tuple(e for e, where in seen.values() if where == set(e["parts"]))
    return held, frozenset(known)


def _whole_sidecar(entries: Any, digest: str) -> bool:
    """Whether a sidecar's ``entries`` are whole clouds whose digest is the stamp's ``digest``."""
    if not isinstance(entries, list) or any(entry_problem(e) is not None for e in entries):
        return False
    return clouds_digest(entries) == digest


# -- where a cloud stands --------------------------------------------------------------------


def patches_of(
    c: Mapping[str, Any],
    tile: TileRef,
    textures: Iterable[TextureId],
    source_zl: Callable[[TextureId], int],
) -> dict[str, CloudPatch]:
    """The cloud ``c`` (an entry) in each of ``textures`` it reaches, by texture name;
    ``source_zl`` gives the level its source is read at in a texture."""
    gain, shift = c["match"]
    match = (tuple(float(v) for v in gain), tuple(float(v) for v in shift))
    out: dict[str, CloudPatch] = {}
    # its outline in the world's pixels, once a level: converted again for each texture, two
    # hundred clouds over 156 textures took a second at every reading of the tile, the page's
    # every half second (-02-049, 2026-11-06); the textures far from its box left at once, wider
    # than ``window``, which says which it reaches
    world: dict[int, tuple[list[np.ndarray], np.ndarray, np.ndarray]] = {}
    for t in textures:
        if t.zl not in world:
            pts = [ring_pixels(r, t.zl, 0.0, 0.0) for r in c["rings"]]
            every = np.concatenate(pts)
            world[t.zl] = (pts, every.min(axis=0), every.max(axis=0))
        pts, low, high = world[t.zl]
        corner = np.array([t.til_x * CHUNK, t.til_y * CHUNK], np.float64)
        if np.any(high - corner < -REACH_PX - 1) or np.any(low - corner > TEXTURE + REACH_PX + 1):
            continue
        rings = tuple(tuple((r - corner).ravel()) for r in pts)
        box = tile_box(tile.lat, tile.lon, t)
        p = CloudPatch(c["id"], c["source"], source_zl(t), rings, match, box)  # type: ignore[arg-type]
        if window(p) is not None:
            out[texture_name(t)] = p
    return out


def entry_patches(
    c: Mapping[str, Any], tile: TileRef, textures: Iterable[TextureId]
) -> dict[str, CloudPatch]:
    """The cloud entry ``c`` in each of ``textures`` it reaches, its source read at the level
    its part there was got at (the texture's own where it has none)."""
    parts = c["parts"]
    return patches_of(c, tile, textures, lambda t: int(parts.get(texture_name(t), [t.zl])[0]))


def stands_not(c: Mapping[str, Any], tile: TileRef, shown: TileClouds) -> str | None:
    """Why the images got for the cloud ``c`` no longer stand on the tile, or ``None``: the
    textures it reaches are others (``level``, ``source``, ``textures``), or one of them is not
    the one they were got on (``images``). Its frozen parts are what laying needs where it lays
    (``laying.plan_folder``)."""
    parts = c["parts"]
    why = _moved(set(parts), set(entry_patches(c, tile, shown.textures)))
    if why is None and any(c["plain"].get(n) != shown.plain(n) for n in parts):
        why = "images"
    return why


def lays_not(c: Mapping[str, Any], tile: TileRef, shown: TileClouds) -> str | None:
    """Why the images got for the cloud ``c`` cannot be laid afresh on the tile, or ``None``:
    they no longer stand (:func:`stands_not`), or one of their frozen parts is not on this
    computer (``parts``). What the tile shows already needs its parts only where its texture is
    made again (``laying.plan_folder``)."""
    why = stands_not(c, tile, shown)
    if why is None and any(find_part(str(p[1])) is None for p in c["parts"].values()):
        why = "parts"
    return why


def _moved(then: set[str], now: set[str]) -> str | None:
    """Why a cloud got in the textures ``then`` cannot be laid in ``now``, or ``None``."""
    if then == now:
        return None
    if not now:
        return "textures"
    zl = {parse_texture_name(n).zl for n in then} != {parse_texture_name(n).zl for n in now}
    if zl:
        return "level"
    source = {parse_texture_name(n).provider for n in then} != {
        parse_texture_name(n).provider for n in now
    }
    return "source" if source else "textures"


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def ring_problem(rings: Any) -> str | None:
    """Why ``rings`` are not an outline's rings (the zones' format, ``[lat0, lon0, ...]`` each,
    three points or more, every one a place on Earth), or ``None``."""
    if not (isinstance(rings, list) and rings):
        return "no rings"
    for r in rings:
        if not (isinstance(r, list) and len(r) >= 6 and len(r) % 2 == 0):
            return "a ring is three points or more"
        if not all(_number(v) for v in r):
            return "a ring is of numbers"
        if any(abs(v) > 90 for v in r[0::2]) or any(abs(v) > 180 for v in r[1::2]):
            return "a ring is of places on Earth"
    return None


def entry_problem(c: Any) -> str | None:
    """Why ``c`` is not a whole cloud to lay, or ``None``: ``{id, source, match, rings, parts:
    {texture: [level, digest]}, plain: {texture: digest}}``, each of its kind."""
    if not isinstance(c, Mapping):
        return "not an object"
    if not (isinstance(c.get("id"), str) and isinstance(c.get("source"), str)):
        return "no id or source"
    match = c.get("match")
    if not (
        isinstance(match, list | tuple)
        and len(match) == 2
        and all(isinstance(m, list | tuple) and len(m) == 3 for m in match)
        and all(_number(v) for m in match for v in m)
    ):
        return "a match is three gains and three shifts"
    rings = ring_problem(c.get("rings"))
    if rings is not None:
        return rings
    parts, plain = c.get("parts"), c.get("plain")
    if not (isinstance(parts, Mapping) and parts and isinstance(plain, Mapping)):
        return "no parts"
    if set(parts) != set(plain):
        return "parts and textures apart"
    for name, part in parts.items():
        try:
            parse_texture_name(name)
        except (ValueError, TypeError):
            return "a part of no texture"
        if not (
            isinstance(part, list | tuple)
            and len(part) == 2
            and isinstance(part[0], int)
            and isinstance(part[1], str)
        ):
            return "a part is a level and a digest"
    if not all(isinstance(v, str) for v in plain.values()):
        return "a texture is named by its digest"
    return None
