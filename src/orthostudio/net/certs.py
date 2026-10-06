# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The certificate authorities OrthoStudio XP trusts.

``curl_cffi`` verifies with libcurl's own default store when nothing is passed, and in the
packaged app that store is the system's, not the one shipped beside it: on 2026-09-22 the app
could not reach ``maps.mail.ru`` at all ("self signed certificate in certificate chain") while
the same code in a development environment reached it, because there another OpenSSL had already
loaded a fuller store. Every session OrthoStudio XP opens therefore names the bundle it ships.

On Windows libcurl opens that bundle with the C library, which reads a file's name in the
system's ANSI code page, while curl_cffi writes the name in Python's preferred encoding: UTF-8 in
Python's UTF-8 mode (``PYTHONUTF8=1``, the default from Python 3.15). A letter outside ASCII in
the bundle's path, a user's name with an accent, then fails every HTTPS request at once (curl's
error 77): on 2026-10-04 a test copy in a folder named « ... fenêtre ... » downloaded nothing on
a user's Windows PC. Such a path is given by its short (8.3) name, which has none.
"""

from __future__ import annotations

import functools
import sys
from typing import Any

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
            if not fp.read(1):
                return True
    except OSError:  # pragma: no cover - unreadable bundle
        return True
    return libcurl_name(path)


def libcurl_name(path: str) -> str:
    """``path``, or the short (8.3) name of the same file where libcurl could not open it: on
    Windows, a path with a letter outside ASCII. ``path`` itself when Windows keeps no short name
    there (none made on that disk): curl_cffi still finds it outside Python's UTF-8 mode."""
    if sys.platform != "win32" or path.isascii():
        return path
    short = short_name(path)
    return short if short and short.isascii() else path


@functools.cache
def _kernel32() -> Any:
    """kernel32, its argument types declared on a private instance (``ctypes.windll``'s is shared
    with every other module)."""
    import ctypes
    from ctypes import wintypes

    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetShortPathNameW.restype = wintypes.DWORD
    return kernel32


def short_name(path: str) -> str | None:
    """The short (8.3) name Windows keeps for ``path``, an existing file's, or None."""
    import ctypes

    get = _kernel32().GetShortPathNameW
    size = get(path, None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    return buffer.value if get(path, buffer, size) else None
