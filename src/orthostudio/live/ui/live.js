// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's page: it asks /api/live/status every second and shows the stream's state,
// what X-Plane is doing, what is being built, the checks and the settings. The streaming itself
// runs in the engine (orthostudio/live/control.py); this page only starts it, stops it and shows it.

import { applyStatic, detectLanguage, language, setLanguage, t } from "./strings.js";

const POLL_MS = 1000;
const API_LEVEL = 1; // orthostudio/live/control.py API

const $ = (id) => document.getElementById(id);

let last = null; // the last status
let logSeq = 0;
let settingsShown = false; // the form is filled once, then left to the user

// --- talking to the engine ---------------------------------------------------------------------

async function api(path, { method = "GET", body } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`api/live/${path}`, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || data.error?.message || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

// --- numbers -----------------------------------------------------------------------------------

function number(value, digits = 0) {
  return new Intl.NumberFormat(language(), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function byLevel(counts) {
  const parts = counts.map((n, level) => (n ? `L${level} ${number(n)}` : null)).filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

// --- drawing -----------------------------------------------------------------------------------

function kv(dl, rows) {
  dl.replaceChildren();
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    if (value == null) {
      dd.textContent = t("build.none");
      dd.className = "none";
    } else {
      dd.textContent = value;
    }
    dl.append(dt, dd);
  }
}

/** The headline, its lead, and the dot's colour, from the state and what X-Plane does. */
function headline(status) {
  const { state, stream, checks } = status;
  if (state === "starting") {
    return [
      t("state.starting"),
      t(checks.region_ready ? "state.starting_install" : "state.starting_region"),
      "running",
    ];
  }
  if (state === "stopping") return [t("state.stopping"), "", "waiting"];
  if (state === "leaving") return [t("state.leaving"), t("state.leaving_lead"), "waiting"];
  if (state === "streaming" && stream) {
    const start = stream.start;
    if (start && start.held) {
      return [t("state.held", { name: start.name }), t("state.held_lead"), "running"];
    }
    if (start && !start.ready) {
      return [t("state.building_start", { name: start.name }), t("state.building_start_lead"), "running"];
    }
    if (start && start.ready && stream.data !== "fresh") {
      return [t("state.start_ready", { name: start.name }), t("state.start_ready_lead"), "done"];
    }
    if (stream.data === "fresh") return [t("state.following"), t("state.following_lead"), "done"];
    if (stream.data === "stale") {
      return checks.xplane_running
        ? [t("state.loading"), t("state.loading_lead"), "running"]
        : [t("state.xplane_gone"), t("state.xplane_gone_lead"), "waiting"];
    }
    return [t("state.waiting"), t("state.waiting_lead"), "waiting"];
  }
  if (state === "streaming") return [t("state.waiting"), t("state.waiting_lead"), "waiting"];
  const problem = blocking(checks);
  if (problem) return [t("state.blocked"), problem, "failed"];
  return [t("state.idle"), t("state.idle_lead"), ""];
}

/** What keeps a stream from starting, in words, or null. */
function blocking(checks) {
  if (!checks.supported) return t("checks.macos_no");
  if (!checks.xplane) return t("checks.xplane_missing", { problem: checks.xplane_problem || "" });
  if (checks.folder_problem) return t("checks.folder_problem", { problem: checks.folder_problem });
  if (checks.autoortho_mounted) return t("checks.ao_mounted");
  if (!checks.mesh_found && !checks.region_ready) return t("checks.mesh_missing", { path: checks.mesh });
  if (checks.xplane_running && !checks.installed) return t("checks.xplane_running_late");
  return null;
}

function drawState(status) {
  const [title, lead, dot] = headline(status);
  $("state-title").textContent = title;
  $("state-lead").textContent = lead;
  $("state-lead").hidden = !lead;
  $("state-dot").className = dot ? `dot dot-${dot}` : "dot";

  const start = status.state === "streaming" && status.stream ? status.stream.start : null;
  const holding = Boolean(start && !start.ready);
  $("hold").hidden = !holding;
  if (holding) {
    $("hold-bar").style.width = `${Math.round(start.progress * 1000) / 10}%`;
    $("hold-text").textContent = t("hold.text", {
      left: number(start.left),
      seconds: number(start.seconds),
      mbs: number(status.stream.mbs, 1),
    });
  }

  const error = status.state === "idle" && status.error;
  $("state-error").hidden = !error;
  $("state-error").textContent = error ? t("state.error", { error: status.error }) : "";

  const idle = status.state === "idle";
  const leftBehind = idle && status.checks.installed && !status.checks.xplane_running;
  $("start-btn").hidden = !idle;
  $("start-btn").disabled = Boolean(blocking(status.checks));
  $("fresh-row").hidden = !idle;
  $("fresh-help").hidden = !idle || !$("fresh").checked;
  $("stop-btn").hidden = !["starting", "streaming"].includes(status.state);
  $("leave-btn").hidden = status.state !== "leaving";
  $("uninstall-btn").hidden = !leftBehind;
}

function drawFlight(status) {
  const stream = status.state === "streaming" ? status.stream : null;
  if (!stream) {
    const running = status.checks.xplane_running;
    kv($("flight"), [[t("flight.xplane"), t(running ? "flight.xplane_running" : "flight.xplane_stopped")]]);
    return;
  }
  const rows = [[t("flight.xplane"), t(`flight.data_${stream.data}`)]];
  const a = stream.aircraft;
  if (a) {
    rows.push([t("flight.position"), `${number(a.lat, 3)}, ${number(a.lon, 3)}`]);
    rows.push([t("flight.altitude"), `${number(a.alt_ft)} ft`]);
    rows.push([t("flight.speed"), `${number(a.gs_kt)} kt`]);
  }
  if (stream.camera_km != null && stream.camera_km > 0.5) {
    rows.push([t("flight.camera"), t("flight.camera_value", { km: number(stream.camera_km, 1) })]);
  }
  if (stream.start) rows.push([t("flight.start"), stream.start.name]);
  kv($("flight"), rows);
}

function drawBuild(status) {
  const stream = status.state === "streaming" ? status.stream : null;
  $("disk").hidden = !stream;
  $("build-idle").hidden = Boolean(stream);
  if (!stream) {
    kv($("build"), []);
    return;
  }
  const s = stream.streamer;
  const queued = byLevel(s.queued);
  const rows = [
    [t("build.queued"), queued && `${queued} (${t("build.urgent", { n: number(s.urgent) })})`],
    [t("build.building"), s.building || s.pieces_awaited
      ? t("build.building_value", { n: number(s.building), pieces: number(s.pieces_awaited) })
      : null],
    [t("build.built"), byLevel(s.built)],
    [t("build.downloaded"), t("build.downloaded_value", {
      n: number(s.downloaded),
      mb: number(s.downloaded_bytes / 1e6),
      cache: number(s.from_cache),
    }) + (s.failed ? ` · ${t("build.failed", { n: number(s.failed) })}` : "")],
    [t("build.rate"), `${number(stream.mbs, 1)} ${language() === "fr" ? "Mo/s" : "MB/s"}`],
    [t("build.disk"), t("build.disk_value", {
      used: number(s.disk_bytes / 1e9, 1),
      budget: number(s.disk_budget / 1e9),
    })],
  ];
  kv($("build"), rows);
  const share = s.disk_budget ? Math.min(1, s.disk_bytes / s.disk_budget) : 0;
  $("disk-bar").style.width = `${Math.round(share * 1000) / 10}%`;
  $("disk").classList.toggle("warn", share > 0.9);
}

function drawChecks(status) {
  const c = status.checks;
  const items = [];
  const add = (kind, text) => items.push([kind, text]);
  if (!c.supported) add("failed", t("checks.macos_no"));
  if (c.folder_problem) add("failed", t("checks.folder_problem", { problem: c.folder_problem }));
  else add("done", t("checks.folder", { path: c.folder }));
  if (c.xplane) add("done", t("checks.xplane", { path: c.xplane }));
  else add("failed", t("checks.xplane_missing", { problem: c.xplane_problem || "" }));
  if (c.xplane_running) {
    add(c.installed || status.state !== "idle" ? "done" : "failed",
      t(c.installed || status.state !== "idle" ? "checks.xplane_running" : "checks.xplane_running_late"));
  } else {
    add("done", t("checks.xplane_stopped"));
  }
  if (c.mesh) {
    add(c.mesh_found || c.region_ready ? "done" : "failed",
      t(c.mesh_found ? "checks.mesh" : "checks.mesh_missing", { path: c.mesh }));
  }
  add(c.region_ready ? "done" : "info", t(c.region_ready ? "checks.region" : "checks.region_todo"));
  add(c.autoortho_mounted ? "failed" : "done", t(c.autoortho_mounted ? "checks.ao_mounted" : "checks.ao"));
  if (c.plugin === "ready") add("done", t("checks.plugin"));
  else if (c.plugin === "to_build") add("info", t("checks.plugin_build"));
  else add("warn", t("checks.plugin_missing"));
  if (status.state === "idle" && c.installed && !c.xplane_running) add("warn", t("checks.left_installed"));

  const ul = $("checks");
  ul.replaceChildren();
  for (const [kind, text] of items) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `dot dot-${kind}`;
    const words = document.createElement("span");
    words.textContent = text;
    li.append(dot, words);
    ul.append(li);
  }
}

function fillSettings(settings, checks) {
  const form = $("settings-form");
  if (checks) form.elements.namedItem("folder").placeholder = checks.folder_default;
  for (const [name, value] of Object.entries(settings)) {
    const field = form.elements.namedItem(name);
    if (field) field.value = String(value);
  }
}

function draw(status) {
  last = status;
  drawState(status);
  drawFlight(status);
  drawBuild(status);
  drawChecks(status);
  if (!settingsShown) {
    fillSettings(status.settings, status.checks);
    settingsShown = true;
  }
}

// --- the log -----------------------------------------------------------------------------------

async function readLog() {
  const got = await api(`log?after=${logSeq}`);
  if (!got.lines.length) return;
  const pre = $("log");
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 20;
  pre.append(document.createTextNode(`${got.lines.map((l) => l.text).join("\n")}\n`));
  logSeq = got.seq;
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

// --- the loop and the buttons ------------------------------------------------------------------

async function poll() {
  try {
    const status = await api("status");
    if (status.api !== API_LEVEL) throw new Error(`API ${status.api}, the page reads ${API_LEVEL}`);
    draw(status);
    if (status.log_seq > logSeq) await readLog();
  } catch (error) {
    $("state-error").hidden = false;
    $("state-error").textContent = t("error.api", { error: error.message });
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

async function act(path, body) {
  try {
    await api(path, { method: "POST", body });
  } catch (error) {
    $("state-error").hidden = false;
    $("state-error").textContent = error.message;
  }
  try {
    draw(await api("status"));
  } catch (_e) {
    // the loop says it
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const form = $("settings-form");
  const body = {
    disk_budget_gb: Number(form.elements.namedItem("disk_budget_gb").value),
    cache_budget_gb: Number(form.elements.namedItem("cache_budget_gb").value),
    camera_reach_km: Number(form.elements.namedItem("camera_reach_km").value),
    quality: form.elements.namedItem("quality").value,
    provider: form.elements.namedItem("provider").value,
    folder: form.elements.namedItem("folder").value.trim(),
  };
  $("settings-error").hidden = true;
  $("settings-status").textContent = "";
  try {
    fillSettings(await api("settings", { method: "PUT", body }));
    $("settings-status").textContent = t("settings.saved");
  } catch (error) {
    $("settings-error").hidden = false;
    $("settings-error").textContent = error.message;
  }
}

function boot() {
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  applyStatic(document);
  $("lang-select").addEventListener("change", (event) => {
    setLanguage(event.target.value, true);
    applyStatic(document);
    if (last) draw(last);
  });
  $("start-btn").addEventListener("click", () => act("start", { fresh: $("fresh").checked }));
  $("stop-btn").addEventListener("click", () => act("stop"));
  $("leave-btn").addEventListener("click", () => act("leave"));
  $("uninstall-btn").addEventListener("click", () => act("uninstall"));
  $("fresh").addEventListener("change", () => {
    $("fresh-help").hidden = !$("fresh").checked;
  });
  $("settings-form").addEventListener("submit", saveSettings);
  poll();
}

if (typeof document !== "undefined") boot();
