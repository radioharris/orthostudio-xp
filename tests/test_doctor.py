# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the doctor answers about the outside world, without going there.

The probe is injected, so nothing here reaches a server.
"""

from __future__ import annotations


def test_the_doctor_says_which_map_data_servers_answer() -> None:
    """The question nobody could answer on 2026-09-22, when two of the three public machines had
    become unusable and three users' failed builds were how it was found. The probe is the
    engine's own ``check_health``, which existed and which nothing called."""
    from orthostudio import doctor as doc

    assert doc._map_data(offline=True).status == "skip"  # no network unless asked

    class Fake:
        def __init__(self, states):
            self.states = states

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def check_health(self):
            from orthostudio.sources.osm import MirrorHealth

            return {
                code: MirrorHealth(code=code, healthy=state != "open", state=state, status=status)
                for code, (state, status) in self.states.items()
            }

    def answers(states):
        import unittest.mock as mock

        with mock.patch("orthostudio.sources.osm.OverpassClient", lambda **kw: Fake(states)):
            return doc._map_data(offline=False)

    ok = answers({"de": ("closed", 200), "z": ("closed", 200), "lz4": ("closed", 200),
                  "fr": ("open", 403), "mailru": ("open", 504)})  # fmt: skip
    assert ok.status == "ok" and "3 map data servers answered" in ok.summary

    half = answers({"de": ("open", 504), "z": ("closed", 200), "lz4": ("open", 0),
                    "fr": ("open", 403), "mailru": ("closed", 200)})  # fmt: skip
    assert half.status == "warn" and "silent: de, lz4" in half.summary

    none = answers({"de": ("open", 504), "z": ("open", 504), "lz4": ("open", 504),
                    "fr": ("open", 403), "mailru": ("open", 504)})  # fmt: skip
    assert none.status == "fail" and "no map data server answered" in none.summary


# -- the library a release carries --------------------------------------------------------------


def test_the_doctor_says_when_a_build_carries_no_map_library(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A release is built with the library's address and key written in from the repository's
    secrets. If that step is skipped, the app works exactly as before and downloads every tile
    from the public servers, with nothing at all to say a whole layer of the design is gone."""
    from orthostudio import doctor
    from orthostudio.sources import library as lib

    monkeypatch.setattr(lib, "shipped_library", lambda: ("", ""))
    check = doctor._prepared_library(False)
    assert check.name == "map_library" and check.status == "skip"
    assert "no prepared map library" in check.summary
    assert check.details["carried"] is False

    monkeypatch.setattr(lib, "shipped_library", lambda: ("https://carried", "a-key"))
    offline = doctor._prepared_library(True)
    assert offline.status == "skip" and offline.details["carried"] is True


def test_a_library_that_refuses_the_key_is_a_warning_not_a_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """It is not a reason to stop: every tile is downloaded live, as every version before."""
    from orthostudio import doctor
    from orthostudio.sources import library as lib

    monkeypatch.setattr(lib, "shipped_library", lambda: ("https://carried", "the-old-key"))
    monkeypatch.setattr(lib.LibrarySource, "_load_index", lambda self: None)
    check = doctor._prepared_library(False)
    assert check.status == "warn" and "public servers" in check.summary
