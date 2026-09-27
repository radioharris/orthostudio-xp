// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The app's question at its first start (orthostudio/live/app_start.py): Live, and this window
// becomes Live's; or building, and this window closes for OrthoStudio XP's, which the app opens.

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
  if (home === "live") {
    location.replace("./");
  } else {
    $("leaving").hidden = false; // the window closes by itself, and OrthoStudio XP's opens
  }
}

function boot() {
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  applyStatic(document);
  $("lang-select").addEventListener("change", (event) => {
    setLanguage(event.target.value, true);
    applyStatic(document);
  });
  $("choose-live").addEventListener("click", () => choose("live"));
  $("choose-build").addEventListener("click", () => choose("build"));
}

if (typeof document !== "undefined") boot();
