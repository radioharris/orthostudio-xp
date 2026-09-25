/**
 * The flight plan of step 1, as the page keeps it (docs/specs/flight-plan.md).
 *
 * The engine computes a plan once: its line, and its squares, the departure's and the arrival's
 * (`ends`) and those it flies over (`along`, in the order flown), GET /api/flightplan/simbrief.
 * The page keeps that answer whole, with two levels, the squares the pilot took out of it and
 * whether the route's squares are wanted at all, and derives everything else from them: which
 * squares are chosen, and at which level. The first
 * flight plan kept the same level in four places and its route in three, and most of what the
 * reviews found was those copies disagreeing (2026-09-23).
 *
 * Nothing here touches the page: every function takes what it needs and returns a value, so the
 * tests run this very file.
 */

/** The level of the squares along a route unless the pilot chooses another (a user, 2026-09-22):
 * a route crosses country seen from altitude, and each level up is four times the imagery. */
export const ROUTE_ZL = 14;

/** Where the page keeps the plan between two visits (the browser's storage, this page only). */
export const STORAGE_KEY = "osxp.flightplan";

/** Where the first flight plan kept its route: read by nothing now, and cleared once. */
export const OLD_STORAGE_KEY = "osxp.route";

const SAVED_VERSION = 1;
const TILE_NAME = /^[+-]\d{2}[+-]\d{3}$/;

/** The two levels a new plan starts with: its ends at step 1's level, its route at ZL14, never
 * above what the imagery source gives (the pilot's choices, 2026-09-25). */
export function defaultLevels(stepZl, maxZl) {
  return { ends: Math.min(stepZl, maxZl), along: Math.min(ROUTE_ZL, maxZl) };
}

/** A plan as the page keeps it: the engine's answer, its two levels, nothing taken out yet, and
 * the route's squares wanted. */
export function newFlightPlan(plan, levels) {
  return { plan, levels: { ...levels }, excluded: [], along: true };
}

/** The squares of one group of the plan, those the pilot took out left out, whether the group is
 * wanted or not: what the box counts. */
export function squaresOf(fp, group) {
  const taken = new Set(fp.excluded);
  const ends = new Set(fp.plan.squares.ends);
  const names = group === "ends" ? fp.plan.squares.ends : fp.plan.squares.along.filter((n) => !ends.has(n));
  return names.filter((n) => !taken.has(n));
}

/** Which group each square the plan chooses is in, `"ends"` or `"along"`: the squares the pilot
 * took out of it left out, and the route's all left out when the pilot wants the departure and
 * the arrival alone (a user, 2026-09-25). */
export function groupsOf(fp) {
  const out = new Map();
  if (!fp) return out;
  for (const name of squaresOf(fp, "ends")) out.set(name, "ends");
  if (fp.along === false) return out;
  for (const name of squaresOf(fp, "along")) if (!out.has(name)) out.set(name, "along");
  return out;
}

/**
 * The level a square is built at: the finest that its reasons for being chosen ask for.
 *
 * Chosen by hand, step 1's level; in the plan, its group's. A square both chosen by hand and on
 * the route is built at the finer of the two. Every level is capped at what the source gives
 * without being changed: back on a source that goes higher, the level chosen comes back, where the
 * first version lowered it for good (2026-09-23).
 */
export function levelOf(name, { byHand, groups, levels, stepZl, maxZl }) {
  const group = groups.get(name);
  const asked = [];
  if (byHand.has(name) || !group) asked.push(stepZl);
  if (group) asked.push(levels[group]);
  return Math.min(Math.max(...asked), maxZl);
}

/**
 * The selection with the plan's squares added: the departure's and the arrival's first, then the
 * route in the order it is flown, as far as one build takes (`cap`), so a plan too long for one
 * build is cut at a place the pilot can name. Squares already chosen stay where they are; squares
 * a build is working on are left out and named (`skipped`).
 */
export function withPlan(tiles, fp, { cap, building = new Set() }) {
  const out = [...tiles];
  const seen = new Set(out);
  const skipped = [];
  let cut = 0;
  let lastKept = null;
  for (const [name, group] of groupsOf(fp)) {
    if (seen.has(name)) continue;
    if (building.has(name)) {
      skipped.push(name);
      continue;
    }
    if (out.length >= cap) {
      cut += 1;
      continue;
    }
    seen.add(name);
    out.push(name);
    if (group === "along") lastKept = name;
  }
  return { tiles: out, skipped, cut, lastKept };
}

/** The selection without the plan, or without one group of it: those squares go, except the ones
 * also chosen by hand. */
export function withoutPlan(tiles, fp, byHand, group = null) {
  const groups = groupsOf(fp);
  const gone = (name) => groups.has(name) && (group === null || groups.get(name) === group);
  return tiles.filter((name) => byHand.has(name) || !gone(name));
}

/** The plan with one more square taken out of it by the pilot (a chip's ×, a click, a sweep). */
export function excluding(fp, names) {
  const groups = groupsOf(fp);
  const more = names.filter((name) => groups.has(name));
  if (!more.length) return fp;
  return { ...fp, excluded: [...fp.excluded, ...more] };
}

/** What the page saves: the plan whole, so nothing is recomputed or asked again at the next visit. */
export function toSaved(fp) {
  return JSON.stringify({ v: SAVED_VERSION, plan: fp.plan, levels: fp.levels, excluded: fp.excluded, along: fp.along !== false });
}

function isLevel(n) {
  return Number.isInteger(n) && n >= 10 && n <= 20;
}

function isNames(list) {
  return Array.isArray(list) && list.every((n) => typeof n === "string" && TILE_NAME.test(n));
}

function isPath(path) {
  return (
    Array.isArray(path) &&
    path.every(
      (piece) =>
        Array.isArray(piece) &&
        piece.every((p) => Array.isArray(p) && p.length === 2 && p.every((v) => Number.isFinite(v))),
    )
  );
}

/** A plan read back from the browser's storage, or `null` when it is not one this page wrote:
 * anything else is dropped rather than half believed. */
export function readSaved(text) {
  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    return null;
  }
  if (!doc || doc.v !== SAVED_VERSION || typeof doc.plan !== "object" || !doc.plan) return null;
  const { plan, levels, excluded, along } = doc;
  const squares = plan.squares || {};
  if (!isNames(squares.ends) || !isNames(squares.along) || !isNames(excluded ?? [])) return null;
  if (!isPath(plan.path) || !Array.isArray(plan.points) || plan.points.length < 2) return null;
  if (!levels || !isLevel(levels.ends) || !isLevel(levels.along)) return null;
  if (along !== undefined && typeof along !== "boolean") return null;
  return { plan, levels: { ends: levels.ends, along: levels.along }, excluded: [...(excluded ?? [])], along: along !== false };
}
