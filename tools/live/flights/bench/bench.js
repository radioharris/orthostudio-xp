// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The test flights' window (tools/live/flights/bench.py): what to fly, the flight under way, the
// results. Everything it shows comes from the API; it keeps nothing of its own.
"use strict";

const $ = (id) => document.getElementById(id);
const WITH = { none: "X-Plane seul", live: "Live", autoortho: "AutoOrtho" };
let since = 0;
let wasRunning = null;
let options = null;

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `erreur ${res.status}`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v; // only the server's escaped report
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

function minutes(m) {
  if (m >= 90) return `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, "0")}`;
  return `${Math.round(m)} min`;
}

// ---- the choice ---------------------------------------------------------------------------------

function choice(input, label, note, extra, tip) {
  const node = el("label", { class: "choice" }, input,
    el("span", { class: "choice-text" }, label, note && el("span", { class: "choice-note", text: note }), extra));
  if (tip) node.title = tip;
  return node;
}

// "2026-09-29 152923" as "29 sept. 15:29"
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
function when(stamp) {
  const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d)(\d\d)/.exec(stamp);
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]}` : stamp;
}

function showOptions(opts) {
  options = opts;
  const scs = $("scenario-list");
  scs.replaceChildren();
  for (const s of opts.scenarios) {
    const box = el("input", { type: "checkbox", name: "scenario", value: s.name });
    if (s.error) box.disabled = true;
    const label = el("span", { class: "choice-label", text: s.title || s.name },
      s.minutes !== undefined ? el("span", { class: "num", text: minutes(s.minutes) }) : null);
    scs.append(choice(box, label, s.error || s.name, null, s.about));
  }
  const camps = $("campaign-list");
  camps.replaceChildren();
  opts.campaigns.forEach((c, i) => {
    const radio = el("input", { type: "radio", name: "campaign", value: c.name });
    if (i === 0) radio.checked = true;
    if (c.error) radio.disabled = true;
    const label = el("span", { class: "choice-label", text: c.title || c.name },
      c.minutes !== undefined ? el("span", { class: "num", text: `environ ${minutes(c.minutes)}` }) : null);
    const runs = c.runs ? el("ol", { class: "bench-runs" }, ...c.runs.map((r) => el("li", { text: r }))) : null;
    const about = c.error || `${c.name}, sur ${(c.scenarios || []).join(", ")}`;
    camps.append(choice(radio, label, about, runs, c.about));
  });
  if (!opts.autoortho) {
    $("ao-choice").classList.add("is-disabled");
    $("ao-choice").querySelector("input").disabled = true;
    $("ao-note").textContent = "introuvable : les sources et le Python d'AutoOrtho Dev manquent";
  }
  refreshChoice();
}

function picked() {
  const mode = document.querySelector("input[name=mode]:checked").value;
  if (mode === "campaign") {
    const c = document.querySelector("input[name=campaign]:checked");
    return { campaign: c ? c.value : "" };
  }
  const streamer = document.querySelector("input[name=streamer]:checked").value;
  return {
    scenarios: [...document.querySelectorAll("input[name=scenario]:checked")].map((b) => b.value),
    streamer,
    ao_settings: document.querySelector("input[name=ao_settings]:checked").value,
    zooms: streamer === "none" ? "" : $("zooms").value,
    cold: streamer !== "none" && $("cold").checked,
    keep_open: $("keep-open").checked,
  };
}

function refreshChoice() {
  const mode = document.querySelector("input[name=mode]:checked").value;
  $("scenario-list").hidden = mode !== "scenarios";
  $("campaign-list").hidden = mode !== "campaign";
  $("with-card").hidden = mode !== "scenarios";
  $("keep-open-q").hidden = mode !== "scenarios"; // a campaign starts X-Plane again for each run
  const streamer = document.querySelector("input[name=streamer]:checked").value;
  $("ao-settings-q").hidden = streamer !== "autoortho";
  $("zooms-q").hidden = streamer === "none";
  $("cold-q").hidden = streamer === "none";
  const p = picked();
  let note = "";
  if (mode === "scenarios" && options) {
    const chosen = options.scenarios.filter((s) => p.scenarios.includes(s.name));
    const m = chosen.reduce((sum, s) => sum + s.minutes, 0);
    note = chosen.length ? `environ ${minutes(m + 1)} : X-Plane s'ouvre, vole, puis se ferme tout seul` : "coche au moins un scénario";
  } else if (options) {
    const c = options.campaigns.find((x) => x.name === p.campaign);
    note = c && c.minutes ? `environ ${minutes(c.minutes)}, personne aux commandes` : "";
  }
  $("start-note").textContent = note;
}

async function start() {
  $("start-error").hidden = true;
  $("start-btn").disabled = true;
  try {
    await api("/api/bench/start", picked());
    since = 0;
    $("run-log").textContent = "";
    await poll();
  } catch (e) {
    $("start-error").textContent = e.message;
    $("start-error").hidden = false;
    $("start-btn").disabled = false;
  }
}

async function stop() {
  $("stop-btn").disabled = true;
  try {
    await api("/api/bench/stop", {});
  } catch (e) {
    $("run-text").textContent = e.message;
  }
}

// ---- the flight under way -------------------------------------------------------------------------

function describe(args) {
  if (args.includes("--campaign")) return `campagne ${args[args.indexOf("--campaign") + 1]}`;
  return "";
}

function showStatus(s) {
  const log = $("run-log");
  if (s.lines.length) {
    const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.textContent += s.lines.join("\n") + "\n";
    if (atEnd) log.scrollTop = log.scrollHeight;
  }
  since = s.next;
  const p = s.progress || {};
  $("run-dot").className = "dot" + (s.running ? " dot-running" : s.args.length ? " dot-done" : "");
  $("stop-btn").hidden = !s.running;
  $("stop-btn").disabled = false;
  $("start-btn").disabled = s.running;
  $("run-progress").hidden = !(s.running && p.duration_s);
  if (s.running) {
    const what = p.scenario ? `${p.scenario}${p.count > 1 ? ` (${p.index} sur ${p.count})` : ""}` : "préparation";
    $("run-title").textContent = `En vol : ${what}`;
    $("run-lead").textContent = p.campaign
      ? `Campagne ${p.campaign}, réglage ${p.run} sur ${p.runs} : ${p.setup}`
      : describe(s.args) || "X-Plane vole tout seul : rien à faire.";
    if (p.duration_s) {
      $("run-bar").style.width = `${Math.min(100, (100 * p.path_s) / p.duration_s)}%`;
      $("run-text").textContent = p.path_s > 0
        ? `${Math.round(p.path_s)} s sur ${Math.round(p.duration_s)} s de vol`
        : "X-Plane démarre, ou prend l'avion au départ";
    }
  } else if (s.args.length) {
    $("run-title").textContent = "Terminé";
    $("run-lead").textContent = "X-Plane se ferme tout seul. Le bilan est dans les résultats, en bas.";
    $("run-text").textContent = "";
  }
  if (wasRunning === true && !s.running) loadResults(true);
  wasRunning = s.running;
}

async function poll() {
  try {
    showStatus(await api(`/api/bench/status?since=${since}`));
  } catch (e) {
    $("run-text").textContent = e.message;
  }
}

async function pollMachine() {
  try {
    const m = await api("/api/bench/machine");
    for (const k of ["xplane", "live", "autoortho"]) $(`m-${k}`).className = "dot" + (m[k] ? " dot-done" : "");
  } catch (e) {
    /* the next poll says it again */
  }
}

// ---- the results ------------------------------------------------------------------------------------

async function loadResults(openNewest) {
  const items = await api("/api/bench/results");
  const body = $("results");
  body.replaceChildren();
  for (const it of items) {
    const row = el("tr", {},
      el("td", { class: "num", text: when(it.when) }),
      el("td", { text: it.what }),
      el("td", { text: WITH[it.with] || it.with }),
      el("td", { text: it.outcome === "done" ? "fait" : it.outcome }));
    row.addEventListener("click", () => openResult(it.path, row));
    body.append(row);
  }
  if (openNewest && items.length) openResult(items[0].path, body.firstChild);
}

async function openResult(path, row) {
  for (const r of document.querySelectorAll("#results tr")) r.classList.toggle("is-open", r === row);
  const view = $("result-view");
  view.hidden = false;
  view.textContent = "…";
  try {
    const r = await api(`/api/bench/result?path=${encodeURIComponent(path)}`);
    view.replaceChildren(...r.parts.map((part) => {
      const shots = (part.screenshots || []).map((src) => {
        const img = el("img", { src, alt: part.title, loading: "lazy" });
        img.addEventListener("click", () => { $("shot-img").src = src; $("shot").hidden = false; });
        return img;
      });
      return el("div", { class: "bench-part" },
        el("div", { html: part.html }),
        shots.length ? el("div", { class: "bench-shots" }, ...shots) : null);
    }));
  } catch (e) {
    view.textContent = e.message;
  }
}

// ---- the page ---------------------------------------------------------------------------------------

document.addEventListener("change", (e) => {
  if (e.target.matches("input, select")) refreshChoice();
});
$("start-btn").addEventListener("click", start);
$("stop-btn").addEventListener("click", stop);
$("shot").addEventListener("click", () => { $("shot").hidden = true; });

(async () => {
  try {
    showOptions(await api("/api/bench/options"));
  } catch (e) {
    $("start-error").textContent = e.message;
    $("start-error").hidden = false;
  }
  await Promise.all([poll(), pollMachine(), loadResults(false)]);
  setInterval(poll, 2000);
  setInterval(pollMachine, 4000);
})();
