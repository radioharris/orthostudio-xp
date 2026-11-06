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

from orthostudio.graph.digest import digest_bytes, digest_file
from orthostudio.home import osxp_home
from orthostudio.imagery.grid import TextureId, parse_texture_name, texture_name, wgs84_to_tile
from orthostudio.install.library import default_library_path
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import tile_home
from orthostudio.pipeline.home import default_chunks_root, default_tiles_root
from orthostudio.pipeline.pack import MANIFEST_NAME, pack_dir_name, read_manifest
from orthostudio.textures.clouds import CloudPatch, tile_box, window

__all__ = [
    "CLOUDS_DIR",
    "DIGEST_HEX",
    "LAID_KEYS",
    "Stamp",
    "TileClouds",
    "clouds_digest",
    "entry_problem",
    "find_part",
    "got_parts_dir",
    "list_path",
    "own_folder",
    "patches_of",
    "previews_dir",
    "reaching",
    "read_stamp",
    "read_tile",
    "stamped",
    "stands_not",
    "texture_digest",
    "tile_folder",
]

CLOUDS_DIR = "clouds"
PART_SUFFIX = ".part"

# -- where the cloud files are ---------------------------------------------------------------


def got_parts_dir(chunks_root: Path) -> Path:
    """The frozen parts of the clouds whose images were got, beside the image pieces
    (``<data>/clouds/parts``), named by their digest."""
    return Path(chunks_root).parent / CLOUDS_DIR / "parts"


def previews_dir(tile: TileRef) -> Path:
    """The tile's picture and its clouds' previews (``<data>/clouds/<tile>``), made at will."""
    return default_chunks_root().parent / CLOUDS_DIR / tile.name


def list_path(tile: TileRef) -> Path:
    """The user's list of the tile's clouds (``$OSXP_HOME/clouds/<tile>.json``)."""
    return osxp_home() / CLOUDS_DIR / f"{tile.name}.json"


def tile_folder(tile: TileRef, *, workshop: Path, home: Path | None = None) -> Path:
    """The folder a build of ``tile`` writes: where it is filed (``filing.tile_home``), else the
    workshop's."""
    return Path(home) if home is not None else Path(workshop) / pack_dir_name(tile)


def own_folder(tile: TileRef, *, custom_scenery: Path | None) -> Path:
    """The tile's folder its clouds are laid in: the one the app's next build of it writes."""
    home, _gone = tile_home(
        tile,
        atelier=default_tiles_root(),
        custom_scenery=custom_scenery,
        library_path=default_library_path(),
    )
    return tile_folder(tile, workshop=default_tiles_root(), home=home)


def find_part(digest: str, folders: Iterable[Path]) -> Path | None:
    """The file of the part ``digest`` in the first of ``folders`` holding it whole."""
    for folder in folders:
        path = Path(folder) / f"{digest}{PART_SUFFIX}"
        try:
            if digest_bytes(path.read_bytes()) == digest:
                return path
        except OSError:
            continue
    return None


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


def read_stamp(path: Path) -> Stamp | None:
    """The stamp of the DDS at ``path``; ``None`` for a texture as built."""
    with open(path, "rb") as f:
        head = f.read(_END)
    if len(head) < _END or head[:4] != b"DDS " or head[_AT : _AT + 4] != _STAMP:
        return None
    return Stamp(head[_AT + 4 : _AT + 24].hex(), head[_AT + 24 : _END].hex())


def stamped(header: bytes, stamp: Stamp) -> bytes:
    """The DDS ``header`` (its 128 bytes) with ``stamp`` in it."""
    mark = _STAMP + bytes.fromhex(stamp.plain) + bytes.fromhex(stamp.clouds)
    return header[:_AT] + mark + header[_END:]


_DIGESTS: dict[tuple[str, int, int, int], str] = {}
_DIGESTS_LOCK = threading.Lock()


def texture_digest(path: Path) -> str:
    """The digest of the texture at ``path``, read again only when the file changed."""
    st = os.stat(path)
    key = (str(path), st.st_ino, st.st_size, st.st_mtime_ns)
    with _DIGESTS_LOCK:
        hit = _DIGESTS.get(key)
    if hit is None:
        hit = digest_file(path)[:DIGEST_HEX]
        with _DIGESTS_LOCK:
            if len(_DIGESTS) > 512:
                _DIGESTS.clear()
            _DIGESTS[key] = hit
    return hit


LAID_KEYS = ("id", "source", "match", "rings", "parts", "plain")
"""What of a cloud's entry makes what it lays; the rest is the list's (its kind, its number)."""


def clouds_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    """The digest of the clouds a texture shows, in their order."""
    laid = [{k: e[k] for k in LAID_KEYS} for e in entries]
    return digest_bytes(json.dumps(laid, sort_keys=True).encode())[:DIGEST_HEX]


# -- the folder ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class TileClouds:
    """What the tile's folder shows of its clouds."""

    folder: Path
    shown: str
    """What the build placed there, one digest (its manifest's): the picture and the previews
    are named by it, so another build makes them again."""
    names: tuple[str, ...]
    """Its textures' names."""
    stamps: Mapping[str, Stamp]
    """The textures showing clouds, and what each is made of."""
    applied: tuple[dict[str, Any], ...] = ()
    """The clouds it shows replaced, in laying order: of the clouds last applied, those every
    texture they reach shows."""

    def held(self, cloud: str) -> dict[str, Any] | None:
        return next((c for c in self.applied if c["id"] == cloud), None)

    @property
    def textures(self) -> list[TextureId]:
        return [parse_texture_name(n) for n in self.names]

    @property
    def textures_dir(self) -> Path:
        """The textures X-Plane shows."""
        return self.folder / "textures"

    @property
    def plains_dir(self) -> Path:
        """The textures showing clouds as the build made them."""
        return self.folder / CLOUDS_DIR

    def plain_path(self, name: str) -> Path:
        """The texture ``name`` as the build made it: the one kept aside when it shows clouds."""
        if name in self.stamps:
            return self.plains_dir / f"{name}.dds"
        return self.textures_dir / f"{name}.dds"

    def plain(self, name: str) -> str | None:
        """The digest of the texture ``name`` as the build made it; ``None`` when it has none."""
        stamp = self.stamps.get(name)
        if stamp is not None:
            return stamp.plain
        if name not in self.names:
            return None
        return texture_digest(self.textures_dir / f"{name}.dds")

    def plains(self) -> dict[str, Path]:
        """Every texture as the build made it, by name: what X-Plane shows with no cloud."""
        return {n: self.plain_path(n) for n in self.names}


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
    """What ``folder`` shows of the clouds ``laid`` (the list's last applied); ``None`` when it
    holds no tile of ours."""
    folder = Path(folder)
    try:
        manifest = (folder / MANIFEST_NAME).read_bytes()
        read_manifest(folder)
    except (OSError, ValueError, KeyError):
        return None
    names = _names(folder / "textures")
    stamps = {}
    for n in names:
        try:
            stamp = read_stamp(folder / "textures" / f"{n}.dds")
        except OSError:
            continue
        if stamp is not None:
            stamps[n] = stamp
    # a texture shows the clouds of the list's that reach it when its stamp names those
    agree = {n for n, s in stamps.items() if s.clouds == clouds_digest(reaching(laid, n))}
    applied = tuple(dict(e) for e in laid if set(e["parts"]) <= agree)
    return TileClouds(folder, digest_bytes(manifest)[:32], names, stamps, applied)


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
        rings = tuple(_ring_px(r, t) for r in c["rings"])
        box = tile_box(tile.lat, tile.lon, t)
        p = CloudPatch(c["id"], c["source"], source_zl(t), rings, match, box)  # type: ignore[arg-type]
        if window(p) is not None:
            out[texture_name(t)] = p
    return out


def _ring_px(ring: Sequence[float], t: TextureId) -> tuple[float, ...]:
    """A ring of the zones' format (``[lat0, lon0, ...]``) in the pixels of ``t``."""
    pts: list[float] = []
    for i in range(0, len(ring) - 1, 2):
        x, y = wgs84_to_tile(float(ring[i]), float(ring[i + 1]), t.zl)
        pts += [(x - t.til_x) * 256.0, (y - t.til_y) * 256.0]
    return tuple(pts)


def stands_not(c: Mapping[str, Any], tile: TileRef, held: TileClouds) -> str | None:
    """Why the images got for the cloud ``c`` no longer stand on the tile, or ``None``: the
    textures it reaches are others (``level``, ``source``, ``textures``), or one of them is not
    the one they were got on (``images``). The one rule Apply, a build after and the page go by."""
    parts = c["parts"]
    zl_of = {name: int(parts[name][0]) for name in parts}
    reached = patches_of(c, tile, held.textures, lambda t: zl_of.get(texture_name(t), t.zl))
    why = _moved(set(parts), set(reached))
    if why is None and any(c["plain"].get(n) != held.plain(n) for n in parts):
        why = "images"
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
