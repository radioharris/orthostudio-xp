# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The gate: X-Plane goes on loading a flight only once the ortho around its start is built.

OrthoStudio XP Live's X-Plane plugin (``native/xplane_plugin/live_gate.c``, put in X-Plane by
``orthostudio.live.plugin``) talks to this gate while X-Plane loads a flight's aircraft, before
X-Plane reads the scenery. It sends one line per datagram over UDP on 127.0.0.1:GATE_PORT:

- ``LIVE1 LOAD lat lon``: a flight is loading there (PLANE_LOADED); no answer is expected.
- ``LIVE1 HOLD lat lon why``: X-Plane waits for the answer. ``why`` is ``flight`` at message 115,
  where a new flight starts loading, and ``aircraft`` at the aircraft's sounds or livery, which a
  livery changed in flight also sends. The gate sends ``LIVE1 GO n`` once every texture X-Plane
  will read there is built. Otherwise it sends, for instance,
  ``LIVE1 WAIT n=12 p=450 s=23 mbs=15.2 lang=en at=LFMN runway 04R``:
  - n is how many textures are left;
  - p is the progress in thousandths, counted from the first moment the start was known;
  - s is the seconds since then;
  - mbs is the download rate, in megabytes a second;
  - lang is the Live window's language, ``en`` or ``fr``, which the plugin's words take;
  - at is the start's name in that language (``LFMN piste 04R``), the rest of the line.

  The plugin's loading window shows all of it.

- ``LIVE1 ASK``, every second of a flight: whether the streamer has a notice for the pilot. Where
  the aircraft has no mesh under it but a region has one, the gate answers ``LIVE1 NOTE id lang=fr
  text`` ("Live : pas de mesh ici, installez Amérique du Nord dans la fenêtre de Live"), which the
  plugin shows in a small panel with its cross; ``id`` tells one notice from the next. Else
  ``LIVE1 NONE lang=en``. (It offered a reload of the scenery too, for the 8192 px textures built
  after a start, until a test showed X-Plane's reload does not read again the textures it holds,
  2026-09-27.)

Lines go out in UTF-8, never cut inside a character: the plugin shows French with its accents.

The plugin lets X-Plane go when its first ``HOLD`` gets no answer within a second: no streamer.
The streamer's loop, which answers, can be busy longer than that: a new start planned under a
start's load kept it 3 s, and X-Plane went on at LGAV and LCLK with nothing built (2026-09-29).
So while the loop is late reading the plugin's lines (``STAND_IN_S``), the gate reads them itself,
keeps them for the loop and answers a ``HOLD`` with the loop's last ``WAIT`` (the start's name
and figures as the loop last said them): X-Plane waits on. Never for a loop gone more than
``ALIVE_S``: then X-Plane goes on, as without a streamer.

The start is known even before the plugin speaks. When the user clicks Start Flight, X-Plane saves
it in ``Output/preferences/Freeflight.prf`` (``_last_start``, a JSON object), and the streamer
starts building right then (hold test, docs/specs/live/hold-test.md).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .xpudp import no_port_unreachable

GATE_PORT = 49250
STAND_IN_S = 0.5  # the loop this late reading the plugin's lines: the gate answers for it
ALIVE_S = 60.0  # ... but not for a loop gone this long

Airports = Callable[[str], tuple[float, float] | None]
Start = tuple[float, float, str]  # latitude, longitude, a name for it
Request = tuple[str, float, float, tuple, str]  # what, lat, lon, where to answer, why


def read_last_start(prefs: Path) -> dict | None:
    """``_last_start`` of Freeflight.prf, e.g.
    ``{"runway_start": {"airport_id": "LFML", "runway": "13L"}}``."""
    try:
        with open(prefs, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("_last_start "):
                    value = json.loads(line[len("_last_start ") :])
                    return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        pass
    return None


def _spot(where: dict) -> str:
    """Where on its airport a saved start is: `` runway 13L``, `` parking 44``, or nothing. A
    parking's name is the airport's own; "parking" goes before a number or a code (``44``,
    ``A12``), not before a name that says it already (``Gate 12``, ``GA Parking``)."""
    runway, ramp = where.get("runway"), where.get("ramp")
    if isinstance(runway, str) and runway.strip():
        return f" runway {runway.strip()}"
    if isinstance(ramp, str) and ramp.strip():
        name = " ".join(ramp.split())
        code = " " not in name and any(c.isdigit() for c in name)
        return f" parking {name}" if code else f" {name}"
    return ""


def start_position(start: dict, airports: Airports) -> Start | None:
    """Where a saved start is: its latitude and longitude, else its airport's (named with its
    runway or its parking)."""
    for where in start.values():
        if not isinstance(where, dict):
            continue
        lat, lon = where.get("latitude"), where.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon), f"{lat:.3f} {lon:.3f}"
        icao = where.get("airport_id")
        if isinstance(icao, str) and icao:
            icao = icao.upper()
            pos = airports(icao)
            if pos is not None:
                return pos[0], pos[1], icao + _spot(where)
    return None


def place_in(name: str, lang: str) -> str:
    """A start's name in the window's language: ``LFBZ runway 09`` is ``LFBZ piste 09`` in French
    (parking is the same word; an airport's own names for its stands, and coordinates, stay)."""
    if lang == "fr":
        return re.sub(r"^([A-Z0-9]{3,7}) runway ", r"\1 piste ", name)
    return name


def _wait_line(left: int, name: str, done: float, took: float, mbs: float, lang: str) -> str:
    return (
        f"LIVE1 WAIT n={left} p={round(max(0.0, min(1.0, done)) * 1000)} s={round(took)} "
        f"mbs={mbs:.1f} lang={lang} at={_fitted(place_in(name, lang), 100)}"
    )


def _fitted(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` bytes of UTF-8 at a character's end: the plugin's own buffer."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _encoded(line: str, limit: int = 200) -> bytes:
    """A line of the protocol in UTF-8, the plugin's words in French with their accents, at most
    ``limit`` bytes, never cut inside a character: the plugin shows what it gets as UTF-8."""
    data = line.encode("utf-8")
    if len(data) <= limit:
        return data
    return data[:limit].decode("utf-8", "ignore").encode("utf-8")


class Gate:
    def __init__(self, xplane: Path, airports: Airports, port: int = GATE_PORT):
        self.prefs = xplane / "Output" / "preferences" / "Freeflight.prf"
        self.airports = airports
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("127.0.0.1", port))
        except OSError:
            self.sock.close()
            raise
        self.sock.setblocking(False)
        no_port_unreachable(self.sock)  # Windows: an answer to a plugin gone with X-Plane
        self._mtime = self._prefs_mtime()
        self._lock = threading.Lock()  # the socket's reads, the loop's or the stand-in's
        self._inbox: list[Request] = []  # read for the loop while it was busy
        self._polled = time.monotonic()  # the loop's last read of the plugin's lines
        self._wait = "LIVE1 WAIT n=0 p=0 s=0 mbs=0.0"  # what the gate says for the loop
        self._stood_in: list[float] = []  # how late the loop was, each time
        self._closed = threading.Event()
        threading.Thread(target=self._stand_in, name="live-gate", daemon=True).start()

    def _prefs_mtime(self) -> float | None:
        try:
            return os.stat(self.prefs).st_mtime
        except OSError:
            return None

    def new_start(self) -> Start | None:
        """The start X-Plane has saved since the last call: the user has clicked Start Flight."""
        mtime = self._prefs_mtime()
        if mtime is None or mtime == self._mtime:
            return None
        start = read_last_start(self.prefs)
        if start is None and time.time() - mtime < 2.0:
            return None  # still being written: look again
        self._mtime = mtime
        return start_position(start, self.airports) if start else None

    def requests(self) -> list[Request]:
        """The plugin's lines since the last call: (LOAD, HOLD or ASK, lat, lon, where to answer,
        why); ASK has no position (0, 0), and its why is what it measured of X-Plane's frames."""
        with self._lock:
            self._polled = time.monotonic()
            out, self._inbox = self._inbox, []
            out.extend(self._read())
        return out

    def stood_in(self) -> list[float]:
        """How late the loop was each time the gate answered a HOLD for it, since the last call."""
        with self._lock:
            late, self._stood_in = self._stood_in, []
        return late

    def hold_at(self, name: str, lang: str = "en") -> None:
        """A start just known, which the loop plans next: what the gate says for it meanwhile."""
        self._wait = _wait_line(0, name, 0.0, 0.0, 0.0, lang)

    def _read(self) -> list[Request]:
        """Every line waiting on the socket (the caller holds the lock)."""
        out: list[Request] = []
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except ConnectionResetError:  # Windows: an answer found no plugin; the rest is there
                continue
            except OSError:  # nothing more (or a stray error): look again next time
                break
            parts = data.decode("ascii", "replace").split()
            if parts[:2] == ["LIVE1", "ASK"]:  # its frames of the second after: "max=123 n=45"
                out.append(("ASK", 0.0, 0.0, addr, " ".join(parts[2:])))
            elif len(parts) in (4, 5) and parts[0] == "LIVE1" and parts[1] in ("LOAD", "HOLD"):
                with contextlib.suppress(ValueError):
                    out.append(
                        (
                            parts[1],
                            float(parts[2]),
                            float(parts[3]),
                            addr,
                            parts[4] if len(parts) == 5 else "aircraft",
                        )
                    )
        return out

    def _stand_in(self) -> None:
        """While the loop is late reading the plugin's lines, read them for it, and tell a
        waiting X-Plane to wait on (the module's docstring)."""
        while not self._closed.wait(0.1):
            with self._lock:
                late = time.monotonic() - self._polled
                if not STAND_IN_S < late < ALIVE_S or self._closed.is_set():
                    continue
                for request in self._read():
                    self._inbox.append(request)
                    if request[0] == "HOLD":
                        self._send(self._wait, request[3])
                        self._stood_in.append(late)

    def _send(self, line: str, addr: tuple) -> None:
        with contextlib.suppress(OSError):
            self.sock.sendto(_encoded(line), addr)

    def answer(
        self,
        addr: tuple,
        go: bool,
        left: int,
        name: str = "",
        done: float = 0.0,
        took: float = 0.0,
        mbs: float = 0.0,
        lang: str = "en",
    ) -> None:
        if go:
            line = f"LIVE1 GO {left}"
        else:
            line = self._wait = _wait_line(left, name, done, took, mbs, lang)
        self._send(line, addr)

    def note(
        self,
        addr: tuple,
        note: str | None = None,
        notice: dict | None = None,
        lang: str = "en",
    ) -> None:
        """The answer to ``LIVE1 ASK``: the notice of a place with no mesh (``LIVE1 NOTE id lang=xx
        text``, its words in the window's language), else ``LIVE1 NONE``. Each says the language
        the plugin's own words take."""
        if note and notice:
            line = f"LIVE1 NOTE {notice['id']} lang={lang} {note}"
        else:
            line = f"LIVE1 NONE lang={lang}"
        self._send(line, addr)

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            self.sock.close()
