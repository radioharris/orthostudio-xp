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
    const note = s.error || (s.camera ? `${s.name} · ta caméra enregistrée le ${s.camera}` : s.name);
    scs.append(choice(box, label, note, null, s.about));
  }
  const camps = $("campaign-list");
  camps.replaceChildren();
  opts.campaigns.forEach((c, i) => {
    const radio = el("input", { type: "radio", name: "campaign", value: c.name });
    if (i === 0) radio.checked = true;
    if (c.error) radio.disabled = true;
    const label = el("span", { class: "choice-label", text: c.title || c.name });
    const about = c.error || `${c.name}, sur ${(c.scenarios || []).join(", ")}`;
    camps.append(choice(radio, label, about, null, c.about));
    if (c.error) return;
    // its comparisons, each to tick; without any, its runs
    const items = c.comparisons && c.comparisons.length
      ? c.comparisons.map((k) => ({ value: k.index, text: `${k.index}. ${k.title}` }))
      : c.runs.map((r, n) => ({ value: n + 1, text: `${n + 1}. ${r.label}` }));
    const list = el("div", { class: "choices bench-picks", "data-campaign": c.name });
    for (const it of items) {
      const box = el("input", { type: "checkbox", name: `pick-${c.name}`, value: String(it.value) });
      box.checked = true;
      list.append(el("label", { class: "choice" }, box, el("span", { class: "choice-text" },
        el("span", { class: "choice-label", text: it.text }))));
    }
    camps.append(list);
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
  const camera = document.querySelector("input[name=camera]:checked").value;
  if (mode === "campaign") {
    const c = document.querySelector("input[name=campaign]:checked");
    if (!c) return { campaign: "", camera };
    const pick = [...document.querySelectorAll(`input[name="pick-${c.value}"]:checked`)].map((b) => Number(b.value));
    return { campaign: c.value, pick, camera };
  }
  const streamer = document.querySelector("input[name=streamer]:checked").value;
  return {
    scenarios: [...document.querySelectorAll("input[name=scenario]:checked")].map((b) => b.value),
    streamer,
    ao_settings: document.querySelector("input[name=ao_settings]:checked").value,
    zooms: streamer === "none" ? "" : $("zooms").value,
    cold: streamer !== "none" && $("cold").checked,
    keep_open: $("keep-open").checked,
    camera,
  };
}

function refreshChoice() {
  const mode = document.querySelector("input[name=mode]:checked").value;
  $("scenario-list").hidden = mode !== "scenarios";
  $("campaign-list").hidden = mode !== "campaign";
  $("with-card").hidden = mode !== "scenarios";
  $("keep-open-q").hidden = mode !== "scenarios"; // a campaign starts X-Plane again for each run
  const ticked = mode === "scenarios" ? picked().scenarios || [] : [];
  $("record-btn").disabled = ticked.length !== 1;
  $("record-btn").hidden = mode !== "scenarios";
  $("record-note").hidden = mode !== "scenarios";
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
    for (const list of document.querySelectorAll(".bench-picks")) list.hidden = !c || list.dataset.campaign !== c.name;
    if (c && !c.error) {
      const flights = new Set(); // a flight two comparisons share is flown once
      if (c.comparisons.length) {
        for (const k of c.comparisons) if (p.pick.includes(k.index)) k.runs.forEach((r) => flights.add(r));
      } else {
        c.runs.forEach((r, n) => { if (p.pick.includes(n + 1)) flights.add(r.id); });
      }
      const n = flights.size;
      note = n ? `${n} vol${n > 1 ? "s" : ""} différent${n > 1 ? "s" : ""}, environ ${minutes(n * c.run_minutes)}, personne aux commandes`
        : "coche au moins une comparaison";
    }
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

async function recordCamera() {
  $("start-error").hidden = true;
  try {
    await api("/api/bench/start", { record_camera: true, scenarios: picked().scenarios });
    since = 0;
    $("run-log").textContent = "";
    await poll();
  } catch (e) {
    $("start-error").textContent = e.message;
    $("start-error").hidden = false;
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
  if (args.includes("--record-camera")) {
    return "Ta caméra est enregistrée : bouge-la comme tu veux pendant tout le vol (cockpit, Shift+4, zoom, loin, près).";
  }
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
  updateRunMap();
}

async function pollMachine() {
  try {
    const m = await api("/api/bench/machine");
    for (const k of ["xplane", "live", "autoortho"]) $(`m-${k}`).className = "dot" + (m[k] ? " dot-done" : "");
  } catch (e) {
    /* the next poll says it again */
  }
}

// ---- Live's map -------------------------------------------------------------------------------------
// Live's own (livemap.js, as Live's window draws it), fed by the window's engine from the test's
// Live (benchmap.py): while Live flies, and for a Live flight's result.

let mapModule = null;
let runMap = null;
let runRoute = "";
let resultMap = null;

async function liveMapModule() {
  if (typeof L === "undefined") return null; // Leaflet did not load: the rest of the page works
  if (!mapModule) {
    const [m, strings] = await Promise.all([import("/live/livemap.js"), import("/live/strings.js")]);
    strings.setLanguage("fr");
    mapModule = m;
  }
  return mapModule;
}

/** The map's own requests (map/built, map/texture...), to the test's Live `src`. */
function mapApi(src) {
  return (path) => api(`/api/bench/${path}${path.includes("?") ? "&" : "?"}src=${encodeURIComponent(src)}`);
}

async function updateRunMap() {
  let state;
  try {
    state = await api("/api/bench/map/state?src=current");
  } catch (e) {
    return;
  }
  $("run-map-box").hidden = !state.live;
  if (!state.live) return;
  const m = await liveMapModule();
  if (!m) return;
  if (!runMap) {
    runMap = m.createLiveMap($("run-map"), { api: mapApi("current"), info: $("run-map-info") });
    runMap.setDetails(true);
    $("run-map-follow").addEventListener("change", (e) => runMap.setFollow(e.target.checked));
    $("run-map").addEventListener("live-map-follow", (e) => { $("run-map-follow").checked = e.detail; });
    runMap.shown();
  }
  runMap.update(state.status);
  const key = `${state.route.length}:${JSON.stringify(state.route[0] || [])}`;
  if (key !== runRoute) { // a new scenario: its path, the map framed on it once
    runRoute = key;
    runMap.showRoute(state.route);
  }
}

/** A Live flight's result: what its Live built, and the path flown. */
async function resultMapBlock(path) {
  let state;
  try {
    state = await api(`/api/bench/map/state?src=${encodeURIComponent(path)}`);
  } catch (e) {
    return null;
  }
  if (!state.live || !state.record) return null;
  const m = await liveMapModule();
  if (!m) return null;
  const mapEl = el("div", { class: "live-map" });
  const info = el("div", { class: "live-map-bar is-hint", "aria-live": "polite" });
  const block = el("div", { class: "bench-map" },
    el("div", { class: "bench-map-head" },
      el("h3", { class: "sub-question-title", text: "La carte de Live : ce qu'il a construit pendant le vol" })),
    el("div", { class: "live-map-wrap" }, mapEl, info),
    $("map-legend").content.cloneNode(true));
  return {
    block,
    start() {
      if (resultMap) resultMap.map.remove();
      resultMap = m.createLiveMap(mapEl, { api: mapApi(path), info });
      resultMap.setDetails(true);
      resultMap.setFollow(false);
      resultMap.update(state.status);
      resultMap.showRoute(state.route);
      resultMap.shown();
    },
  };
}

// ---- the results ------------------------------------------------------------------------------------

async function loadResults(openNewest) {
  const items = await api("/api/bench/results");
  const body = $("results");
  body.replaceChildren();
  for (const it of items) {
    const box = el("input", { type: "checkbox", "aria-label": "à comparer", value: it.path });
    if (it.kind !== "series") box.disabled = true; // a campaign's table: its runs are compared already
    box.addEventListener("click", (e) => e.stopPropagation());
    box.addEventListener("change", refreshCompare);
    const row = el("tr", {},
      el("td", { class: "bench-pick" }, box),
      el("td", { class: "num", text: when(it.when) }),
      el("td", { text: it.what }),
      el("td", { text: WITH[it.with] || it.with }),
      el("td", { text: it.outcome === "done" ? "fait" : it.outcome }));
    row.addEventListener("click", () => openResult(it.path, row));
    body.append(row);
  }
  refreshCompare();
  if (openNewest && items.length) openResult(items[0].path, body.firstChild);
}

function checkedResults() {
  return [...document.querySelectorAll("#results input[type=checkbox]:checked")].map((b) => b.value);
}

function refreshCompare() {
  $("compare-btn").disabled = checkedResults().length < 2;
}

/** The results checked side by side: their figures, then their photos at the same moments. */
async function compareResults() {
  const view = $("result-view");
  view.hidden = false;
  view.textContent = "…";
  for (const r of document.querySelectorAll("#results tr")) r.classList.remove("is-open");
  try {
    const c = await api(`/api/bench/compare?paths=${encodeURIComponent(checkedResults().join("|"))}`);
    const parts = [el("h2", { text: "Comparaison" }), el("div", { html: c.html })];
    if (c.shots.length) parts.push(el("h3", { text: "Les photos, au même moment du même vol" }));
    for (const row of c.shots) {
      const cells = row.images.map((src, k) => {
        const fig = el("figure", { class: "bench-compare-cell" }, el("figcaption", { text: c.labels[k] }));
        if (src) {
          const img = el("img", { src, alt: c.labels[k], loading: "lazy" });
          img.addEventListener("click", () => { $("shot-img").src = src; $("shot").hidden = false; });
          fig.append(img);
        } else {
          fig.append(el("p", { class: "question-help", text: "pas de photo" }));
        }
        return fig;
      });
      const grid = el("div", { class: "bench-compare-grid" }, ...cells);
      grid.style.gridTemplateColumns = `repeat(${row.images.length}, minmax(0, 1fr))`;
      parts.push(el("div", { class: "bench-part" },
        el("p", { class: "question-help", text: `${row.scenario}, à ${Math.round(row.t)} s de vol` }), grid));
    }
    view.replaceChildren(...parts);
  } catch (e) {
    view.textContent = e.message;
  }
}

async function openResult(path, row) {
  for (const r of document.querySelectorAll("#results tr")) r.classList.toggle("is-open", r === row);
  const view = $("result-view");
  view.hidden = false;
  view.textContent = "…";
  try {
    const [r, liveMap] = await Promise.all([
      api(`/api/bench/result?path=${encodeURIComponent(path)}`),
      resultMapBlock(path),
    ]);
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
    if (liveMap) {
      view.prepend(liveMap.block);
      liveMap.start();
    }
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
$("compare-btn").addEventListener("click", compareResults);
$("record-btn").addEventListener("click", recordCamera);
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
