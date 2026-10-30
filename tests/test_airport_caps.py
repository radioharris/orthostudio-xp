# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The airports of a source that stops below their level (``DsfParams.cover_zl_caps``).

EOX stops at ZL14 and serves its ZL14 image enlarged above it: an EOX tile's airports raised to
ZL18 were 16 128 pieces more each, and minutes, for not one more detail (2026-10-29)."""

from __future__ import annotations

from orthostudio.dsf import DsfParams, texture_map
from orthostudio.dsf.zones import AirportCover
from orthostudio.imagery.providers import load_registry
from orthostudio.model import TileRef
from orthostudio.pipeline.build import cover_zl_caps

T = TileRef(32, -83)
AIRPORT = [AirportCover(0.4, 0.4, 0.5, 0.5, True)]


def _params(**kw: object) -> DsfParams:
    base: dict[str, object] = {
        "default_zl": 14,
        "default_website": "EOX",
        "cover_airports_with_highres": "ICAO",
        "cover_zl": 18,
        "mesh_zl": 18,
    }
    return DsfParams(**{**base, **kw})


def test_an_eox_tiles_airports_stay_at_its_highest_level() -> None:
    raised = texture_map(T, _params(), airports=AIRPORT)
    assert int(raised.zl.max()) == 18  # without the cap: ZL18 textures EOX only enlarges
    capped = texture_map(T, _params(cover_zl_caps={"EOX": 14}), airports=AIRPORT)
    assert int(capped.zl.max()) == 14 and int(capped.zl.min()) == 14
    bing_params = _params(default_website="BI", cover_zl_caps={"EOX": 14})
    bing = texture_map(T, bing_params, airports=AIRPORT)
    assert int(bing.zl.max()) == 18  # the cap is EOX's alone


def test_no_cap_leaves_the_key_as_it_was() -> None:
    assert "cover_zl_caps" not in _params().canonical()
    assert _params(cover_zl_caps={"EOX": 14}).canonical()["cover_zl_caps"] == {"EOX": 14}


def test_the_caps_are_the_tiles_sources_below_the_airports_level() -> None:
    registry = load_registry()
    cfg = {"default_website": "EOX", "cover_airports_with_highres": "ICAO", "cover_zl": 18}
    assert cover_zl_caps(cfg, registry) == {"EOX": 14}
    assert cover_zl_caps({**cfg, "default_website": "BI"}, registry) == {}
    assert cover_zl_caps({**cfg, "cover_airports_with_highres": "False"}, registry) == {}
    assert cover_zl_caps({**cfg, "cover_zl": 14}, registry) == {}  # not above EOX's own
    zone = [[[32.1, -82.9, 32.1, -82.8, 32.2, -82.8, 32.2, -82.9], 15, "EOX"]]
    assert cover_zl_caps({**cfg, "default_website": "BI", "zone_list": zone}, registry) == {
        "EOX": 14
    }
