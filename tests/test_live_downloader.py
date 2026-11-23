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


class Refused(Answer):
    status_code = 403
    content = b""


class LiveSession:
    """Answering every request with ``answer``; while ``up`` is False, refusing every connection
    (the source answers nothing)."""

    up = True
    answer: ClassVar[type] = Answer

    def __init__(self) -> None:
        self.asked = 0

    async def get(self, url, headers=None):
        self.asked += 1
        if not LiveSession.up:
            raise ConnectionError("curl: (7) Failed to connect: Connection refused")
        return LiveSession.answer()

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


# --- a chosen source that answers nothing at all, said; downloads failing now


def _source():
    from orthostudio.live.providers import Provider

    return Provider(code="SRC", name="Source", url_template="http://source/{zoom}/{x}/{y}",
                    max_zl=19)  # fmt: skip


def _answers():
    """on_piece, and ``wait(n)``: the next n answers it got."""
    import queue

    got: queue.Queue = queue.Queue()

    def wait(n: int) -> list:
        return [got.get(timeout=10) for _ in range(n)]

    return (lambda key, status, body: got.put((key, status, body))), wait


def _until(test, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while not test() and time.monotonic() < end:
        time.sleep(0.02)
    return test()


def _on_good_sessions(monkeypatch: pytest.MonkeyPatch, **limits: float) -> None:
    for name, value in {"WATCH_S": 0.05, **limits}.items():
        monkeypatch.setattr(downloader_module, name, value)
    Sessions.made = [None]  # good sessions only, answering as LiveSession says


def test_a_source_that_answers_nothing_is_said_until_it_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REFUSED_IN_A_ROW requests in a row refused and no answer for DEAD_S: the chosen source
    answers nothing, said once in the log and to the window, downloads failing, until it answers
    again."""
    _on_good_sessions(monkeypatch, DEAD_S=0.5, REFUSED_IN_A_ROW=4)
    monkeypatch.setattr(LiveSession, "up", False)
    lines: list[str] = []
    on_piece, wait = _answers()
    d = Sessions(_source(), on_piece, max_in_flight=8, attempts=1, fallback=None, log=lines.append)
    try:
        for x in range(6):
            d.submit((16, x, 7), float(x))
        wait(6)
        assert _until(lambda: d.unanswered() is not None)
        assert d.unanswered() == "Source" and d.failing()
        said = [line for line in lines if "does not answer" in line]
        assert len(said) == 1 and "curl: (7)" in said[0]
        assert "stop Live and choose another source" in said[0]
        monkeypatch.setattr(LiveSession, "up", True)
        d.submit((16, 9, 7), 0.0)
        assert wait(1)[0][1] == IMAGE
        assert _until(lambda: d.unanswered() is None)
        assert "  Source answers again" in lines
    finally:
        d.close()


def test_a_quiet_time_then_a_moment_the_line_stops_is_not_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answer, a quiet time longer than DEAD_S (over the sea, X-Plane loading, the Mac
    asleep), then a few requests failing: counted from when they began, not from the answer
    before the quiet time; said only once they fail for DEAD_S (the review of 2026-11-23). DEAD_S
    1 s: a failed try waits 0.5 s before its piece counts as failed."""
    _on_good_sessions(monkeypatch, DEAD_S=1.0, REFUSED_IN_A_ROW=4)
    on_piece, wait = _answers()
    d = Sessions(_source(), on_piece, max_in_flight=8, attempts=1, fallback=None)
    try:
        d.submit((16, 0, 7), 0.0)
        wait(1)  # an answer
        time.sleep(1.2)  # a quiet time
        monkeypatch.setattr(LiveSession, "up", False)  # the line drops a moment
        t0 = time.monotonic()
        for x in range(1, 5):
            d.submit((16, x, 7), float(x))
        wait(4)
        assert not _until(lambda: d.unanswered() is not None, timeout=0.2)
        x = 5
        while time.monotonic() - t0 < 1.3:  # failing on: said DEAD_S after they began
            d.submit((16, x, 7), float(x))
            wait(1)
            x += 1
        assert _until(lambda: d.unanswered() is not None, timeout=1.0)
    finally:
        d.close()


def test_silent_through_a_quiet_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silent, a quiet time longer than DEAD_S (X-Plane loading, the textures built again later),
    then requests failing still: silent all along, never « answers again » without an answer (the
    review of 2026-11-23)."""
    _on_good_sessions(monkeypatch, DEAD_S=1.0, REFUSED_IN_A_ROW=4)
    monkeypatch.setattr(LiveSession, "up", False)
    lines: list[str] = []
    on_piece, wait = _answers()
    d = Sessions(_source(), on_piece, max_in_flight=8, attempts=1, fallback=None, log=lines.append)
    try:
        x, t0 = 0, time.monotonic()
        while time.monotonic() - t0 < 1.3:
            for i in range(8):
                d.submit((16, x + i, 7), float(x + i))
            wait(8)
            x += 8
        assert _until(lambda: d.unanswered() is not None, timeout=1.0)
        time.sleep(1.5)  # a quiet time, the source still answering nothing
        for i in range(8):
            d.submit((16, 999 + i, 7), 0.0)
        wait(8)
        time.sleep(0.3)
        assert d.unanswered() == "Source" and d.failing()
        assert not [line for line in lines if "answers again" in line]
    finally:
        d.close()


def test_a_refusal_is_the_refusal_s_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """403s in a row: refusing this computer, said as such, never « does not answer »."""
    _on_good_sessions(monkeypatch, DEAD_S=0.2)
    monkeypatch.setattr(LiveSession, "answer", Refused)
    lines: list[str] = []
    on_piece, wait = _answers()
    d = Sessions(_source(), on_piece, max_in_flight=64, attempts=1, fallback=None, log=lines.append)
    try:
        n = downloader_module.REFUSED_IN_A_ROW + 8
        for x in range(n):
            d.submit((16, x, 7), float(x))
        wait(n)
        time.sleep(0.4)
        assert d.refusing() == ["Source"] and d.unanswered() is None
        assert not [line for line in lines if "does not answer" in line]
    finally:
        d.close()


def test_downloads_failing_now_whatever_came_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failing: a piece failed within FAILING_S and none came in it, whatever came before (13
    pieces early in a flight, then 12,928 failed, and the window never said it)."""
    _on_good_sessions(monkeypatch, FAILING_S=0.3)
    on_piece, wait = _answers()
    d = Sessions(_source(), on_piece, max_in_flight=2, attempts=1, fallback=None)
    try:
        assert not d.failing()
        d.submit((16, 1, 7), 0.0)
        wait(1)
        monkeypatch.setattr(LiveSession, "up", False)
        time.sleep(0.35)  # what came is older than FAILING_S
        d.submit((16, 2, 7), 0.0)
        wait(1)
        assert d.downloaded == 1 and d.failing()
        monkeypatch.setattr(LiveSession, "up", True)
        d.submit((16, 3, 7), 0.0)
        wait(1)
        assert not d.failing()
    finally:
        d.close()


def test_not_failing_while_pieces_come() -> None:
    """A country's source answering nothing while the pieces beyond its bounds keep coming:
    downloads are not failing (the review of 2026-11-23)."""
    Sessions.made = [None]
    d = Sessions(_source(), lambda *a: None, max_in_flight=2, fallback=None)
    try:
        d._silent_now = True
        assert d.failing()  # nothing came
        d._came_at = time.monotonic()  # a piece just came
        assert not d.failing()
    finally:
        d.close()
