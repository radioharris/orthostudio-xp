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
