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

import { t } from "./i18n.js";

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

/**
 * Cmd+plus, Cmd+minus and Cmd+0. Called only in the window of its own: a browser has these keys
 * already. `say` is given the new size in words, the way a browser shows it for a moment.
 */
export function bindZoom(say) {
  let now = savedZoom();
  const set = async (factor) => {
    if (!(await ask(factor))) return;
    now = factor;
    remember(factor);
    say?.(t("app.zoom_at", { percent: Math.round(factor * 100) }));
  };
  if (now !== NORMAL) ask(now); // the size this window was left at
  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    if (e.key === "=" || e.key === "+") set(nextZoom(now, 1));
    else if (e.key === "-" || e.key === "_") set(nextZoom(now, -1));
    else if (e.key === "0") set(NORMAL);
    else return;
    e.preventDefault();
  });
}
