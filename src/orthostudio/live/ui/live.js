// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's page: it asks /api/live/status every second and shows the stream's state,
// what X-Plane is doing, what is being built, the checks and the settings. The streaming itself
// runs in the engine (orthostudio/live/control.py); this page only starts it, stops it and shows it.

import { applyStatic, countryName, detectLanguage, language, setLanguage, t } from "./strings.js";

const POLL_MS = 1000;
const MAP_DETAILS_KEY = "osxp.live.mapDetails"; // the bar over the map, shown or hidden
const API_LEVEL = 1; // orthostudio/live/control.py API
const SHARP_ZOOM = 16; // a source stopping below it shows blurrier ortho up close

const $ = (id) => document.getElementById(id);

let last = null; // the last status
let logSeq = 0;
let providers = []; // GET /api/live/providers
let saved = null; // the settings as saved: what "Undo my changes" goes back to
let liveMap = null; // the map (livemap.js), when Leaflet has loaded

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

/** "7 × 4096 px · 5 × 2048 px": counts by level, the first ``first`` (-1: 8192 px). */
function byLevel(counts, first) {
  const side = (level) => (level >= 0 ? 4096 >> level : 4096 << -level);
  const parts = counts.map((n, i) => (n ? `${number(n)} × ${side(first + i)} px` : null)).filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

const gb = (bytes) => number(bytes / 1e9, 1);

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
  if (state === "deleting") return [t("state.deleting"), "", "running"];
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

  const { state, checks } = status;
  const idle = state === "idle";
  $("start-btn").hidden = !idle;
  $("start-btn").disabled = Boolean(blocking(checks));
  $("fresh-row").hidden = !idle;
  $("stop-btn").hidden = !["starting", "streaming"].includes(state);
  // X-Plane sees the pack and loads the plugin only if they are there when it starts
  $("xplane-btn").disabled = !(state === "streaming" && !checks.xplane_running);
  $("xplane-btn").hidden = checks.xplane_running || !["idle", "starting", "streaming"].includes(state);
  // the reload offered after a start, in the "reload" start mode
  const offer = (state === "streaming" && status.stream?.streamer?.offer) || null;
  $("reload-btn").hidden = !offer || offer.state === "asked";
  $("reload-btn").disabled = !offer || offer.state !== "ready";
  $("offer-text").hidden = !offer;
  $("offer-text").textContent = offer ? t(`offer.${offer.state}`, { n: number(offer.left) }) : "";
  $("leave-btn").hidden = state !== "leaving";
  $("uninstall-btn").hidden = !(idle && checks.installed && !checks.xplane_running);
  let help = "";
  if (idle && $("fresh").checked) help = t("action.fresh_help");
  else if ((idle || state === "starting") && !checks.xplane_running) help = t("action.xplane_wait");
  $("action-help").textContent = help;
  $("action-help").hidden = !help;
}

function drawFlight(status) {
  const stream = status.state === "streaming" ? status.stream : null;
  // where the aircraft (or the start) has no mesh under it: the region to install
  const notice = stream?.notice;
  $("flight-notice").hidden = !notice;
  if (notice) {
    $("flight-notice").textContent = t(notice.installed ? "flight.notice_installed" : "flight.notice",
      { region: t(`region.${notice.region}`) });
  }
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
  const plan = stream.streamer?.flight_plan;
  if (plan) {
    rows.push([t("flight.plan"), t(plan.following ? "flight.plan_following" : "flight.plan_off", {
      name: plan.name,
    })]);
  }
  const screen = stream.streamer?.screen;
  if (screen) {
    rows.push([t("flight.screen"), t("flight.screen_value", {
      width: number(screen.width),
      fov: number(screen.fov),
      km: number(screen.km_4096),
    })]);
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
  const queued = byLevel(s.queued, s.first_level);
  const building = s.building || s.pieces_awaited
    ? t("build.building_value", { n: number(s.building), pieces: number(s.pieces_awaited) })
    : null;
  const downloaded = t("build.downloaded_value", {
    n: number(s.downloaded),
    mb: number(s.downloaded_bytes / 1e6),
    cache: number(s.from_cache),
  }) + (s.failed ? ` · ${t("build.failed", { n: number(s.failed) })}` : "");
  kv($("build"), [
    [t("build.queued"), queued && `${queued} (${t("build.urgent", { n: number(s.urgent) })})`],
    [t("build.building"), building],
    [t("build.built"), byLevel(s.built, s.first_level)],
    ...(s.grown ? [[t("build.grown"), number(s.grown)]] : []),
    [t("build.downloaded"), downloaded],
    [t("build.rate"), `${number(stream.mbs, 1)} ${language() === "fr" ? "Mo/s" : "MB/s"}`],
    [t("build.disk"), t("build.disk_value", { used: gb(s.disk_bytes), budget: number(s.disk_budget / 1e9) })],
  ]);
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
    const fine = c.installed || status.state !== "idle";
    add(fine ? "done" : "failed", t(fine ? "checks.xplane_running" : "checks.xplane_running_late"));
  } else {
    add("done", t("checks.xplane_stopped"));
  }
  if (c.mesh) {
    const names = (c.mesh_regions || []).map((id) => t(`region.${id}`)).join(", ") || c.mesh;
    add(c.mesh_found || c.region_ready ? "done" : "failed",
      t(c.mesh_found ? "checks.mesh" : "checks.mesh_missing", { path: names }));
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

/** Disk space: what each takes now, and whether they can be deleted. */
function drawDisk(status) {
  const streaming = status.state === "streaming" && status.stream;
  const textures = streaming ? status.stream.streamer.disk_bytes : status.checks.textures_bytes;
  $("disk-now").textContent = t("disk.now", { gb: gb(textures) });
  $("cache-now").textContent = t("disk.now", { gb: gb(status.checks.imagery_bytes) });
  const idle = status.state === "idle";
  $("delete-btn").disabled = !idle;
  $("delete-help").textContent = idle ? "" : t("disk.delete_streaming");
}

/** Live's folder now, and the Finder's button beside it. */
function drawFolder(status) {
  const line = $("folder-now");
  const path = status.checks.folder;
  if (line.dataset.path === `${language()}:${path}`) return; // drawn already: keep the button
  line.dataset.path = `${language()}:${path}`;
  const show = document.createElement("button");
  show.type = "button";
  show.className = "btn btn-small reveal-btn";
  show.textContent = t("folder.show");
  show.addEventListener("click", () => act("reveal", { path }));
  line.replaceChildren(document.createTextNode(`${t("folder.now", { path })} `), show);
  $("set-folder").placeholder = status.checks.folder_default;
}

/** OrthoStudio XP's SimBrief name, offered to take: never asked of SimBrief until it is. */
function drawSimbrief() {
  const name = last?.checks?.osxp_simbrief;
  const button = $("simbrief-osxp");
  button.hidden = !name || $("set-simbrief").value.trim() === name;
  if (name) button.textContent = t("plan.take", { name });
}

// --- AutoOrtho's regions: the mesh -----------------------------------------------------------

let regionsDrawn = ""; // what the list shows: drawn again only when it changes, keeping the focus
let removeArmed = null; // the region whose Delete was clicked once: a second click deletes it

function regionButton(label, onClick, { danger = false, disabled = false } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = danger ? "btn btn-small btn-danger" : "btn btn-small";
  button.textContent = label;
  button.disabled = disabled;
  button.addEventListener("click", onClick);
  return button;
}

async function regionAct(path, method = "POST") {
  $("regions-error").hidden = true;
  try {
    const regions = await api(path, { method });
    if (last) draw({ ...last, regions });
    liveMap?.regionsChanged();
  } catch (error) {
    $("regions-error").hidden = false;
    $("regions-error").textContent = error.message;
  }
}

function drawRegions(status) {
  const regions = status.regions || { list: [], install: null };
  const install = regions.install;
  const running = install && !["done", "failed", "cancelled"].includes(install.phase);
  const idle = status.state === "idle";
  $("regions-loading").hidden = regions.list.length > 0 || Boolean(regions.error);
  if (regions.error) {
    $("regions-error").hidden = false;
    $("regions-error").textContent = t("regions.error", { error: regions.error });
  }
  const shape = JSON.stringify([language(), regions.list, install && [install.region, install.phase,
    install.error], running, idle, removeArmed]);
  if (shape !== regionsDrawn) {
    regionsDrawn = shape;
    const ul = $("regions-list");
    ul.replaceChildren();
    for (const r of regions.list) {
      const li = document.createElement("li");
      li.className = "region";
      li.dataset.region = r.id;
      const head = document.createElement("div");
      head.className = "region-head";
      const name = document.createElement("span");
      name.className = "region-name";
      name.textContent = t(`region.${r.id}`);
      const size = document.createElement("span");
      size.className = "region-size";
      size.textContent = r.size ? t("regions.size", { gb: gb(r.size) }) : "";
      const state = document.createElement("span");
      state.className = "region-state";
      const actions = document.createElement("span");
      actions.className = "region-actions";
      const mine = install && install.region === r.id;
      if (mine && running) {
        actions.append(regionButton(t("regions.cancel"), () => regionAct("regions/cancel")));
      } else if (r.installed_by) {
        const newer = r.installed_by === "live" && r.release && r.installed_release
          && r.release !== r.installed_release;
        state.textContent = newer ? t("regions.newer", { release: r.release }) : t("regions.installed");
        if (newer) {
          actions.append(regionButton(t("regions.update"), () => regionAct(`regions/${r.id}/install`),
            { disabled: running }));
        }
        const armed = removeArmed === r.id;
        const name = t(`region.${r.id}`);
        const remove = regionButton(
          armed ? t("regions.confirm_remove", { name }) : t("regions.remove"),
          () => {
            if (armed) {
              removeArmed = null;
              regionAct(`regions/${r.id}`, "DELETE");
            } else {
              removeArmed = r.id;
              if (last) draw(last);
            }
          },
          { danger: true, disabled: !idle || running });
        actions.append(remove);
      } else if (r.size) {
        actions.append(regionButton(t("regions.install"), () => regionAct(`regions/${r.id}/install`),
          { disabled: running }));
      }
      head.append(name, size, state, actions);
      li.append(head);
      if (mine) {
        const line = document.createElement("p");
        line.className = "question-help region-progress";
        line.id = "region-progress";
        li.append(line);
        if (running) {
          const bar = document.createElement("div");
          bar.className = "progress";
          const fill = document.createElement("span");
          fill.id = "region-bar";
          bar.append(fill);
          li.append(bar);
        }
      }
      ul.append(li);
    }
  }
  if (install && $("region-progress")) {
    const done = { done: gb(install.done_bytes), total: gb(install.total_bytes),
      unpacked: install.unpacked, zips: install.zips };
    const line = $("region-progress");
    line.classList.toggle("is-warn", install.phase === "failed");
    if (running) line.textContent = t("regions.progress", done);
    else if (install.phase === "failed") line.textContent = t("regions.failed", { error: install.error });
    else if (install.phase === "cancelled") line.textContent = t("regions.cancelled");
    else line.textContent = t("regions.done");
    if ($("region-bar")) {
      const share = install.total_bytes ? install.done_bytes / install.total_bytes : 0;
      $("region-bar").style.width = `${Math.round(share * 1000) / 10}%`;
    }
  }
}

function draw(status) {
  last = status;
  drawSimbrief();
  drawState(status);
  drawFlight(status);
  drawBuild(status);
  drawChecks(status);
  drawDisk(status);
  drawFolder(status);
  drawRegions(status);
  liveMap?.update({ ...status, providers });
  if (saved === null) {
    saved = status.settings;
    fillSettings(saved);
  }
}

// --- the imagery source ------------------------------------------------------------------------

function sourceLabel(p) {
  return p.extent ? `${countryName(p.extent)} · ${p.name}` : p.name;
}

/** The list grouped as OrthoStudio XP's Plan groups it: the whole world, the user's own, then
 * the countries' sources. */
function renderProviders(current) {
  const select = $("set-provider");
  select.replaceChildren();
  const groups = [
    ["source.group_world", providers.filter((p) => !p.extent && !p.custom)],
    ["source.group_mine", providers.filter((p) => p.custom)],
    ["source.group_country", providers.filter((p) => p.extent && !p.custom)
      .sort((a, b) => sourceLabel(a).localeCompare(sourceLabel(b), language()))],
  ];
  for (const [title, list] of groups) {
    if (!list.length) continue;
    const group = document.createElement("optgroup");
    group.label = t(title);
    for (const p of list) {
      const option = document.createElement("option");
      option.value = p.code;
      option.textContent = sourceLabel(p);
      group.append(option);
    }
    select.append(group);
  }
  if (providers.some((p) => p.code === current)) select.value = current;
  drawSource();
}

/** Under the list: the source's credit and licence, and what it does not cover. */
function drawSource() {
  const p = providers.find((q) => q.code === $("set-provider").value);
  const credit = p ? p.attribution || "" : "";
  const licence = p ? p.licence || "" : "";
  $("provider-attribution").textContent = credit && licence ? `${credit} ${licence}.` : credit;
  const notes = [];
  if (p && p.extent) notes.push(t("source.fallback", { country: countryName(p.extent) }));
  if (p && p.max_zl < SHARP_ZOOM) notes.push(t("source.low_zoom", { zl: p.max_zl }));
  $("provider-note").textContent = notes.join(" ");
  $("provider-note").hidden = !notes.length;
}

// --- the settings ------------------------------------------------------------------------------

/** The zooms offered, as AutoOrtho's: below the package's, the same, one above (8192 px). */
const ZOOMS = { max_zoom: [16, [14, 15, 16, 17]], max_zoom_airports: [18, [16, 17, 18, 19]] };

function renderZooms() {
  for (const [name, [mesh, zooms]] of Object.entries(ZOOMS)) {
    const select = $(name === "max_zoom" ? "set-max-zoom" : "set-max-zoom-airports");
    const current = select.value;
    select.replaceChildren(...zooms.map((zl) => {
      const option = document.createElement("option");
      option.value = String(zl);
      if (zl < mesh) option.textContent = t("zoom.lower", { zl, times: 4 ** (mesh - zl) });
      else if (zl === mesh) option.textContent = t("zoom.same", { zl });
      else option.textContent = t("zoom.higher", { zl });
      return option;
    }));
    if (current) select.value = current;
  }
}

/** What X-Plane waits for at a start, as OrthoStudio XP's own choices: the label, then a note. */
const START_MODES = ["full", "around", "normal", "reload"];
const START_RECOMMENDED = "around";

function renderStartModes() {
  const box = $("start-choices");
  const current = box.querySelector("input:checked")?.value || saved?.start_mode || START_RECOMMENDED;
  box.replaceChildren(...START_MODES.map((mode) => {
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "start_mode";
    input.value = mode;
    input.checked = mode === current;
    const label = document.createElement("span");
    label.className = "choice-label";
    label.textContent = t(`start.${mode}`);
    if (mode === START_RECOMMENDED) {
      const tag = document.createElement("span");
      tag.className = "choice-tag";
      tag.textContent = t("settings.recommended");
      label.append(tag);
    }
    const note = document.createElement("span");
    note.className = "choice-note";
    note.textContent = t(`start.${mode}_note`);
    const text = document.createElement("span");
    text.className = "choice-text";
    text.append(label, note);
    const choice = document.createElement("label");
    choice.className = "choice";
    choice.append(input, text);
    return choice;
  }));
  drawStartChoices();
}

/** Without a zoom above the package's, no texture is 8192 px and the four would start the same:
 * one line says what a start does instead (a user found the four, then a warning, confusing). */
function drawStartChoices() {
  const sharp = Number($("set-max-zoom").value) > ZOOMS.max_zoom[0]
    || Number($("set-max-zoom-airports").value) > ZOOMS.max_zoom_airports[0];
  $("start-options").hidden = !sharp;
  $("start-plain").hidden = sharp;
}

function startMode() {
  return $("start-choices").querySelector("input:checked")?.value || saved?.start_mode || START_RECOMMENDED;
}

function fillSettings(settings) {
  $("set-max-zoom").value = String(settings.max_zoom);
  $("set-max-zoom-airports").value = String(settings.max_zoom_airports);
  $("set-disk").value = String(settings.disk_budget_gb);
  $("set-cache").value = String(settings.cache_budget_gb);
  $("set-folder").value = settings.folder || "";
  $("set-simbrief").value = settings.simbrief_user || "";
  for (const input of document.querySelectorAll('input[name="app_home"]')) {
    input.checked = input.value === (settings.app_home || "ask");
  }
  for (const input of document.querySelectorAll('input[name="overlays"]')) {
    input.checked = input.value === (settings.overlays || "auto");
  }
  drawSimbrief();
  for (const input of $("start-choices").querySelectorAll("input")) {
    input.checked = input.value === settings.start_mode;
  }
  drawStartChoices();
  if (providers.length) renderProviders(settings.provider);
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    max_zoom: Number($("set-max-zoom").value),
    max_zoom_airports: Number($("set-max-zoom-airports").value),
    disk_budget_gb: Number($("set-disk").value),
    cache_budget_gb: Number($("set-cache").value),
    provider: $("set-provider").value || saved?.provider || "BI",
    folder: $("set-folder").value.trim(),
    start_mode: startMode(),
    simbrief_user: $("set-simbrief").value.trim(),
    app_home: document.querySelector('input[name="app_home"]:checked')?.value || saved?.app_home || "ask",
    overlays: document.querySelector('input[name="overlays"]:checked')?.value || saved?.overlays || "auto",
  };
  $("settings-error").hidden = true;
  $("settings-status").textContent = "";
  try {
    saved = await api("settings", { method: "PUT", body });
    fillSettings(saved);
    $("settings-status").textContent = t("settings.saved");
  } catch (error) {
    $("settings-error").hidden = false;
    $("settings-error").textContent = error.message;
  }
}

async function chooseFolder() {
  const start = $("set-folder").value.trim() || last?.checks.folder || null;
  try {
    const got = await api("choose-folder", { method: "POST", body: { prompt: t("folder.prompt"), start } });
    if (got.path) $("set-folder").value = got.path;
  } catch (error) {
    $("settings-error").hidden = false;
    $("settings-error").textContent = error.message;
  }
}

// --- delete ------------------------------------------------------------------------------------

function openDelete() {
  const c = last.checks;
  $("delete-textures-name").textContent = t("delete.textures_name", { gb: gb(c.textures_bytes) });
  $("delete-imagery-name").textContent = t("delete.imagery_name", { gb: gb(c.imagery_bytes) });
  // X-Plane reads the textures: they stay while it runs
  $("delete-textures").disabled = c.xplane_running;
  $("delete-textures").checked = !c.xplane_running;
  $("delete-textures").closest(".choice").title = c.xplane_running ? t("delete.xplane_running") : "";
  $("delete-imagery").checked = true;
  $("delete-dialog").showModal();
}

async function deleteClosed() {
  if ($("delete-dialog").returnValue !== "delete") return;
  const textures = $("delete-textures").checked && !$("delete-textures").disabled;
  const imagery = $("delete-imagery").checked;
  if (textures || imagery) await act("delete", { textures, imagery });
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

/**
 * Whether a click lands on a field's title: a label naming a field outside it. OrthoStudio XP's own
 * (app.js isTitleClick): a label hands its clicks to its field, so a double click on a title to
 * copy it put the caret in the field. A title is text, and such a click is left to the text. A
 * label around its own box (a choice, a switch) is that box's target, and keeps it.
 */
export function isTitleClick(target) {
  const label = target?.closest?.("label");
  const control = label?.control;
  return Boolean(control) && !label.contains(control);
}

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

async function showSimbriefRoute() {
  $("map-error").hidden = true;
  try {
    const route = await api("map/route", { method: "POST" });
    liveMap?.showRoute(route.points);
  } catch (error) {
    $("map-error").hidden = false;
    $("map-error").textContent = error.status === 409
      ? t("map.route_no_name") // no SimBrief name in Live's settings
      : t("map.route_error", { error: error.message });
  }
}

async function startMap() {
  if (typeof L === "undefined") return; // Leaflet did not load: the rest of the page works
  try {
    // loaded here, not with the page: the map's modules read OrthoStudio XP's own (static/geo.js)
    const { createLiveMap } = await import("./livemap.js");
    liveMap = createLiveMap($("live-map"), { api, info: $("map-info") });
  } catch (_e) {
    liveMap = null;
    return;
  }
  $("map-follow").addEventListener("change", (event) => liveMap.setFollow(event.target.checked));
  // the bar over the map: shown unless hidden once, as remembered in this window
  let details = true;
  try {
    details = localStorage.getItem(MAP_DETAILS_KEY) !== "0";
  } catch (_e) {
    // storage may be unavailable: shown
  }
  $("map-details").checked = details;
  liveMap.setDetails(details);
  $("map-details").addEventListener("change", (event) => {
    liveMap.setDetails(event.target.checked);
    try {
      localStorage.setItem(MAP_DETAILS_KEY, event.target.checked ? "1" : "0");
    } catch (_e) {
      // not remembered: shown again next time
    }
  });
  $("live-map").addEventListener("live-map-follow", (event) => {
    $("map-follow").checked = event.detail;
  });
  $("map-route").addEventListener("click", showSimbriefRoute);
}

function tellLanguage() {
  // the plugin's words in X-Plane follow the window's language
  api("language", { method: "POST", body: { language: language() } }).catch(() => {});
}

async function askRegions() {
  // GitHub's list of AutoOrtho's regions, read by the engine in the background: the status brings it
  try {
    const regions = await api("regions");
    if (last) draw({ ...last, regions });
  } catch (_e) {
    // the status says it
  }
}

async function loadProviders() {
  try {
    providers = await api("providers");
  } catch (_e) {
    providers = [{ code: "BI", name: "Bing Maps", attribution: "", licence: "", max_zl: 19 }];
  }
  renderProviders(saved?.provider || "BI");
}

function boot() {
  // before any other listener: the click is the text's, not the field's
  document.addEventListener("click", (ev) => {
    if (isTitleClick(ev.target)) ev.preventDefault();
  }, true);
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  applyStatic(document);
  renderZooms();
  renderStartModes();
  $("lang-select").addEventListener("change", (event) => {
    setLanguage(event.target.value, true);
    tellLanguage();
    liveMap?.languageChanged();
    applyStatic(document);
    renderZooms();
    renderStartModes();
    renderProviders($("set-provider").value);
    if (last) draw(last);
  });
  $("start-btn").addEventListener("click", () => act("start", { fresh: $("fresh").checked }));
  $("stop-btn").addEventListener("click", () => act("stop"));
  $("xplane-btn").addEventListener("click", () => act("launch-xplane"));
  $("reload-btn").addEventListener("click", () => act("reload"));
  $("osxp-btn").addEventListener("click", () => act("osxp"));
  $("set-max-zoom").addEventListener("change", drawStartChoices);
  $("set-max-zoom-airports").addEventListener("change", drawStartChoices);
  $("leave-btn").addEventListener("click", () => act("leave"));
  $("uninstall-btn").addEventListener("click", () => act("uninstall"));
  $("fresh").addEventListener("change", () => last && drawState(last));
  $("set-provider").addEventListener("change", drawSource);
  $("set-simbrief").addEventListener("input", drawSimbrief);
  $("simbrief-osxp").addEventListener("click", () => {
    $("set-simbrief").value = last?.checks?.osxp_simbrief || "";
    drawSimbrief();
  });
  $("folder-choose").addEventListener("click", chooseFolder);
  $("delete-btn").addEventListener("click", openDelete);
  $("delete-dialog").addEventListener("close", deleteClosed);
  $("settings-form").addEventListener("submit", saveSettings);
  $("settings-undo").addEventListener("click", () => {
    if (saved) fillSettings(saved);
    $("settings-status").textContent = "";
    $("settings-error").hidden = true;
  });
  startMap();
  loadProviders();
  tellLanguage();
  askRegions();
  setInterval(askRegions, 60_000); // read again only when old (orthostudio/live/control.py)
  poll();
}

if (typeof document !== "undefined") boot();
