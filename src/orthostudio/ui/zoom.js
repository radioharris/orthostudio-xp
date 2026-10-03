// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The page's own zoom (Cmd+plus, Cmd+minus, Cmd+0), because the window has none.
//
// A browser gives every page these three keys; the window of 0.1.8 gives none. A WKWebView will
// not zoom without being asked in Objective-C, and pywebview turns the browser's own shortcuts
// off in WebView2 (`AreBrowserAcceleratorKeysEnabled` follows `debug`), so Windows lost them as
// well. A user found the text bigger in the window than in his browser and had no way back
// (2026-09-20).
//
// The window does the scaling, through `PageTools.set_zoom` (window.py): a CSS zoom would take
// `100vh` with it and cut the map off at the foot of the page, which is the bug 0.1.8 just fixed.
// Where the window will not say, nothing is scaled and nothing is remembered.
//
// The keys showed nowhere: a user on an ultrawide screen, whose text was too small, asked for a
// way to make it bigger, which was there all along (TinkerNZ, 2026-10-03). The zoom has its own
// control at the foot of the page: − 100 % +, the level bringing it back to 100 %.

import { applyStatic, t } from "./i18n.js";

/** The steps a browser walks through, and the one it starts on. */
export const STEPS = [0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
export const NORMAL = 1;

const KEY = "osxp.zoom";

/** The step after `from`, `by` places along; the ends hold. */
export function nextZoom(from, by) {
  const near = STEPS.reduce((best, s) => (Math.abs(s - from) < Math.abs(best - from) ? s : best), STEPS[0]);
  const where = STEPS.indexOf(near) + by;
  return STEPS[Math.max(0, Math.min(STEPS.length - 1, where))];
}

export function savedZoom() {
  try {
    const saved = Number(localStorage.getItem(KEY));
    return Number.isFinite(saved) && saved > 0 ? saved : NORMAL;
  } catch (_e) {
    return NORMAL; // a page whose storage is refused simply opens at its own size
  }
}

function remember(factor) {
  try {
    if (factor === NORMAL) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(factor));
  } catch (_e) {
    // ignore: the zoom holds for this window, and is asked for again next time
  }
}

/** Ask the window to scale the page; whether it did. */
async function ask(factor) {
  const api = typeof window !== "undefined" ? window.pywebview?.api : null;
  if (!api?.set_zoom) return false;
  try {
    return Boolean(await api.set_zoom(factor));
  } catch (_e) {
    return false;
  }
}

/** Whether the keys are the Mac's: the control's help names ⌘ there, Ctrl elsewhere. */
function isMac() {
  if (typeof navigator === "undefined") return false;
  const platform = navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "";
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** The control's parts (`data-zoom` in index.html): the step each one takes (0 is 100 %), and the
 * words of its help, which name the keys of the Mac there and Ctrl elsewhere. */
const PARTS = [
  ["out", -1, "app.zoom_out", "app.zoom_out_mac"],
  ["level", 0, "app.zoom_level", "app.zoom_level_mac"],
  ["in", 1, "app.zoom_in", "app.zoom_in_mac"],
];

/**
 * Cmd+plus, Cmd+minus and Cmd+0, and the control `box` at the foot of the page. Called only in
 * the window of its own: a browser has these keys, and its own zoom, already. The control shows
 * once the window has said it scales the page, and stays hidden where it will not. `say` is given
 * the new size in words when a key changed it, the way a browser shows it for a moment.
 *
 * Returns `refresh`, which writes the level again in the page's language once it changed.
 */
export function bindZoom(say, box = null) {
  let now = savedZoom();
  const level = box?.querySelector("[data-zoom='level']") || null;
  const show = () => {
    if (level) level.textContent = t("app.zoom_percent", { percent: Math.round(now * 100) });
  };
  const set = async (factor, { quiet = false } = {}) => {
    if (!(await ask(factor))) return;
    now = factor;
    remember(factor);
    show();
    if (!quiet) say?.(t("app.zoom_at", { percent: Math.round(factor * 100) }));
  };
  // the size this window was left at, and whether the window scales the page at all
  ask(now).then((scales) => {
    if (!scales || !box) return;
    show();
    box.hidden = false;
  });
  const mac = isMac();
  for (const [part, by, words, macWords] of box ? PARTS : []) {
    const button = box.querySelector(`[data-zoom='${part}']`);
    if (!button) continue;
    // its words follow the page's language (i18n.js applyStatic), its keys the system's
    button.dataset.i18nTitle = mac ? macWords : words;
    button.dataset.i18nAriaLabel = mac ? macWords : words;
    button.addEventListener("click", () => set(by ? nextZoom(now, by) : NORMAL, { quiet: true }));
  }
  if (box) applyStatic(box);
  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    if (e.key === "=" || e.key === "+") set(nextZoom(now, 1));
    else if (e.key === "-" || e.key === "_") set(nextZoom(now, -1));
    else if (e.key === "0") set(NORMAL);
    else return;
    e.preventDefault();
  });
  return show;
}
