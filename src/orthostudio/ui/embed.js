// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
//
// A page shown in the app's frame: a page of the app's own, on this computer, which holds the
// app's pages in frames, one shown at a time, under one top bar and one foot. The page leaves the
// frame what the frame shows once for all of them (the app's name, the language, the theme, the
// text size, the zoom, Quit), gives it its screens to show as tabs, and takes from it what the
// pilot chose there. Outside such a frame nothing changes: the page keeps its own bars.
//
// What the page leaves the frame is marked in its HTML (`data-frame-owned`), and hidden by
// styles.css once the page knows it is in one (`html.in-frame`). Its foot keeps its own items, the
// frame's text size and zoom laid over its right end: the page keeps room for them as wide as
// the frame says (`--frame-foot-w`), and tells the frame how tall its foot is.

/** Where the frame is served: the loopback alone. A website that showed the page in a frame of
 * its own is no frame of the app's, and the page keeps its own bars there. */
const LOOPBACK = /^http:\/\/127\.0\.0\.1:\d+$/;

/** The frame's origin when this page is shown in the app's frame, else "". */
export function frameOrigin(win = typeof window !== "undefined" ? window : null) {
  if (!win || win.parent === win) return "";
  const origin = win.location?.ancestorOrigins?.[0] || "";
  return LOOPBACK.test(origin) ? origin : "";
}

/** Whether this page is shown in the app's frame. */
export function inFrame(win) {
  return frameOrigin(win) !== "";
}

/** The step of the window's zoom a key asks for (Cmd or Ctrl with plus, minus, 0), else null. */
export function zoomStep(e) {
  if (!(e.metaKey || e.ctrlKey) || e.altKey) return null;
  if (e.key === "=" || e.key === "+") return 1;
  if (e.key === "-" || e.key === "_") return -1;
  if (e.key === "0") return 0;
  return null;
}

/**
 * Bind the page to the app's frame, when it is in one. `page` gives the page's own ways:
 * `tabs()` its screens, [{id, label}], in its language; `current()` the one shown; `show(id)`;
 * `prefs({language, theme, textSize})`, what the pilot chose in the frame; `ownPrefs()`, what the
 * page had kept of its own, which the frame takes the first time it has none; `shown()`, told
 * when the frame shows the page again after the other part (a resize is sent first); and
 * `busy()`, what the page is doing in a word the frame says beside its name while the other part
 * is shown ("" for nothing worth saying).
 *
 * Returns `tell()`, which gives the frame the tabs again (a screen shown, the language changed)
 * and what the page is doing (each time it may have changed); nothing happens outside a frame.
 */
export function bindFrame(page, win = typeof window !== "undefined" ? window : null) {
  const origin = frameOrigin(win);
  if (!origin) return () => {};
  win.document.documentElement.classList.add("in-frame");
  const post = (message) => win.parent.postMessage(message, origin);
  const tell = () =>
    post({ type: "page:tabs", tabs: page.tabs(), current: page.current(), busy: page.busy ? page.busy() : "" });
  win.addEventListener("message", (event) => {
    if (event.origin !== origin || event.source !== win.parent) return;
    const data = event.data || {};
    if (data.type === "frame:show") page.show(data.screen);
    else if (data.type === "frame:prefs") page.prefs(data.prefs || {});
    else if (data.type === "frame:shown") {
      // shown again after the other part: what measured itself while hidden measured nothing
      // (a map, the room under the top bar, a table's width), and measures again on a resize
      win.dispatchEvent(new win.Event("resize"));
      page.shown?.();
    } else if (data.type === "frame:foot") {
      // the room the frame's text size and zoom take over the right end of this page's own foot,
      // which follows their width (the text size, the language)
      const width = Number(data.width);
      if (Number.isFinite(width) && width >= 0) {
        win.document.documentElement.style.setProperty("--frame-foot-w", `${Math.ceil(width)}px`);
      }
    }
  });
  // and the frame's foot is as tall as this page's own, which grows on two lines when the window
  // is narrow, or with the text size: told each time it changes
  const bar = win.document.querySelector(".statusbar");
  if (bar) {
    const tellFoot = () => post({ type: "page:foot", height: bar.offsetHeight });
    if (typeof win.ResizeObserver !== "undefined") new win.ResizeObserver(tellFoot).observe(bar);
    tellFoot();
  }
  // the window's zoom is the frame's, and its keys too when they are typed in this page
  win.document.addEventListener("keydown", (e) => {
    const step = zoomStep(e);
    if (step === null) return;
    e.preventDefault();
    post({ type: "page:zoom", step });
  });
  post({ type: "page:ready", prefs: page.ownPrefs ? page.ownPrefs() : null });
  tell();
  return tell;
}

/** Ask the frame to show another of the app's pages (`part`), when this page is in one. */
export function askFrame(part, win = typeof window !== "undefined" ? window : null) {
  const origin = frameOrigin(win);
  if (!origin) return false;
  win.parent.postMessage({ type: "page:choose", part }, origin);
  return true;
}
