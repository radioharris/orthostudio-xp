"""Whether a newer OrthoStudio XP has been published: GitHub is asked at most once a day.

Nothing is downloaded or installed here. The page says that a version exists and links to its
release page, where the notes and the installers are, and the user installs it as before. Until
now a user who did not read the forum stayed on the version he had, with bugs fixed since, and
nothing told him: a settings save that changed the source of a build under him, fixed in 0.1.9,
is one of them.

The answer is kept in :data:`CACHE_NAME` in the OrthoStudio XP home, so that starting the app ten
times a day asks GitHub once. Offline, refused or rate-limited, the answer is simply "nothing
newer": the page shows no banner rather than an error, since not knowing about a release is no
fault of the user's.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orthostudio.net import USER_AGENT

__all__ = [
    "CACHE_NAME",
    "CHECK_EVERY_S",
    "RELEASES_API",
    "check",
    "is_newer",
    "latest_release",
    "release_page",
    "version_tuple",
]

REPOSITORY = "radioharris/orthostudio-xp"

RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
"""The newest release that is neither a draft nor a pre-release, as GitHub decides it."""

CHECK_EVERY_S = 24 * 3600
"""GitHub lets an address ask 60 times an hour without an account; once a day is plenty."""

TIMEOUT_S = 6.0

CACHE_NAME = "update.json"


def version_tuple(text: object) -> tuple[int, ...] | None:
    """``"v0.1.9"`` or ``"0.1.9"`` as ``(0, 1, 9)``; None for anything else.

    Anything else includes a pre-release suffix: such a tag is never offered as an update.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().removeprefix("v").split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


_PRE_RELEASE = re.compile(r"(\d+(?:\.\d+)*)[-.]?(?:a|b|rc)\.?\d+")
"""A running version with a pre-release suffix, ``0.1.17rc1`` (the tag ``v0.1.17-rc.1``)."""


def _running(text: object) -> tuple[tuple[int, ...], bool] | None:
    """The version this build calls itself, and whether it is a pre-release.

    A build of a pre-release comes just before its final. Read the way a published version is
    read, it was no version at all, and such a build was never told of anything again, its own
    final included (2026-09-25).
    """
    plain = version_tuple(text)
    if plain is not None:
        return plain, False
    match = (
        _PRE_RELEASE.fullmatch(text.strip().removeprefix("v")) if isinstance(text, str) else None
    )
    numbers = version_tuple(match.group(1)) if match else None
    return None if numbers is None else (numbers, True)


def is_newer(latest: object, current: object) -> bool:
    """Whether ``latest`` is a later version than ``current``; False when either is unreadable.

    ``latest`` must be a plain version, since a pre-release is never offered; ``current`` may be
    one, and then its final is newer than it.
    """
    a, running = version_tuple(latest), _running(current)
    if a is None or running is None:
        return False
    b, pre_release = running
    return a > b or (pre_release and a == b)


def release_page(version: str) -> str:
    """The release page of ``version``, built here rather than taken from GitHub's answer: the
    page only ever links to this repository's own releases, whatever an answer says."""
    return f"https://github.com/{REPOSITORY}/releases/tag/v{version}"


def latest_release(timeout: float = TIMEOUT_S) -> str | None:
    """The version of the latest published release, ``"0.1.10"``; None without an answer."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            doc = json.loads(answer.read(64 * 1024))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    tag = doc.get("tag_name") if isinstance(doc, dict) else None
    numbers = version_tuple(tag)
    return None if numbers is None else ".".join(str(n) for n in numbers)


def _read(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(path: Path, doc: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    except OSError:
        pass  # a home that cannot be written asks again next time, which harms nobody


def check(
    current: str,
    *,
    path: Path,
    now: float | None = None,
    fetch: Callable[[], str | None] = latest_release,
) -> dict[str, Any]:
    """What the page needs to say whether a newer version exists.

    ``{"current", "latest", "url", "available"}``. GitHub is asked when the last question is more
    than :data:`CHECK_EVERY_S` old; a question that got no answer is remembered all the same, so
    that a machine offline or a GitHub that refuses is not asked again at every start.
    """
    now = time.time() if now is None else now
    kept = _read(path)
    latest = kept.get("latest") if version_tuple(kept.get("latest")) else None
    asked = kept.get("checked_at")
    if not isinstance(asked, (int, float)) or not 0 <= now - asked < CHECK_EVERY_S:
        answer = fetch()
        if answer is not None:
            latest = answer
        _write(path, {"checked_at": now, "latest": latest})
    available = is_newer(latest, current)
    return {
        "current": current,
        "latest": latest,
        "url": release_page(latest) if available and latest else None,
        "available": available,
    }
