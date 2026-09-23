"""The scenery pack of a tile and its installation: rules ``tile.pack`` and ``tile.install``.

Spec: ``docs/specs/pipeline-build.md`` sections 2.3 and 3. The pack is an *effect* (files
under the output directory, links under ``Custom Scenery``); the artefacts of these rules are
small receipts keyed on their inputs and destination, so an unchanged build hits, and
:func:`pack_is_intact` / :func:`install_is_intact` let the caller redo the effect after a hit.
:func:`uninstall_receipt` and :func:`delete_receipt` undo them (``docs/specs/install.md`` 4.1
and 4.2): what ``osxp uninstall`` and the page's library call.

Both rule functions read their environment (store, output root, Custom Scenery, library) from
a context variable set by :func:`pack_env`; the scheduler's ``run`` wrapper in
``orthostudio.pipeline.build`` sets it in the worker thread.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import threading
import tomllib
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_link_or_copy, atomic_write_text
from orthostudio.graph import ResolvedInput, Rule, RuleParams, RunContext, Store, rule
from orthostudio.home import default_store_root, default_tiles_root
from orthostudio.install import (
    Library,
    LibraryEntry,
    default_library_path,
    install_pack,
    is_link,
    uninstall_pack,
    xplane_running,
)
from orthostudio.install import packs as install_packs
from orthostudio.install.library import pack_tile
from orthostudio.install.packs import SCENERY_PACKS_INI
from orthostudio.install.scenery_packs import (
    IMPORTED_PACK_PREFIX,
    ORTHO_PREFIXES,
    OVERLAY_LINK_NAME,
    SceneryPackEntry,
    SceneryPacks,
    pack_kind,
)
from orthostudio.model import OVERLAY_PACK, PACK_PREFIX, TileRef, pack_dir_name

__all__ = [
    "LEFT_OVERLAY",
    "OVERLAY_PACK",
    "PACK_FORMAT",
    "TILE_INSTALL",
    "TILE_PACK",
    "TILE_SETTINGS_NAME",
    "ArtefactEntry",
    "InstallParams",
    "OverlayState",
    "PackEnv",
    "PackManifest",
    "PackParams",
    "assemble_pack",
    "delete_receipt",
    "install_is_intact",
    "install_receipt",
    "is_installed",
    "leave_overlay",
    "library_pack",
    "library_packs",
    "links_to",
    "overlay_link",
    "overlay_states",
    "pack_dir_name",
    "pack_env",
    "pack_is_intact",
    "pack_to_delete",
    "read_manifest",
    "take_back_overlay",
    "uninstall_receipt",
    "write_pack",
]

PACK_FORMAT = "osxp-pack-1"
ORTHO4XP_MAIN = "Ortho4XP.py"
"""What an Ortho4XP installation holds at its root, and what its import asks the user to point at.
A pack under one is never deleted, whatever the library says about it."""

ORTHO4XP_LOOK_UP = 4
"""How far above a pack that marker is looked for: ``<Ortho4XP>/Tiles/zOrtho4XP_<tile>`` needs
two, and a custom build dir a little more."""

MANIFEST_NAME = "orthostudio.toml"
CREDITS_NAME = "CREDITS.txt"
RECEIPT_FORMAT = "osxp-install-1"
UNINSTALL_FORMAT = "osxp-uninstall-1"
DELETE_FORMAT = "osxp-delete-1"
PARKED_OVERLAY = "osxp-overlay.dsf.uninstalled"
"""Where :func:`uninstall_receipt` parks the tile's DSF of the shared overlay pack: inside the
tile pack, which X-Plane no longer reads, and where :func:`install_receipt` finds it again."""
LEFT_OVERLAY = "osxp-overlay.dsf.left"
"""The overlay DSF of a tile whose roads, forests and buildings the user left to another pack's
overlays (AutoOrtho's, XPME's, Ortho4XP's): X-Plane does not read it here, and it stays through an
uninstall, an install and a rebuild until the user takes the overlay back (``install.md`` 4.3)."""
_OTHER_OVERLAYS = re.compile(r"(?i)overlays?$")
"""An overlay pack of another tool (``yAutoOrtho_Overlays``, ``XPME_Overlays``,
``yOrtho4XP_Overlays``)."""
TILE_SETTINGS_NAME = "tile_settings.cfg"
"""The tile variables a pack was built with, one ``name=value`` per line."""
WATER_TRANSITION_PNG = "water_transition.png"

_INSTALL_LOCK = threading.Lock()
"""``scenery_packs.ini`` and the library are edited by one install at a time."""


# -- params ------------------------------------------------------------------------------------


class PackParams(RuleParams):
    """What the pack depends on beyond its inputs: where it goes and how files are placed."""

    tile: str
    provider: str
    zl: int
    out_dir: str
    """The output root, which holds ``zOrthoStudio_<tile>/`` and ``yOrthoStudio_Overlays/``."""
    link: bool = True
    """Hard-link the DSF and DDS files from the store (a copy when the file systems differ)."""
    tile_cfg: str = ""
    """Text of ``tile_settings.cfg`` written in the pack (the 44 tile variables the build
    consumed, ``tile_cfg_text``); empty = no such file."""
    photo_brightness: float = 0.0
    photo_contrast: float = 0.0
    photo_saturation: float = 0.0
    """Colours of the square, written into the manifest so that the page can say when the tile in
    X-Plane no longer matches its setting (2026-09-18)."""

    def canonical(self) -> dict[str, Any]:
        doc = super().canonical()
        if not (self.photo_brightness or self.photo_contrast or self.photo_saturation):
            for name in ("photo_brightness", "photo_contrast", "photo_saturation"):
                doc.pop(name, None)  # a plain pack keeps the key it had
        return doc


class InstallParams(RuleParams):
    """Destination of the installation."""

    tile: str
    custom_scenery: str
    link: bool = True
    """Symbolic link (junction on Windows) into Custom Scenery; ``False`` copies the pack."""


# -- manifest ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArtefactEntry:
    key: str
    digest: str
    rule: str
    """``<rule>@<version>``."""


@dataclass(slots=True)
class PackManifest:
    """Content of ``orthostudio.toml`` (spec section 3): deterministic, no timestamp."""

    tile: str
    provider: str
    zl: int
    artefacts: dict[str, ArtefactEntry] = field(default_factory=dict)
    files: dict[str, Any] = field(default_factory=dict)
    photo: dict[str, float] = field(default_factory=dict)
    """Colours of the square this pack was built with (``brightness``, ``contrast``,
    ``saturation``); empty when they were the plain ones. The page compares them with the square's
    setting and says when the tile in X-Plane no longer matches it (a user, 2026-09-18). The zones
    of the tile may carry others: this is the square's answer."""
    built: dict[str, Any] = field(default_factory=dict)
    """What the tile was built with, for the page to say (a user asked where to see it,
    2026-09-21): the OrthoStudio XP ``version``; the ``relief`` read and the overlays
    ``relief_laid`` over it, both from the relief artefact itself, and the overlays
    ``relief_asked``, so that one asked and not laid (Canada's lidar where it never flew) can be
    told apart from one laid; the hand-made ``patches``; and the ``zones`` that reached the tile.
    Empty for a pack written before 0.1.10. Only what the build decided: no date, which would make
    two identical builds different -- the page reads the manifest's own time instead."""

    def to_toml(self) -> str:
        lines = [f'format = "{PACK_FORMAT}"', "", "[tile]"]
        lines += [f'name = "{self.tile}"', f'provider = "{self.provider}"', f"zl = {self.zl}", ""]
        lines.append("[artefacts]")
        for name in sorted(self.artefacts):
            e = self.artefacts[name]
            lines.append(
                f'{name} = {{ key = "{e.key}", digest = "{e.digest}", rule = "{e.rule}" }}'
            )
        lines += ["", "[files]"]
        for name in sorted(self.files):
            lines.append(f"{name} = {_toml_value(self.files[name])}")
        if self.photo:
            lines += ["", "[photo]"]
            for name in sorted(self.photo):
                lines.append(f"{name} = {_toml_value(self.photo[name])}")
        if self.built:
            lines += ["", "[built]"]
            for name in sorted(self.built):
                lines.append(f"{name} = {_toml_value(self.built[name])}")
        return "\n".join(lines) + "\n"

    @classmethod
    def from_toml(cls, text: str) -> PackManifest:
        doc = tomllib.loads(text)
        if doc.get("format") != PACK_FORMAT:
            raise ValueError(f"not an {PACK_FORMAT} manifest (format {doc.get('format')!r})")
        tile = doc["tile"]
        artefacts = {
            name: ArtefactEntry(str(e["key"]), str(e["digest"]), str(e.get("rule", "")))
            for name, e in dict(doc.get("artefacts", {})).items()
        }
        return cls(
            tile=str(tile["name"]),
            provider=str(tile["provider"]),
            zl=int(tile["zl"]),
            artefacts=artefacts,
            files=dict(doc.get("files", {})),
            photo={k: float(v) for k, v in dict(doc.get("photo", {})).items()},
            built=dict(doc.get("built", {})),
        )

    @property
    def keys(self) -> dict[str, str]:
        """``{artefact name: key}`` (what the library records)."""
        return {name: e.key for name, e in self.artefacts.items()}


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # a JSON string is a valid TOML basic string
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):  # an inline table, keys in order so that it never changes
        return "{ " + ", ".join(f"{k} = {_toml_value(value[k])}" for k in sorted(value)) + " }"
    raise TypeError(f"unsupported manifest value {value!r}")


def read_manifest(pack_dir: Path) -> PackManifest:
    """The manifest of a pack directory (``<pack>/orthostudio.toml``), or ``FileNotFoundError``."""
    return PackManifest.from_toml((Path(pack_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))


# -- layout ------------------------------------------------------------------------------------


def overlay_dsf_path(out_root: Path, tile: TileRef) -> Path:
    return Path(out_root) / OVERLAY_PACK / tile.dsf_relpath


def _place(src: Path, dest: Path, *, link: bool, backup: bool = False) -> bool:
    """Put ``src`` at ``dest`` (hard link or copy).

    Returns True when the destination changed. An existing file that is the same inode or the
    same bytes is left alone; a different one is replaced, after a ``<name>.bak`` copy when
    ``backup`` is set (the DSF only, as Ortho4XP did: a ``.dds.bak`` per re-encoded texture would
    leave gigabytes behind).
    """
    if dest.exists():
        try:
            if os.path.samefile(src, dest):
                return False
        except OSError:
            pass
        if src.stat().st_size == dest.stat().st_size and _same_bytes(src, dest):
            return False
        if backup:
            os.replace(dest, dest.with_name(dest.name + ".bak"))
    atomic_link_or_copy(src, dest, link=link)
    return True


def _sweep_bak(directory: Path) -> int:
    """Remove ``*.bak`` files left in ``directory`` by earlier versions of the pack writer."""
    n = 0
    for stale in directory.glob("*.bak"):
        stale.unlink()
        n += 1
    return n


def _same_bytes(a: Path, b: Path, chunk: int = 1 << 20) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


@dataclass(slots=True)
class PackFiles:
    """What :func:`write_pack` placed."""

    pack_dir: Path
    dsf: Path
    dsf_size: int
    textures: int
    terrain: int
    overlay: Path | None
    changed: int
    """Files written or replaced (0 when the pack was already up to date)."""
    removed: int
    """Stale ``.dds`` / ``.ter`` / ``.bak`` files removed from the pack."""
    cfg: Path | None = None
    """``tile_settings.cfg`` when a tile cfg text was given."""


def write_pack(
    out_root: Path,
    tile: TileRef,
    *,
    dsf_dir: Path,
    textures_dir: Path | None,
    overlay_file: Path | None,
    link: bool = True,
    tile_cfg: str | None = None,
) -> PackFiles:
    """Assemble ``<out_root>/zOrthoStudio_<tile>/`` from the DSF and textures artefacts.

    ``dsf_dir`` holds ``<tile>.dsf`` and ``terrain/``; ``textures_dir`` holds ``textures/``
    (hard-linked DDS) and possibly ``terrain/`` (ignored: the DSF artefact's is authoritative).
    Files the new DSF no longer references are removed from ``textures/`` and ``terrain/``;
    an existing DSF with different bytes becomes ``.dsf.bak`` (DDS are replaced without a
    backup). ``tile_cfg`` (the text of the 44 tile variables) is written as
    ``tile_settings.cfg``.
    """
    out_root = Path(out_root)
    pack_dir = out_root / pack_dir_name(tile)
    changed = removed = 0
    dsf_src = Path(dsf_dir) / f"{tile.name}.dsf"
    if not dsf_src.is_file():
        raise OsxpError(
            "MESH_INPUT_MISSING",
            context={"path": str(dsf_src), "reason": "DSF artefact has no DSF file"},
        )
    dsf_dest = pack_dir / tile.dsf_relpath
    dsf_dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        changed += _place(dsf_src, dsf_dest, link=link, backup=True)
    except PermissionError as exc:
        raise _pack_locked(dsf_dest, exc) from exc

    terrain_src = Path(dsf_dir) / "terrain"
    terrain_dest = pack_dir / "terrain"
    terrain_dest.mkdir(parents=True, exist_ok=True)
    wanted_ter: set[str] = set()
    if terrain_src.is_dir():
        for src in sorted(terrain_src.glob("*.ter")):
            wanted_ter.add(src.name)
            dest = terrain_dest / src.name
            text = src.read_bytes()
            if not dest.is_file() or dest.read_bytes() != text:
                atomic_link_or_copy(src, dest, link=False)
                changed += 1
    for stale in terrain_dest.glob("*.ter"):
        if stale.name not in wanted_ter:
            stale.unlink()
            removed += 1
    removed += _sweep_bak(terrain_dest)

    textures_dest = pack_dir / "textures"
    textures_dest.mkdir(parents=True, exist_ok=True)
    wanted_tex: set[str] = set()
    n_textures = 0
    if textures_dir is not None and (Path(textures_dir) / "textures").is_dir():
        for src in sorted((Path(textures_dir) / "textures").iterdir()):
            if src.suffix.lower() not in (".dds", ".png"):
                continue
            wanted_tex.add(src.name)
            if src.suffix.lower() == ".dds":
                n_textures += 1
            try:
                changed += _place(src, textures_dest / src.name, link=link)
            except PermissionError as exc:
                raise _pack_locked(textures_dest / src.name, exc) from exc
    for stale in textures_dest.iterdir():
        if stale.suffix.lower() in (".dds", ".png") and stale.name not in wanted_tex:
            stale.unlink()
            removed += 1
    removed += _sweep_bak(textures_dest)

    cfg_dest: Path | None = None
    if tile_cfg:
        cfg_dest = pack_dir / TILE_SETTINGS_NAME
        if not cfg_dest.is_file() or cfg_dest.read_text(encoding="utf-8") != tile_cfg:
            atomic_write_text(cfg_dest, tile_cfg)
            changed += 1

    overlay_dest: Path | None = None
    if overlay_file is not None and (pack_dir / LEFT_OVERLAY).is_file():
        # Its roads and objects were left to another pack's overlays: the new DSF waits beside
        # the old one's place, not where X-Plane would draw it twice.
        changed += _place(Path(overlay_file), pack_dir / LEFT_OVERLAY, link=link)
        shared = overlay_dsf_path(out_root, tile)
        if shared.is_file() or shared.is_symlink():
            shared.unlink()
            removed += 1
    elif overlay_file is not None:
        overlay_dest = overlay_dsf_path(out_root, tile)
        overlay_dest.parent.mkdir(parents=True, exist_ok=True)
        changed += _place(Path(overlay_file), overlay_dest, link=link, backup=True)
    else:
        # Built without an overlay (its roads and objects come from another pack, simHeaven
        # X-World for instance): the overlay DSF of an earlier build, in the shared pack or
        # parked in this one, would still be drawn or put back.
        for stale in (
            overlay_dsf_path(out_root, tile),
            pack_dir / PARKED_OVERLAY,
            pack_dir / LEFT_OVERLAY,
        ):
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
                removed += 1

    return PackFiles(
        pack_dir=pack_dir,
        dsf=dsf_dest,
        dsf_size=dsf_dest.stat().st_size,
        textures=n_textures,
        terrain=len(wanted_ter),
        overlay=overlay_dest,
        changed=changed,
        removed=removed,
        cfg=cfg_dest,
    )


def _pack_locked(path: Path, exc: OSError) -> OsxpError:
    """A file of an installed pack that the OS refuses to replace (Windows: X-Plane holds it)."""
    return OsxpError(
        "XP_PACK_CONFLICT",
        context={"tile": path.name, "pack": str(path), "reason": f"{type(exc).__name__}: {exc}"},
        message=f"{path} is in use and cannot be replaced ({exc}).",
        remedy="Quit X-Plane (or the tool holding the file), then run osxp build again.",
    )


def _count(directory: Path, pattern: str) -> int:
    return sum(1 for _ in directory.glob(pattern)) if directory.is_dir() else 0


def pack_is_intact(pack_dir: Path, manifest: PackManifest) -> bool:
    """True when the pack directory still holds what the manifest lists (sizes and counts)."""
    pack_dir = Path(pack_dir)
    files = manifest.files
    dsf = pack_dir / str(files.get("dsf", ""))
    if not dsf.is_file() or dsf.stat().st_size != int(files.get("dsf_size", -1)):
        return False
    n_dds = _count(pack_dir / "textures", "*.dds")
    n_ter = _count(pack_dir / "terrain", "*.ter")
    if n_dds != int(files.get("textures", 0)) or n_ter != int(files.get("terrain", 0)):
        return False
    overlay = files.get("overlay")
    if (
        overlay
        and not (pack_dir / str(overlay)).is_file()
        and not (pack_dir / LEFT_OVERLAY).is_file()
    ):
        return False
    cfg = files.get("cfg")
    if cfg and not (pack_dir / str(cfg)).is_file():
        return False
    return (pack_dir / MANIFEST_NAME).is_file()


# -- provenance -> manifest ------------------------------------------------------------------


def _entry(store: Store, key: str) -> ArtefactEntry | None:
    info = store.info(key)
    if info is None:
        return None
    return ArtefactEntry(info.key, info.digest, f"{info.rule}@{info.version}")


def _upstream(store: Store, key: str, names: dict[str, str], out: dict[str, ArtefactEntry]) -> None:
    """Walk the provenance edges of ``key`` and record the artefacts named in ``names``."""
    with contextlib.suppress(Exception):
        prov = store.why(key)
        for ref in prov.inputs:
            if ref.key is None or ref.name not in names:
                continue
            label = names[ref.name]
            if label in out:
                continue
            entry = _entry(store, ref.key)
            if entry is not None:
                out[label] = entry
                _upstream(store, ref.key, names, out)


def relief_read(store: Store, key: str | None) -> dict[str, Any]:
    """The relief a DSF was built on, from the relief artefact among its ancestors.

    ``{"relief": base source, "relief_laid": overlays really laid}``, as the relief stage wrote
    them in its ``meta.json``; empty when there is none to read (a store cleaned since, or a test
    without one). An overlay asked for that had nothing on the square is not in ``relief_laid``:
    that is how a lidar relief chosen where the lidar never flew shows for what it is.
    """
    if key is None:
        return {}
    seen: set[str] = set()
    todo = [key]
    with contextlib.suppress(Exception):
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for ref in store.why(current).inputs:
                if ref.key is None:
                    continue
                if ref.name == "dem":
                    meta = json.loads((store.path(ref.key) / "meta.json").read_text("utf-8"))
                    return {
                        "relief": str(meta.get("source", "")),
                        "relief_laid": [str(x) for x in meta.get("laid_over", [])],
                    }
                todo.append(ref.key)
    return {}


_UPSTREAM_NAMES = {"vectors": "vectors", "mesh": "mesh", "masks": "masks", "rasters": "xp12"}


def assemble_pack(
    store: Store,
    out_root: Path,
    tile: TileRef,
    *,
    provider: str,
    zl: int,
    dsf: ResolvedInput,
    textures: ResolvedInput,
    overlay: ResolvedInput,
    link: bool = True,
    tile_cfg: str = "",
    photo: dict[str, float] | None = None,
    built: dict[str, Any] | None = None,
) -> tuple[PackManifest, PackFiles]:
    """Write the pack from the three inputs and build its manifest (upstream keys from the
    store's provenance edges of the DSF artefact)."""
    assert dsf.path is not None and dsf.key is not None
    files = write_pack(
        out_root,
        tile,
        dsf_dir=dsf.path,
        textures_dir=textures.path,
        overlay_file=overlay.path,
        link=link,
        tile_cfg=tile_cfg or None,
    )
    artefacts: dict[str, ArtefactEntry] = {}
    for label, inp in (("dsf", dsf), ("textures", textures), ("overlay", overlay)):
        if inp.key is not None:
            entry = _entry(store, inp.key)
            if entry is not None:
                artefacts[label] = entry
    _upstream(store, dsf.key, _UPSTREAM_NAMES, artefacts)
    # What the build decided, and the relief the DSF really stands on (``relief_read``). A pack
    # written again after a repair is given the facts of the one it replaces, so it does not change.
    facts = {**(built or {})}
    if built is not None and "relief" not in facts:
        facts.update(relief_read(store, dsf.key))
    manifest = PackManifest(
        tile=tile.name,
        provider=provider,
        zl=zl,
        artefacts=artefacts,
        photo={k: float(v) for k, v in (photo or {}).items() if v},
        built=facts,
        files={
            "dsf": tile.dsf_relpath.as_posix(),
            "dsf_size": files.dsf_size,
            "textures": files.textures,
            "terrain": files.terrain,
            "overlay": (
                Path("..", OVERLAY_PACK, tile.dsf_relpath).as_posix() if files.overlay else ""
            ),
            "cfg": TILE_SETTINGS_NAME if files.cfg is not None else "",
        },
    )
    atomic_write_text(files.pack_dir / MANIFEST_NAME, manifest.to_toml())
    _write_credits(files.pack_dir, tile, facts)
    return manifest, files


def _write_credits(pack_dir: Path, tile: TileRef, facts: Mapping[str, Any]) -> None:
    """The imagery's credit, in plain words, beside the tile it was used for.

    A pack is a folder people pass around, and the credit lived only in the page that built it.
    EOX's Sentinel-2 is CC BY-NC-SA: the attribution has to travel with the work (found in
    review, 2026-09-23). Deterministic, like the manifest: a pack written again is the same pack.
    Nothing is written for a source that gives no credit.
    """
    credit = str(facts.get("imagery_credit") or "")
    if not credit:
        return
    lines = [f"{tile.name}, built with OrthoStudio XP.", "", "Aerial imagery:", credit]
    licence = str(facts.get("imagery_licence") or "")
    if licence:
        lines += ["", f"Licence: {licence}."]
    lines += [
        "",
        "Roads, water and land use come from OpenStreetMap, (c) OpenStreetMap contributors,",
        "available under the Open Database Licence (ODbL).",
    ]
    atomic_write_text(pack_dir / CREDITS_NAME, "\n".join(lines) + "\n")


# -- install -------------------------------------------------------------------------------------


def install_receipt(
    pack_dir: Path,
    custom_scenery: Path,
    *,
    tile: TileRef,
    link: bool = True,
    library_path: Path | None = None,
) -> dict[str, Any]:
    """Install the tile pack (and the overlays pack next to it), register the library row.

    Serialised process-wide (``scenery_packs.ini``). Returns the receipt written as the
    ``tile.install`` artefact. Raises ``XP_RUNNING``, ``XP_PACK_CONFLICT``...

    The overlays pack goes in under its link (:func:`overlay_link`): ``yOrthoStudio_Overlays``, or
    ``yOrthoStudio_Overlays_2``... when X-Plane already shows the overlays of another tiles folder,
    so that the tiles of both keep their roads, forests and buildings. A build of the same tile
    X-Plane shows from another folder is taken out first (:func:`_take_out_other_build`). A line of
    the overlays pack the user disabled stays disabled. A tile built without an overlay takes the
    overlays pack out of X-Plane when no tile's overlay is left in it (its link, when it leads to
    that pack, and its line), and forgets its own overlay row.
    """
    pack_dir = Path(pack_dir)
    custom_scenery = Path(custom_scenery)
    manifest = read_manifest(pack_dir)
    with _INSTALL_LOCK:
        _take_out_other_build(pack_dir, custom_scenery, tile)
        target = install_pack(pack_dir, custom_scenery, link=link, update_ini=False)
        overlay_target: Path | None = None
        overlay_pack = pack_dir.parent / OVERLAY_PACK
        has_overlay = bool(manifest.files.get("overlay"))
        overlay_removed: str | None = None
        if has_overlay:
            _unpark_overlay(pack_dir, tile)
            if (overlay_pack / "Earth nav data").is_dir():
                # a copy keeps the pack's own name, as it always did
                where = overlay_link(custom_scenery, overlay_pack) or _new_overlay_link(
                    custom_scenery
                )
                overlay_target = install_pack(
                    overlay_pack,
                    custom_scenery,
                    link=link,
                    update_ini=False,
                    name=where.name if link else None,
                )
        else:
            shared = overlay_link(custom_scenery, overlay_pack)
            empty = not any((overlay_pack / "Earth nav data").glob("*/*.dsf"))
            if (
                shared is not None
                and empty
                and uninstall_pack(shared.name, custom_scenery, update_ini=False)
            ):
                overlay_removed = shared.name
        # One load / save round for both packs: scenery_packs.ini.bak is the state before
        # OrthoStudio XP touched the file, not the half-updated list between the two installs.
        ini = custom_scenery / SCENERY_PACKS_INI
        packs = SceneryPacks.load(ini)
        # Above an imported tile of the same square, if one is listed: this one is drawn.
        changed = packs.ensure(
            target.name, kind=pack_kind(target.name), above=IMPORTED_PACK_PREFIX + tile.name
        )
        if overlay_target is not None:
            name = overlay_target.name
            changed = packs.ensure(name, kind=pack_kind(name), reenable=False) or changed
        if overlay_removed is not None:
            changed = packs.remove(overlay_removed) or changed
        if changed:
            packs.save(ini, backup=True)
        with Library(library_path) as lib:
            # Installing says nothing about who built the pack: a row that says Ortho4XP keeps
            # saying it, or Delete would stop refusing a tile it did not build.
            lib.register(
                tile,
                manifest.provider,
                manifest.zl,
                pack_dir,
                "osxp",
                manifest.keys,
                keep_built_by=True,
            )
            if overlay_target is not None:
                lib.register(
                    tile, "", 0, overlay_pack, "osxp", None, kind="overlay", keep_built_by=True
                )
            elif not has_overlay:
                lib.forget(tile, kind="overlay", path=overlay_pack)
    return {
        "format": RECEIPT_FORMAT,
        "tile": tile.name,
        "pack": str(pack_dir),
        "target": str(target),
        "overlay_target": None if overlay_target is None else str(overlay_target),
        "custom_scenery": str(custom_scenery),
        "link": link,
    }


def _unpark_overlay(pack_dir: Path, tile: TileRef) -> None:
    """Put back the overlay DSF :func:`uninstall_receipt` parked in the pack, if any."""
    parked = pack_dir / PARKED_OVERLAY
    if not parked.is_file():
        return
    dest = pack_dir.parent / OVERLAY_PACK / tile.dsf_relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(parked, dest)


def uninstall_receipt(
    name: str,
    custom_scenery: Path,
    *,
    delete_pack: bool = False,
    library_path: Path | None = None,
) -> dict[str, Any]:
    """Take a tile pack (``zOrthoStudio_<tile>``, or an imported one) out of X-Plane, its overlay
    with it.

    Removed: the link (a real folder only when it is an OrthoStudio XP pack, i.e. holds
    ``orthostudio.toml``); the tile's DSF in the shared overlay pack, parked inside the tile pack --
    left there, X-Plane would keep drawing the tile's roads and objects over the default scenery,
    which already has them; the overlay pack's link when no DSF is left in it; and the lines of both
    in ``scenery_packs.ini``, in one save.

    The overlay is OrthoStudio XP's to move only for an OrthoStudio XP pack (``orthostudio.toml``,
    so not a pack that is gone) whose overlay pack beside it has a link in Custom Scenery
    (:func:`overlay_link`), where ``osxp build`` put the DSF and :func:`install_receipt` puts it
    back. Anything else stays where it is: the overlays of an imported tile, which are not in that
    pack (parking them used to destroy them when the pack was deleted, and Install never put them
    back), and an overlay pack that is a real folder (a copy).
    ``delete_pack`` deletes the tile pack directory too, after which ``osxp clean`` can free the
    store artefacts it held.

    Raises ``XP_RUNNING`` and ``XP_PACK_CONFLICT`` like :func:`install_pack`.
    """
    with _INSTALL_LOCK:
        return _uninstall(
            name, Path(custom_scenery), delete_pack=delete_pack, library_path=library_path
        )


def _pack_lives_elsewhere(tile: TileRef | None, target: Path, library_path: Path | None) -> bool:
    """Whether this tile's pack also sits somewhere other than ``target``.

    A real folder inside Custom Scenery is removed when the tile is taken out of X-Plane, because
    ``install --copy`` puts a copy there and the tile's own folder is elsewhere. A pack built
    straight into Custom Scenery is a real folder too, and it is the only one the user has:
    removing it destroyed his tile, silently, with no question asked, under a button whose own
    words promise that its files stay on the computer (found in review, 2026-09-23).

    When no other home can be seen -- because there is none, or because the library cannot be
    read -- the answer is no, and the tile is not touched. We can always refuse; we can never
    give a folder back.
    """
    if tile is None:
        return False
    here = Path(os.path.realpath(target))
    with contextlib.suppress(Exception), Library(library_path or default_library_path()) as lib:
        for row in lib.list(tile=tile):
            other = Path(os.path.realpath(row.path))
            if other != here and (other / MANIFEST_NAME).is_file():
                return True
    return False


def _uninstall(
    name: str,
    custom_scenery: Path,
    *,
    delete_pack: bool = False,
    library_path: Path | None = None,
) -> dict[str, Any]:
    """:func:`uninstall_receipt`, the install lock held."""
    target = custom_scenery / name
    linked = is_link(target)
    pack_dir: Path | None = Path(os.path.realpath(target)) if linked else None
    if not linked and (target / MANIFEST_NAME).is_file():
        pack_dir = target
    tile = pack_tile(name)
    a_copy = pack_dir is target and _pack_lives_elsewhere(tile, target, library_path)
    if pack_dir is target and not a_copy:
        raise OsxpError(
            "XP_PACK_ONLY_COPY",
            context={"tile": "" if tile is None else tile.name, "path": str(target)},
        )
    parked: Path | None = None
    overlay_removed: str | None = None
    removed = uninstall_pack(name, custom_scenery, update_ini=False, remove_copy=a_copy)
    ours = pack_dir is not None and (pack_dir / MANIFEST_NAME).is_file()
    if linked and tile is not None and pack_dir is not None and ours:
        overlay_dir = pack_dir.parent / OVERLAY_PACK
        shared = overlay_link(custom_scenery, overlay_dir)
        if shared is not None:
            dsf = overlay_dir / tile.dsf_relpath
            if dsf.is_file():
                parked = pack_dir / PARKED_OVERLAY
                os.replace(dsf, parked)
            empty = not any((overlay_dir / "Earth nav data").glob("*/*.dsf"))
            if empty and uninstall_pack(shared.name, custom_scenery, update_ini=False):
                overlay_removed = shared.name
    ini = custom_scenery / SCENERY_PACKS_INI
    changed = False
    if ini.is_file():
        packs = SceneryPacks.load(ini)
        changed = packs.remove(name)
        if overlay_removed is not None:
            changed = packs.remove(overlay_removed) or changed
        if changed:
            packs.save(ini, backup=True)
    deleted = a_copy  # the copy in Custom Scenery went; the tile's own folder is elsewhere
    if delete_pack and linked and pack_dir is not None and (pack_dir / MANIFEST_NAME).is_file():
        shutil.rmtree(pack_dir)
        deleted = True
    return {
        "format": UNINSTALL_FORMAT,
        "name": name,
        "removed": bool(removed or changed),
        "pack": None if pack_dir is None else str(pack_dir),
        "overlay_parked": None if parked is None or deleted else str(parked),
        "overlay_pack_removed": overlay_removed is not None,
        "pack_deleted": deleted,
        "custom_scenery": str(custom_scenery),
    }


# -- the tiles of several folders (a data folder chosen in Settings, 2026-09-15) ----------------


def overlay_link(custom_scenery: Path, overlay_pack: Path) -> Path | None:
    """The link of Custom Scenery that leads to ``overlay_pack``, the overlays pack of a tiles
    folder, among OrthoStudio XP's overlays pack names (``yOrthoStudio_Overlays``,
    ``yOrthoStudio_Overlays_2``...); ``None`` when none does.

    Each tiles folder has its overlays pack beside its tiles. When the data folder changes while
    the tiles of the one before stay installed, X-Plane shows the tiles of two folders, and each
    folder's overlays pack gets a link of its own: the tiles of both keep their roads, forests and
    buildings, and nothing has to be deleted first (a user found that asking for it made no sense).
    """
    cs = Path(custom_scenery)
    return next((cs / n for n in _overlay_link_names(cs) if links_to(cs / n, overlay_pack)), None)


def _overlay_link_names(custom_scenery: Path) -> list[str]:
    """OrthoStudio XP's overlays pack names present in Custom Scenery, in their order."""
    found = [n for n in _entry_names(custom_scenery) if OVERLAY_LINK_NAME.fullmatch(n)]
    return sorted(found, key=lambda n: 1 if n == OVERLAY_PACK else int(n.rsplit("_", 1)[1]))


def _new_overlay_link(custom_scenery: Path) -> Path:
    """Where the overlays pack of a tiles folder that has no link yet goes: the first free name of
    ``yOrthoStudio_Overlays``, ``yOrthoStudio_Overlays_2``... A broken link counts as free when no
    tile link leads into the folder it led to (that folder was deleted by hand); while tile links
    do, its disk may only be unplugged, and the link stays for the day it comes back."""
    cs = Path(custom_scenery)
    tiles = [
        os.path.dirname(os.path.realpath(cs / n))
        for n in _entry_names(cs)
        if n.startswith(PACK_PREFIX) and is_link(cs / n)
    ]
    n = 1
    while True:
        target = cs / (OVERLAY_PACK if n == 1 else f"{OVERLAY_PACK}_{n}")
        if not os.path.lexists(target):
            return target
        gone = is_link(target) and not target.exists()
        if gone and os.path.dirname(os.path.realpath(target)) not in tiles:
            return target  # install_pack replaces the broken link
        n += 1


def _entry_names(folder: Path) -> list[str]:
    try:
        return [entry.name for entry in os.scandir(folder)]
    except OSError:
        return []


def _take_out_other_build(pack_dir: Path, custom_scenery: Path, tile: TileRef) -> None:
    """Before ``pack_dir`` is installed, take out of X-Plane the build of the same tile it shows
    from another folder (the data folder chosen before): its link, its line and its overlay,
    parked in that pack as :func:`uninstall_receipt` parks it. That pack stays on the disk and in
    the library, where it can be installed again or deleted. Anything else under the tile's name
    (a folder, a link to what is not an OrthoStudio XP pack of this tile) stays, and
    :func:`install_pack` refuses it."""
    target = custom_scenery / pack_dir.name
    if not is_link(target) or not target.exists() or links_to(target, pack_dir):
        return  # nothing, a broken link (install_pack replaces it), or this pack already
    try:
        other = read_manifest(Path(os.path.realpath(target)))
    except (OSError, ValueError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return
    if other.tile == tile.name:
        _uninstall(pack_dir.name, custom_scenery)


# -- the overlays of other packs (install.md 4.3) ---------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlayState:
    """Whose roads, forests and buildings X-Plane draws on the square of an installed tile."""

    tile: str
    pack: Path
    """The tile's pack, where its link leads."""
    state: str
    """``own``: this tile's alone; ``double``: this tile's and another pack's, so drawn twice;
    ``left``: another pack's alone, as the user chose; ``missing``: left to packs no longer
    active, so none drawn."""
    others: tuple[str, ...]
    """The other active overlay packs that hold the square, in the list's order."""


def _entry_dir(entry: SceneryPackEntry, custom_scenery: Path) -> Path:
    path = Path(entry.path.rstrip("/\\"))
    return path if path.is_absolute() else custom_scenery.parent / path


def overlay_states(custom_scenery: Path) -> dict[str, OverlayState]:
    """For each OrthoStudio XP tile X-Plane shows (its line active, its link leading to a pack that
    holds ``orthostudio.toml``), whose overlays are drawn on its square.

    X-Plane draws the overlays of every active pack that holds a square: with this tile's and
    AutoOrtho's (or XPME's, or those of a tile Ortho4XP built), roads, forests and buildings come
    twice (a user asked, 2026-09-14). Another overlay pack is an active line, other than
    ``yOrthoStudio_Overlays``, whose name ends with ``Overlays`` and whose folder holds the
    square's DSF. Reads only: nothing is changed.
    """
    cs = Path(custom_scenery)
    packs = SceneryPacks.load(cs / SCENERY_PACKS_INI)
    active = [e for e in packs.body if isinstance(e, SceneryPackEntry) and e.enabled]
    names = {e.name for e in active}
    others = [
        (e.name, _entry_dir(e, cs))
        for e in active
        if not OVERLAY_LINK_NAME.fullmatch(e.name) and _OTHER_OVERLAYS.search(e.name)
    ]
    shared_links: dict[Path, Path | None] = {}
    out: dict[str, OverlayState] = {}
    for entry in active:
        tile = pack_tile(entry.name) if entry.name.startswith(PACK_PREFIX) else None
        link = cs / entry.name
        if tile is None or not is_link(link):
            continue
        pack_dir = Path(os.path.realpath(link))
        if not (pack_dir / MANIFEST_NAME).is_file():
            continue
        shared = pack_dir.parent / OVERLAY_PACK
        if shared not in shared_links:
            shared_links[shared] = overlay_link(cs, shared)
        shared_link = shared_links[shared]
        own = (
            shared_link is not None
            and shared_link.name in names
            and (shared / tile.dsf_relpath).is_file()
        )
        left = (pack_dir / LEFT_OVERLAY).is_file()
        holding = tuple(name for name, folder in others if (folder / tile.dsf_relpath).is_file())
        if left:
            state = "left" if holding else "missing"
        elif own and holding:
            state = "double"
        elif own:
            state = "own"
        else:
            continue
        out[tile.name] = OverlayState(tile.name, pack_dir, state, holding)
    return out


def leave_overlay(pack_dir: Path, tile: TileRef) -> bool:
    """Leave the square's roads, forests and buildings to the other packs' overlays: this tile's
    overlay DSF moves into its pack (:data:`LEFT_OVERLAY`), where X-Plane does not read it.
    ``XP_RUNNING`` while X-Plane runs. False when there was no overlay of this tile to move."""
    pack_dir = Path(pack_dir)
    with _INSTALL_LOCK:
        if install_packs.xplane_running():
            raise OsxpError("XP_RUNNING")
        shared = pack_dir.parent / OVERLAY_PACK / tile.dsf_relpath
        if not shared.is_file():
            return False
        os.replace(shared, pack_dir / LEFT_OVERLAY)
        return True


def take_back_overlay(
    pack_dir: Path, custom_scenery: Path, *, tile: TileRef, library_path: Path | None = None
) -> dict[str, Any]:
    """Draw this tile's own overlay again: its DSF goes back into the overlays pack, which is
    then installed as :func:`install_receipt` installs it (its link and its line)."""
    pack_dir = Path(pack_dir)
    with _INSTALL_LOCK:
        if install_packs.xplane_running():
            raise OsxpError("XP_RUNNING")
        left = pack_dir / LEFT_OVERLAY
        if left.is_file():
            dest = pack_dir.parent / OVERLAY_PACK / tile.dsf_relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(left, dest)
    return install_receipt(pack_dir, custom_scenery, tile=tile, library_path=library_path)


# -- delete --------------------------------------------------------------------------------------


def library_packs(
    name: str, *, library_path: Path | None = None
) -> tuple[TileRef, list[LibraryEntry]]:
    """The tile ``name`` designates and its ortho rows in the library, newest first.

    ``name`` is a tile (``+43+005``: every pack of the tile) or a pack directory name
    (``zOrthoStudio_+43+005``, an imported ``zOrtho4XP_+43+005``: the packs of that name).
    ``CFG_LATLON_INVALID`` when it is neither,
    ``SYS_WORKING_DIR_INVALID`` when the library holds no such pack.
    """
    tile = pack_tile(name)
    if tile is None:
        raise OsxpError(
            "CFG_LATLON_INVALID",
            context={"value": name},
            message=f"{name!r} is neither a tile nor a tile pack name.",
            remedy="Use +43+005 or zOrthoStudio_+43+005.",
        )
    with Library(library_path) as lib:
        rows = lib.list(tile=tile, kind="ortho")
    if name.startswith(ORTHO_PREFIXES):
        rows = [r for r in rows if r.path.name == name]
    if not rows:
        raise OsxpError(
            "SYS_WORKING_DIR_INVALID",
            context={"path": name},
            message=f"No pack of {tile.name} in the library.",
            remedy="Build the tile first, or import an Ortho4XP directory.",
        )
    rows.sort(key=lambda r: r.updated_at, reverse=True)
    return tile, rows


def library_pack(
    name: str, *, path: str | Path | None = None, library_path: Path | None = None
) -> LibraryEntry:
    """The library row a request on ``name`` means (``name`` as in :func:`library_packs`).

    With ``path``, the ortho row of exactly that directory, the row the user clicked: a tile may be
    in the library twice, built and imported, and two builds may sit in two output folders
    (``SYS_WORKING_DIR_INVALID`` when no pack of that name is at ``path``). Without it, the newest
    row, as the command line and the first page had it.
    """
    tile, rows = library_packs(name, library_path=library_path)
    if path is None:
        return rows[0]
    for row in rows:
        if row.path == Path(path):
            return row
    raise OsxpError(
        "SYS_WORKING_DIR_INVALID",
        context={"path": str(path)},
        message=f"The library has no pack of {tile.name} at {path}.",
        remedy="Reload the library: the tile may have been deleted or moved meanwhile.",
    )


def pack_to_delete(
    name: str, *, path: str | Path | None = None, library_path: Path | None = None
) -> LibraryEntry:
    """The library row a delete of ``name`` means: the row at ``path`` (:func:`library_pack`);
    without it, the newest osxp build, else the newest row, which :func:`delete_receipt` refuses.
    """
    if path is not None:
        return library_pack(name, path=path, library_path=library_path)
    _tile, rows = library_packs(name, library_path=library_path)
    return min(rows, key=lambda r: r.built_by != "osxp")  # min keeps the first: the newest


def delete_receipt(
    pack_dir: Path,
    *,
    tile: TileRef,
    custom_scenery: Path | None = None,
    library_path: Path | None = None,
    store_root: Path | None = None,
    tiles_root: Path | None = None,
    grace_s: float | None = None,
) -> dict[str, Any]:
    """Delete a tile OrthoStudio XP built, for good: the page's Delete and
    ``osxp uninstall --delete``.

    In this order, and nothing at all when one of the first two steps refuses:

    1. ``SYS_PACK_NOT_OSXP`` for what OrthoStudio XP did not build: a tile the library records as
       imported from Ortho4XP, or a ``pack_dir`` that is a link, a file, a folder without
       ``orthostudio.toml`` (it may hold the user's own files) or the pack of another tile. The
       folder of an OrthoStudio XP pack already gone is no refusal: its library rows still have
       to go;
    2. ``XP_RUNNING`` whenever ``custom_scenery`` is given and X-Plane runs, installed or not:
       the tile's overlay DSF may sit in an overlay pack X-Plane reads;
    3. when the pack is installed in ``custom_scenery`` (:func:`_installed_as`),
       :func:`uninstall_receipt` takes it out with its ``scenery_packs.ini`` lines, and its
       overlay when that is OrthoStudio XP's. Without ``custom_scenery`` this step is skipped;
    4. the tile's DSF in the OrthoStudio XP overlay pack beside the pack goes (left there, X-Plane
       would draw the deleted tile's roads and objects as soon as another tile links that pack),
       then the pack directory, ``orthostudio.toml`` last so that a deletion stopped halfway (a file
       held open: ``SYS_WRITE_FAILED``) can be asked again;
    5. the library forgets the pack's row, the row of the overlay pack beside it, and every
       overlay row of the tile once no ortho row of it is left;
    6. the store gives back the pack's own cache: what its ``orthostudio.toml`` (or, the pack being
       gone, its library row) names and what that was built from, less what a remaining pack needs,
       what is pinned and what was used in the last ``grace_s``
       (``orthostudio.clean.clean_after_delete``, ``DELETE_GRACE_S`` by default). Superseded builds
       stay for ``osxp clean``.

    ``freed_bytes`` is what the disk got back: the files of the pack (and of a copy in Custom
    Scenery) that nothing else links to, plus the store's share. The tile is gone once step 5 is
    done: a store clean that fails then is no error but a ``warning``, a plain sentence (``None``
    otherwise), and ``freed_bytes`` counts what was freed before it. The roots default to the
    OrthoStudio XP home's, the ones ``osxp clean`` uses.
    """
    # imported here: orthostudio.clean imports this module
    from orthostudio.clean import DELETE_GRACE_S, clean_after_delete, freed_bytes

    pack_dir = Path(pack_dir)
    library_path = Path(library_path) if library_path is not None else default_library_path()
    with Library(library_path) as lib:
        rows = lib.list(tile=tile)
    row = next((r for r in rows if r.kind == "ortho" and r.path == pack_dir), None)
    manifest = _manifest_to_delete(pack_dir, tile, row)
    cs = Path(custom_scenery) if custom_scenery is not None else None
    if cs is not None and install_packs.xplane_running():
        raise OsxpError(
            "XP_RUNNING",
            message="X-Plane is running and may be reading this tile's files, so nothing was "
            "deleted.",
            remedy="Quit X-Plane, then delete the tile again.",
        )
    present = manifest is not None
    # What the store clean at the end frees: read now, the manifest goes with the pack; a pack
    # already gone left the same keys in its library row.
    if manifest is not None:
        roots = list(manifest.keys.values())
    else:
        roots = list((row.keys or {}).values()) if row is not None else []
    overlay_dsf = _overlay_dsf_to_delete(pack_dir, tile, manifest, rows)
    freed = 0

    removed = False
    if cs is not None and _installed_as(cs / pack_dir.name, pack_dir, present=present, rows=rows):
        if not is_link(cs / pack_dir.name):  # a copy, or the pack itself: its files leave the disk
            freed += freed_bytes([cs / pack_dir.name])
        removed = bool(uninstall_receipt(pack_dir.name, cs)["removed"])
    with _INSTALL_LOCK:
        if overlay_dsf is not None:  # unless the uninstall parked it in the pack
            freed += freed_bytes([overlay_dsf])
            _delete(overlay_dsf, os.unlink)
        if present and pack_dir.is_dir():  # built in Custom Scenery, the uninstall deleted it
            freed += freed_bytes([pack_dir])
            _delete(pack_dir, _delete_pack_dir)
        with Library(library_path) as lib:
            lib.forget(tile, kind="ortho", path=pack_dir)
            left = lib.list(tile=tile, kind="ortho")
            for r in lib.list(tile=tile, kind="overlay"):
                beside = r.path == pack_dir.parent / OVERLAY_PACK and r.built_by == "osxp"
                if beside or not left:
                    lib.forget(tile, kind="overlay", path=r.path)
    warning = None
    try:
        report = clean_after_delete(
            Path(store_root) if store_root is not None else default_store_root(),
            roots,
            library_path=library_path,
            tiles_root=Path(tiles_root) if tiles_root is not None else default_tiles_root(),
            grace_s=DELETE_GRACE_S if grace_s is None else grace_s,
        )
        freed += report.freed_bytes
    except Exception as exc:  # the tile is gone already: a warning, not an error
        warning = (
            f"The tile is deleted, but the space its cache takes could not be freed ({exc}). "
            "Running osxp clean later frees it."
        )
    return {
        "format": DELETE_FORMAT,
        "name": pack_dir.name,
        "tile": tile.name,
        "removed_from_xplane": removed,
        "pack_deleted": present and not os.path.lexists(pack_dir),
        "freed_bytes": freed,
        "custom_scenery": None if cs is None else str(cs),
        "warning": warning,
    }


def _ortho4xp_above(pack_dir: Path) -> Path | None:
    """The Ortho4XP installation this pack sits in, or None.

    Ortho4XP's own folder holds ``Ortho4XP.py``, which is what the import asks the user to point
    at. Looked for over the pack's parents so that a build of ours never has to be trusted not to
    be somewhere it should not be deleted from.
    """
    for folder in list(pack_dir.parents)[:ORTHO4XP_LOOK_UP]:
        try:
            if (folder / ORTHO4XP_MAIN).is_file():
                return folder
        except OSError:  # an unreadable folder is not an answer either way
            continue
    return None


def _manifest_to_delete(
    pack_dir: Path, tile: TileRef, row: LibraryEntry | None
) -> PackManifest | None:
    """The manifest of the OrthoStudio XP pack about to be deleted, ``None`` when its directory
    is gone.

    Raises ``SYS_PACK_NOT_OSXP`` for what OrthoStudio XP did not build, before anything is touched.
    """
    context = {"path": str(pack_dir), "tile": tile.name}
    by_hand = (
        "Nothing was deleted. Uninstall takes the tile out of X-Plane without deleting "
        f"anything; to delete the tile for good, delete the folder {pack_dir} by hand."
    )
    if row is not None and row.built_by != "osxp":
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": "built by Ortho4XP"},
            message=f"{tile.name} in {pack_dir} was built by Ortho4XP: OrthoStudio XP deletes only "
            "the tiles it built.",
            remedy=by_hand,
        )
    # Two more, because this deletes a folder for good and the guard above believes the library:
    # a row missing for this very path, or one an older version wrote wrong, left nothing between
    # a folder with an orthostudio.toml in it and rm -rf (a user asked for the guard,
    # 2026-09-20). These two read the disk instead, and hold whatever any row says.
    if pack_dir.name.startswith(IMPORTED_PACK_PREFIX):
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": "an Ortho4XP pack name"},
            message=f"{pack_dir.name} is named the way Ortho4XP names its packs, so OrthoStudio "
            "XP does not delete it.",
            remedy=by_hand,
        )
    inside = _ortho4xp_above(pack_dir)
    if inside is not None:
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": "inside an Ortho4XP folder", "ortho4xp": str(inside)},
            message=f"{pack_dir} is inside {inside}, which is an Ortho4XP installation: "
            "OrthoStudio XP does not delete anything there.",
            remedy=by_hand,
        )
    if not pack_dir.exists():  # deleted by hand, or a link that leads nowhere
        return None
    if is_link(pack_dir) or not pack_dir.is_dir():
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": "a link or a file"},
            message=f"{pack_dir} is a link or a file, not a tile folder OrthoStudio XP built, so "
            "OrthoStudio XP does not delete it.",
        )
    try:
        manifest = read_manifest(pack_dir)
    except FileNotFoundError:
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": "no orthostudio.toml"},
            message=f"The folder {pack_dir} has no orthostudio.toml: OrthoStudio XP did not build "
            "it and it may hold your own files, so OrthoStudio XP does not delete it.",
        ) from None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": "orthostudio.toml unreadable"},
            message=f"The orthostudio.toml of {pack_dir} cannot be read ({exc}): OrthoStudio XP "
            "cannot tell that it built this folder, so it does not delete it.",
        ) from exc
    if manifest.tile != tile.name:
        raise OsxpError(
            "SYS_PACK_NOT_OSXP",
            context={**context, "reason": f"a pack of {manifest.tile}"},
            message=f"The folder {pack_dir} holds tile {manifest.tile}, not {tile.name}, so "
            "OrthoStudio XP does not delete it.",
        )
    return manifest


def _overlay_dsf_to_delete(
    pack_dir: Path, tile: TileRef, manifest: PackManifest | None, rows: list[LibraryEntry]
) -> Path | None:
    """The tile's DSF in the overlay pack beside ``pack_dir``, when OrthoStudio XP put it there.

    Known, not guessed: the pack's manifest lists an overlay, or, the pack being gone, the
    library records that overlay pack as OrthoStudio XP's for this tile.
    """
    overlay_pack = pack_dir.parent / OVERLAY_PACK
    dsf = overlay_pack / tile.dsf_relpath
    if not dsf.is_file() or dsf.is_symlink():
        return None
    listed = manifest is not None and bool(manifest.files.get("overlay"))
    recorded = any(
        r.kind == "overlay" and r.built_by == "osxp" and r.path == overlay_pack for r in rows
    )
    return dsf if listed or recorded else None


def _installed_as(
    target: Path, pack_dir: Path, *, present: bool, rows: Iterable[LibraryEntry]
) -> bool:
    """Whether ``target``, the pack's name in Custom Scenery, is this pack installed.

    A link counts when it leads to the pack, or to where the pack was once it is gone
    (:func:`links_to`); a link of that name leading anywhere else is another pack's, another build
    of the tile on a disk that is not mounted for instance. A real folder counts when it is the pack
    itself, built straight into Custom Scenery, or a copy of it (``osxp install --copy``: the same
    ``orthostudio.toml``, byte for byte) that is not the folder of another library row: the same
    build made twice, once straight into Custom Scenery, has the same manifest. Anything else of
    that name belongs to the user and stays.
    """
    if is_link(target):
        return links_to(target, pack_dir)
    if not present or not target.is_dir():
        return False
    if _same_file(target, pack_dir):
        return True
    if any(_same_file(r.path, target) for r in rows):
        return False  # the pack of another row, not a copy of this one
    try:
        return (target / MANIFEST_NAME).read_bytes() == (pack_dir / MANIFEST_NAME).read_bytes()
    except OSError:
        return False


def is_installed(pack_dir: Path, custom_scenery: Path, *, library_path: Path | None = None) -> bool:
    """Whether what Custom Scenery holds under the pack's name is this pack (:func:`_installed_as`,
    against the library's rows of its tile): what an uninstall of that row may take out."""
    pack_dir = Path(pack_dir)
    tile = pack_tile(pack_dir.name)
    rows: list[LibraryEntry] = []
    if tile is not None:
        with Library(library_path) as lib:
            rows = lib.list(tile=tile)
    target = Path(custom_scenery) / pack_dir.name
    return _installed_as(target, pack_dir, present=pack_dir.is_dir(), rows=rows)


def links_to(link: Path, pack_dir: Path) -> bool:
    """Whether ``link`` is a link (a junction on Windows) leading to ``pack_dir``.

    The real paths are compared, so that it still holds once ``pack_dir`` is gone (deleted by
    hand) or when it is reached through a linked folder. A link leading anywhere else, broken or
    not, belongs to another pack. What the library says is installed, and what an uninstall or a
    delete takes out of X-Plane.
    """
    return is_link(link) and os.path.realpath(link) == os.path.realpath(pack_dir)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _delete_pack_dir(pack_dir: Path) -> None:
    """Delete a pack directory, ``orthostudio.toml`` last: stopped halfway, the folder is still
    recognisably OrthoStudio XP's, and the delete can be asked again."""

    def vanished(_function: object, _path: object, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            raise exc

    for child in pack_dir.iterdir():
        if child.name == MANIFEST_NAME:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, onexc=vanished)
        else:
            child.unlink(missing_ok=True)
    (pack_dir / MANIFEST_NAME).unlink(missing_ok=True)
    pack_dir.rmdir()


def _delete(path: Path, remove: Callable[[Path], object]) -> None:
    """``remove(path)``: a path already gone is fine, any other failure is ``SYS_WRITE_FAILED``."""
    try:
        remove(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OsxpError(
            "SYS_WRITE_FAILED",
            context={"path": str(path), "reason": f"{type(exc).__name__}: {exc}"},
            message=f"OrthoStudio XP could not delete {path} ({exc}).",
            remedy="Quit X-Plane and any program that may be using the tile's files, then "
            "delete the tile again: what is already deleted stays deleted.",
        ) from exc


def install_is_intact(receipt: dict[str, Any]) -> bool:
    """True when the links (or copies) and the ``scenery_packs.ini`` lines are still there."""
    custom_scenery = Path(str(receipt.get("custom_scenery", "")))
    targets = [receipt.get("target"), receipt.get("overlay_target")]
    names: list[str] = []
    for t in targets:
        if not t:
            continue
        p = Path(str(t))
        if not p.exists():  # a dangling link "exists" as False
            return False
        names.append(p.name)
    ini = custom_scenery / "scenery_packs.ini"
    if not ini.is_file():
        return False
    packs = SceneryPacks.load(ini)
    listed = set(packs.names())
    return all(name in listed for name in names)


# -- rules ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackEnv:
    """What the pack and install rules need beyond params and inputs."""

    store: Store
    out_root: Path
    custom_scenery: Path | None = None
    library_path: Path | None = None
    built: dict[str, Any] | None = None
    """What the tile is built with (``PackManifest.built``), kept here rather than in the params
    because the params are the key: a version or a list of patches there would never let a pack be
    found again."""


_ENV: ContextVar[PackEnv | None] = ContextVar("osxp_pack_env", default=None)


@contextlib.contextmanager
def pack_env(env: PackEnv) -> Iterator[PackEnv]:
    token = _ENV.set(env)
    try:
        yield env
    finally:
        _ENV.reset(token)


def _env() -> PackEnv:
    env = _ENV.get()
    if env is None:
        raise RuntimeError("pack rules need an environment: run them under pack_env(...)")
    return env


def _tile_pack(ctx: RunContext) -> None:
    """Assemble the scenery pack of a tile; the artefact is its ``orthostudio.toml`` manifest."""
    env = _env()
    params = ctx.params
    assert isinstance(params, PackParams)
    tile = TileRef.parse(params.tile)
    _refuse_rewrite_under_running_xplane(env, tile)
    manifest, _files = assemble_pack(
        env.store,
        Path(params.out_dir),
        tile,
        provider=params.provider,
        zl=params.zl,
        dsf=ctx.inputs["dsf"],
        textures=ctx.inputs["textures"],
        overlay=ctx.inputs["overlay"],
        link=params.link,
        tile_cfg=params.tile_cfg,
        photo={
            "brightness": params.photo_brightness,
            "contrast": params.photo_contrast,
            "saturation": params.photo_saturation,
        },
        built=env.built,
    )
    ctx.out.write_text(manifest.to_toml(), encoding="utf-8")


def _refuse_rewrite_under_running_xplane(env: PackEnv, tile: TileRef) -> None:
    """Windows only: a DSF or DDS of an installed pack cannot be replaced while X-Plane holds
    it open (``os.replace`` -> ``PermissionError``); refuse before writing anything."""
    if os.name != "nt" or env.custom_scenery is None:
        return
    if (Path(env.custom_scenery) / pack_dir_name(tile)).exists() and xplane_running():
        raise OsxpError(
            "XP_RUNNING",
            message="X-Plane is running and this tile is installed: its files cannot be "
            "replaced now.",
            remedy="Quit X-Plane, then run osxp build again.",
        )


def _tile_install(ctx: RunContext) -> None:
    """Link the pack into Custom Scenery, order ``scenery_packs.ini``, register the library."""
    env = _env()
    params = ctx.params
    assert isinstance(params, InstallParams)
    tile = TileRef.parse(params.tile)
    pack_dir = Path(env.out_root) / pack_dir_name(tile)
    receipt = install_receipt(
        pack_dir,
        Path(params.custom_scenery),
        tile=tile,
        link=params.link,
        library_path=env.library_path,
    )
    ctx.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")


TILE_PACK: Rule = rule(
    name="tile.pack",
    version=2,
    params=PackParams,
    inputs=("dsf", "textures", "overlay"),
    ram_mb=100,
    kind="file",
)(_tile_pack)
TILE_INSTALL: Rule = rule(
    name="tile.install",
    version=2,
    params=InstallParams,
    inputs=("pack",),
    ram_mb=50,
    kind="file",
)(_tile_install)


def remove_pack(out_root: Path, tile: TileRef) -> bool:
    """Delete ``<out_root>/zOrthoStudio_<tile>`` (the overlays pack is shared and kept)."""
    pack_dir = Path(out_root) / pack_dir_name(tile)
    if not pack_dir.is_dir():
        return False
    shutil.rmtree(pack_dir)
    return True
