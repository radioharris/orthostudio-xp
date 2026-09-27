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
  ``LIVE1 WAIT n=12 p=450 s=23 mbs=15.2 at=LFMN runway 04R``:
  - n is how many textures are left;
  - p is the progress in thousandths, counted from the first moment the start was known;
  - s is the seconds since then;
  - mbs is the download rate, in megabytes a second;
  - at is the start's name, the rest of the line.

  The plugin's loading window shows all of it.

- ``LIVE1 ASK``, every second of a flight: whether a reload of the scenery is offered (the "reload"
  start mode: X-Plane started at 4096 px, and the 8192 px textures around the aircraft are built
  afterwards). The gate answers ``LIVE1 OFFER READY id``, ``LIVE1 OFFER BUILD id n`` (n textures
  to go) or ``LIVE1 OFFER NONE``; ``id`` tells one offer from the next. Without an offer, where the
  aircraft has no mesh under it but a region has one, it answers ``LIVE1 NOTE id text`` instead
  ("Live: no mesh here: install North America in the Live window"), which the plugin shows in the
  same small panel, with only its cross.
- ``LIVE1 RELOAD``: the user clicked the plugin's button in X-Plane; the streamer reloads the
  scenery over X-Plane's UDP, as the Live window's button does.

The start is known even before the plugin speaks. When the user clicks Start Flight, X-Plane saves
it in ``Output/preferences/Freeflight.prf`` (``_last_start``, a JSON object), and the streamer
starts building right then (hold test, docs/specs/live/hold-test.md).
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

GATE_PORT = 49250

Airports = Callable[[str], tuple[float, float] | None]
Start = tuple[float, float, str]  # latitude, longitude, a name for it


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


def _ascii(text: str) -> bytes:
    """The protocol's ASCII: a parking's accents dropped (Aérogare: Aerogare), anything else
    beyond ASCII a question mark."""
    bare = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return bare.encode("ascii", "replace")


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
        self._mtime = self._prefs_mtime()

    def _prefs_mtime(self) -> float | None:
        try:
            return os.stat(self.prefs).st_mtime
        except OSError:
            return None

    def last_start(self) -> Start | None:
        """The start X-Plane saved last time: where the next flight will likely begin."""
        start = read_last_start(self.prefs)
        return start_position(start, self.airports) if start else None

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

    def requests(self) -> list[tuple[str, float, float, tuple, str]]:
        """The plugin's lines since the last call: (LOAD, HOLD, ASK or RELOAD, lat, lon, where to
        answer, why); ASK and RELOAD have no position (0, 0)."""
        out: list[tuple[str, float, float, tuple, str]] = []
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:  # nothing more (or a stray error): look again next time
                break
            parts = data.decode("ascii", "replace").split()
            if parts in (["LIVE1", "ASK"], ["LIVE1", "RELOAD"]):
                out.append((parts[1], 0.0, 0.0, addr, ""))
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

    def answer(
        self,
        addr: tuple,
        go: bool,
        left: int,
        name: str = "",
        done: float = 0.0,
        took: float = 0.0,
        mbs: float = 0.0,
    ) -> None:
        if go:
            line = f"LIVE1 GO {left}"
        else:
            line = (
                f"LIVE1 WAIT n={left} p={round(max(0.0, min(1.0, done)) * 1000)} s={round(took)} "
                f"mbs={mbs:.1f} at={name}"
            )
        with contextlib.suppress(OSError):
            self.sock.sendto(_ascii(line)[:200], addr)

    def offer(
        self, addr: tuple, offer: dict | None, note: str | None = None, notice: dict | None = None
    ) -> None:
        """The answer to ``LIVE1 ASK``: the reload offered, as ``Streamer.offer_state`` has it;
        else the notice of a place with no mesh (``LIVE1 NOTE id text``); else nothing."""
        if (offer is None or offer["state"] == "asked") and note and notice:
            line = f"LIVE1 NOTE {notice['id']} Live: {note}"
        elif offer is None or offer["state"] == "asked":
            line = "LIVE1 OFFER NONE"
        elif offer["state"] == "ready":
            line = f"LIVE1 OFFER READY {offer['id']}"
        else:
            line = f"LIVE1 OFFER BUILD {offer['id']} {offer['left']}"
        with contextlib.suppress(OSError):
            self.sock.sendto(_ascii(line)[:200], addr)

    def close(self) -> None:
        self.sock.close()
