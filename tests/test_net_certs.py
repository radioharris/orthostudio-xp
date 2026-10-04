# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The certificate bundle every session verifies with (``orthostudio/net/certs.py``).

2026-09-22: the packaged app could not reach ``maps.mail.ru``, the last-resort Overpass mirror,
with "self signed certificate in certificate chain", while the same code reached it from a
development environment. curl_cffi had been left to libcurl's own default store, which in the
app is the system's and holds fewer authorities than the bundle shipped beside it.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import certifi
import pytest

from orthostudio.net import certs, fetch
from orthostudio.net.certs import ca_bundle
from orthostudio.sources import osm


def test_the_bundle_is_the_one_we_ship() -> None:
    bundle = ca_bundle()
    assert bundle == certs.libcurl_name(certifi.where())
    text = Path(str(bundle)).read_text("utf-8", "replace")
    assert text.count("BEGIN CERTIFICATE") > 100
    # the authority of the last-resort mirror, absent from the system store of the packaged app
    assert "HARICA" in text


def test_a_missing_bundle_falls_back_to_libcurl(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(certifi, "where", lambda: "/nowhere/cacert.pem")
    ca_bundle.cache_clear()
    try:
        assert ca_bundle() is True  # a broken install still downloads
    finally:
        ca_bundle.cache_clear()


def test_every_session_names_the_bundle() -> None:
    """The two places that open a curl session: the imagery fetcher and the Overpass client."""
    assert "verify=ca_bundle()" in inspect.getsource(fetch.Fetcher._ensure_session)
    assert "verify=ca_bundle()" in inspect.getsource(osm.CurlTransport._get_session)


def test_a_path_libcurl_cannot_open_is_given_by_its_short_name(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """On Windows libcurl reads a file's name in the ANSI code page, curl_cffi writes it in
    Python's preferred encoding, UTF-8 in Python's UTF-8 mode: a test copy in a folder named
    « ... fenêtre ... » failed every request (curl's error 77, 2026-10-04)."""
    asked: list[str] = []

    def short_name(path: str) -> str | None:
        asked.append(path)
        return "C:\\Users\\HLNE~1\\cacert.pem"

    monkeypatch.setattr(certs.sys, "platform", "win32")
    monkeypatch.setattr(certs, "short_name", short_name)
    assert (
        certs.libcurl_name("C:\\Users\\H\u00e9l\u00e8ne\\cacert.pem")
        == "C:\\Users\\HLNE~1\\cacert.pem"
    )
    assert certs.libcurl_name("C:\\Users\\Helene\\cacert.pem") == "C:\\Users\\Helene\\cacert.pem"
    assert asked == ["C:\\Users\\H\u00e9l\u00e8ne\\cacert.pem"]  # an ASCII path is not looked up
    monkeypatch.setattr(certs, "short_name", lambda path: None)  # no short names on that disk
    assert (
        certs.libcurl_name("D:\\H\u00e9l\u00e8ne\\cacert.pem") == "D:\\H\u00e9l\u00e8ne\\cacert.pem"
    )
    monkeypatch.setattr(certs.sys, "platform", "darwin")  # elsewhere a name is read as written
    assert (
        certs.libcurl_name("/Users/h\u00e9l\u00e8ne/cacert.pem")
        == "/Users/h\u00e9l\u00e8ne/cacert.pem"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' short names")
def test_a_short_name_on_windows(tmp_path: Path) -> None:
    pem = tmp_path / "fen\u00eatre" / "cacert.pem"
    pem.parent.mkdir()
    pem.write_bytes(b"-----BEGIN CERTIFICATE-----")
    short = certs.short_name(str(pem))
    if short is None or not short.isascii():
        pytest.skip("no short names on this disk")
    assert Path(short).read_bytes() == pem.read_bytes()
    assert certs.libcurl_name(str(pem)) == short
