"""Unit tests of the provider registry, URL grammar and placeholders (spec tests P2-P5)."""

from __future__ import annotations

from pathlib import Path

import blake3
import pytest

from orthostudio.imagery.providers import (
    PLACEHOLDERS,
    PlaceholderRule,
    Provider,
    is_placeholder,
    load_registry,
    tile_url,
)

EXPECTED_CODES = {
    "Arc",
    "Arc@",
    "BI",
    "Lux",
    "NL",
    "PDOK",
    "PDOK18",
    "PDOK19",
    "PDOK20",
    "SP",
    "JP",
    "USGS",
}
BING_URL = "https://ecn.t0.tiles.virtualearth.net/tiles/a120222133031221.jpeg?g=15312"


@pytest.fixture(scope="module")
def registry() -> dict[str, Provider]:
    return load_registry()


# ---------------------------------------------------------------- registry


def test_registry_content(registry: dict[str, Provider]) -> None:
    assert set(registry) == EXPECTED_CODES
    for code, p in registry.items():
        assert p.code == code
        assert p.grid_type == "webmercator"
        assert p.tile_size == 256
        assert 1 <= p.max_in_flight <= 192
        assert p.attribution and p.terms_url.startswith("https://")
        # every template is fully consumed at any zoom, including single digits
        for zl in (5, 9, 14, p.max_zl):
            url = tile_url(p, 3, 4, zl)
            assert "{" not in url and "}" not in url, (code, url)
            assert url.startswith(("http://", "https://"))


# docs/benchmarks/network.md section 7 (2026-09-15): the most each server took before it slowed,
# refused or gave no more; the services of a state were not tried past 128.
MEASURED_IN_FLIGHT = {
    "BI": 128,
    "Arc": 128,
    "Arc@": 192,
    "Lux": 16,
    "NL": 32,
    "PDOK": 32,
    "PDOK18": 32,
    "PDOK19": 32,
    "PDOK20": 32,
    "SP": 128,
    "JP": 64,
    "USGS": 128,
}


MEASURED_SERVER_RATES = {
    "Arc@": 522,
    "Lux": 130,
    "NL": 181,
    "PDOK": 181,
    "PDOK18": 181,
    "PDOK19": 181,
    "PDOK20": 181,
    "SP": 584,
    "JP": 103,
    "USGS": 280,
}
"""Requests per second of the servers that, not the line, were the limit (2026-09-15)."""


def test_each_server_takes_the_requests_in_flight_it_was_measured_at(
    registry: dict[str, Provider],
) -> None:
    assert {code: p.max_in_flight for code, p in registry.items()} == MEASURED_IN_FLIGHT
    rates = {code: p.server_req_per_s for code, p in registry.items() if p.server_req_per_s}
    assert rates == MEASURED_SERVER_RATES
    assert registry["BI"].server_req_per_s is None and registry["Arc"].server_req_per_s is None


def test_bing_url_and_switch(registry: dict[str, Provider]) -> None:
    bi = registry["BI"]
    assert bi.max_in_flight == 128
    assert bi.switch_servers == ("0", "1", "2", "3")
    assert tile_url(bi, 16857, 11990, 15, switch=0) == BING_URL
    hosts = {tile_url(bi, 16857, 11990, 15, switch=k).split("/")[2] for k in range(8)}
    assert hosts == {f"ecn.t{k}.tiles.virtualearth.net" for k in range(4)}
    # default switch is deterministic: (x + y) % 4
    assert tile_url(bi, 16857, 11990, 15) == tile_url(
        bi, 16857, 11990, 15, switch=(16857 + 11990) % 4
    )


def test_bing_placeholder_rule(registry: dict[str, Provider]) -> None:
    rule = registry["BI"].placeholder
    assert rule == PlaceholderRule(
        header_name="X-VE-Tile-Info",
        header_value="no-tile",
        size=1033,
        blake3="1abc2dcd9d153b229306d34096a74a2e97587a1b5a74f00b75990207d3e0b484",
    )
    assert registry["Arc"].placeholder == PlaceholderRule(size=2521)
    assert registry["Arc@"].placeholder is None


def test_wmts_templates_pad_the_zoom(registry: dict[str, Provider]) -> None:
    for code in ("NL", "PDOK", "PDOK18", "PDOK19", "PDOK20"):
        p = registry[code]
        assert p.max_zl == 19
        assert p.extent == "Netherlands"
        assert p.extent_bounds == (3.06, 50.72, 7.26, 53.76)
        url = tile_url(p, 264, 168, 9)
        assert "&TILEMATRIX=09&TILEROW=168&TILECOL=264" in url
        assert "&TILEMATRIX=15&" in tile_url(p, 16850, 10810, 15)
    assert "LAYER=Actueel_ortho25&" in tile_url(registry["PDOK"], 1, 1, 10)
    assert "LAYER=2018_ortho25&" in tile_url(registry["PDOK18"], 1, 1, 10)


def test_audit_urls_for_the_other_providers(registry: dict[str, Provider]) -> None:
    """The exact URLs the P0 audit sent (docs/providers-audit.md), modulo the http->https change."""
    assert (
        tile_url(registry["Arc"], 16857, 11990, 15)
        == "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/15/11990/16857"
    )
    assert (
        tile_url(registry["USGS"], 7775, 12511, 15)
        == "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/15/12511/7775"
    )
    assert (
        tile_url(registry["JP"], 29106, 12903, 15)
        == "https://cyberjapandata.gsi.go.jp/xyz/ort/15/29106/12903.jpg"
    )
    assert (
        tile_url(registry["Lux"], 16941, 11168, 15, switch=0)
        == "https://wmts1.geoportail.lu/opendata/wmts/ortho_latest/GLOBAL_WEBMERCATOR_4_V3/15/16941/11168.jpeg"
    )
    assert tile_url(registry["SP"], 16047, 12355, 15).endswith(
        "TILEMATRIX=15&TILEROW=12355&TILECOL=16047"
    )


def test_every_shipped_source_is_fetched_over_https() -> None:
    """The README says the downloads go over HTTPS: Spain's IGN was the last source over http://,
    and its server answers the same image over https:// (2026-09-22). A source a user adds may
    still be http://, so the shipped file alone is read here."""
    import orthostudio.imagery

    shipped = load_registry(Path(orthostudio.imagery.__file__).parent / "registry.toml")
    assert (
        shipped
        and [c for c, p in shipped.items() if not p.url_template.startswith("https://")] == []
    )


def test_tile_url_rejects_tiles_outside_the_grid(registry: dict[str, Provider]) -> None:
    with pytest.raises(ValueError):
        tile_url(registry["BI"], 2**15, 0, 15)
    with pytest.raises(ValueError):
        tile_url(registry["BI"], 0, -1, 15)


def test_load_registry_from_file_and_errors(tmp_path: Path) -> None:
    good = tmp_path / "r.toml"
    good.write_text(
        'schema = 1\n[providers.X]\nurl_template = "https://h/{zoom}/{x}/{-y}.png"\nmax_zl = 12\n'
    )
    reg = load_registry(good)
    assert list(reg) == ["X"]
    assert tile_url(reg["X"], 1, 2, 3) == "https://h/3/1/5.png"
    assert reg["X"].max_in_flight == 64 and reg["X"].headers == {}

    bad_schema = tmp_path / "s.toml"
    bad_schema.write_text('schema = 2\n[providers.X]\nurl_template = "https://h/{x}"\nmax_zl = 1\n')
    with pytest.raises(ValueError, match="schema"):
        load_registry(bad_schema)

    typo = tmp_path / "t.toml"
    typo.write_text(
        'schema = 1\n[providers.X]\nurl_template = "https://h/{x}"\nmax_zl = 1\nmaxzl = 2\n'
    )
    with pytest.raises(ValueError, match="maxzl"):
        load_registry(typo)

    leftover = tmp_path / "l.toml"
    leftover.write_text(
        'schema = 1\n[providers.X]\nurl_template = "https://h/{z}/{x}"\nmax_zl = 1\n'
    )
    with pytest.raises(ValueError, match=r"\{z\}"):
        load_registry(leftover)

    empty = tmp_path / "e.toml"
    empty.write_text("schema = 1\n")
    with pytest.raises(ValueError, match="providers"):
        load_registry(empty)


def test_provider_model_is_frozen_and_strict() -> None:
    p = Provider(code="X", url_template="https://h/{x}/{y}/{zoom}", max_zl=10)
    with pytest.raises(Exception, match="frozen"):
        p.max_zl = 11  # type: ignore[misc]
    with pytest.raises(ValueError):
        Provider(code="bad code", url_template="https://h/{x}", max_zl=10)
    with pytest.raises(ValueError):
        Provider(code="X", url_template="https://h/{x}", max_zl=10, extent_bounds=(7, 50, 3, 53))
    with pytest.raises(ValueError):
        PlaceholderRule(blake3="xyz")


# ---------------------------------------------------------------- grammar


def test_grammar_placeholders() -> None:
    p = Provider(
        code="G",
        url_template="{zoom}/{zoom:02d}/{x}/{y}/{-y}/{|y|}/{quadkey}/{switch: a , b}",
        max_zl=20,
    )
    assert tile_url(p, 3, 5, 3, switch=0) == "3/03/3/5/2/4/213/a"
    assert tile_url(p, 3, 5, 3, switch=1) == "3/03/3/5/2/4/213/b"
    assert tile_url(p, 3, 5, 3) == "3/03/3/5/2/4/213/a"  # (3 + 5) % 2 == 0
    assert set(PLACEHOLDERS) >= {"{zoom}", "{x}", "{y}", "{-y}", "{|y|}", "{quadkey}", "{switch:"}
    assert p.switch_servers == ("a", "b")
    assert Provider(code="N", url_template="https://h/{x}", max_zl=1).switch_servers == ()


# ---------------------------------------------------------------- placeholders


def test_is_placeholder_signals() -> None:
    body = b"\x89PNG" + b"\0" * 1029
    digest = blake3.blake3(body).hexdigest()
    p = Provider(
        code="B",
        url_template="https://h/{quadkey}",
        max_zl=19,
        placeholder=PlaceholderRule(
            header_name="X-VE-Tile-Info", header_value="no-tile", size=1033, blake3=digest
        ),
    )
    assert is_placeholder(p, {"x-ve-tile-info": "No-Tile"}, b"anything") == "header"
    assert is_placeholder(p, {"X-VE-Tile-Info": "other"}, body) == "blake3"
    assert is_placeholder(p, {}, body) == "blake3"
    assert is_placeholder(p, {}, b"x" * 1033) == "size"
    assert is_placeholder(p, {}, b"x" * 1034) is None
    size_only = p.model_copy(update={"placeholder": PlaceholderRule(size=2521)})
    assert is_placeholder(size_only, {"X-VE-Tile-Info": "no-tile"}, b"x" * 2521) == "size"
    assert is_placeholder(size_only, {}, b"x" * 2520) is None
    none = p.model_copy(update={"placeholder": None})
    assert is_placeholder(none, {"X-VE-Tile-Info": "no-tile"}, body) is None
    # a header name without value constraint matches on presence
    presence = p.model_copy(update={"placeholder": PlaceholderRule(header_name="X-No-Data")})
    assert is_placeholder(presence, {"X-No-Data": "1"}, b"") == "header"


# ---------------------------------------------------------------- .lay reader


# ---------------------------------------------------------------- coverage and the user's sources


def test_a_source_covers_the_tiles_its_rectangle_meets(registry: dict[str, Provider]) -> None:
    pdok, bing = registry["PDOK20"], registry["BI"]
    assert pdok.covers(52, 4) and pdok.covers(50, 3)  # a corner of the rectangle
    assert not pdok.covers(27, 34) and not pdok.covers(53, 7.26)  # Egypt; east of the rectangle
    assert bing.covers(27, 34) and bing.covers(-85, -180)
    assert registry["JP"].covers(35, 139) and not registry["JP"].covers(46, 6)
    assert registry["USGS"].covers(40, -105) and registry["USGS"].covers(61, -150)
    assert not registry["USGS"].covers(46, 6)
    assert registry["NL"].same_as == "PDOK" and registry["BI"].name == "Bing Maps"


def test_the_sources_a_user_adds_are_read_saved_and_never_replace_a_shipped_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.imagery.providers import (
        new_source_code,
        read_user_sources,
        save_user_sources,
        user_sources_path,
    )

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    path = user_sources_path()
    assert path == tmp_path / "home" / "sources.toml" and read_user_sources() == ({}, [])
    mine = Provider(code="Mine", name="Mine «sat»", attribution="Mine «sat»", custom=True,
                    url_template='https://h/{zoom}/{x}/{y}.jpg?k="a"', max_zl=20)  # fmt: skip
    save_user_sources({"Mine": mine})
    sources, problems = read_user_sources()
    assert problems == [] and sources["Mine"] == mine.model_copy(update={"max_in_flight": 64})
    assert load_registry()["Mine"].custom and list(load_registry())[:3] == ["BI", "Arc", "Arc@"]
    assert "BI" in load_registry() and not load_registry()["BI"].custom

    # a broken entry is left out, and so is a shipped code; the others stay usable
    text = path.read_text("utf-8")
    text += '\n[providers.BI]\nurl_template = "https://evil/{quadkey}"\nmax_zl = 19\n'
    text += '\n[providers.Broken]\nurl_template = "https://h/{nothing}"\nmax_zl = 19\n'
    path.write_text(text, "utf-8")
    sources, problems = read_user_sources()
    assert list(sources) == ["Mine"] and len(problems) == 2
    assert problems[0].startswith("BI:") and problems[1].startswith("Broken:")
    assert load_registry()["BI"].url_template.startswith("https://ecn.")
    path.write_text("schema = [", "utf-8")  # not TOML: nothing, and one problem
    assert read_user_sources()[0] == {} and len(read_user_sources()[1]) == 1

    assert new_source_code("Google satellite!", {}) == "Googlesatellite"
    assert new_source_code("Mine", {"Mine": 1, "Mine_2": 1}) == "Mine_3"
    assert new_source_code("PDOK20", {}) == "PDOK20_2" and new_source_code("  ", {}) == "Source"
