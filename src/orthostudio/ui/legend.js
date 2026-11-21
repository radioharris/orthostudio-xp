// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
/**
 * The legend laid on a map (styles.css .map-legend), the same on Build's Plan map (map.js) and on
 * Live's map (live/livemap.js): folded away to one button, or open with its head and the chevron
 * that folds it; its lines, each a mark (.legend-swatch) and its words, some with the box that
 * shows or hides what they mark; a group's title with its own chevron ("Show"). Each page gives
 * its words, in its language, and the key under which the legend and each group are kept folded
 * or open for the viewer (kept.js), here alone; the page keeps what is shown; nothing here knows
 * what a map draws.
 */

import { h } from "./dom.js";
import { pageKeep, pageKept } from "./kept.js";

/** Whether the legend, or a group of it, kept under `key` is open: open unless folded. */
export function legendOpen(key) {
  return pageKept(key) !== "0";
}

function keepOpen(key, open) {
  pageKeep(key, open ? "1" : "0");
}

/**
 * The legend in `box`, drawn anew, folded or open as kept under `key`. Folded, one ordinary button
 * brings it back (a user folded it to see the map under it, 2026-09-25); open, the line `head()`
 * gives with the chevron at its end, one line high, then the lists `body()` gives, both asked only
 * then. The control that had the focus has it again, named by its `data-keep`: a redraw on every
 * zoom took it to the first box, whichever had it. `words`: {show, hide}, the button's and the
 * chevron's.
 */
export function drawLegend(box, { key, words, head, body }) {
  const open = legendOpen(key);
  const setOpen = (on) => {
    keepOpen(key, on);
    drawLegend(box, { key, words, head, body });
  };
  const focused = box.contains(document.activeElement) ? document.activeElement.closest("[data-keep]")?.dataset.keep : null;
  box.classList.toggle("is-folded", !open);
  if (open) {
    const fold = h("button", { type: "button", class: "legend-fold", "data-keep": "fold", "aria-expanded": "true", title: words.hide, "aria-label": words.hide, onclick: () => setOpen(false) });
    box.replaceChildren(h("div", { class: "legend-head" }, head(), fold), ...body());
  } else {
    box.replaceChildren(h("button", { type: "button", class: "btn legend-unfold", "data-keep": "fold", "aria-expanded": "false", onclick: () => setOpen(true) }, words.show));
  }
  if (focused) box.querySelector(`[data-keep="${focused}"]`)?.focus();
}

/** A group's title with a chevron that folds its lines away, as the legend itself folds (a user,
 * 2026-09-26), open or folded as kept under `key` (legendOpen() tells the page whether to give
 * its lines): `keep` names the chevron for the focus, `words` {hide, show} its hover,
 * `redraw` draws the legend again once it is clicked. */
export function groupHead(title, key, keep, words, redraw) {
  const open = legendOpen(key);
  const button = h("button", {
    type: "button",
    class: open ? "legend-fold" : "legend-fold is-closed",
    "data-keep": keep,
    "aria-expanded": open ? "true" : "false",
    title: open ? words.hide : words.show,
    "aria-label": open ? words.hide : words.show,
    onclick: () => {
      keepOpen(key, !open);
      redraw();
    },
  });
  return h("div", { class: "legend-head legend-group-head" }, h("p", { class: "legend-title" }, title), button);
}

/** A list of the legend's lines; `marks`, a list whose lines without a box keep the box's room,
 * so that the signs line up with those of the lines that have one (styles.css .legend-marks). */
export function legendList(lines, marks = false) {
  return h("ul", { class: marks ? "legend-marks" : null }, ...lines);
}

/** A line without a box: its mark (`swatch`, its class) and its words; `cls`, the line's own
 * class (a zone's level, whose colour the mark takes). */
export function legendRow(swatch, text, cls = null) {
  return h("li", { class: cls }, h("span", { class: `legend-swatch ${swatch}`, "aria-hidden": "true" }), text);
}

/**
 * A line with the box that shows or hides what it marks, the page keeping the choice: `keep`
 * names the box for the focus; `idle`, ticked but not drawn at this zoom, its words dimmed, why
 * in `hint`, on hover, where it was in brackets after the name (a user, 2026-09-26).
 */
export function legendToggle({ swatch, text, hint = null, checked, keep, idle = false, onChange }) {
  const input = h("input", { type: "checkbox", checked, "data-keep": keep, onchange: (ev) => onChange(ev.target.checked) });
  return h("li", { class: idle ? "legend-toggle is-idle" : "legend-toggle" },
    h("label", { title: hint }, input, h("span", { class: `legend-swatch ${swatch}`, "aria-hidden": "true" }), text));
}
