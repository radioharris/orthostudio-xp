# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The gate: X-Plane goes on loading a flight only once the ortho around its start is built.

OrthoStudio XP Live's X-Plane plugin (``native/xplane_plugin/live_gate.c``, put in X-Plane by
``orthostudio.live.plugin``) talks to this gate while X-Plane loads a flight's aircraft, before
X-Plane reads the scenery. It sends one line per datagram over UDP on 127.0.0.1:GATE_PORT:

- ``LIVE1 LOAD lat lon``: a flight is loading there (PLANE_LOADED); no answer is expected.
- ``LIVE1 HOLD lat lon why day=N utc=S``: X-Plane waits for the answer. ``why`` is ``aircraft``: the
  aircraft's sounds or livery, which a livery changed in flight also sends (a new location chosen
  in flight loads no aircraft: Live pauses X-Plane itself there, ``orthostudio.live.pause``).
  ``day`` is the flight's date, its day of the year from 0 (``sim/time/local_date_days``), kept
  as :attr:`Gate.held_day` for the photos' season: a flight started otherwise than by a click on
  Start Flight (X-Plane's web API, a situation) saves no date in Freeflight.prf (2026-10-13).
  ``utc`` is its UTC time in seconds (``sim/time/zulu_time_sec``), kept as
  :attr:`Gate.held_utc`: whether it is night at the start (orthostudio.live.nightwork), where
  Freeflight.prf saves only the local time of a zone Live does not know. An older plugin sends
  ``day`` alone.
  The gate sends ``LIVE1 GO n`` once every texture X-Plane will read there is built. Otherwise
  it sends, for instance,
  ``LIVE1 WAIT n=12 p=450 s=23 mbs=15.2 lang=en at=LFMN runway 04R``:
  - n is how many textures are left;
  - p is the progress in thousandths, counted from the first moment the start was known;
  - s is the seconds since then;
  - mbs is the download rate, in megabytes a second;
  - lang is the Live window's language, ``en`` or ``fr``, which the plugin's words take;
  - at is the start's name in that language (``LFMN piste 04R``), the rest of the line.

  The plugin's loading window shows all of it.

- ``LIVE1 QUALITY 5``: X-Plane's texture quality, 0 to 5 (Maximum), the plugin's reading of the
  setting in X-Plane (``sim/private/controls/reno/tex_res``), sent before each ``HOLD``: the
  streamer builds the start for it (:attr:`Gate.quality`). No answer. A plugin that cannot read
  it sends none, and the streamer goes by X-Plane's preferences as it started.
- ``LIVE1 SHARPEN lat lon 5``: in flight, X-Plane's texture quality rose to 5, and X-Plane waits
  for the answer, as at a ``HOLD``: X-Plane reads again, 3 to 6 s after « Resume flight », the
  textures round the aircraft whose sharper levels it reads now, which the streamer had not built
  (it builds none sharper than the quality). The same ``WAIT`` and ``GO`` answer it, once those
  levels and their night textures are built (:func:`orthostudio.live.streamer.sharpen_count`).
- ``LIVE1 ASK``, every second of a flight: whether the streamer has a notice for the pilot. Where
  the aircraft has no mesh under it but a region has one, the gate answers ``LIVE1 NOTE id lang=fr
  text`` ("Live : pas de mesh ici, installez Amérique du Nord dans la fenêtre de Live"), which the
  plugin shows in a small panel with its cross; ``id`` tells one notice from the next. Else
  ``LIVE1 NONE lang=en``. (It offered a reload of the scenery too, for the 8192 px textures built
  after a start, until a test showed X-Plane's reload does not read again the textures it holds,
  2026-09-27.) The ask says X-Plane's frames of the second and its texture quality:
  ``LIVE1 ASK max=123 n=45 q=5`` (the longest frame in ms, how many; ``q`` from a newer plugin,
  which tells a lower quality so, and a streamer started in flight).

Lines go out in UTF-8, never cut inside a character: the plugin shows French with its accents.

The plugin lets X-Plane go when its first ``HOLD`` (or ``SHARPEN``) gets no answer within a
second: no streamer.
The streamer's loop, which answers, can be busy longer than that: a new start planned under a
start's load kept it 3 s, and X-Plane went on at LGAV and LCLK with nothing built (2026-09-29).
So while the loop is late reading the plugin's lines (``STAND_IN_S``), the gate reads them itself,
keeps them for the loop and answers a ``HOLD`` with the loop's last ``WAIT`` (the start's name
and figures as the loop last said them), a ``SHARPEN`` with its last one for that: X-Plane waits
on. Never for a loop gone more than
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
QUALITY_MAX = 5  # X-Plane's texture quality, Maximum

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


def read_flight_time(prefs: Path) -> tuple[int, float | None] | None:
    """The date of the last flight X-Plane started, from ``_time`` of Freeflight.prf, saved with
    ``_last_start`` at the click on Start Flight: (its day of the year, 1 for 1 January; the
    latitude saved with it, or None). E.g. ``{"local_time": {"day_of_year": 259, "latitude":
    37.43, ...}}``; the day is found wherever the object holds it. X-Plane counts the days from 0
    (``sim/time/local_date_days``), so one is added."""
    try:
        with open(prefs, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("_time "):
                    return _day_in(json.loads(line[len("_time ") :]))
    except (OSError, ValueError):
        pass
    return None


def _day_in(value) -> tuple[int, float | None] | None:
    if isinstance(value, dict):
        day = value.get("day_of_year")
        if isinstance(day, int | float) and not isinstance(day, bool) and 0 <= day <= 366:
            lat = value.get("latitude")
            ok = isinstance(lat, int | float) and not isinstance(lat, bool) and abs(lat) <= 90
            return int(day) + 1, float(lat) if ok else None
        for inner in value.values():
            found = _day_in(inner)
            if found is not None:
                return found
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
    """Where a saved start is: its latitude and longitude, else its runway's threshold or its
    stand when ``airports`` knows them (``route.AirportIndex.spot``), else its airport's point
    (named with its runway or its parking)."""
    for where in start.values():
        if not isinstance(where, dict):
            continue
        lat, lon = where.get("latitude"), where.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon), f"{lat:.3f} {lon:.3f}"
        icao = where.get("airport_id")
        if isinstance(icao, str) and icao:
            icao = icao.upper()
            spot = getattr(airports, "spot", None)  # the runway's threshold or the stand
            pos = (
                spot(icao, where.get("runway"), where.get("ramp")) if spot else None
            ) or airports(icao)
            if pos is not None:
                return pos[0], pos[1], icao + _spot(where)
    return None


def place_in(name: str, lang: str) -> str:
    """A start's name in the window's language: ``LFBZ runway 09`` is ``LFBZ piste 09`` in French
    (parking is the same word; an airport's own names for its stands, and coordinates, stay)."""
    if lang == "fr":
        return re.sub(r"^([A-Z0-9]{3,7}) runway ", r"\1 piste ", name)
    return name


def _quality(word: str) -> int | None:
    """X-Plane's texture quality as the plugin says it: 0 to 5 (Maximum), else None."""
    with contextlib.suppress(ValueError):
        value = int(word)
        if 0 <= value <= QUALITY_MAX:
            return value
    return None


def _wait_line(
    left: int,
    name: str,
    done: float,
    took: float,
    mbs: float,
    lang: str,
    building: int = 0,
    night: int = 0,
) -> str:
    """``building``: of the ``left``, those with all their photos, being built (the plugin's
    second line); ``night``: of the ``left``, the night textures (the plugin says « night
    textures to build » once only they are left; an older plugin reads past the word). Sent
    always, 0 too: the plugin keeps the last value it read, and said night textures after
    there were none (the review of 2026-11-11)."""
    return (
        f"LIVE1 WAIT n={left} b={building} p={round(max(0.0, min(1.0, done)) * 1000)} "
        f"s={round(took)} "
        f"mbs={mbs:.1f} lang={lang} night={night} "
        f"at={_fitted(place_in(name, lang), 100)}"
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
        self.held_day: int | None = None  # the date of the flight the plugin last held, from 1
        self.held_utc: float | None = None  # ... and its UTC time, in seconds (a newer plugin)
        self._lock = threading.Lock()  # the socket's reads, the loop's or the stand-in's
        self._inbox: list[Request] = []  # read for the loop while it was busy
        self._polled = time.monotonic()  # the loop's last read of the plugin's lines
        self._wait = "LIVE1 WAIT n=0 b=0 p=0 s=0 mbs=0.0"  # what the gate says for the loop
        self._sharpen_wait = self._wait  # ... and to a SHARPEN
        # X-Plane's texture quality, as the plugin last said it (QUALITY, SHARPEN, the ask's q=):
        # None until it does, X-Plane's preferences standing
        self.quality: int | None = None
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
        """The plugin's lines since the last call: (LOAD, HOLD, SHARPEN or ASK, lat, lon, where to
        answer, why); ASK has no position (0, 0), and its why is what it measured of X-Plane's
        frames; SHARPEN's is the quality X-Plane rose to. The quality any line said is
        :attr:`quality` by then."""
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
                for word in parts[2:]:
                    if word.startswith("q=") and (q := _quality(word[2:])) is not None:
                        self.quality = q
                out.append(("ASK", 0.0, 0.0, addr, " ".join(parts[2:])))
            elif parts[:2] == ["LIVE1", "QUALITY"] and len(parts) == 3:
                if (q := _quality(parts[2])) is not None:
                    self.quality = q
            elif parts[:2] == ["LIVE1", "SHARPEN"] and len(parts) == 5:
                q = _quality(parts[4])
                with contextlib.suppress(ValueError):
                    lat, lon = float(parts[2]), float(parts[3])
                    if q is not None:
                        self.quality = q
                        out.append(("SHARPEN", lat, lon, addr, str(q)))
            elif 4 <= len(parts) <= 7 and parts[0] == "LIVE1" and parts[1] in ("LOAD", "HOLD"):
                words = dict(w.split("=", 1) for w in parts[5:] if "=" in w)
                if "day" in words:  # the flight's date, and its UTC time from a newer plugin
                    self.held_day = self.held_utc = None
                    with contextlib.suppress(ValueError):
                        day = int(words["day"])
                        self.held_day = day + 1 if 0 <= day <= 365 else None
                    with contextlib.suppress(ValueError):
                        utc = float(words.get("utc", ""))
                        self.held_utc = utc if 0.0 <= utc <= 86400.0 else None
                    parts = parts[:5]
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
                    if request[0] in ("HOLD", "SHARPEN"):
                        wait = self._wait if request[0] == "HOLD" else self._sharpen_wait
                        self._send(wait, request[3])
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
        building: int = 0,
        night: int = 0,
        sharpen: bool = False,
    ) -> None:
        """The answer to a ``HOLD``, or to a ``SHARPEN`` (``sharpen``): ``GO``, or ``WAIT`` with
        the figures, kept for the stand-in to say meanwhile."""
        if go:
            line = f"LIVE1 GO {left}"
        else:
            line = _wait_line(left, name, done, took, mbs, lang, building, night)
            if sharpen:
                self._sharpen_wait = line
            else:
                self._wait = line
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
