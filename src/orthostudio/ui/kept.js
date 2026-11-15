// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
/**
 * What the page keeps from one visit to the next: the boxes and folds of the map, the Library's
 * order, the Experts band, the folder of the last plan file, the flight plan of step 1, the notes
 * put away (docs/specs/ui.md 1, What the page keeps).
 *
 * The engine keeps them (api/page_kept.py) and writes them into the page as it serves it, in
 * `#page-kept`; a change goes back to it at once. The browser's storage kept them before, and in
 * the app's frame, an iframe on another port, the web view of macOS forgot them at every launch
 * (2026-11-15). The first visit after takes what the browser still holds, key by key, once.
 * Served as plain files, with no engine, the page keeps its choices for the visit alone.
 *
 * The language, the theme, the text size and the zoom are the frame's, for both pages, and stay
 * where they are (i18n.js, textsize.js, zoom.js).
 */

const ROUTE = "/api/page-kept";

/** `{kept}` as the engine wrote it into the page, or null when no engine served it. */
function given() {
  try {
    const found = typeof document === "undefined" ? null : document.getElementById("page-kept");
    const value = JSON.parse(found?.textContent || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

const served = given();
const values = new Map(Object.entries(served?.kept && typeof served.kept === "object" ? served.kept : {}));
/** Nothing kept yet by the engine: what the browser's storage holds is taken, once. */
const firstVisit = Boolean(served) && served.kept == null;
let sending = Promise.resolve();

function send(changes) {
  if (!served) return;
  const body = JSON.stringify({ values: changes });
  // one after the other, in the order made: two quick clicks end on the second
  sending = sending
    .then(() => fetch(ROUTE, { method: "PUT", headers: { "Content-Type": "application/json" }, body }))
    .catch(() => {
      // an engine older than the page, or gone: kept for this visit
    });
}

/** The value kept under `key`, a string, or null. */
export function pageKept(key) {
  if (values.has(key)) return values.get(key);
  if (firstVisit) {
    let held = null;
    try {
      held = localStorage.getItem(key);
    } catch {
      // a browser that keeps nothing
    }
    values.set(key, held);
    if (held != null) send({ [key]: held });
    return held;
  }
  return null;
}

/** Keep `value` under `key` (its text), or forget it (null). */
export function pageKeep(key, value) {
  const text = value == null ? null : String(value);
  if (values.get(key) === text && values.has(key)) return;
  values.set(key, text);
  send({ [key]: text });
}
