// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
/**
 * What the page keeps from one visit to the next (docs/specs/ui.md 1, *What the page keeps*), kept
 * by the program that serves it (api/page_kept.py): written into the page in `#page-kept`, each
 * change sent back at once, one after the other. Live's page keeps its own the same way.
 *
 * Served as plain files, with nothing to keep them, the page keeps its choices for the visit alone.
 * The language, the theme, the text size and the zoom are the frame's, not the page's (i18n.js,
 * app.js, textsize.js, zoom.js).
 */

const ROUTE = "/api/page-kept";

/** `{kept}` as the page's server wrote it, or null when the page was served as plain files. */
function served() {
  try {
    const value = JSON.parse(document.getElementById("page-kept")?.textContent || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null; // no page at all: under node
  }
}

const given = served();
const values = new Map(Object.entries(given?.kept || {}));
let sending = Promise.resolve();

/** The value kept under `key`, a string, or null. */
export function pageKept(key) {
  return values.get(key) ?? null;
}

/** Keep `value` under `key` (its text), or forget it (null). */
export function pageKeep(key, value) {
  const text = value == null ? null : String(value);
  if (pageKept(key) === text) return;
  if (text === null) values.delete(key);
  else values.set(key, text);
  if (!given) return;
  const body = JSON.stringify({ values: { [key]: text } });
  // one after the other, in the order made: two quick clicks end on the second
  sending = sending
    .then(() => fetch(ROUTE, { method: "PUT", headers: { "Content-Type": "application/json" }, body }))
    .catch(() => {
      // a server older than the page, or gone: kept for this visit
    });
}
