// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// Find on the page (Cmd+F, Ctrl+F), because the window does not have one.
//
// Since 0.1.8 the app opens in a window of its own (``window.py``). Neither WKWebView nor
// WebView2 carries a Find, and pywebview's Edit menu holds Cut, Copy, Paste and Select All only:
// a user who reached his settings with Cmd+F in the browser found nothing there (2026-09-20).
//
// Nothing is written into the page. app.js draws its screens again and again and compares what
// it drew, so wrapping matches in `<mark>` would fight it: the matches are Ranges handed to the
// CSS Custom Highlight API, which paints them without touching the tree. Where that API is
// missing, `window.find()` scrolls and selects instead, and the count is left out.
//
// In a browser the keys are left alone: its own Find is better than this one.

import { t } from "./i18n.js";

const ALL = "osxp-find";
const CURRENT = "osxp-find-current";

/** What never holds text a reader looks for. */
const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "SVG", "CANVAS"]);

let ranges = [];
let at = -1;

export function highlightsWork() {
  return typeof CSS !== "undefined" && Boolean(CSS.highlights) && typeof Highlight === "function";
}

/** Every visible text node of the screen being shown, in the order they are read. */
function textNodes(root) {
  const out = [];
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        if (child.nodeValue.trim()) out.push(child);
      } else if (child.nodeType === 1) {
        if (SKIP.has(child.tagName) || child.hidden) continue;
        // A closed <details> hides its body; its summary is read.
        if (child.tagName === "DETAILS" && !child.open) {
          const summary = child.querySelector("summary");
          if (summary) walk(summary);
          continue;
        }
        walk(child);
      }
    }
  };
  walk(root);
  return out;
}

/** The ranges of `needle` in the screen shown, case ignored. */
export function findRanges(root, needle) {
  const found = [];
  if (!needle) return found;
  const wanted = needle.toLowerCase();
  for (const node of textNodes(root)) {
    const hay = node.nodeValue.toLowerCase();
    let from = hay.indexOf(wanted);
    while (from !== -1) {
      const range = document.createRange();
      range.setStart(node, from);
      range.setEnd(node, from + wanted.length);
      found.push(range);
      from = hay.indexOf(wanted, from + wanted.length);
    }
  }
  return found;
}

function paint() {
  if (!highlightsWork()) return;
  CSS.highlights.set(ALL, new Highlight(...ranges));
  const here = ranges[at];
  if (here) CSS.highlights.set(CURRENT, new Highlight(here));
  else CSS.highlights.delete(CURRENT);
}

function clearPaint() {
  if (!highlightsWork()) return;
  CSS.highlights.delete(ALL);
  CSS.highlights.delete(CURRENT);
}

/** The match in view, roughly in the middle, as a browser does. */
function reveal() {
  const range = ranges[at];
  const el = range?.startContainer?.parentElement;
  if (el) el.scrollIntoView({ block: "center", behavior: "auto" });
}

function screenShown() {
  return document.querySelector(".screen:not([hidden])") || document.body;
}

export function findBar() {
  return {
    box: document.getElementById("find-bar"),
    input: document.getElementById("find-input"),
    count: document.getElementById("find-count"),
  };
}

/** Look again for what is in the field, from the top. */
export function findAgain({ keep = false } = {}) {
  const { input, count } = findBar();
  const needle = input.value;
  if (!highlightsWork()) {
    count.textContent = "";
    if (needle) window.find?.(needle, false, false, true);
    return;
  }
  const before = keep && ranges[at] ? ranges[at].startContainer : null;
  ranges = findRanges(screenShown(), needle);
  at = ranges.length ? 0 : -1;
  if (before) {
    const same = ranges.findIndex((r) => r.startContainer === before);
    if (same !== -1) at = same;
  }
  paint();
  if (ranges.length) reveal();
  count.textContent = !needle ? "" : ranges.length ? t("find.count", { n: at + 1, total: ranges.length }) : t("find.none");
  count.classList.toggle("is-warn", Boolean(needle) && ranges.length === 0);
}

export function findStep(by) {
  const { input, count } = findBar();
  if (!highlightsWork()) {
    if (input.value) window.find?.(input.value, false, by < 0, true);
    return;
  }
  if (!ranges.length) return;
  at = (at + by + ranges.length) % ranges.length;
  paint();
  reveal();
  count.textContent = t("find.count", { n: at + 1, total: ranges.length });
}

/** A Range into a node the page has replaced paints nothing, so an open bar follows the redraws.
 *
 * The Works screen redraws every second while a build runs; without this the highlights would
 * vanish a moment after they appeared, and the bar would look broken. Watched only while the bar
 * is open, and never oftener than every 200 ms. */
let watcher = null;
let soon = 0;

function watch() {
  const main = document.getElementById("main");
  if (!main || watcher) return;
  watcher = new MutationObserver(() => {
    clearTimeout(soon);
    soon = setTimeout(() => findAgain({ keep: true }), 200);
  });
  watcher.observe(main, { childList: true, subtree: true, characterData: true });
}

function unwatch() {
  clearTimeout(soon);
  watcher?.disconnect();
  watcher = null;
}

export function openFind() {
  const { box, input } = findBar();
  box.hidden = false;
  input.focus();
  input.select();
  if (input.value) findAgain();
  watch();
}

export function closeFind() {
  const { box, count } = findBar();
  box.hidden = true;
  count.textContent = "";
  ranges = [];
  at = -1;
  clearPaint();
  unwatch();
}

/** Another screen: look again in the one now shown. */
export function findForget() {
  if (findBar().box?.hidden !== false) return;
  findAgain({ keep: true });
}

/** The bar's own field and buttons, which a browser needs too: it is the same page there. */
export function bindFind() {
  const { box, input } = findBar();
  if (!box) return;
  input.addEventListener("input", () => findAgain());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      findStep(e.shiftKey ? -1 : 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeFind();
    }
  });
  document.getElementById("find-prev")?.addEventListener("click", () => findStep(-1));
  document.getElementById("find-next")?.addEventListener("click", () => findStep(1));
  document.getElementById("find-close")?.addEventListener("click", () => closeFind());
}

/**
 * Cmd+F and Ctrl+F, taken only in OrthoStudio XP's own window: a browser's own Find does more
 * than this one, and stealing the key there would be a loss.
 */
export function bindFindKeys() {
  const { box } = findBar();
  if (!box) return;
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "f") {
      e.preventDefault();
      openFind();
    } else if (e.key === "Escape" && !box.hidden) {
      closeFind();
    }
  });
}
