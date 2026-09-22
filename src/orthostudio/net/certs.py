"""The certificate authorities OrthoStudio XP trusts.

``curl_cffi`` verifies with libcurl's own default store when nothing is passed, and in the
packaged app that store is the system's, not the one shipped beside it: on 2026-09-22 the app
could not reach ``maps.mail.ru`` at all ("self signed certificate in certificate chain") while
the same code in a development environment reached it, because there another OpenSSL had already
loaded a fuller store. Every session OrthoStudio XP opens therefore names the bundle it ships.
"""

from __future__ import annotations

import functools

__all__ = ["ca_bundle"]


@functools.cache
def ca_bundle() -> str | bool:
    """Path of the certificate bundle to verify with, or ``True`` for libcurl's own default.

    ``True`` only when the bundle is missing, which never happens in a normal install: it keeps
    a broken installation downloading rather than refusing every address.
    """
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi ships with curl_cffi
        return True
    path = certifi.where()
    try:
        with open(path, "rb") as fp:
            return path if fp.read(1) else True
    except OSError:  # pragma: no cover - unreadable bundle
        return True
