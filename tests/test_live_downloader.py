# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The downloader of Live's photos (orthostudio.live.downloader): its connections renewed when they
stop answering (Calabria, 2026-10-19)."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import ClassVar

import pytest

from orthostudio.live import downloader as downloader_module
from orthostudio.live.cache import IMAGE
from orthostudio.live.downloader import Downloader


class Answer:
    status_code = 200
    content = b"\xff\xd8 a photo"
    headers: ClassVar[dict] = {}


class DeadSession:
    """Connections open and asked on, never answering: curl's requests on them hung until their
    own timeout, and the next went on the same connection."""

    def __init__(self) -> None:
        self.closed = False

    async def get(self, url, headers=None):
        while not self.closed:
            await asyncio.sleep(0.02)
        raise ConnectionError("the session closed")

    async def close(self) -> None:
        self.closed = True


class LiveSession:
    def __init__(self) -> None:
        self.asked = 0

    async def get(self, url, headers=None):
        self.asked += 1
        return Answer()

    async def close(self) -> None:
        pass


class Sessions(Downloader):
    """Its sessions taken from a list: a dead one first, then good ones."""

    made: ClassVar[list] = []

    def _new_session(self):
        session = DeadSession() if not Sessions.made else LiveSession()
        Sessions.made.append(session)
        return session


def test_dead_connections_are_renewed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader_module, "DEAD_S", 0.3)
    monkeypatch.setattr(downloader_module, "WATCH_S", 0.05)
    Sessions.made = []
    came: list = []
    done = threading.Event()
    lines: list[str] = []

    def on_piece(key, status, body) -> None:
        came.append((key, status))
        done.set()

    d = Sessions("BI", on_piece, max_in_flight=4, fallback=None, log=lines.append)
    try:
        t0 = time.monotonic()
        d.submit((16, 34000, 23000), 0.0)
        assert done.wait(5), "the piece never came: the dead session was kept"
        assert came == [((16, 34000, 23000), IMAGE)]
        assert time.monotonic() - t0 >= 0.3  # after DEAD_S without an answer
        assert d.renewed == 1 and Sessions.made[0].closed
        assert any("new ones opened" in line for line in lines)
    finally:
        d.close()


def test_a_quiet_time_is_not_a_dead_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing asked for a while (the aircraft over the sea), then a piece: answered on the same
    connections, never taken for dead ones."""
    monkeypatch.setattr(downloader_module, "DEAD_S", 0.3)
    monkeypatch.setattr(downloader_module, "WATCH_S", 0.05)
    Sessions.made = [None]  # good sessions only
    came = threading.Event()
    d = Sessions("BI", lambda key, status, body: came.set(), max_in_flight=4, fallback=None)
    try:
        time.sleep(0.6)  # quiet, longer than DEAD_S
        d.submit((16, 34000, 23000), 0.0)
        assert came.wait(5)
        time.sleep(0.2)
        assert d.renewed == 0
    finally:
        d.close()
