// What this release offers. One line, read by every part of the page that has to know.

/**
 * Whether the flight plan is offered: the route field, its two buttons, their detail levels,
 * SimBrief and the line on the map. It waits for the version that brings the prepared map data,
 * and then goes out apart from it, each checkable on its own (2026-09-25).
 *
 * Choosing squares along a route is a whole feature of its own, and it arrived in the same
 * release as three faults users are waiting on: a release carrying both is one nobody can
 * check. Its code, its tests and its words stay where they are; the page does not offer it,
 * and turning it back on is this one line (2026-09-23).
 */
export const FLIGHT_PLAN = false;
