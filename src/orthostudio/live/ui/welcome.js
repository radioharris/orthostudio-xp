// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The app's question at its first start (orthostudio/live/app_start.py): Live, and this window
// becomes Live's; or building, and this window closes for OrthoStudio XP's, which the app opens.

import { askFrame, bindFrame } from "../static/embed.js";
import { applyStatic, detectLanguage, language, setLanguage } from "./strings.js";

const $ = (id) => document.getElementById(id);

async function choose(home) {
  for (const button of document.querySelectorAll(".welcome-choose")) button.disabled = true;
  try {
    await fetch("api/live/home", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ home, remember: $("remember").checked }),
    });
  } catch (_e) {
    // the answer is the window's to act on; nothing to say here
  }
  // in the app's frame: the part chosen is shown there; this page becomes Live's own for Live, and
  // is let go for building, the frame loading Live's page the first time Live is shown
  if (askFrame(home === "live" ? "live" : "build")) {
    if (home === "live") location.replace("./");
    return;
  }
  if (home === "live") {
    location.replace("./");
  } else {
    $("leaving").hidden = false; // the window closes by itself, and OrthoStudio XP's opens
  }
}

// In the browser, the app's Live goes a while after its page last spoke
// (orthostudio/live/web.py): this page asks for no status, so it says it is open.
const PRESENCE_EVERY_MS = 30000;

function present() {
  fetch("api/live/presence", { method: "POST" }).catch(() => {});
}

function boot() {
  present();
  setInterval(present, PRESENCE_EVERY_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") present();
  });
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  applyStatic(document);
  $("lang-select").addEventListener("change", (event) => {
    setLanguage(event.target.value, true);
    applyStatic(document);
  });
  $("choose-live").addEventListener("click", () => choose("live"));
  $("choose-build").addEventListener("click", () => choose("build"));
  // in the app's frame, its language and theme (embed.js); the question has no screens to give
  bindFrame({
    tabs: () => [],
    current: () => "",
    show: () => {},
    prefs: ({ language: wanted, theme }) => {
      if (wanted && wanted !== language()) {
        setLanguage(wanted, true);
        applyStatic(document);
      }
      if (theme !== undefined && theme !== null) {
        if (theme) document.documentElement.dataset.theme = theme;
        else delete document.documentElement.dataset.theme;
      }
    },
  });
}

if (typeof document !== "undefined") boot();
