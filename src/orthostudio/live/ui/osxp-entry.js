// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// OrthoStudio XP Live's button in OrthoStudio XP's top bar (orthostudio/live/app_start.py): shown
// only when Live is switched on, its dot saying whether Live streams, a click opening Live's
// window, or bringing it to the front; and the question OrthoStudio XP's page asks before a build
// while Live streams. OrthoStudio XP's page loads it with one line; everything else of it is here,
// in its own words, EN and FR as the page's language.

const POLL_MS = 5000;
const WORDS = {
  en: { live: "Live", off: "stopped", starting: "starting", idle: "ready", streaming: "streaming",
    stopping: "stopping", leaving: "waiting for X-Plane", deleting: "deleting", title: "Open OrthoStudio XP Live",
    before_build: "OrthoStudio XP Live is streaming. A build uses a lot of processor time and may slow X-Plane and Live down." },
  fr: { live: "Live", off: "arrêté", starting: "démarre", idle: "prêt", streaming: "en cours",
    stopping: "s'arrête", leaving: "attend X-Plane", deleting: "efface", title: "Ouvrir OrthoStudio XP Live",
    before_build: "OrthoStudio XP Live est en vol. Un build utilise beaucoup le processeur et peut ralentir X-Plane et Live." },
};
const FLYING = new Set(["starting", "streaming"]);
const DOT = { off: "var(--skip)", idle: "var(--fg-3)", streaming: "var(--ok)" };

const words = () => (document.documentElement.lang || navigator.language || "en").toLowerCase().startsWith("fr")
  ? WORDS.fr
  : WORDS.en;

async function status() {
  try {
    const answer = await fetch("api/live-app", { cache: "no-store" });
    return answer.ok ? await answer.json() : null;
  } catch (_e) {
    return null;
  }
}

function makeButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tool-btn live-entry";
  button.style.cssText = "width: auto; padding: 0 8px; gap: 6px;";
  const dot = document.createElement("span");
  dot.style.cssText = "width: 8px; height: 8px; border-radius: 4px; flex: none;";
  const name = document.createElement("span");
  name.style.fontWeight = "600";
  const state = document.createElement("span");
  state.style.color = "var(--fg-2)";
  button.append(dot, name, state);
  button.addEventListener("click", () => {
    fetch("api/live-app/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).catch(() => {});
  });
  return { button, dot, name, state };
}

/** OrthoStudio XP's page asks this before a build starts (app.js askedBeforeBuild), in its own
 * language: while Live streams, a build may slow X-Plane and Live down. A question, never a
 * refusal. */
export async function beforeBuild(lang, ask = status) {
  const live = await ask();
  if (!live || !live.enabled || !FLYING.has(live.state)) return null;
  return (lang === "fr" ? WORDS.fr : WORDS.en).before_build;
}

async function boot() {
  const first = await status();
  const tools = document.querySelector(".topbar-tools");
  if (!first || !first.enabled || !tools) return;
  const parts = makeButton();
  tools.prepend(parts.button);
  const draw = (live) => {
    const w = words();
    const state = live && live.state ? live.state : "off";
    parts.dot.style.background = DOT[state] || "var(--warn)";
    parts.name.textContent = w.live;
    parts.state.textContent = w[state] || state;
    parts.button.title = w.title;
  };
  draw(first);
  setInterval(async () => draw(await status()), POLL_MS);
}

if (typeof window !== "undefined") {
  (window.osxpBeforeBuild ||= []).push((lang) => beforeBuild(lang));
  boot();
}
