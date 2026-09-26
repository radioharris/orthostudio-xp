# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Whether a newer OrthoStudio XP has been published (``orthostudio.update``).

A user who did not read the forum stayed on the version he had, with bugs fixed since, and nothing
told him (2026-09-21). The page now says so once, with a link; nothing is downloaded or installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orthostudio import update


def test_versions_are_read_as_numbers_not_as_text() -> None:
    v = update.version_tuple
    assert v("0.1.9") == (0, 1, 9)
    assert v("v0.1.10") == (0, 1, 10)
    # as text, "0.1.10" sorts before "0.1.9", and 0.1.10 would never be offered to 0.1.9
    assert update.is_newer("0.1.10", "0.1.9")
    assert not update.is_newer("0.1.9", "0.1.10")
    assert not update.is_newer("0.1.9", "0.1.9")
    # a pre-release, or anything else, is never offered
    for odd in ("0.2.0-rc1", "latest", "", None, 19):
        assert v(odd) is None
        assert not update.is_newer(odd, "0.1.9")


def test_a_pre_release_build_is_offered_its_final_and_what_follows() -> None:
    """The tag v0.1.17-rc.1 builds an app that calls itself 0.1.17rc1. Read as a published
    version is read, that was no version at all, and the app was never told of anything again,
    its own final included (2026-09-25)."""
    assert update.is_newer("0.1.17", "0.1.17rc1")
    assert update.is_newer("0.1.17", "0.1.17-rc.1")
    assert update.is_newer("0.1.18", "0.1.17rc1")
    assert not update.is_newer("0.1.16", "0.1.17rc1")
    # a pre-release is still never offered, to a pre-release or to anyone
    assert not update.is_newer("0.1.17rc2", "0.1.17rc1")
    assert not update.is_newer("0.1.17", "0.1.17-nightly")


def test_the_link_is_built_here_and_never_taken_from_the_answer() -> None:
    """The page links only to this repository's own releases, whatever an answer might say."""
    assert update.release_page("0.1.10") == (
        "https://github.com/radioharris/orthostudio-xp/releases/tag/v0.1.10"
    )


def test_github_is_asked_once_a_day(tmp_path: Path) -> None:
    cache = tmp_path / update.CACHE_NAME
    asked: list[int] = []

    def fetch() -> str:
        asked.append(1)
        return "0.1.10"

    first = update.check("0.1.9", path=cache, now=1000.0, fetch=fetch)
    assert first == {
        "current": "0.1.9",
        "latest": "0.1.10",
        "url": "https://github.com/radioharris/orthostudio-xp/releases/tag/v0.1.10",
        "available": True,
    }
    # started again an hour later: the answer kept is used, GitHub is not asked
    again = update.check("0.1.9", path=cache, now=1000.0 + 3600, fetch=fetch)
    assert again == first and len(asked) == 1
    # the next day it is asked again
    update.check("0.1.9", path=cache, now=1000.0 + update.CHECK_EVERY_S + 1, fetch=fetch)
    assert len(asked) == 2
    # and a clock set back, which would otherwise freeze the answer for ever, asks too
    update.check("0.1.9", path=cache, now=10.0, fetch=fetch)
    assert len(asked) == 3


def test_no_answer_says_nothing_and_does_not_ask_at_every_start(tmp_path: Path) -> None:
    """Offline, refused or rate-limited: no banner rather than an error, since not knowing about a
    release is no fault of the user's; and the question is not asked again until tomorrow."""
    cache = tmp_path / update.CACHE_NAME
    asked: list[int] = []

    def offline() -> None:
        asked.append(1)
        return None

    got = update.check("0.1.9", path=cache, now=1000.0, fetch=offline)
    assert got == {"current": "0.1.9", "latest": None, "url": None, "available": False}
    update.check("0.1.9", path=cache, now=1000.0 + 60, fetch=offline)
    assert len(asked) == 1


def test_an_answer_kept_survives_a_day_without_one(tmp_path: Path) -> None:
    cache = tmp_path / update.CACHE_NAME
    update.check("0.1.9", path=cache, now=1000.0, fetch=lambda: "0.1.10")
    later = update.check(
        "0.1.9", path=cache, now=1000.0 + update.CHECK_EVERY_S + 1, fetch=lambda: None
    )
    assert later["available"] and later["latest"] == "0.1.10"


def test_a_version_already_installed_is_not_offered(tmp_path: Path) -> None:
    got = update.check("0.1.10", path=tmp_path / "u.json", now=1.0, fetch=lambda: "0.1.10")
    assert got["available"] is False and got["url"] is None


def test_a_broken_or_foreign_cache_is_read_as_none(tmp_path: Path) -> None:
    cache = tmp_path / update.CACHE_NAME
    for junk in ("not json", json.dumps([1, 2]), json.dumps({"latest": "<script>"})):
        cache.write_text(junk, encoding="utf-8")
        got = update.check("0.1.9", path=cache, now=1.0, fetch=lambda: None)
        assert got["available"] is False and got["latest"] is None


def test_github_is_asked_with_our_name_and_a_short_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class Answer:
        def __enter__(self) -> Answer:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps({"tag_name": "v0.1.10", "html_url": "https://elsewhere/"}).encode()

    def urlopen(request: object, timeout: float) -> Answer:
        seen["ua"] = request.get_header("User-agent")  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        return Answer()

    monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)
    assert update.latest_release() == "0.1.10"
    assert str(seen["ua"]).startswith("OrthoStudio-XP/")
    assert float(seen["timeout"]) <= 10  # type: ignore[arg-type]

    def unreachable(*_a: object, **_k: object) -> Answer:
        raise update.urllib.error.URLError("offline")

    monkeypatch.setattr(update.urllib.request, "urlopen", unreachable)
    assert update.latest_release() is None
