"""Read, reorder and rewrite ``Custom Scenery/scenery_packs.ini``.

Spec: ``docs/specs/install.md`` section 3. The file is kept as a list of lines where the
``SCENERY_PACK`` / ``SCENERY_PACK_DISABLED`` lines are parsed and every other line (header,
blank lines, unknown keywords) is preserved verbatim, so that a load/save round-trip of an
untouched list reproduces the file byte for byte, line ending included.

The ordering rule is OrthoStudio XP's (spec section 3.2).
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.model import OVERLAY_PACK, PACK_PREFIX

__all__ = [
    "BACKUP_SUFFIX",
    "DEFAULT_HEADER",
    "GLOBAL_AIRPORTS",
    "IMPORTED_OVERLAY_PACK",
    "IMPORTED_PACK_PREFIX",
    "ORTHO_PREFIX",
    "ORTHO_PREFIXES",
    "OVERLAY_LINK_NAME",
    "OVERLAY_PACK_NAME",
    "OVERLAY_PACK_NAMES",
    "PREVIOUS_SUFFIX",
    "PackKind",
    "SceneryPackEntry",
    "SceneryPacks",
    "is_overlay_name",
    "pack_kind",
]

PackKind = Literal["ortho", "overlay"]

GLOBAL_AIRPORTS = "*GLOBAL_AIRPORTS*"
ORTHO_PREFIX = PACK_PREFIX
OVERLAY_PACK_NAME = OVERLAY_PACK
IMPORTED_PACK_PREFIX = "zOrtho4XP_"
IMPORTED_OVERLAY_PACK = "yOrtho4XP_Overlays"
"""The folders of the tiles the Library imports from an Ortho4XP folder, listed and ordered like
OrthoStudio XP's own packs."""
ORTHO_PREFIXES = (ORTHO_PREFIX, IMPORTED_PACK_PREFIX)
OVERLAY_PACK_NAMES = (OVERLAY_PACK_NAME, IMPORTED_OVERLAY_PACK)
OVERLAY_LINK_NAME = re.compile(rf"{re.escape(OVERLAY_PACK_NAME)}(?:_(?:[2-9]|[1-9][0-9]+))?")
"""OrthoStudio XP's overlays packs in Custom Scenery: ``yOrthoStudio_Overlays``, then
``yOrthoStudio_Overlays_2``, ``_3``... for the tiles of other folders X-Plane shows at the same time
(a data folder chosen in Settings while the tiles of the one before stay installed, 2026-09-15;
``pipeline.pack.overlay_link``)."""
BACKUP_SUFFIX = ".bak"
"""``scenery_packs.ini.bak``: the list before OrthoStudio XP first changed it, never overwritten."""
PREVIOUS_SUFFIX = ".osxp-previous"
"""``scenery_packs.ini.osxp-previous``: the list before the latest OrthoStudio XP change."""
AUTOORTHO_PREFIXES = ("z_autoortho", "z_ao_")
MESH_PREFIXES = ("zzz_", "XPME_", "zz_")
"""Known base-mesh packs (HD Mesh ``zzz_hd_global_scenery4``, X-Plane Mesh Europe ``XPME_*``)."""
_BELOW_ORTHO = min(prefix + "~" for prefix in ORTHO_PREFIXES)
"""Every name sorting after this one (``z_*``, ``zz*``) is a base-layer pack for X-Plane."""
DEFAULT_HEADER: tuple[str, ...] = ("I", "1000 Version", "SCENERY", "")

_ENABLED = "SCENERY_PACK "
_DISABLED = "SCENERY_PACK_DISABLED "
_ENCODING = "utf-8"
_ERRORS = "surrogateescape"


def is_overlay_name(name: str) -> bool:
    """Whether ``name`` is an overlays pack's: OrthoStudio XP's (:data:`OVERLAY_LINK_NAME`) or an
    imported one."""
    return name == IMPORTED_OVERLAY_PACK or OVERLAY_LINK_NAME.fullmatch(name) is not None


def pack_kind(name: str) -> PackKind:
    """``overlay`` for an overlays pack (``yOrthoStudio_Overlays``, ``yOrthoStudio_Overlays_2``,
    or an imported one), ``ortho`` for everything else."""
    return "overlay" if is_overlay_name(name) else "ortho"


@dataclass(slots=True)
class SceneryPackEntry:
    """One ``SCENERY_PACK[_DISABLED]`` line: the path as written, and whether it is enabled."""

    path: str
    enabled: bool = True

    @property
    def name(self) -> str:
        """Last path component (``z_autoortho`` for ``Custom Scenery/z_autoortho/``)."""
        if self.path == GLOBAL_AIRPORTS:
            return GLOBAL_AIRPORTS
        stripped = self.path.rstrip("/\\")
        pure = PureWindowsPath(stripped) if "\\" in stripped else PurePosixPath(stripped)
        return pure.name or stripped

    @property
    def beside_xplanes_own(self) -> bool:
        """Whether the line names a folder in X-Plane's own ``Custom Scenery``.

        A line pointing anywhere else -- an archive copy on another disk -- is the user's, not
        ours to take out.
        """
        written = self.path.strip().replace("\\", "/").lstrip("./").rstrip("/")
        return written.lower().startswith("custom scenery/")

    @property
    def is_global_airports(self) -> bool:
        return self.path == GLOBAL_AIRPORTS

    @property
    def is_ortho(self) -> bool:
        return self.name.startswith(ORTHO_PREFIXES)

    @property
    def is_overlay(self) -> bool:
        return is_overlay_name(self.name)

    @property
    def is_autoortho(self) -> bool:
        return self.name.startswith(AUTOORTHO_PREFIXES)

    @property
    def is_mesh(self) -> bool:
        """A base-mesh pack that an ortho tile must stay above (spec 3.2, rule 3)."""
        name = self.name
        return name.startswith(MESH_PREFIXES) or (
            name > _BELOW_ORTHO and not name.startswith(ORTHO_PREFIXES)
        )

    def render(self) -> str:
        return (_ENABLED if self.enabled else _DISABLED) + self.path


def _parse_line(line: str) -> SceneryPackEntry | None:
    if line.startswith(_DISABLED):
        return SceneryPackEntry(line[len(_DISABLED) :].strip(), enabled=False)
    if line.startswith(_ENABLED):
        return SceneryPackEntry(line[len(_ENABLED) :].strip(), enabled=True)
    return None


@dataclass(slots=True)
class SceneryPacks:
    """The parsed file: header lines, body lines (text or entries), line ending, final newline."""

    header: list[str] = field(default_factory=lambda: list(DEFAULT_HEADER))
    body: list[str | SceneryPackEntry] = field(default_factory=list)
    newline: str = "\r\n" if os.name == "nt" else "\n"
    trailing_newline: bool = True

    # ------------------------------------------------------------ load/dump

    @classmethod
    def from_bytes(cls, data: bytes) -> SceneryPacks:
        """Parse the file content; the line ending is ``\\r\\n`` when present anywhere."""
        newline = "\r\n" if b"\r\n" in data else "\n"
        text = data.decode(_ENCODING, _ERRORS)
        trailing = text.endswith(newline)
        if trailing:
            text = text[: -len(newline)]
        lines = text.split(newline) if text else []
        header: list[str] = []
        body: list[str | SceneryPackEntry] = []
        in_header = True
        for line in lines:
            if in_header:
                header.append(line)
                if line.strip() == "SCENERY":
                    in_header = False
                    continue
                if line.startswith(("SCENERY_PACK", "SCENERY_PACK_DISABLED")):
                    # No SCENERY line: keep what came before as header, parse from here.
                    header.pop()
                    in_header = False
                else:
                    continue
            body.append(_parse_line(line) or line)
        if not any(h.strip() == "SCENERY" for h in header):
            # Empty or headerless file: canonical header, stray non-blank lines kept in the body.
            body = [_parse_line(h) or h for h in header if h.strip()] + body
            header = list(DEFAULT_HEADER)
        elif header[-1].strip() == "SCENERY" and body and body[0] == "":
            # The blank line after SCENERY belongs to the header (X-Plane writes one).
            header.append("")
            body.pop(0)
        return cls(header=header, body=body, newline=newline, trailing_newline=trailing)

    @classmethod
    def load(cls, path: Path) -> SceneryPacks:
        """Parse ``path``; a missing file yields an empty list with the default header."""
        path = Path(path)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return cls()
        return cls.from_bytes(data)

    def to_bytes(self) -> bytes:
        lines = list(self.header)
        lines.extend(x.render() if isinstance(x, SceneryPackEntry) else x for x in self.body)
        text = self.newline.join(lines)
        if self.trailing_newline and lines:
            text += self.newline
        return text.encode(_ENCODING, _ERRORS)

    def save(self, path: Path, *, backup: bool = True) -> Path:
        """Write atomically, after two copies of the file being replaced when ``backup`` is set.

        ``<path>.bak`` is written only when it does not exist yet: it is the list as it was
        before OrthoStudio XP first changed it, and it stays that way. It used to be overwritten
        on every save, so after a few installs the original was gone. ``<path>.osxp-previous`` is
        the list before this save, overwritten each time. X-Plane reads neither.
        """
        path = Path(path)
        try:
            if backup and path.exists():
                original = path.with_name(path.name + BACKUP_SUFFIX)
                if not original.exists():
                    shutil.copy2(path, original)
                shutil.copy2(path, path.with_name(path.name + PREVIOUS_SUFFIX))
            return atomic_write_bytes(path, self.to_bytes())
        except OSError as exc:
            raise OsxpError(
                "XP_SCENERY_PACKS_UNWRITABLE",
                context={"path": str(path), "reason": f"{type(exc).__name__}: {exc}"},
            ) from exc

    # ------------------------------------------------------------ queries

    @property
    def entries(self) -> list[SceneryPackEntry]:
        return [x for x in self.body if isinstance(x, SceneryPackEntry)]

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def find(self, name: str) -> SceneryPackEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def _index(self, name: str) -> int | None:
        for i, x in enumerate(self.body):
            if isinstance(x, SceneryPackEntry) and x.name == name:
                return i
        return None

    def _floor(self) -> int:
        """First body index at which a new line may go: right after ``*GLOBAL_AIRPORTS*``."""
        for i, x in enumerate(self.body):
            if isinstance(x, SceneryPackEntry) and x.is_global_airports:
                return i + 1
        return 0

    def _insertion_index(self, kind: PackKind) -> int:
        floor = self._floor()
        entries = [(i, x) for i, x in enumerate(self.body) if isinstance(x, SceneryPackEntry)]
        if kind == "overlay":
            first_ortho = next((i for i, x in entries if x.is_ortho and i >= floor), None)
            if first_ortho is not None:
                return first_ortho
        else:
            last_ortho = next((i for i, x in reversed(entries) if x.is_ortho), None)
            if last_ortho is not None:
                return max(last_ortho + 1, floor)
        first_ao = next((i for i, x in entries if x.is_autoortho and i >= floor), None)
        if first_ao is not None:
            return first_ao
        first_mesh = next((i for i, x in entries if x.is_mesh and i >= floor), None)
        if first_mesh is not None:
            return first_mesh
        return len(self.body)

    # ------------------------------------------------------------ mutators

    def ensure(
        self, pack_name: str, *, kind: PackKind, reenable: bool = True, above: str | None = None
    ) -> bool:
        """Make ``pack_name`` listed and enabled; returns whether the list changed.

        An existing line keeps its position; a disabled one becomes enabled unless
        ``reenable`` is false: a user of simHeaven X-World disables the shared overlays, which
        X-World replaces, and a later install must not turn them back on. A new line goes,
        enabled, where the ordering rule says (spec section 3.2), never above
        ``*GLOBAL_AIRPORTS*`` -- or right above the line of ``above`` when that one is listed: a
        tile OrthoStudio XP built draws over an imported tile of the same square.
        """
        idx = self._index(pack_name)
        if idx is not None:
            entry = self.body[idx]
            assert isinstance(entry, SceneryPackEntry)
            if entry.enabled or not reenable:
                return False
            entry.enabled = True
            return True
        entry = SceneryPackEntry(f"Custom Scenery/{pack_name}/", enabled=True)
        other = None if above is None else self._index(above)
        index = self._insertion_index(kind) if other is None else max(other, self._floor())
        self.body.insert(index, entry)
        return True

    def disable(self, pack_name: str) -> bool:
        """Turn the line into ``SCENERY_PACK_DISABLED`` without moving it."""
        entry = self.find(pack_name)
        if entry is None or not entry.enabled:
            return False
        entry.enabled = False
        return True

    def remove(self, pack_name: str) -> bool:
        """Delete the line naming ``pack_name`` in Custom Scenery; returns whether one existed.

        Every line ending in that name used to go, wherever it pointed. A simmer who keeps a copy
        of a tile on another disk has two, and taking the tile out of X-Plane took his other
        scenery out with it, silently (found in review, 2026-09-23). Only what sits beside
        X-Plane's own is ours.
        """
        before = len(self.body)
        self.body = [
            x
            for x in self.body
            if not (
                isinstance(x, SceneryPackEntry) and x.name == pack_name and x.beside_xplanes_own
            )
        ]
        return len(self.body) != before
