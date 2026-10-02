# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's folder kept out of Windows Search. Its hundreds of thousands of files (a region's terrain,
the pack's textures) kept the indexer busy for whole test flights on a user's Shadow PC: half a core
and more, all along (2026-10-02). No mark on the files keeps them out: Windows Search still lists
those marked not to be content-indexed (it only skips reading them), hidden or system, all tried on
Windows 11. A rule of the indexer's crawl scope does, and a program running for the user may add
one without an administrator's rights, as the Indexing Options do: ISearchCrawlScopeManager's
AddUserScopeRule, tried there unelevated; the folder's items leave the index within a minute.

Windows only, and never in Live's way: where the indexer is off, or does not answer within
TIMEOUT_S, nothing is done."""

from __future__ import annotations

import contextlib
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

WINDOWS = sys.platform == "win32"
TIMEOUT_S = 15.0
CLSID_SEARCH_MANAGER = "7D096C5F-AC08-4F1F-BEB7-5C22C517CE39"
IID_SEARCH_MANAGER = "AB310581-AC80-11D1-8DF3-00C04FB6EF69"
# the methods' places in their interfaces' tables (searchapi.h), IUnknown's three first
RELEASE = 2
GET_CATALOG = 10  # ISearchManager
GET_CRAWL_SCOPE_MANAGER = 28  # ISearchCatalogManager
ADD_USER_SCOPE_RULE = 8  # ISearchCrawlScopeManager
REMOVE_SCOPE_RULE = 9
INCLUDED_IN_CRAWL_SCOPE = 13
SAVE_ALL = 16
CLSCTX_LOCAL_SERVER = 4
COINIT_MULTITHREADED = 0


def folder_url(folder: PurePath) -> str:
    """The indexer's URL of a folder: ``file:///C:\\Users\\me\\.orthostudio\\live\\``."""
    return "file:///" + str(folder).rstrip("\\/") + "\\"


def rule_url(folder: PurePath) -> str:
    """The rule's pattern: everything in the folder."""
    return folder_url(folder) + "*"


class _Scope:
    """The indexer's crawl scope, through its COM interface (an ISearchCrawlScopeManager)."""

    def __init__(self, pointer: Any):
        self.pointer = pointer

    def _method(self, index: int, *argtypes):
        import ctypes

        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        call = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])  # type: ignore[attr-defined]
        return lambda *args: call(self.pointer, *args)

    def included(self, folder: Path) -> bool | None:
        import ctypes
        import ctypes.wintypes as wt

        answer = wt.BOOL()
        hr = self._method(INCLUDED_IN_CRAWL_SCOPE, wt.LPCWSTR, ctypes.POINTER(wt.BOOL))(
            folder_url(folder), ctypes.byref(answer)
        )
        return bool(answer.value) if hr == 0 else None

    def exclude(self, folder: Path) -> bool:
        import ctypes.wintypes as wt

        add = self._method(ADD_USER_SCOPE_RULE, wt.LPCWSTR, wt.BOOL, wt.BOOL, wt.DWORD)
        # excluded, over the rules of its subfolders, without following complex URLs
        return add(rule_url(folder), False, True, 0) == 0 and self._method(SAVE_ALL)() == 0

    def remove(self, folder: Path) -> bool:
        import ctypes.wintypes as wt

        removed = self._method(REMOVE_SCOPE_RULE, wt.LPCWSTR)(rule_url(folder)) == 0
        return removed and self._method(SAVE_ALL)() == 0


def _guid(text: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("raw", ctypes.c_ubyte * 16)]

    g = GUID()
    ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
    return g


def _with_scope(work: Callable[[_Scope], Any]) -> Any:
    """``work(scope)`` with the indexer's crawl scope, in a thread of its own, in COM's
    multithreaded apartment whatever the caller's; None where the indexer is not there or does not
    answer within TIMEOUT_S."""
    import ctypes

    result: list = []

    def run() -> None:
        ole32 = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
        if ole32.CoInitializeEx(None, COINIT_MULTITHREADED) < 0:
            return
        held: list = []
        try:
            manager = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(_guid(CLSID_SEARCH_MANAGER)), None, CLSCTX_LOCAL_SERVER,
                ctypes.byref(_guid(IID_SEARCH_MANAGER)), ctypes.byref(manager),
            )  # fmt: skip
            if hr != 0 or not manager.value:
                return  # the indexer is off, or not on this Windows
            held.append(manager)
            catalog, scope = ctypes.c_void_p(), ctypes.c_void_p()
            get_catalog = _Scope(manager)._method(
                GET_CATALOG, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)
            )
            if get_catalog("SystemIndex", ctypes.byref(catalog)) != 0 or not catalog.value:
                return
            held.append(catalog)
            get_scope = _Scope(catalog)._method(
                GET_CRAWL_SCOPE_MANAGER, ctypes.POINTER(ctypes.c_void_p)
            )
            if get_scope(ctypes.byref(scope)) != 0 or not scope.value:
                return
            held.append(scope)
            result.append(work(_Scope(scope)))
        finally:
            for pointer in reversed(held):
                with contextlib.suppress(OSError):
                    _Scope(pointer)._method(RELEASE)()
            ole32.CoUninitialize()

    thread = threading.Thread(target=run, name="Windows Search's scope", daemon=True)
    thread.start()
    thread.join(TIMEOUT_S)
    return result[0] if result else None


def keep_out(folder: Path, log: Callable[[str], None] | None = None) -> bool:
    """``folder`` (Live's) out of Windows Search, by a rule of its own, added once: True when it
    was added now. Nothing elsewhere than on Windows, nor where the folder is out already (on a
    disk the indexer does not crawl, under AppData...) or the indexer is off."""
    if not WINDOWS:
        return False

    def work(scope: _Scope) -> bool:
        return scope.included(folder) is True and scope.exclude(folder)

    added = bool(_with_scope(work))
    if added and log is not None:
        log(f"Live's folder kept out of Windows Search, its files too many to list: {folder}")
    return added


def included(folder: Path) -> bool | None:
    """Whether Windows Search crawls ``folder``; None off Windows or where the indexer is off."""
    return _with_scope(lambda scope: scope.included(folder)) if WINDOWS else None


def let_in(folder: Path) -> bool:
    """``folder``'s rule taken out again (for the tests: Live itself keeps its folder out)."""
    return bool(_with_scope(lambda scope: scope.remove(folder))) if WINDOWS else False
