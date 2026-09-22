"""The certificate bundle every session verifies with (``orthostudio/net/certs.py``).

2026-09-22: the packaged app could not reach ``maps.mail.ru``, the last-resort Overpass mirror,
with "self signed certificate in certificate chain", while the same code reached it from a
development environment. curl_cffi had been left to libcurl's own default store, which in the
app is the system's and holds fewer authorities than the bundle shipped beside it.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import certifi

from orthostudio.net import fetch
from orthostudio.net.certs import ca_bundle
from orthostudio.sources import osm


def test_the_bundle_is_the_one_we_ship() -> None:
    bundle = ca_bundle()
    assert bundle == certifi.where()
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
