// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
/**
 * The one way the pages make an element, each module importing it: Build's page, its map and its
 * cloud mode (app.js, map.js, clouds.js; its Settings through app.js), the legend of Build's map
 * and of Live's (legend.js), the new version's banner of both pages (update.js), Live's page and
 * its map (live/live.js, live/livemap.js, which import it as Live's server serves it, from
 * ../static/).
 */

/**
 * `tag` with its attributes (`attrs`, or null) and its children: `class`, `dataset`, `text`, an
 * `on…` function a listener, true an attribute without a value; null and false left out, of the
 * attributes and of the children, which may come in lists; a child not a node, a text.
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
