# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A relief file downloaded as it arrives (``dem.sources.http_download``, ``dem.md`` 3.5).

It went through the imagery's fetcher, which cut every transfer at 30 s whole and started a
second copy of it after 3 s: a USGS square of 443 MB never arrived on a line under some
215 Mbit/s, and shared with the images, a test's tile failed on it (2026-09-27). A local server
stands for the USGS here: a file still arriving is waited for, a silent server is given up, the
answers keep the codes the fetcher gave them, and Cancel stops a transfer at once.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from orthostudio.dem import sources
from orthostudio.errors import OsxpError
from orthostudio.net import fetch

BODY = bytes(range(256)) * 160  # 40 KiB


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    asked: ClassVar[Counter[str]] = Counter()
    cut: ClassVar[set[str]] = set()  # the paths whose client went away in the middle of the file
    lock = threading.Lock()

    def log_message(self, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        with self.lock:
            self.asked[self.path] += 1
            count = self.asked[self.path]
        if self.path == "/missing":
            self._answer(404, b"<Error>NoSuchKey</Error>")
        elif self.path == "/busy" and count < 3:
            self._answer(503, b"slow down")
        elif self.path == "/refused" or (self.path == "/pushed" and count < 6):
            self._answer(429, b"too many", retry_after="0")
        elif self.path in ("/busy", "/pushed", "/whole"):
            self._answer(200, BODY)
        elif self.path == "/slow":  # slow and steady: 40 pieces, 0.1 s apart, longer than the limit
            self._trickle(BODY, 40)
        elif self.path == "/late":  # the headers after a second, then ten seconds of pieces
            time.sleep(1.0)
            self._trickle(BODY * 5, 100)
        elif self.path in ("/stall", "/silent"):  # a first piece, or none, then nothing
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            if self.path == "/stall":
                self.wfile.write(BODY[:1024])
            self.wfile.flush()
            time.sleep(60.0)

    def _answer(self, status: int, body: bytes, *, retry_after: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if retry_after is not None:
            self.send_header("Retry-After", retry_after)
        self.end_headers()
        self.wfile.write(body)

    def _trickle(self, body: bytes, pieces: int) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        size = len(body) // pieces
        try:
            for i in range(pieces):
                self.wfile.write(body[i * size : (i + 1) * size])
                self.wfile.flush()
                time.sleep(0.1)
        except ConnectionError:  # Windows may say ConnectionAbortedError (WinError 10053)
            with self.lock:
                self.cut.add(self.path)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # a client that left in the middle of a file: what the cancel tests ask for


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # a silence of 2 s rather than 30: curl adds the connection's limit to the read's
    monkeypatch.setattr(sources, "RELIEF_CONNECT_S", 1.0)
    monkeypatch.setattr(sources, "_RELIEF_BACKOFF_S", (0.1,))
    _Handler.asked = Counter()
    _Handler.cut = set()
    httpd = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        sources._RELIEF_HTTP.close()  # no connection kept to a server that is gone


def test_a_file_still_arriving_is_waited_for(server: str) -> None:
    """Four seconds of steady pieces against a silence limit of two: the whole file, from one
    transfer. The fetcher cut it at its limit, and had asked for it twice by then."""
    started = time.monotonic()
    got = sources.http_download(f"{server}/slow", timeout_s=2.0)
    took = time.monotonic() - started
    assert got.ok and got.body == BODY and got.status == 200
    assert took > 3.0, "longer than the silence limit, and not cut"
    assert _Handler.asked["/slow"] == 1, "one transfer, no second copy"


def test_a_file_that_arrives_says_how_much_of_it_is_in(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page showed a relief's rate only once the whole file was in, then kept it over the
    45 s the file took to read (a user, 2026-09-29). The transfer now says when the file starts
    to arrive, with its announced size, then how much of it is in, spaced by RELIEF_PROGRESS_S."""
    monkeypatch.setattr(sources, "RELIEF_PROGRESS_S", 0.5)
    heard: list[tuple[float, int, int | None]] = []
    started = time.monotonic()
    got = sources.http_download(
        f"{server}/slow",
        timeout_s=2.0,
        on_bytes=lambda received, size: heard.append((time.monotonic(), received, size)),
    )
    assert got.ok and got.body == BODY
    assert heard[0][1:] == (0, len(BODY)), "the start, with the size the server announced"
    counts = [received for _, received, _ in heard]
    assert counts == sorted(counts) and 0 < counts[-1] < len(BODY)
    assert all(size == len(BODY) for _, _, size in heard)
    took = time.monotonic() - started
    assert 4 <= len(heard) <= took / 0.5 + 2, f"{len(heard)} words in {took:.1f} s"


def test_a_silent_server_is_given_up_and_asked_again(server: str) -> None:
    started = time.monotonic()
    got = sources.http_download(f"{server}/silent", timeout_s=2.0, max_attempts=2)
    took = time.monotonic() - started
    assert not got.ok and got.error == "NET_TIMEOUT" and not got.final
    assert _Handler.asked["/silent"] == 2, "each attempt one transfer"
    assert took < 12.0, f"waited {took:.1f} s for two silences of 2 s"


def test_a_transfer_that_stops_midway_is_given_up_long_before_the_server(server: str) -> None:
    """curl averages the speed over some six seconds: a transfer that stops after a first piece
    is declared silent about that long after the limit (the library's reads alike). Still far
    from the minute the server would hold it: 30 s of silence become about 36 in a build."""
    started = time.monotonic()
    got = sources.http_download(f"{server}/stall", timeout_s=2.0, max_attempts=1)
    took = time.monotonic() - started
    assert got.error == "NET_TIMEOUT" and _Handler.asked["/stall"] == 1
    assert took < 15.0, f"waited {took:.1f} s"


def test_the_answers_keep_the_fetchers_codes(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = sources.http_download(f"{server}/missing", timeout_s=2.0)
    assert missing.status == 404 and missing.error is None and missing.final  # memoised
    assert _Handler.asked["/missing"] == 1, "the server's word is not asked again"
    busy = sources.http_download(f"{server}/busy", timeout_s=2.0)
    assert busy.ok and busy.body == BODY and _Handler.asked["/busy"] == 3  # two 503, then the file
    # Windows retries a refused connection on the machine itself before it gives up, longer
    # than the fixture's 1 s: it was a timeout there (CI, 2026-09-29). The app gives 10 s.
    monkeypatch.setattr(sources, "RELIEF_CONNECT_S", 5.0)
    down = sources.http_download("http://127.0.0.1:9/nothing", timeout_s=2.0, max_attempts=2)
    assert down.error == "NET_CONNECTION_FAILED" and down.status == 0 and not down.final


def test_a_429_is_obeyed_without_costing_an_attempt_within_its_budget(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fetcher's rule, kept (``net-download.md`` R2): five refusals, two attempts, the file."""
    monkeypatch.setattr(fetch, "RETRY_AFTER_MIN_S", 0.05)
    got = sources.http_download(f"{server}/pushed", timeout_s=2.0, max_attempts=2)
    assert got.ok and got.body == BODY and _Handler.asked["/pushed"] == 6
    # beyond the budget a refusal costs an attempt, and the last one says why
    monkeypatch.setattr(fetch, "MAX_PUSHBACKS", 1)
    refused = sources.http_download(f"{server}/refused", timeout_s=2.0, max_attempts=2)
    assert refused.error == "NET_RATE_LIMITED" and refused.status == 429 and not refused.final
    assert _Handler.asked["/refused"] == 3, "one obeyed, then two attempts"


def _cut(path: str, within_s: float) -> bool:
    """Whether the server saw the client go away from ``path`` within ``within_s``."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if path in _Handler.cut:
            return True
        time.sleep(0.05)
    return False


def test_cancel_stops_a_transfer_at_once(server: str) -> None:
    """A file of 400 MB is not cut at 30 s any more: Cancel must not wait for its end, and the
    transfer must not go on behind it (the server sees the client leave)."""
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(OsxpError) as err:
        sources.http_download(f"{server}/slow", timeout_s=2.0, cancel=cancel)
    assert err.value.code == "SYS_CANCELLED"
    assert time.monotonic() - started < 1.5
    assert _cut("/slow", 2.0), "the file went on arriving after the cancel"
    cancel.set()
    with pytest.raises(OsxpError, match="SYS_CANCELLED"):  # already cancelled: nothing is asked
        sources.http_download(f"{server}/whole", timeout_s=2.0, cancel=cancel)
    assert _Handler.asked["/whole"] == 0


def test_cancel_before_the_headers_stops_the_transfer_at_them(server: str) -> None:
    """Cancelled while the server has not answered yet: the build moves on at once, and the
    transfer stops when the headers come instead of reading the whole file into memory for
    nobody (curl_cffi goes on with it when the wait for the headers is cancelled)."""
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(OsxpError, match="SYS_CANCELLED"):
        sources.http_download(f"{server}/late", timeout_s=4.0, cancel=cancel)
    assert time.monotonic() - started < 1.0, "the headers were waited for"
    assert _cut("/late", 4.0), "the file went on arriving after the cancel"
