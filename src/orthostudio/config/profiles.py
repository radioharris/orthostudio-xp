# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Settings profiles: named sets of the build settings, one of them current
(``docs/specs/settings.md`` section 7).

``config.toml`` keeps holding what every build and the command line read: the current profile's
settings, with what belongs to this computer. ``profiles.json`` beside it keeps the build settings
of every profile by name, the current one's as they were last saved or left. A computer without the
file has one profile, unnamed, and the page shows no list of them: nothing changes for a user until
they keep a second set (a user asked to keep settings by name and choose them for each build,
2026-11-02).

The functions here are pure: they take a :class:`Profiles` and settings and give the new ones; the
API reads and writes the files around them.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from orthostudio.config.models import Settings
from orthostudio.config.store import default_config_path, settings_and_problems
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text

__all__ = [
    "COMPUTER_SETTINGS",
    "MAX_NAME",
    "MAX_PROFILES",
    "PROFILES_FILE",
    "ProfileError",
    "Profiles",
    "build_part",
    "check_name",
    "create",
    "default_profiles_path",
    "delete",
    "load_profiles",
    "remember",
    "rename",
    "replace_provider",
    "save_profiles",
    "switch",
    "with_build_part",
]

log = logging.getLogger("orthostudio.config")

PROFILES_FILE = "profiles.json"
FORMAT = 1
MAX_NAME = 40
"""A name fits on the line of the list, in the Plan's narrow column too."""
MAX_PROFILES = 50

COMPUTER_SETTINGS: tuple[str, ...] = (
    "essential.xplane_dir",
    "essential.data_dir",
    "essential.simbrief_user",
    "essential.relief.private_sources",
    "expert.osm_folder",
    "expert.osm_library",
    "expert.osm_library_token",
    "expert.patches_dir",
    "expert.check_updates",
    "expert.build_memory",
)
"""What belongs to this computer and to its pilot, the same whatever the profile: the folders, the
SimBrief name, where the map data comes from and its key, the patches, the update check and how
much memory a build takes. A profile changing them would send the tiles to another disk, or a build
to the public servers, because a look was chosen; and the library's key stays out of a file a user
may pass around. The page's *Default values* keeps the first seven (``settings.js``
``COMPUTER_FOLDERS``)."""


class ProfileError(Exception):
    """A request about the profiles that cannot be done: an API error of its own (``CFG_PROFILE_*``,
    not in the registry, as ``SYS_BUSY``)."""

    def __init__(
        self, code: str, message: str, remedy: str, *, status: int, name: str = ""
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy
        self.status = status
        self.context = {"name": name}


@dataclass(frozen=True)
class Profiles:
    """The profiles of a computer, in the order they were made, and the current one's name."""

    current: str | None = None
    entries: tuple[tuple[str, dict[str, Any]], ...] = field(default_factory=tuple)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.entries]

    def find(self, name: str) -> int | None:
        """The index of the profile called ``name``, upper or lower case alike, else ``None``."""
        wanted = name.strip().casefold()
        for i, (have, _) in enumerate(self.entries):
            if have.casefold() == wanted:
                return i
        return None

    def answer(self) -> dict[str, Any]:
        """What ``GET /api/profiles`` gives: the names, which is current, what they all share."""
        return {
            "current": self.current,
            "profiles": self.names,
            "computer": list(COMPUTER_SETTINGS),
        }


# -- what a profile holds ----------------------------------------------------------------------


def _drop(data: dict[str, Any], path: str) -> None:
    keys = path.split(".")
    here: Any = data
    for key in keys[:-1]:
        here = here.get(key) if isinstance(here, dict) else None
        if here is None:
            return
    if isinstance(here, dict):
        here.pop(keys[-1], None)


def _get(data: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    here: Any = data
    for key in path.split("."):
        if not isinstance(here, Mapping) or key not in here:
            return False, None
        here = here[key]
    return True, here


def _set(data: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    here = data
    for key in keys[:-1]:
        if not isinstance(here.get(key), dict):
            here[key] = {}
        here = here[key]
    here[keys[-1]] = value


def _merge(into: dict[str, Any], part: Mapping[str, Any]) -> None:
    for key, value in part.items():
        if isinstance(value, Mapping) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        else:
            into[key] = json.loads(json.dumps(value))


def build_part(settings: Settings) -> dict[str, Any]:
    """The settings a profile keeps: all of them but :data:`COMPUTER_SETTINGS`."""
    data = settings.model_dump(mode="json")
    for path in COMPUTER_SETTINGS:
        _drop(data, path)
    return data


def with_build_part(base: Settings, part: Mapping[str, Any]) -> tuple[Settings, list[str]]:
    """``base`` with a profile's build settings in place of its own, and the values of the profile
    this version cannot read (left at their default, as a ``config.toml`` written by another
    version is read).

    A setting the profile does not hold takes its default, not ``base``'s: the profile was kept by
    a version before that setting, which built as its default does.
    """
    data = Settings().model_dump(mode="json")
    own = json.loads(json.dumps(part))
    for path in COMPUTER_SETTINGS:
        _drop(own, path)
    _merge(data, own)
    kept = base.model_dump(mode="json")
    for path in COMPUTER_SETTINGS:
        found, value = _get(kept, path)
        if found:
            _set(data, path, value)
    return settings_and_problems(data, source="profile")


# -- names -------------------------------------------------------------------------------------


def check_name(name: Any, taken: Iterable[str] = ()) -> str:
    """``name`` without its outer spaces, or :class:`ProfileError`: empty, longer than
    :data:`MAX_NAME`, with a control character, or already the name of one of ``taken`` (upper and
    lower case alike: two profiles « Alpes » and « alpes » would read as one in a list)."""
    text = name.strip() if isinstance(name, str) else ""
    if not text or len(text) > MAX_NAME or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ProfileError(
            "CFG_PROFILE_NAME",
            f"A profile's name is 1 to {MAX_NAME} characters, without a line break.",
            "Give the profile another name.",
            status=422,
            name=text,
        )
    # the one that has it, as it is written: « alpes » is taken by « Alpes »
    held = next((t for t in taken if t.casefold() == text.casefold()), None)
    if held is not None:
        raise ProfileError(
            "CFG_PROFILE_TAKEN",
            f"A profile is already called {held!r}.",
            "Give the profile another name.",
            status=409,
            name=held,
        )
    return text


def _index(profiles: Profiles, name: Any) -> int:
    i = profiles.find(name) if isinstance(name, str) else None
    if i is None:
        raise ProfileError(
            "CFG_PROFILE_UNKNOWN",
            f"There is no profile called {name!r}.",
            "Choose one of the list: it may have been renamed or deleted in another window.",
            status=404,
            name=str(name),
        )
    return i


# -- what the page asks ------------------------------------------------------------------------


def check_current(profiles: Profiles, name: str) -> None:
    """``name``, the profile a page's Save names (``""`` for none), still the current one.

    Another page of the same engine (starting the app again opens one in the browser) may have made
    another profile current, renamed or deleted it: Save would then write that page's answers into
    a profile it never showed, which loses its own settings without a word (a campaign,
    2026-11-04). ``CFG_PROFILE_CHANGED`` names the one current now."""
    now = profiles.current or ""
    if profiles.entries and now != name:
        raise ProfileError(
            "CFG_PROFILE_CHANGED",
            f"The profiles were changed in another window: the one in use is now {now!r}.",
            "Your answers are still on screen: save them in it, save them as a new profile, or"
            " undo them.",
            status=409,
            name=now,
        )


def remember(profiles: Profiles, settings: Settings) -> Profiles:
    """The current profile holding ``settings``'s build settings, as saved: called at each save,
    and before the current profile is left."""
    if profiles.current is None:
        return profiles
    i = profiles.find(profiles.current)
    if i is None:
        return profiles
    entries = list(profiles.entries)
    entries[i] = (entries[i][0], build_part(settings))
    return replace(profiles, entries=tuple(entries))


def create(
    profiles: Profiles, name: str, saved: Settings, new: Settings, first_name: str | None
) -> Profiles:
    """A new profile called ``name`` holding ``new``, made current. ``saved`` is what the current
    profile held until then, kept under its own name; a computer with no profile yet names it
    ``first_name`` (the page's word for it, « Principal »)."""
    if not profiles.entries:
        first = check_name(first_name or "Main")
        name = check_name(name, [first])
        entries: list[tuple[str, dict[str, Any]]] = [(first, build_part(saved))]
    else:
        if len(profiles.entries) >= MAX_PROFILES:
            raise ProfileError(
                "CFG_PROFILE_NAME",
                f"There are already {MAX_PROFILES} profiles.",
                "Delete one you no longer use, then save again.",
                status=409,
                name=str(name),
            )
        name = check_name(name, profiles.names)
        entries = list(remember(profiles, saved).entries)
    entries.append((name, build_part(new)))
    return Profiles(current=name, entries=tuple(entries))


def switch(
    profiles: Profiles, name: str, settings: Settings
) -> tuple[Profiles, Settings, list[str]]:
    """``name`` made current: the profile left keeps ``settings``'s build settings, and the
    settings to save are ``settings`` with ``name``'s in their place."""
    i = _index(profiles, name)
    kept = remember(profiles, settings)
    target, part = kept.entries[i]
    new, problems = with_build_part(settings, part)
    return replace(kept, current=target), new, problems


def rename(profiles: Profiles, name: str, new_name: str) -> Profiles:
    i = _index(profiles, name)
    old = profiles.entries[i][0]
    others = [n for j, n in enumerate(profiles.names) if j != i]
    text = check_name(new_name, others)
    entries = list(profiles.entries)
    entries[i] = (text, entries[i][1])
    current = text if profiles.current == old else profiles.current
    return Profiles(current=current, entries=tuple(entries))


def delete(
    profiles: Profiles, name: str, settings: Settings
) -> tuple[Profiles, Settings | None, list[str]]:
    """``name`` deleted. Deleting the current profile makes the first of the others current, and
    gives its settings to save; otherwise the settings stay as they are (``None``). The last
    profile cannot go: the settings are always one of them."""
    i = _index(profiles, name)
    if len(profiles.entries) <= 1:
        raise ProfileError(
            "CFG_PROFILE_LAST",
            "The last profile cannot be deleted: the settings are always those of a profile.",
            "Keep it, or rename it.",
            status=409,
            name=profiles.entries[i][0],
        )
    gone = profiles.entries[i][0]
    entries = tuple(e for j, e in enumerate(profiles.entries) if j != i)
    if profiles.current != gone:
        return replace(profiles, entries=entries), None, []
    target, part = entries[0]
    new, problems = with_build_part(settings, part)
    return Profiles(current=target, entries=entries), new, problems


def replace_provider(profiles: Profiles, code: str, by: str) -> Profiles:
    """Every profile whose imagery source is ``code`` taking ``by``: a source the user added and
    then removed would otherwise come back with the profile, and its builds fail."""
    entries = []
    for name, part in profiles.entries:
        essential = part.get("essential")
        if isinstance(essential, dict) and essential.get("provider") == code:
            part = {**part, "essential": {**essential, "provider": by}}
        entries.append((name, part))
    return replace(profiles, entries=tuple(entries))


# -- file --------------------------------------------------------------------------------------


def default_profiles_path(config_path: Path | None = None) -> Path:
    """``profiles.json`` beside ``config.toml``."""
    return (Path(config_path) if config_path is not None else default_config_path()).with_name(
        PROFILES_FILE
    )


def load_profiles(path: Path) -> Profiles:
    """The profiles of ``path``; none when it does not exist. A current name the file does not
    hold falls back on its first profile."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Profiles()
    except OSError as exc:
        raise OsxpError(
            "CFG_LINE_INVALID", context={"path": str(path), "line": 0, "reason": str(exc)}
        ) from exc
    try:
        doc = json.loads(raw)
        entries: list[tuple[str, dict[str, Any]]] = []
        for item in doc.get("profiles") or []:
            name = item["name"]
            part = item.get("settings") or {}
            if not isinstance(name, str) or not name.strip() or not isinstance(part, dict):
                raise ValueError(f"a profile without a name or settings: {name!r}")
            entries.append((name, part))
        current = doc.get("current")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        # the line JSON names, where it knows it: "line 0" of a file cut short said nothing
        line = exc.lineno if isinstance(exc, json.JSONDecodeError) else 0
        raise OsxpError(
            "CFG_LINE_INVALID", context={"path": str(path), "line": line, "reason": str(exc)}
        ) from None
    profiles = Profiles(current=None, entries=tuple(entries))
    at = profiles.find(current) if isinstance(current, str) else None
    if at is None and entries:
        at = 0
    return replace(profiles, current=None if at is None else entries[at][0])


def save_profiles(profiles: Profiles, path: Path) -> None:
    """Write ``profiles`` atomically; the previous file becomes ``profiles.json.bak``."""
    target = Path(path)
    doc = {
        "format": FORMAT,
        "current": profiles.current,
        "profiles": [{"name": name, "settings": part} for name, part in profiles.entries],
    }
    try:
        if target.is_file():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        atomic_write_text(target, json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise OsxpError(
            "CFG_TILE_WRITE_FAILED", context={"path": str(target), "reason": str(exc)}
        ) from exc
