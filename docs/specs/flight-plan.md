# The flight plan of step 1

Status: rebuilt for the version after 0.1.17, replacing the first flight plan, which was switched
off before 0.1.14 shipped (2026-09-23) and never offered.

Code: `src/orthostudio/flightplan.py` (the squares and the line), `src/orthostudio/api/simbrief.py`
(`GET /api/flightplan/simbrief`, `POST /api/flightplan`), `src/orthostudio/ui/flightplan.js` (what
the page holds and keeps, and every rule on it), the flight plan's section of `ui/app.js`, `drawRoute` and `routeChanged` in
`ui/map.js`. Tests: `tests/test_flightplan.py`, `tests/test_api_simbrief.py`, the flight plan's
section of `tests/test_ui_static.py`.

## 1. What the pilot asked for

"Arrivée/départ à ZL des settings, Route : ZL 14 par défaut, sélection automatique des tuiles,
centrage de la route sur la map, possibilité de supprimer la route et les tuiles sélectionnées",
and, after the first version, "s'il faut faire autrement on fait autrement" (2026-09-25). Then, on
the levels: a list beside the departure and arrival, starting at step 1's level; a list on the
right of the route, starting at ZL14; both within what the imagery source gives.

One button, *My SimBrief plan*, reads the pilot's last SimBrief plan (the name set in Settings).
Its squares are chosen at once, the map is brought to the route, and the plan's box says the
route, its two groups of squares with their levels, and what was left out. *Delete the flight
plan* takes the plan and its squares away; squares chosen by hand stay.

Testing it, the pilot saw the route pass some seven kilometres from the corner of a square it did
not choose, a square plainly seen from the aircraft, and asked for a corridor: every square within
the airport field's radius of the route (15 km unless changed). And to be able not to choose the
route's squares at all: a tick before *Along the route*, which leaves the departure and the arrival
alone (2026-09-25). Then a *Recenter* button, for a map moved away from the route, and the button
of the plan in the middle of its words rather than against their last line (the same evening).

Typing the airports of a route (`LSGG LFMN`), which the first version also offered, is left out:
that is where several of its faults were (codes dropped without a word, one request per code), and
a pilot who files a plan has it in SimBrief.

## 2. Why it was rebuilt

The reviews of 2026-09-23 found most of their faults in the first version, and nearly all of them
came from copies of one thing disagreeing:

* **the same level in four places** (step 1's level, the level last chosen, a level per square, and
  the two lists of the route), reconciled by a function that rewrote step 1's level: pressing
  *Along the route* first put the departure and the arrival at ZL14;
* **the route in three places** (the page's memory, the text field, the browser's storage), which
  drifted: after a reload only the line came back, and *Draw* then replaced the plan with a
  straight leg;
* **three geometries**: the line drawn straight on the map, the squares counted along straight
  lines in degrees, the length measured on the sphere. On KJFK to EGLL the squares counted and the
  great circle parted by 83 squares, and the map was fitted the long way round on a Pacific
  crossing;
* **tests that proved nothing**: most read the source for a string; one tested a copy of the
  function it named.

## 3. The engine: one computation for the line and the squares

`orthostudio.flightplan.plan_of(points, radius_km, has_land)`:

* the plan's points (SimBrief's navigation log, departure and arrival procedures included) are
  joined by **great circles**, cut in steps of 10 km (`STEP_KM`): the path;
* **along**, the corridor (`squares_near`): every square within `radius_km` of the path, in the
  order it is first approached. The squares the path enters are always there, found exactly by
  walking the grid step by step, corners clipped included; with a radius, so is every square whose
  nearest point lies within it of a point of the path, sampled every kilometre
  (`CORRIDOR_STEP_KM`), so a square is found within half a kilometre of its true distance. With a
  radius of 0 the corridor is the line's own squares. Measured with 15 km: 16 squares from Geneva to
  Palma against 12 on the line alone, 30 from London to Rome against 24;
* **ends**: the squares within `radius_km` of the departure, then of the arrival, the airport
  field's own rule (`airports.tiles_within`, the true distance to each square), which the corridor
  follows along the whole flight; they are left out of *along*;
* **left out**: with `has_land`, the squares X-Plane has no scenery file for (open sea, or a region
  the installer was not asked for). A build stops on such a square (`DSF_GLOBAL_SCENERY_MISSING`),
  so the plan does not choose it and says how many it left;
* the path is cut where it crosses the antimeridian, as a chart draws it, since the map cannot pan
  beyond ±180°; a fix on the meridian itself, given as -180 or 180, belongs to the side the line
  comes from (drawn as given, it joined 179.9 to -180 across the whole map, review of
  2026-09-26); the **bounds** keep a Pacific crossing in one frame (east may pass 180) so the map
  can be fitted to it.

What the map draws is this path, so what is drawn is what is chosen. Measured against a walk in
steps of 200 m along the same great circles, the squares the line enters are the same ones, and the
corridor holds every square nearer than its radius less half a kilometre and none beyond it (the
tests, on routes including New York to London and Tokyo to Honolulu).

## 4. The route: `GET /api/flightplan/simbrief?radius_km=15`

The engine reads the plan from SimBrief (the page talks to nothing but its own address), with the
name set in Settings: a name goes as `username`, encoded, and a pilot ID, all digits, as `userid`.
The answer:

| Field | What |
|---|---|
| `from`, `to`, `points` | the two airports and the points between (`ident`, `name`, `lat`, `lon`) |
| `path` | the line, in pieces within the world: `[[[lat, lon], ...], ...]` |
| `squares` | `{ends: [...], along: [...]}`, tile names, *along* in the order flown |
| `left_out` | the squares X-Plane has no scenery for |
| `length_km`, `bounds` | the plan's length on the sphere; `{south, north, west, east}` |
| `radius_km` | the radius the squares were chosen with |
| `scenery_checked` | false when no X-Plane folder is known: then nothing is left out |

`radius_km` is the airport field's radius (0 to 300 km, 15 by default), around the two airports
and along the whole route; to widen the corridor, change it and read the plan again. Errors come in the engine's
one shape (code, words, remedy, context): `CFG_SIMBRIEF_USER_MISSING` (400),
`CFG_SIMBRIEF_USER_UNKNOWN` (404, with the name), `CFG_SIMBRIEF_PLAN_EMPTY` (404),
`NET_SIMBRIEF_FAILED` (502, with the reason; the network's general code said "retried with
backoff", which this button never does).

`POST /api/flightplan` takes a route the page kept, `{from, to, points, radius_km}`, and answers the
same: its squares and its line computed again, by the rules of the version that answers, without
SimBrief and without a name (section 5). It takes nothing else: squares sent with it are refused
(422), like fewer than 2 points or more than 402, a point off the globe, a radius outside 0 to 300.
Both routes are one computation (`answer` in the router). API level 24.

## 5. The page: one object, everything else derived

`state.flightPlan` holds the engine's answer, the two levels, the squares the pilot took out of
the plan and whether the route's squares are wanted (`along`). Nothing else is held;
`ui/flightplan.js` derives the rest, and the tests run that very file.

* **The levels.** A new plan's departure and arrival start at step 1's level, its route at ZL14
  (`ROUTE_ZL`), both within the source's maximum (`defaultLevels`). A square is built at the finest
  level its reasons for being chosen ask for (`levelOf`): chosen by hand, step 1's; in the plan, its
  group's; both, the finer. Each list commands its own squares only: step 1's list is the level of
  the squares chosen by hand, the plan's two lists the level of its own. A source that stops lower
  caps them all without changing them, and the level chosen comes back on a source that goes higher.
* **The squares.** The departure's and the arrival's first, then the route in the order flown, up to
  what one build takes (500): a plan too long is cut after a square the box names. A square a build
  is working on is left out and named, as everywhere else.
* **The route, or not.** The tick before *Along the route* chooses its squares or leaves them all
  out: unticked, the plan chooses its departure and arrival alone, its route's list is greyed, and
  the box still counts the squares the route would add; ticked again, they come back as far as one
  build takes. Squares chosen by hand stay either way, and the choice is kept with the plan.
* **By hand.** A square added by hand (a click, a sweep, a name, an airport, a zone) is the pilot's
  (`state.byHand`); one taken out by hand (its chip's cross, a click, a sweep) leaves the plan too,
  or the next visit would choose it again. A sweep counts from where it started: a square its
  rectangle leaves again is as it was before the sweep, neither the pilot's nor out of the plan.
* **Deleting.** *Delete the flight plan* takes the plan's squares away, except those also chosen by
  hand. Step 1's trash empties the whole selection and takes the plan with it. Starting a build
  with the selection forgets the plan too: kept, the next visit would have chosen its squares again.
* **Kept between visits.** What the pilot chose is saved in the browser's storage
  (`osxp.flightplan`, version 2): the route and the radius its squares were chosen with, the two
  levels, the squares taken out, the tick; never the squares. A reload sends the route to the
  engine (`POST /api/flightplan`), which computes the squares again, and they are chosen with the
  pilot's levels, exclusions and tick; SimBrief is not asked. Testing the corridor, the pilot
  still saw the line's squares alone: the plan read before it came was saved whole, squares
  included, and a reload chose them as they were (2026-09-25). Now a plan kept across a new
  version follows that version's rules. The reload holds the button like any reading of the plan;
  a selection emptied or built meanwhile forgets the kept plan and the answer is dropped; an
  engine that cannot answer (older than the page, before a restart) leaves the plan kept for the
  next visit. Anything else found there is dropped, version 1 (the engine's answer whole)
  included, and the first version's `osxp.route` is cleared.
* **One request at a time.** The button is busy while the plan is read; a second click does
  nothing. What went wrong is said right under it, in the page's words for the code.

The map draws the engine's line (`osxpRoute`, over the grid, no pointer event), a ring at each end
and a smaller one at each point, colours the departure and arrival squares in the route's colour,
and fits the view to the plan's bounds when it is read (`fitRoute`); *Recenter*, beside *Delete the
flight plan*, fits it again once the map has moved away.

## 6. What is not there

A route typed by hand; zones around the departure and arrival (the airport settings already make
airports sharper); building squares during the flight.
