// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.

/**
 * The new version's banner, at the top of Build's page and of Live's: the version said, then
 * *Install and relaunch* where the app replaces itself (orthostudio/self_update.py), its notes,
 * and *Not now*, which hides it on both pages until a newer one is out. While it installs: its one
 * bar, then the words of the hand-over. Each page gives its own way to ask its server, which
 * answers the same paths (Build's engine; Live's server relays to it, orthostudio/live/update_relay.py).
 */

import { language, t, tOpt } from "./i18n.js";

const POLL_MS = 500;
/** The states of a try going on (orthostudio/self_update.py UNDER_WAY). */
const UNDER_WAY = ["preparing", "starting", "installing"];

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

/**
 * The banner of one page. `request(method, path, body)`: the page's server, its answer or a
 * thrown error; `place`: where the banner goes, first; `resized`: told when the banner comes or
 * goes; `failed(error)`: a refused request said; `gone()`: the page's server has stopped;
 * `earlierLater()`: a version *Not now* was said to before it was kept for both pages.
 * Returns `load` (ask again), `render` (in a new language) and `installing` (the app quits for it).
 */
export function bindUpdate({ request, place, resized = () => {}, failed = () => {}, gone = () => false, earlierLater = () => null }) {
  let offer = null; // GET /api/update
  let now = null; // the try's state, while one goes on
  let timer = 0;
  let note = null;

  const phase = () => (UNDER_WAY.includes(now?.state) ? now.state : "offer");

  async function load() {
    try {
      offer = await request("GET", "/api/update");
    } catch (_err) {
      return; // no answer, or a server older than the question: nothing to say
    }
    render();
    if (offer?.installable && !now) {
      // a try under way, started from the other page or before this one was loaded: followed here
      const state = await request("GET", "/api/update/install").catch(() => null);
      if (state && UNDER_WAY.includes(state.state)) await onState(state);
    }
  }

  function hidden() {
    return !offer?.available || !offer.url || offer.later || earlierLater() === offer.latest;
  }

  function render() {
    const shown = phase();
    if (shown === "offer" && hidden()) {
      if (note) {
        note.remove();
        note = null;
        resized();
      }
      return;
    }
    const tried = offer?.last_try;
    const said = shown === "offer" ? `${offer.latest} ${tried?.version || ""} ${tried?.reason || ""}` : "";
    const key = `${language()} ${shown} ${said}`;
    if (note?.dataset.phase === key) {
      if (shown === "preparing") progress();
      return; // the same buttons: a click under way is not lost to a new one
    }
    const fresh = !note;
    if (fresh) note = el("p", { id: "update-note", class: "window-note update-note", role: "status" });
    note.dataset.phase = key;
    note.replaceChildren(...parts(shown));
    if (fresh) place.prepend(note);
    if (shown === "preparing") progress();
    resized();
  }

  function parts(shown) {
    const version = now?.version || offer?.latest || "";
    if (shown === "preparing") {
      return [
        el("span", { class: "update-progress" }),
        el("span", { class: "progress update-bar", "aria-hidden": "true" }, el("span", { class: "update-fill" })),
        el("button", { type: "button", class: "btn", onclick: cancel }, t("app.update_cancel")),
      ];
    }
    if (shown === "starting") return [t("app.update_starting")];
    if (shown === "installing") return [t("app.update_installing", { version })];
    let words = t("app.update_available", { version: offer.latest, current: offer.current });
    const tried = offer.last_try;
    if (tried && tried.version === offer.latest) {
      const why = tOpt(`app.update_why.${tried.reason}`) ?? t("app.update_why.unfinished");
      words += ` ${t("app.update_failed", { version: tried.version, why })}`;
    }
    const buttons = [];
    if (offer.installable) {
      buttons.push(el("button", { type: "button", class: "btn btn-primary", onclick: () => install(false) }, t("app.update_install")));
    }
    // A link, not a button calling window.open: the window hands a clicked target="_blank" link
    // to the system's browser (pywebview, OPEN_EXTERNAL_LINKS_IN_BROWSER), and only a link.
    const notes = offer.installable ? t("app.update_notes") : t("app.update_open");
    buttons.push(
      el("a", { class: offer.installable ? "btn" : "btn btn-primary", href: offer.url, target: "_blank", rel: "noopener" }, notes),
      el("button", { type: "button", class: "btn", onclick: later }, t("app.update_later")),
    );
    return [el("span", null, words), ...buttons];
  }

  /** The bar and its words: the bytes the engine counted, of all it does while the app is open. */
  function progress() {
    const percent = now?.total > 0 ? Math.floor((100 * (now.done || 0)) / now.total) : 0;
    const version = now?.version || offer?.latest || "";
    note.querySelector(".update-progress").textContent = t("app.update_preparing", { version, percent });
    note.querySelector(".update-fill").style.width = `${percent}%`;
  }

  async function later() {
    const version = offer.latest;
    offer = { ...offer, later: true };
    render();
    await request("POST", "/api/update/later", { version }).catch(() => null);
  }

  /** *Install and relaunch*: the try's state is the answer, `ask` first while a build or Live runs
   * that the app's quitting stops, then the page follows it until the app quits. */
  async function install(force) {
    let state;
    try {
      state = await request("POST", "/api/update/install", { force });
    } catch (err) {
      failed(err);
      return;
    }
    await onState(state);
  }

  async function onState(state) {
    if (state.state === "ask") {
      // asked when the try starts, and again if a build or Live started while it prepared
      now = null;
      render();
      if (await confirm(state.running)) await install(true);
      return;
    }
    if (state.state === "failed" || state.state === "idle") {
      now = null;
      await load(); // the banner again, saying why when it failed
      return;
    }
    now = state;
    render();
    if (state.state !== "installing") follow();
  }

  function follow() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      let state;
      try {
        state = await request("GET", "/api/update/install");
      } catch (_err) {
        // a request lost while it prepares is asked again; once handed over, the server goes,
        // and the page says so in the install's words
        if (!gone() && now?.state === "preparing") follow();
        return;
      }
      await onState(state);
    }, POLL_MS);
  }

  async function cancel() {
    try {
      await request("DELETE", "/api/update/install");
    } catch (err) {
      failed(err);
    }
  }

  /** The question before installing while something runs: what the app's quitting stops. */
  function confirm(running) {
    let dialog = document.getElementById("update-confirm");
    if (!dialog) {
      dialog = el(
        "dialog",
        { id: "update-confirm", class: "dialog", role: "alertdialog", "aria-labelledby": "update-confirm-title", "aria-describedby": "update-confirm-text" },
        el(
          "form",
          { method: "dialog" },
          el("h2", { class: "dialog-title", id: "update-confirm-title" }),
          el("div", { class: "dialog-text", id: "update-confirm-text" }),
          el(
            "div",
            { class: "dialog-actions" },
            el("button", { type: "submit", class: "btn", value: "stay", autofocus: "" }),
            el("button", { type: "submit", class: "btn btn-primary", value: "install" }),
          ),
        ),
      );
      document.body.append(dialog);
    }
    if (dialog.open) return Promise.resolve(false);
    dialog.querySelector(".dialog-title").textContent = t("update.title", { version: offer?.latest || "" });
    const lines = [t("update.text")];
    if (running?.build) lines.push(t("update.build"));
    if (running?.live) lines.push(running.flying ? t("update.flying") : t("update.live"));
    dialog.querySelector(".dialog-text").replaceChildren(...lines.map((line) => el("p", null, line)));
    const [stay, go] = dialog.querySelectorAll("button");
    stay.textContent = t("app.update_later");
    go.textContent = t("app.update_install");
    dialog.returnValue = "";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "install"), { once: true });
      dialog.showModal();
    });
  }

  return { load, render, installing: () => ["starting", "installing"].includes(now?.state) };
}
