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

The clouds last applied are the list's (``cloudlist.CloudList.laid``); the tile shows those of them
whose every texture shows them (:func:`read_tile`). A cloud's images stand on the tile as long as
every texture it reaches is the one they were got on, as built (:func:`stands_not`).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.graph.digest import digest_bytes, digest_file
from orthostudio.home import default_chunks_root, default_tiles_root, osxp_home
from orthostudio.imagery.grid import TextureId, parse_texture_name, texture_name
from orthostudio.install.library import default_library_path
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import tile_home
from orthostudio.pipeline.pack import MANIFEST_NAME, PackManifest, pack_dir_name, pack_is_intact
from orthostudio.textures.assemble import CHUNK
from orthostudio.textures.clouds import CloudPatch, ring_pixels, tile_box, window

__all__ = [
    "CLOUDS_DIR",
    "DIGEST_HEX",
    "LAID_KEYS",
    "Stamp",
    "TileClouds",
    "clouds_digest",
    "entry_patches",
    "entry_problem",
    "file_digest",
    "find_part",
    "held_by_id",
    "identity",
    "is_own_folder",
    "laid_fields",
    "list_path",
    "lists_dir",
    "own_folder",
    "part_path",
    "parts_dir",
    "patches_of",
    "previews_dir",
    "reaching",
    "read_head",
    "read_stamp",
    "read_tile",
    "stamped",
    "stands_not",
    "texture_digest",
]

CLOUDS_DIR = "clouds"

# -- where the cloud files are ---------------------------------------------------------------


def lists_dir() -> Path:
    """The users' lists of clouds, one per tile (``$OSXP_HOME/clouds``)."""
    return osxp_home() / CLOUDS_DIR


def list_path(tile: TileRef) -> Path:
    """The user's list of the tile's clouds (``$OSXP_HOME/clouds/<tile>.json``)."""
    return lists_dir() / f"{tile.name}.json"


def parts_dir() -> Path:
    """The frozen parts of the clouds whose images were got, beside the image pieces
    (``<data>/clouds/parts``)."""
    return default_chunks_root().parent / CLOUDS_DIR / "parts"


def part_path(digest: str) -> Path:
    """The file of the part ``digest``."""
    return parts_dir() / f"{digest}.part"


def find_part(digest: str) -> Path | None:
    """The file of the part ``digest`` when it is there whole."""
    path = part_path(digest)
    try:
        return path if file_digest(path) == digest else None
    except OSError:
        return None


def previews_dir(tile: TileRef) -> Path:
    """The tile's picture and its clouds' previews (``<data>/clouds/<tile>``), made at will."""
    return default_chunks_root().parent / CLOUDS_DIR / tile.name


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


def identity(path: Path) -> Identity:
    st = os.stat(path)
    return st.st_ino, st.st_size, st.st_mtime_ns


def read_head(path: Path) -> tuple[Stamp | None, Identity]:
    """The stamp of the DDS at ``path`` (``None`` for a texture as built) and the identity of the
    very file it was read from."""
    with open(path, "rb") as f:
        st = os.fstat(f.fileno())
        head = f.read(_END)
    known = (st.st_ino, st.st_size, st.st_mtime_ns)
    if len(head) < _END or head[:4] != b"DDS " or head[_AT : _AT + 4] != _STAMP:
        return None, known
    return Stamp(head[_AT + 4 : _AT + 24].hex(), head[_AT + 24 : _END].hex()), known


def read_stamp(path: Path) -> Stamp | None:
    """The stamp of the DDS at ``path``; ``None`` for a texture as built."""
    return read_head(path)[0]


def stamped(header: bytes, stamp: Stamp) -> bytes:
    """The DDS ``header`` (its 128 bytes) with ``stamp`` in it."""
    mark = _STAMP + bytes.fromhex(stamp.plain) + bytes.fromhex(stamp.clouds)
    return header[:_AT] + mark + header[_END:]


_DIGESTS: dict[tuple[str, int, int, int], str] = {}
_DIGESTS_LOCK = threading.Lock()


def file_digest(path: Path) -> str:
    """The digest of the file at ``path``, read again only when the file changed."""
    key = (str(path), *identity(path))
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


def clouds_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    """The digest of the clouds a texture shows, in their order."""
    laid = [laid_fields(e) for e in entries]
    return digest_bytes(json.dumps(laid, sort_keys=True).encode())[:DIGEST_HEX]


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
    """The clouds it shows replaced, in laying order: of the clouds last laid, those every
    texture they reach shows."""

    def holding(self, cloud: str) -> dict[str, Any] | None:
        """The cloud ``cloud`` as the tile shows it replaced; ``None`` when it does not."""
        return next((c for c in self.held if c["id"] == cloud), None)

    @property
    def textures(self) -> list[TextureId]:
        return [parse_texture_name(n) for n in self.names]

    def texture_path(self, name: str) -> Path:
        """The texture ``name`` X-Plane shows."""
        return self.folder / "textures" / f"{name}.dds"

    def kept_path(self, name: str) -> Path:
        """Where the texture ``name`` is kept as the build made it while it shows clouds."""
        return self.folder / CLOUDS_DIR / f"{name}.dds"

    def plain_path(self, name: str) -> Path:
        """The texture ``name`` as the build made it: the one kept aside when it shows clouds."""
        return self.kept_path(name) if name in self.stamps else self.texture_path(name)

    def plain(self, name: str) -> str | None:
        """The digest of the texture ``name`` as the build made it; ``None`` when it has none."""
        stamp = self.stamps.get(name)
        if stamp is not None:
            return stamp.plain
        return texture_digest(self.texture_path(name)) if name in self.names else None

    def plains(self) -> dict[str, Path]:
        """Every texture as the build made it, by name: what X-Plane shows with no cloud."""
        return {n: self.plain_path(n) for n in self.names}


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


def read_tile(folder: Path, laid: Sequence[Mapping[str, Any]] = ()) -> TileClouds | None:
    """What ``folder`` shows of the clouds ``laid`` (the list's last laid); ``None`` when it
    holds no tile of ours."""
    folder = Path(folder)
    try:
        text = (folder / MANIFEST_NAME).read_bytes()
        manifest = PackManifest.from_toml(text.decode("utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    names = _names(folder / "textures")
    stamps, files = {}, {}
    for n in names:
        path = folder / "textures" / f"{n}.dds"
        try:
            stamp, files[n] = read_head(path)
        except OSError as exc:  # never taken for a texture as built: it may show clouds
            raise OsxpError(
                "CLOUD_TEXTURE_UNREADABLE",
                context={"tile": manifest.tile, "path": str(path), "reason": str(exc)},
            ) from exc
        if stamp is not None:
            stamps[n] = stamp
    # a texture shows the clouds of the list's that reach it when its stamp names those
    agree = {n for n, s in stamps.items() if s.clouds == clouds_digest(reaching(laid, n))}
    return TileClouds(
        folder=folder,
        built=digest_bytes(text)[:32],
        provider=manifest.provider,
        whole=pack_is_intact(folder, manifest, with_overlay=False),
        names=names,
        stamps=stamps,
        files=files,
        held=tuple(dict(e) for e in laid if set(e["parts"]) <= agree),
    )


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
    for t in textures:
        rings = tuple(
            tuple(ring_pixels(r, t.zl, t.til_x * CHUNK, t.til_y * CHUNK).ravel())
            for r in c["rings"]
        )
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
    textures it reaches are others (``level``, ``source``, ``textures``), one of them is not the
    one they were got on (``images``), or its frozen parts are not on this computer (``parts``).
    The one rule Apply, a build after and the page go by."""
    parts = c["parts"]
    why = _moved(set(parts), set(entry_patches(c, tile, shown.textures)))
    if why is None and any(c["plain"].get(n) != shown.plain(n) for n in parts):
        why = "images"
    if why is None and any(find_part(str(parts[n][1])) is None for n in parts):
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


def entry_problem(c: Any) -> str | None:
    """Why ``c`` is not a whole cloud to lay, or ``None``: ``{id, source, match, rings, parts:
    {texture: [level, digest]}, plain: {texture: digest}}``."""
    if not isinstance(c, Mapping):
        return "not an object"
    if not (isinstance(c.get("id"), str) and isinstance(c.get("source"), str)):
        return "no id or source"
    try:
        gain, shift = c["match"]
        if len(gain) != 3 or len(shift) != 3:
            return "a match is three gains and three shifts"
    except (KeyError, TypeError, ValueError):
        return "no match"
    rings = c.get("rings")
    if not (isinstance(rings, list) and rings and all(isinstance(r, list) for r in rings)):
        return "no rings"
    parts, plain = c.get("parts"), c.get("plain")
    if not (isinstance(parts, Mapping) and parts and isinstance(plain, Mapping)):
        return "no parts"
    if set(parts) != set(plain):
        return "parts and textures apart"
    return None
