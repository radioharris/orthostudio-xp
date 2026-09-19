"""The last flight plan of a pilot, from SimBrief: ``GET /api/simbrief``.

The Plan draws a route to help choose the squares to build, and typing the airports is not what a
pilot who already filed a plan wants to do. SimBrief, which most of them use, serves the last plan
of a user without a key and without a password: one address, a name or a pilot ID, and the whole
briefing comes back (``https://www.simbrief.com/api/xml.fetcher.php?username=...&json=1``).

Only what draws a line is kept here: the two airports and the points between them, with their
coordinates. The engine asks, not the page, as for the street map, so the page still talks to
nothing but the address it was opened at. The name travels to simbrief.com, which the Settings
question says in plain words; nothing else of the user leaves the machine, and nothing is kept on
disk.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("orthostudio.api.simbrief")

__all__ = ["SIMBRIEF_URL", "route_of", "simbrief_router"]

SIMBRIEF_URL = "https://www.simbrief.com/api/xml.fetcher.php"
"""Where the last plan comes from; ``json=1`` asks for JSON rather than the XML it was built on."""

DEADLINE_S = 20.0
"""No request outlives this: the button fails, it never hangs the page."""

MAX_POINTS = 400
"""A navigation log holds one line per fix; a long-haul plan stays well under this."""


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if -90.0 <= out <= 90.0 or abs(out) <= 180.0 else None


def _point(ident: str, name: str, lat: Any, lon: Any) -> dict[str, Any] | None:
    """One point of the line, or ``None`` when the plan does not say where it is."""
    y, x = _number(lat), _number(lon)
    if y is None or x is None or not (-90.0 <= y <= 90.0) or not (-180.0 <= x <= 180.0):
        return None
    label = ident.strip().upper()[:8] or "?"
    return {"ident": label, "name": name.strip()[:60], "lat": y, "lon": x}


def route_of(doc: Any) -> dict[str, Any]:
    """The flight plan reduced to a line: ``{from, to, points: [{ident, name, lat, lon}]}``.

    The navigation log gives the real path, departure procedure and arrival included, which is what
    an aircraft flies and therefore which squares it flies over. A plan with no log, or one whose
    log says nothing of where its fixes are, still draws the leg between its two airports.
    """
    origin = doc.get("origin") or {}
    destination = doc.get("destination") or {}
    ends = [
        _point(
            str(end.get("icao_code") or end.get("iata_code") or "?"),
            str(end.get("name") or ""),
            end.get("pos_lat"),
            end.get("pos_long"),
        )
        for end in (origin, destination)
    ]
    navlog = (doc.get("navlog") or {}).get("fix") or []
    if isinstance(navlog, dict):  # a log of one fix comes back as an object, not a list
        navlog = [navlog]
    middle = []
    for fix in navlog[:MAX_POINTS]:
        if not isinstance(fix, dict):
            continue
        point = _point(
            str(fix.get("ident") or ""),
            str(fix.get("name") or ""),
            fix.get("pos_lat"),
            fix.get("pos_long"),
        )
        if point is not None:
            middle.append(point)
    points = [p for p in (ends[0], *middle, ends[1]) if p is not None]
    # The log usually ends on the destination and may open on the departure: no point twice over
    line: list[dict[str, Any]] = []
    for point in points:
        if line and (point["lat"], point["lon"]) == (line[-1]["lat"], line[-1]["lon"]):
            continue
        line.append(point)
    return {
        "from": (ends[0] or {}).get("ident") or (line[0]["ident"] if line else ""),
        "to": (ends[1] or {}).get("ident") or (line[-1]["ident"] if line else ""),
        "points": line,
    }


def _fetch(url: str) -> tuple[bytes | None, str]:
    """One request through the fetcher the rest of the engine uses; never raises."""
    from orthostudio.net.fetch import FetchRequest, fetch_all

    results = fetch_all(
        [FetchRequest(key=url, url=url, host_group="simbrief")],
        timeout_s=DEADLINE_S,
        max_attempts=1,
        max_in_flight=1,
        start_in_flight=1,
    )
    if not results:
        return None, "NET_CONNECTION_FAILED"
    answer = results[0]
    if answer.error is not None:
        return None, answer.error
    if answer.status == 400:
        return None, "unknown user"
    if not (200 <= answer.status < 300) or not answer.body:
        return None, f"HTTP {answer.status}"
    return answer.body, ""


def simbrief_router(
    user_of: Callable[[], str | None],
    *,
    fetch: Callable[[str], tuple[bytes | None, str]] | None = None,
) -> APIRouter:
    """The router: ``app.include_router(simbrief_router(lambda: settings...simbrief_user))``.

    ``user_of`` is called on each request, so a name changed in Settings is honoured without a
    restart. ``fetch`` replaces the upstream request, which is how the tests run without a network.
    """
    ask = fetch if fetch is not None else _fetch
    router = APIRouter()

    def failed(code: str, message: str, remedy: str, status: int) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": code, "severity": "blocking", "message": message, "remedy": remedy}},
            status_code=status,
        )

    @router.get("/api/simbrief")
    async def simbrief() -> Any:
        """The last plan of the SimBrief user named in Settings, as a line to draw."""
        import asyncio

        user = (user_of() or "").strip()
        if not user:
            return failed(
                "CFG_SIMBRIEF_USER_MISSING",
                "No SimBrief name is set.",
                "Settings asks for your SimBrief name, the one you sign in with.",
                400,
            )
        url = f"{SIMBRIEF_URL}?username={user}&json=1"
        body, why = await asyncio.to_thread(ask, url)
        if body is None:
            if why == "unknown user":
                return failed(
                    "CFG_SIMBRIEF_USER_UNKNOWN",
                    f"SimBrief does not know {user}.",
                    "Check the name in Settings: it is your SimBrief name, or your pilot ID.",
                    404,
                )
            return failed(
                "NET_CONNECTION_FAILED",
                f"SimBrief could not be reached ({why}).",
                "Try again later, or type the airports of your route by hand.",
                502,
            )
        try:
            doc = json.loads(body)
        except ValueError:
            return failed(
                "NET_CONNECTION_FAILED",
                "SimBrief answered something this version cannot read.",
                "Try again later, or type the airports of your route by hand.",
                502,
            )
        line = route_of(doc if isinstance(doc, dict) else {})
        if len(line["points"]) < 2:
            return failed(
                "CFG_SIMBRIEF_PLAN_EMPTY",
                "The last SimBrief plan says nothing of where it goes.",
                "Generate a flight plan on simbrief.com, then try again.",
                404,
            )
        log.info("simbrief: %s to %s, %d point(s)", line["from"], line["to"], len(line["points"]))
        return line

    return router
