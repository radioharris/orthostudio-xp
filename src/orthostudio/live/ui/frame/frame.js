// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
//
// The app's frame (frame.html, orthostudio/live/frame.py): OrthoStudio XP and OrthoStudio XP Live
// in one window, one top bar and one foot for both. Build | Live shows one part or the other, the
// other's state in a word; the part shown gives its screens as the bar's tabs; the language, the
// theme, the text size and the zoom are chosen here once for both, kept by the app and given to
// each page (static/embed.js on its side). Each part's page is in a frame of its own, loaded the
// first time the part is shown and kept alive after: a build or a stream goes on while the other
// part is shown.

import { applyStatic, detectLanguage, language, setLanguage } from "../static/i18n.js";
import { TEXT_SIZES, applyTextSize, bindTextSize, savedTextSize } from "../static/textsize.js";
import { NORMAL, bindZoom, savedZoom } from "../static/zoom.js";

export const PARTS = ["build", "live"];
const LOOPBACK = /^http:\/\/127\.0\.0\.1:\d+$/;
const ZOOM_KEY = "osxp.zoom"; // zoom.js's own, read by it as it binds
const THEME_KEY = "osxp.theme";

/** The frame's own words; the rest are OrthoStudio XP's (static/i18n.js). */
export const WORDS = {
  en: {
    build: "Build", live: "Live", parts: "OrthoStudio XP or OrthoStudio XP Live",
    building: "build under way", starting: "starting", streaming: "in flight", stopping: "stopping",
    leaving: "waiting for X-Plane", deleting: "deleting",
  },
  fr: {
    build: "Construire", live: "Live", parts: "OrthoStudio XP ou OrthoStudio XP Live",
    building: "build en cours", starting: "démarre", streaming: "en vol", stopping: "s'arrête",
    leaving: "attend X-Plane", deleting: "efface",
  },
};

/** What the frame says beside a part's name: what its page said it is doing (embed.js `busy`), in
 * a word, or "" when nothing worth saying. */
export function stateWord(part, busy, lang = "en") {
  const words = WORDS[lang] || WORDS.en;
  if (part === "build") return busy === "building" ? words.building : "";
  return ["starting", "streaming", "stopping", "leaving", "deleting"].includes(busy) ? words[busy] : "";
}

/** The step of the zoom a page asked for (embed.js page:zoom), as the key that asks it here. */
export function zoomKey(step) {
  return step > 0 ? "=" : step < 0 ? "-" : "0";
}

const $ = (id) => document.getElementById(id);
const word = (key) => (WORDS[language()] || WORDS.en)[key] || key;

/** What the frame starts from, which frame.py writes into the page as it serves it. */
function readGiven() {
  try {
    return JSON.parse($("frame-given")?.textContent || "{}") || {};
  } catch (_e) {
    return {};
  }
}

const given = typeof document !== "undefined" ? readGiven() : {};
const frames = {};
const loaded = { build: false, live: false };
const tabsOf = { build: [], live: [] };
const currentOf = { build: "", live: "" };
const footOf = { build: null, live: null }; // the height of each page's own foot (embed.js page:foot)
const busyOf = { build: "", live: "" }; // what each page said it is doing (embed.js page:tabs)
const welcomeOnce = { live: Boolean(given.welcome) };
// what nothing kept yet, taken once from the first page that has it (its own choices of before)
const pending = new Set(given.kept ? [] : ["language", "theme", "textSize"]);
let shownPart = null;
let zoomRefresh = null;
let textSizeRefresh = null;
let keptAs = null;

/** The app's side of the frame (frame.py FrameTools), once pywebview has written it in. */
function tools() {
  if (window.pywebview?.api?.ensure) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
}

/** The kept choices, written where the shared modules read theirs: the zoom's before it binds. */
function seed(prefs) {
  try {
    if (prefs.zoom && prefs.zoom !== NORMAL) localStorage.setItem(ZOOM_KEY, String(prefs.zoom));
    else localStorage.removeItem(ZOOM_KEY);
  } catch (_e) {
    // the window says the zoom at its own size: nothing worse
  }
}

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || "";
  } catch (_e) {
    return "";
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "dark" || theme === "light") root.dataset.theme = theme;
  else delete root.dataset.theme;
  try {
    if (theme) localStorage.setItem(THEME_KEY, theme);
    else localStorage.removeItem(THEME_KEY);
  } catch (_e) {
    // kept by the app all the same (keep)
  }
}

/** What the pilot chose here, as the app keeps it (frame.py clean_prefs). */
function choices() {
  return {
    language: language(),
    theme: document.documentElement.dataset.theme || "",
    textSize: savedTextSize(),
    zoom: savedZoom(),
  };
}

/** Keep what the pilot chose with the app, which gives it back at the next start. */
async function keep() {
  const now = choices();
  const said = JSON.stringify(now);
  if (said === keptAs) return;
  keptAs = said;
  try {
    await (await tools()).keep(now);
  } catch (_e) {
    keptAs = null; // asked again at the next change
  }
}

// ------------------------------------------------------------------ the parts' pages

function post(part, message) {
  if (!loaded[part]) return;
  frames[part]?.contentWindow?.postMessage(message, "*");
}

/** The room the foot takes over the right end of a page's own (styles.css html.in-frame). */
function footRoom() {
  const left = $("frame-foot").getBoundingClientRect().left;
  return Math.max(0, Math.ceil(window.innerWidth - left + 12));
}

/** Give `part`'s page what the pilot chose here, and the room the foot takes. */
function tellPage(part) {
  const { language: lang, theme, textSize } = choices();
  post(part, { type: "frame:prefs", prefs: { language: lang, theme, textSize } });
  post(part, { type: "frame:foot", width: footRoom() });
}

const tellPages = () => PARTS.forEach(tellPage);

/** Show `part`: its page loaded the first time, and what it takes started (frame.py ensure). */
async function show(part) {
  if (!PARTS.includes(part)) return;
  const again = shownPart !== null && shownPart !== part;
  shownPart = part;
  for (const p of PARTS) {
    frames[p].hidden = p !== part;
    document.querySelector(`.frame-part[data-part='${p}']`)?.setAttribute("aria-pressed", String(p === part));
  }
  drawTabs();
  drawStates();
  placeFoot();
  if (!loaded[part]) {
    loaded[part] = true;
    try {
      const url = await (await tools()).ensure(part, Boolean(welcomeOnce[part]));
      welcomeOnce[part] = false;
      if (url) frames[part].src = url;
      else loaded[part] = false;
    } catch (_e) {
      loaded[part] = false; // asked again at the next click
    }
  } else if (again) {
    post(part, { type: "frame:shown" }); // what measured itself hidden measures again
  }
  frames[part].focus();
}

/** Forget `part`'s page, loaded again the next time it is shown: the welcome, once answered. */
function forget(part) {
  loaded[part] = false;
  frames[part].src = "about:blank";
  tabsOf[part] = [];
  currentOf[part] = "";
  footOf[part] = null;
  busyOf[part] = "";
}

/** Take, once, what a page had kept of its own, where the app kept nothing yet. */
function adopt(own) {
  if (!pending.size || !own) return;
  if (pending.has("language") && (own.language === "en" || own.language === "fr")) {
    pending.delete("language");
    if (own.language !== language()) {
      setLanguage(own.language, true);
      languageChanged();
    }
  }
  if (pending.has("theme") && typeof own.theme === "string") {
    pending.delete("theme");
    applyTheme(own.theme);
  }
  if (pending.has("textSize") && TEXT_SIZES.includes(Number(own.textSize))) {
    pending.delete("textSize");
    applyTextSize(Number(own.textSize));
    textSizeRefresh?.();
  }
  keep();
  tellPages();
}

function heard(event) {
  const part = PARTS.find((p) => frames[p]?.contentWindow === event.source);
  if (!part || !LOOPBACK.test(event.origin)) return;
  const data = event.data || {};
  if (data.type === "page:ready") {
    adopt(data.prefs);
    tellPage(part);
  } else if (data.type === "page:tabs") {
    // told at each of its statuses (Live's every second): drawn again only when it changed, so
    // that the tabs under the pointer stay as they are
    const tabs = Array.isArray(data.tabs) ? data.tabs : [];
    const current = data.current || "";
    const busy = typeof data.busy === "string" ? data.busy : "";
    const moved = JSON.stringify([tabs, current]) !== JSON.stringify([tabsOf[part], currentOf[part]]);
    const changed = busy !== busyOf[part];
    tabsOf[part] = tabs;
    currentOf[part] = current;
    busyOf[part] = busy;
    if (moved && part === shownPart) drawTabs();
    if (changed) drawStates();
  } else if (data.type === "page:zoom") {
    // the zoom's keys typed in a page: the window's zoom, which is the frame's (zoom.js)
    const key = zoomKey(Number(data.step) || 0);
    document.dispatchEvent(new KeyboardEvent("keydown", { key, metaKey: true, ctrlKey: true, cancelable: true }));
  } else if (data.type === "page:choose") {
    if (part === "live" && data.part === "build") forget("live"); // the welcome, answered
    show(data.part);
  } else if (data.type === "page:foot") {
    footOf[part] = Number(data.height) > 0 ? Number(data.height) : null;
    if (part === shownPart) placeFoot();
  }
}

// ------------------------------------------------------------------ the bar and the foot

function drawParts() {
  for (const p of PARTS) {
    const name = document.querySelector(`.frame-part[data-part='${p}'] .frame-part-name`);
    if (name) name.textContent = word(p);
  }
  $("frame-parts").setAttribute("aria-label", word("parts"));
}

/** The part shown's screens, as its page gave them: a click shows one there. */
function drawTabs() {
  const part = shownPart;
  $("nav").replaceChildren(...(part ? tabsOf[part] : []).map(({ id, label }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-btn";
    button.dataset.screen = id;
    button.textContent = label;
    if (id === currentOf[part]) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => post(part, { type: "frame:show", screen: id }));
    return button;
  }));
}

/** The other part's state beside its name; nothing beside the part shown, whose page says it. */
function drawStates() {
  for (const p of PARTS) {
    const text = p === shownPart ? "" : stateWord(p, busyOf[p], language());
    const el = $(`state-${p}`);
    el.textContent = text;
    el.hidden = !text;
  }
}

/** The foot over the right end of the shown page's own, as tall as it; on its own where the page
 * has none (the welcome). */
function placeFoot() {
  const height = shownPart ? footOf[shownPart] : null;
  const foot = $("frame-foot");
  foot.classList.toggle("frame-foot-alone", !height);
  foot.style.height = height ? `${height}px` : "";
}

function languageChanged() {
  applyStatic(document);
  $("lang-select").value = language();
  zoomRefresh?.();
  textSizeRefresh?.();
  drawParts();
  drawStates();
}

/**
 * The question before quitting while something runs, in OrthoStudio XP's own dialog (#quit-confirm,
 * put here from its page by frame.py): what runs said by the app (frame.py quit_words), Stay or
 * Quit; nothing runs, gone at once. The frame's Quit and the app's (Cmd+Q, its menu's, the
 * Dock's, the close button on Windows and Linux: frame.py may_quit) ask here alike, as OrthoStudio
 * XP's page asked before 1.1 (the user, 2026-10-09: not a box of the system's).
 */
async function askQuit() {
  const dialog = $("quit-confirm");
  if (dialog.open) return;
  const api = await tools();
  const { ask, lines } = await api.quit_words();
  if (!ask) {
    await api.quit_now();
    return;
  }
  $("quit-confirm-text").replaceChildren(...lines.map((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    return p;
  }));
  dialog.returnValue = "";
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close(); // as Stay
  };
  dialog.addEventListener("close", () => {
    if (dialog.returnValue === "quit") api.quit_now();
  }, { once: true });
  dialog.showModal();
}

function toast(text) {
  const box = $("toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 2500);
}

function boot() {
  frames.build = $("part-build");
  frames.live = $("part-live");
  const prefs = given.prefs || {};
  if (given.kept) {
    seed(prefs);
    setLanguage(prefs.language || detectLanguage(), true);
    applyTheme(prefs.theme || "");
    applyTextSize(prefs.textSize || 100);
  } else {
    setLanguage(detectLanguage());
    applyTheme(storedTheme());
    applyTextSize(savedTextSize());
  }
  keptAs = given.kept ? JSON.stringify(choices()) : null;
  if (given.version) $("brand-version").textContent = given.version;
  applyStatic(document);
  $("lang-select").value = language();
  drawParts();

  $("lang-select").addEventListener("change", (event) => {
    pending.delete("language");
    setLanguage(event.target.value, true);
    languageChanged();
    tellPages();
    keep();
  });
  $("theme-btn").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme === "dark" || (!root.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    pending.delete("theme");
    applyTheme(dark ? "light" : "dark");
    tellPages();
    keep();
  });
  // OrthoStudio XP's own Quit (frame.py frame_page), which its page shows once its engine may stop
  $("quit-btn").hidden = false;
  $("quit-btn").addEventListener("click", () => {
    askQuit().catch(() => {}); // the app's own Quit stays
  });
  // the app's Quit asks here (frame.py may_quit): true once the dialog can be shown, else the app
  // asks in the system's box
  window.askQuit = () => {
    if (!$("quit-confirm") || !window.pywebview?.api?.quit_words) return false;
    askQuit().catch(() => {});
    return true;
  };
  for (const button of document.querySelectorAll(".frame-part")) {
    button.addEventListener("click", () => show(button.dataset.part));
  }

  textSizeRefresh = bindTextSize($("text-size"));
  $("text-size").addEventListener("change", () => {
    pending.delete("textSize");
    tellPages();
    keep();
  });
  zoomRefresh = bindZoom(toast, $("status-zoom"));
  // the zoom's level is written again whenever it changes: kept then (keep says nothing new else)
  const level = document.querySelector("#status-zoom [data-zoom='level']");
  if (level) new MutationObserver(() => keep()).observe(level, { childList: true, characterData: true, subtree: true });
  // the room the foot takes follows its width: the text size, the language, the zoom shown or not
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => PARTS.forEach((p) => post(p, { type: "frame:foot", width: footRoom() }))).observe($("frame-foot"));

  window.addEventListener("message", heard);
  window.showPart = (part) => show(part); // frame.py show_part
  show(PARTS.includes(given.first) ? given.first : "build");
}

if (typeof document !== "undefined" && document.getElementById("part-build")) boot();
