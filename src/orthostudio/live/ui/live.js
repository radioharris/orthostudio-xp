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

/** A texture's side at a level: 4096 px at level 0, 8192 px at -1, 256 px at 4. */
const side = (level) => (level >= 0 ? 4096 >> level : 4096 << -level);

/** "7 × 4096 px · 5 × 2048 px": counts by level, the first ``first`` (-1: 8192 px). */
function byLevel(counts, first) {
  const parts = counts.map((n, i) => (n ? `${number(n)} × ${side(first + i)} px` : null)).filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

const sum = (counts) => counts.reduce((total, n) => total + n, 0);

const gb = (bytes) => number(bytes / 1e9, 1);

/** Bytes in megabytes under a gigabyte, in gigabytes from one: "553 MB", "1.55 GB". */
function amount(bytes) {
  if (bytes < 0.9995e9) return `${number(bytes / 1e6)} ${t("unit.mb")}`;
  return `${number(bytes / 1e9, 2)} ${t("unit.gb")}`;
}

// --- drawing -----------------------------------------------------------------------------------

/** Names and values (OrthoStudio XP's .kv), each name kept with its value. */
function kv(dl, rows) {
  dl.replaceChildren();
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    const pair = document.createElement("div");
    pair.append(dt, dd);
    dl.append(pair);
  }
}

/** One of OrthoStudio XP's pills: its words, and its colour by its kind ("" for none). */
function drawPill(pill, [text, kind]) {
  pill.textContent = text;
  pill.className = kind ? `pill pill-${kind}` : "pill";
}

/** Figures in OrthoStudio XP's boxes (.cost, its estimate's): the value large, what it is under
 * it (its secondary text), or beside it in italics (`beside`, the user, 2026-10-09), then a detail
 * (its box's .sub), what failed first. `lines`: every box its three lines, its detail kept even
 * empty, no line wrapped, one too long cut short with its whole text in its hover (`hover`, the
 * detail's): boxes whose height never changes with what they say (the user, 2026-10-09). */
function drawFigures(box, figures, { lines = false } = {}) {
  box.hidden = !figures.length;
  box.classList.toggle("is-lines", lines);
  box.replaceChildren(...figures.map(({ value, label, beside, more, hover, problem }) => {
    const figure = document.createElement("div");
    figure.className = "cost live-figure";
    const big = document.createElement("b");
    big.textContent = value;
    const name = document.createElement(beside ? "i" : "span");
    name.className = "help";
    name.textContent = label;
    name.title = lines ? label : "";
    if (beside) {
      const line = document.createElement("span");
      line.append(big, " ", name);
      figure.append(line);
    } else {
      figure.append(big, name);
    }
    if (lines || more || problem) {
      const detail = document.createElement("span");
      detail.className = "sub num";
      if (problem) {
        const failed = document.createElement("span");
        failed.className = "is-fail";
        failed.textContent = problem;
        detail.append(failed, more ? ` · ${more}` : "");
      } else {
        detail.textContent = more || "\u00a0"; // empty, a line still
      }
      if (lines) detail.title = [problem, hover || more].filter(Boolean).join(" · ");
      figure.append(detail);
    }
    return figure;
  }));
}

/** The headline, its lead, and the dot's colour, from the state and what X-Plane does. */
export function headline(status) {
  const { state, stream, checks } = status;
  if (state === "starting") {
    // the region set up the first time; the textures at the size of a new zoom (status.relayout,
    // some seconds); else the pack and the plugin put in X-Plane
    let lead = "state.starting_install";
    if (!checks.region_ready) lead = "state.starting_region";
    else if (status.relayout) lead = "state.starting_zoom";
    return [t("state.starting"), t(lead), "running"];
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

/** The other programs streaming ortho that run (XPME, AutoOrtho), and that they must go. */
function streamersRunning(names) {
  if (names.length === 1) return t("checks.streamer_running", { name: names[0] });
  const last = names[names.length - 1];
  return t("checks.streamers_running", { names: `${names.slice(0, -1).join(", ")} ${t("checks.and")} ${last}` });
}

/** What keeps a stream from starting, in words, or null. */
function blocking(checks) {
  if (!checks.supported) return t("checks.unsupported");
  if (!checks.xplane) return t("checks.xplane_missing", { problem: checks.xplane_problem || "" });
  if (checks.folder_problem) return t("checks.folder_problem", { problem: checks.folder_problem });
  if (checks.ortho_streamers?.length) return streamersRunning(checks.ortho_streamers);
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
    // as the plugin's line: once every photo the start waits for is in, that instead of a rate of
    // 0.0, which read as stuck while the count went down (the user's Shadow, 2026-10-11)
    const photosIn = start.left > 0 && (start.building || 0) >= start.left;
    $("hold-text").textContent = t(photosIn ? "hold.photos_in" : "hold.text", {
      left: number(start.left),
      seconds: number(start.seconds),
      mbs: number(status.stream.mbs, 1),
    });
  }

  const error = status.state === "idle" && status.error;
  $("state-error").hidden = !error;
  $("state-error").textContent = error ? t("state.error", { error: status.error }) : "";
  const refused = refusedText(status);
  $("source-refused").hidden = !refused;
  // written when it changes only: an alert is read aloud again each time it is written
  if ($("source-refused").textContent !== refused) $("source-refused").textContent = refused;

  const { state, checks } = status;
  const idle = state === "idle";
  // the test of the 8192 px: said while it is on, the stream's own, else the next start's
  const running = status.stream?.streamer;
  const tint = running ? Boolean(running.tint_8192) : Boolean(status.settings?.tint_8192);
  $("tint-note").hidden = !tint;
  $("tint-note").textContent = tint ? t(running ? "state.tint_on" : "state.tint_next") : "";
  // the photos and their zooms: chosen before Start, and kept until Live stops
  $("set-provider").disabled = !idle;
  $("set-provider").title = idle ? "" : t("source.running");
  drawZooms();
  $("start-btn").hidden = !idle;
  $("start-btn").disabled = Boolean(blocking(checks));
  $("fresh-row").hidden = !idle;
  $("stop-btn").hidden = !["starting", "streaming"].includes(state);
  // X-Plane sees the pack and loads the plugin only if they are there when it starts
  $("xplane-btn").disabled = !(state === "streaming" && !checks.xplane_running);
  $("xplane-btn").hidden = checks.xplane_running || !["idle", "starting", "streaming"].includes(state);
  $("leave-btn").hidden = state !== "leaving";
  $("uninstall-btn").hidden = !(idle && checks.installed && !checks.xplane_running);
  // a departure prepared: Live started for it when idle, told of it while it streams
  $("departure-btn").disabled = !(state === "streaming" || (idle && !blocking(checks)));
  // From scratch's cost, when ticked; when to start X-Plane is said by its button, grey until
  // Live is ready, and by the line under the state (the user: no sentence saying it again)
  const help = idle && $("fresh").checked ? t("action.fresh_help") : "";
  $("action-help").textContent = help;
  $("action-help").hidden = !help;
}

/** While Live runs, the sources whose server refuses this computer (Downloader.refusing), in
 * OrthoStudio XP's words for its map (map.base_refused) and what to do with Live; "" otherwise. */
export function refusedText(status) {
  const names = status.state === "streaming" ? status.stream?.streamer?.refused_by || [] : [];
  return names.length ? t("source.refused", { provider: names.join(", ") }) : "";
}

let noticeDrawn = ""; // what the flight's notice says

/** A button to the regions, in the settings: where there is no mesh, the one to install. */
function goToRegions() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-small live-go";
  button.textContent = t("regions.go");
  button.addEventListener("click", () => showScreen("settings", "regions"));
  return button;
}

/** What the flight's card shows: X-Plane's state as a pill, the aircraft's three figures, the
 * rest as names and values. */
export function flightView(status) {
  const stream = status.state === "streaming" ? status.stream : null;
  if (!stream) {
    const running = status.checks.xplane_running;
    return {
      state: [running ? t("flight.xplane_running") : t("flight.xplane_stopped"), ""],
      figures: [],
      rows: [],
    };
  }
  let state = [t("flight.data_none"), ""];
  if (stream.data === "fresh") state = [t("flight.data_fresh"), "ok"];
  else if (stream.data === "stale") state = [t("flight.data_stale"), ""];
  const a = stream.aircraft;
  const figures = a
    ? [
      { value: `${number(a.alt_ft)}\u00a0ft`, label: t("flight.altitude") },
      { value: `${number(a.gs_kt)}\u00a0kt`, label: t("flight.speed") },
      // on two lines in a narrow box, the dot before the longitude
      { value: `${number(a.lat, 3)} ·\u00a0${number(a.lon, 3)}`, label: t("flight.position") },
    ]
    : [];
  const rows = [];
  const screen = stream.streamer?.screen;
  if (screen) {
    rows.push([t("flight.sharp"), t("flight.sharp_value", {
      km: number(screen.km_4096),
      width: number(screen.width),
      fov: number(screen.fov),
    })]);
  }
  // the SimBrief plan, only while the aircraft flies it: nothing says a pilot means to fly the
  // last one they made (a user, 2026-09-27)
  const plan = stream.streamer?.flight_plan;
  if (plan?.following) rows.push([t("flight.plan"), t("flight.plan_following", { name: plan.name })]);
  if (stream.camera_km != null && stream.camera_km > 0.5) {
    rows.push([t("flight.camera"), t("flight.camera_value", { km: number(stream.camera_km, 1) })]);
  }
  if (stream.start) rows.push([t("flight.start"), stream.start.name]);
  return { state, figures, rows };
}

function drawFlight(status) {
  const stream = status.state === "streaming" ? status.stream : null;
  // where the aircraft (or the start) has no mesh under it: the region to install
  const notice = stream?.notice;
  $("flight-notice").hidden = !notice;
  const said = notice
    ? t(notice.installed ? "flight.notice_installed" : "flight.notice", { region: t(`region.${notice.region}`) })
    : "";
  if (said !== noticeDrawn) { // drawn again only when it changes: its button keeps the click
    noticeDrawn = said;
    const words = document.createElement("span");
    words.textContent = said;
    $("flight-notice").replaceChildren(words);
    if (notice && !notice.installed) $("flight-notice").append(" ", goToRegions());
  }
  const view = flightView(status);
  drawPill($("flight-state"), view.state);
  drawFigures($("flight-figures"), view.figures);
  kv($("flight"), view.rows);
  $("flight-head").classList.toggle("is-alone", !view.figures.length && !view.rows.length && !notice);
}

/** What the textures' card shows while Live runs, null when it does not: their state as a pill,
 * the rate, four figures, the textures built by size, and the disk. */
export function buildView(status) {
  const stream = status.state === "streaming" ? status.stream : null;
  if (!stream) return null;
  const s = stream.streamer;
  const queued = sum(s.queued);
  const built = sum(s.built);
  // while X-Plane waits for its start, all of the start's textures are urgent: the state above
  // says so, with its bar
  const starting = Boolean(stream.start && !stream.start.ready);
  let state = [t("build.state_nothing"), ""];
  if (s.refused_by?.length) state = [t("build.state_refused"), "fail"]; // said under the source
  else if (s.failed && !s.downloaded) state = [t("build.state_failed"), "fail"];
  else if (s.urgent && !starting) state = [t("build.state_late", { n: number(s.urgent) }), "warn"];
  else if (queued || s.building) state = [t("build.state_building"), "running"];
  else if (built) state = [t("build.state_done"), "ok"];
  // Live's one disk space, as set: its textures and its downloaded imagery together
  const space = diskUsed(status);
  const budget = (Number(status.settings?.disk_gb) || 0) * 1e9;
  const share = budget ? Math.min(1, space.total / budget) : 0;
  const disk = t("build.disk_value", { used: gb(space.total), budget: number(budget / 1e9) });
  return {
    state,
    rate: [t("build.rate"), `${number(stream.mbs, 1)} ${t("unit.mb")}/s`],
    figures: [
      {
        value: number(queued),
        label: s.urgent ? t("build.queued_urgent", { n: number(s.urgent) }) : t("build.queued"),
        more: byLevel(s.queued, s.first_level),
      },
      {
        value: number(s.building),
        label: t("build.building"),
        more: s.building || s.pieces_awaited ? t("build.pieces_awaited", { n: number(s.pieces_awaited) }) : null,
      },
      { value: number(built), label: t("build.built"), more: null },
      {
        value: amount(s.downloaded_bytes),
        label: t("build.downloaded"),
        beside: true, // "263 MB downloaded", the pieces under them
        more: t("build.pieces", { n: number(s.downloaded) }), // what came from the cache in its hover
        hover: t("build.downloaded_pieces", { n: number(s.downloaded), cache: number(s.from_cache) }),
        problem: s.failed ? t("build.failed", { n: number(s.failed) }) : null,
      },
    ],
    sizes: s.built.map((n, i) => (n ? `${side(s.first_level + i)} px · ${number(n)}` : null)).filter(Boolean),
    disk: {
      text: s.at_8192 ? `${disk} · ${t("build.at_8192", { n: number(s.at_8192) })}` : disk,
      share,
      warn: share > 0.9,
    },
  };
}

function drawBuild(status) {
  const view = buildView(status);
  $("build").hidden = !view;
  $("build-state").hidden = !view;
  $("build-rate").hidden = !view;
  $("build-idle").hidden = Boolean(view);
  $("build-head").classList.toggle("is-alone", !view);
  if (!view) return;
  drawPill($("build-state"), view.state);
  $("build-rate").textContent = `${view.rate[0]} ${view.rate[1]}`;
  drawFigures($("build-figures"), view.figures, { lines: true });
  const title = document.createElement("span");
  title.className = "help";
  title.textContent = t("build.sizes");
  $("build-sizes").hidden = !view.sizes.length;
  $("build-sizes").replaceChildren(title, ...view.sizes.map((text) => {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = text;
    return pill;
  }));
  $("disk-text").textContent = view.disk.text;
  $("disk-bar").style.width = `${Math.round(view.disk.share * 1000) / 10}%`;
  $("disk").classList.toggle("warn", view.disk.warn);
}

let checksDrawn = ""; // the checks as drawn

/** A square's name as OrthoStudio XP writes it: +46+006. */
export function tileName(lat, lon) {
  const sign = (v) => (v < 0 ? "-" : "+");
  return `${sign(lat)}${String(Math.abs(lat)).padStart(2, "0")}${sign(lon)}${String(Math.abs(lon)).padStart(3, "0")}`;
}

function drawChecks(status) {
  const c = status.checks;
  const items = [];
  const add = (kind, text, go = false) => items.push([kind, text, go]);
  if (!c.supported) add("failed", t("checks.unsupported"));
  const m = c.machine;
  if (m) {
    const s = status.settings || {};
    if (shortOfMemory(m, s.max_zoom, s.max_zoom_airports)) {
      add("warn", t("zoom.memory", { ram: m.ram_gb, need: m.sharp_ram_gb }));
    } else {
      add("done", t("checks.machine", { cores: m.cores, ram: m.ram_gb }));
    }
  }
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
      t(c.mesh_found ? "checks.mesh" : "checks.mesh_missing", { path: names }), !c.mesh_found);
  }
  add(c.region_ready ? "done" : "info", t(c.region_ready ? "checks.region" : "checks.region_todo"));
  // the user's own tiles: X-Plane shows them, and Live leaves their squares alone
  const own = (c.own_tiles || []).map(([lat, lon]) => tileName(lat, lon));
  if (own.length) {
    add("done", t("checks.own_tiles", {
      tiles: own.length <= 4 ? own.join(", ") : t("checks.own_squares", { n: number(own.length) }),
    }));
  }
  const others = c.ortho_streamers || [];
  add(others.length ? "failed" : "done", others.length ? streamersRunning(others) : t("checks.streamers"));
  if (c.plugin === "ready") add("done", t("checks.plugin"));
  else if (c.plugin === "to_build") add("info", t("checks.plugin_build"));
  else add("warn", t("checks.plugin_missing"));
  if (status.state === "idle" && c.installed && !c.xplane_running) add("warn", t("checks.left_installed"));

  const key = JSON.stringify(items);
  if (key === checksDrawn) return; // drawn again only when they change: a button keeps its click
  checksDrawn = key;
  const ul = $("checks");
  ul.replaceChildren();
  for (const [kind, text, go] of items) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `dot dot-${kind}`;
    const words = document.createElement("span");
    words.textContent = text;
    if (go) words.append(" ", goToRegions());
    li.append(dot, words);
    ul.append(li);
  }
}

/** What Live takes on the disk now: its textures built and its downloaded imagery. */
function diskUsed(status) {
  const streaming = status.state === "streaming" && status.stream;
  const textures = streaming ? status.stream.streamer.disk_bytes : status.checks.textures_bytes;
  const imagery = status.checks.imagery_bytes || 0;
  return { textures, imagery, total: textures + imagery };
}

/** Disk space: what Live takes now, of the two, and whether they can be deleted. */
function drawDisk(status) {
  const used = diskUsed(status);
  $("disk-now").textContent = t("disk.now", {
    gb: gb(used.total), textures: gb(used.textures), imagery: gb(used.imagery),
  });
  const idle = status.state === "idle";
  $("delete-btn").disabled = !idle;
  $("delete-help").textContent = idle ? "" : t("disk.delete_streaming");
}

/** The file manager's name for a Show button: Finder, File Explorer, or the Linux folder. */
export function revealLabel(platform) {
  if (platform === "mac") return t("reveal.mac");
  if (platform === "win") return t("reveal.win");
  return t("reveal.lin");
}

/** A line of text with a Show button that shows ``path`` in the file manager. */
function pathLine(line, text, path, platform) {
  if (line.dataset.path === `${language()}:${path}`) return; // drawn already: keep the button
  line.dataset.path = `${language()}:${path}`;
  const show = document.createElement("button");
  show.type = "button";
  show.className = "btn btn-small reveal-btn";
  show.textContent = revealLabel(platform);
  show.addEventListener("click", () => act("reveal", { path }));
  line.replaceChildren(document.createTextNode(`${text} `), show);
}

/** Live's folder now, and the Finder's button beside it. */
function drawFolder(status) {
  const path = status.checks.folder;
  pathLine($("folder-now"), t("folder.now", { path }), path, status.platform);
  $("set-folder").placeholder = status.checks.folder_default;
}

/** The log's file, LogLive.txt beside X-Plane's Log.txt (orthostudio/live/logfile.py). */
function drawLogFile(status) {
  const line = $("log-file");
  line.hidden = !status.log_file;
  if (status.log_file) {
    pathLine(line, t("log.file", { path: status.log_file }), status.log_file, status.platform);
  }
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
  tellFrame?.(); // its state, which the app's frame says beside Live's name (embed.js)
  drawSimbrief();
  drawState(status);
  drawFlight(status);
  drawBuild(status);
  drawChecks(status);
  drawMemory();
  drawDisk(status);
  drawFolder(status);
  drawLogFile(status);
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

/** The source chosen on Live's screen, before Start: saved at once, the next start takes it. */
async function chooseProvider() {
  drawSource();
  $("source-error").hidden = true;
  try {
    saved = await api("settings", { method: "PUT", body: { provider: $("set-provider").value } });
  } catch (error) {
    $("source-error").textContent = error.message;
    $("source-error").hidden = false;
    renderProviders(saved?.provider || "BI");
  }
}

/** Under the list: what the source does not cover (its credit and licence: at the map's foot). */
function drawSource() {
  const p = providers.find((q) => q.code === $("set-provider").value);
  const notes = [];
  if (p && p.extent) notes.push(t("source.fallback", { country: countryName(p.extent) }));
  if (p && p.max_zl < SHARP_ZOOM) notes.push(t("source.low_zoom", { zl: p.max_zl }));
  $("provider-note").textContent = notes.join(" ");
  $("provider-note").hidden = !notes.length;
}

// --- the settings ------------------------------------------------------------------------------

/** The zooms offered, as AutoOrtho's: below the package's, the same, one above (8192 px). */
const ZOOMS = { max_zoom: [16, [14, 15, 16, 17]], max_zoom_airports: [18, [16, 17, 18, 19]] };

/** The combinations offered, in one list (the user, 2026-10-12): the package's, sharper airports,
 * sharper everywhere. 14 and 15 went (a saving nobody was measured to need) and 18 away from
 * airports never came (16,384 px textures). A pilot's other combination is shown as it is. */
const ZOOM_CHOICES = [
  [16, 18, "zoom.choice_standard"],
  [16, 19, "zoom.choice_airports"],
  [17, 19, "zoom.choice_sharpest"],
];

/** The zooms chosen in the list: [away from airports, around them]. */
function zoomsChosen() {
  const [zoom, airports] = ($("set-zooms").value || "16/18").split("/").map(Number);
  return [zoom, airports];
}

/** The list of combinations, the pilot's own kept when it is none of them. */
function renderZooms(current) {
  const select = $("set-zooms");
  const value = current || select.value || "16/18";
  const choices = ZOOM_CHOICES.map(([zoom, airports, key]) => [`${zoom}/${airports}`, t(key)]);
  if (!choices.some(([v]) => v === value)) choices.push([value, `ZL${value.replace("/", " / ZL")}`]);
  select.replaceChildren(...choices.map(([v, text]) => {
    const option = document.createElement("option");
    option.value = v;
    option.textContent = text;
    return option;
  }));
  select.value = value;
  drawZooms();
}

/** The list locked while Live runs, as the photos; in its hover, what each combination does and
 * when a new one takes effect. */
function drawZooms() {
  const idle = !last || last.state === "idle";
  const select = $("set-zooms");
  select.disabled = !idle;
  select.title = [t("zoom.choices_help"), idle ? t("zoom.change") : t("zoom.running")].join("\n");
}

/* The two zooms chosen apart, before the one list (2026-10-12), kept in case they come back:
const zoomSelect = (name) => $(name === "max_zoom" ? "set-max-zoom" : "set-max-zoom-airports");

/** The zooms beside the photos, on Live's screen: their choices short (ZL16), on one line with the
 * photos (the user, 2026-10-09), what each does in their hover (drawZooms). * /
function renderZooms() {
  for (const [name, [, zooms]] of Object.entries(ZOOMS)) {
    const select = zoomSelect(name);
    const current = select.value;
    select.replaceChildren(...zooms.map((zl) => {
      const option = document.createElement("option");
      option.value = String(zl);
      option.textContent = `ZL${zl}`;
      return option;
    }));
    if (current) select.value = current;
  }
  drawZooms();
}

/** The zooms locked while Live runs, as the photos; in their hover, where each applies, what the
 * chosen one does, and when a new one takes effect. * /
function drawZooms() {
  const idle = !last || last.state === "idle";
  for (const [name, [mesh]] of Object.entries(ZOOMS)) {
    const select = zoomSelect(name);
    const zl = Number(select.value);
    let choice = t("zoom.same", { zl });
    if (zl < mesh) choice = t("zoom.lower", { zl, times: 4 ** (mesh - zl) });
    else if (zl > mesh) choice = t("zoom.higher", { zl });
    select.disabled = !idle;
    select.title = [
      name === "max_zoom" ? t("zoom.max_help") : t("zoom.airports_help"),
      choice,
      idle ? t("zoom.change") : t("zoom.running"),
    ].join("\n");
  }
}
*/

/** A zoom chosen on Live's screen, before Start, as the photos: saved at once, the next start lays
 * the textures out for it. */
async function chooseZoom() {
  drawStart();
  drawZooms();
  $("source-error").hidden = true;
  $("zoom-disk").hidden = true;
  const disk = saved?.disk_gb;
  try {
    saved = await api("settings", {
      method: "PUT",
      body: {
        max_zoom: zoomsChosen()[0],
        max_zoom_airports: zoomsChosen()[1],
      },
    });
    // a space under the floor of these zooms, raised by Live as it saved them: said here
    if (disk !== undefined && saved.disk_gb > disk) {
      $("set-disk").value = String(saved.disk_gb);
      $("zoom-disk").textContent = t("zoom.disk", { gb: number(saved.disk_gb) });
      $("zoom-disk").hidden = false;
    }
  } catch (error) {
    $("source-error").textContent = error.message;
    $("source-error").hidden = false;
    if (saved) renderZooms(`${saved.max_zoom}/${saved.max_zoom_airports}`);
    drawStart();
    drawZooms();
  }
}

/** What a start does: with a zoom above the package's, the 8192 px around the aircraft too, and
 * the pink test of them. Nothing to choose: every file has its size from the start. */
function drawStart() {
  const [zoom, airports] = zoomsChosen();
  const sharp = zoom > ZOOMS.max_zoom[0] || airports > ZOOMS.max_zoom_airports[0];
  $("start-options").hidden = !sharp;
  $("start-plain").hidden = sharp;
  drawMemory();
}

/** Whether zooms above the package's (8192 px textures) are chosen on a computer with less memory
 * than they need (the checks' `machine`): the checks and the zoom settings say so. */
export function shortOfMemory(machine, maxZoom, maxZoomAirports) {
  if (!machine) return false;
  const sharp = maxZoom > ZOOMS.max_zoom[0] || maxZoomAirports > ZOOMS.max_zoom_airports[0];
  return sharp && machine.ram_gb < machine.sharp_ram_gb;
}

/** Under the zooms, as they are chosen: ZL17 or ZL19 on a computer short of memory for them. */
function drawMemory() {
  const m = last?.checks?.machine;
  const note = $("zoom-memory");
  const short = shortOfMemory(m, ...zoomsChosen());
  note.hidden = !short;
  if (short) note.textContent = t("zoom.memory", { ram: m.ram_gb, need: m.sharp_ram_gb });
}

function fillSettings(settings) {
  renderZooms(`${settings.max_zoom}/${settings.max_zoom_airports}`);
  $("set-disk").value = String(settings.disk_gb);
  $("set-tint").checked = Boolean(settings.tint_8192);
  $("set-folder").value = settings.folder || "";
  $("set-simbrief").value = settings.simbrief_user || "";
  for (const input of document.querySelectorAll('input[name="app_home"]')) {
    input.checked = input.value === (settings.app_home || "ask");
  }
  for (const input of document.querySelectorAll('input[name="overlays"]')) {
    input.checked = input.value === (settings.overlays || "auto");
  }
  drawSimbrief();
  drawStart();
  drawZooms();
  if (providers.length) renderProviders(settings.provider);
}

/** The answers the form holds, as Save sends them. The source and its zooms are not among them:
 * Live's screen saves them as soon as they are chosen. */
function formSettings() {
  return {
    disk_gb: Number($("set-disk").value),
    tint_8192: $("set-tint").checked,
    folder: $("set-folder").value.trim(),
    simbrief_user: $("set-simbrief").value.trim(),
    app_home: document.querySelector('input[name="app_home"]:checked')?.value || saved?.app_home || "ask",
    overlays: document.querySelector('input[name="overlays"]:checked')?.value || saved?.overlays || "auto",
  };
}

/** Whether an answer was changed without Save. */
function settingsChanged() {
  if (!saved) return false;
  const now = formSettings();
  return Object.keys(now).some((key) => String(now[key] ?? "") !== String(saved[key] ?? ""));
}

async function saveSettings(event) {
  event.preventDefault();
  const body = formSettings();
  $("settings-error").hidden = true;
  $("settings-status").textContent = "";
  try {
    const before = saved?.simbrief_user;
    saved = await api("settings", { method: "PUT", body });
    fillSettings(saved);
    $("settings-status").textContent = t("settings.saved");
    // under the floor of the zooms chosen: Live raised it, and says so beside the field
    $("disk-raised").hidden = !(saved.disk_gb > body.disk_gb);
    if (saved.disk_gb > body.disk_gb) {
      $("disk-raised").textContent = t("disk.raised", { gb: number(saved.disk_gb) });
    }
    if (saved.simbrief_user !== before) suggestDeparture({ fresh: true, now: true }); // their plan
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

/** OrthoStudio XP's question before quitting (app.js confirmQuit), with Live's lines. */
function confirmQuit(lines) {
  const dialog = $("quit-confirm");
  if (dialog.open) return Promise.resolve(false);
  $("quit-confirm-text").replaceChildren(...lines.map((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    return p;
  }));
  dialog.returnValue = "";
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close();
  };
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "quit"), { once: true });
    dialog.showModal();
  });
}

/** "Quit", as OrthoStudio XP's: asks, saying what a stream is worth, then Live goes, at once when
 * nothing streams, else once the stream is out and X-Plane has let go of the pack. Cmd+Q asks
 * elsewhere: in the app's frame (frame.py may_quit), or, with two windows (OSXP_FRAME=0), in a box
 * of the system's (web.py app_may_quit). */
async function quitLive() {
  let status = last;
  try {
    status = await api("status");
  } catch (_e) {
    // Live may be going already: the question stays the same
  }
  const running = Boolean(status && status.state !== "idle");
  const lines = [t("quit.text")];
  if (running) lines.push(t("quit.streaming"));
  if (running && status.checks?.xplane_running) lines.push(t("quit.xplane"));
  if (!(await confirmQuit(lines))) return;
  await act("close");
}

/**
 * Whether to go on while OrthoStudio XP builds (GET /api/live/osxp-build, a second at most): a
 * build uses a lot of processor time and may slow X-Plane and Live down. Asked in the page's modal
 * <dialog> before a start or a departure prepared; never refused, the pilot may fly all the same.
 */
async function askedWhileBuilding() {
  let building = false;
  try {
    building = Boolean((await api("osxp-build"))?.building);
  } catch (_e) {
    building = false; // no answer: nothing worth asking about
  }
  if (!building) return true;
  const dialog = $("build-running");
  if (dialog.open) return false;
  dialog.returnValue = "";
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close();
  };
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "go"), { once: true });
    dialog.showModal();
  });
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

// --- a departure prepared before X-Plane loads it ------------------------------------------------

let departurePlan = null; // the SimBrief plan the box was filled from: {icao, plan, briefing}
let departureOffer = null; // a newer plan, offered to load ("Flight plan updated")
let departureAskedAt = 0;
const DEPARTURE_AGAIN_MS = 30_000; // asked again at most this often, the window back in front...
const DEPARTURE_EVERY_MS = 120_000; // ... and this often while it is shown

/**
 * What the "Prepare" box shows of the pilot's SimBrief plans, from the plan it was filled from
 * (`shown`), the last one SimBrief gave (`got`: {icao, plan, briefing}, or {}) and the code in the
 * box (`typed`): `fill`, the code to put in the box (the first plan, into an empty box); `shown`,
 * the plan the box now comes from; `offer`, a plan to offer instead, never put in the box unasked
 * (the pilot may have typed another departure): one of another briefing than the box's.
 */
export function departureView(shown, got, typed) {
  const plan = got && got.icao ? got : null;
  const box = (typed || "").trim();
  if (plan && !shown && !box) return { fill: plan.icao, shown: plan, offer: null };
  const offer = plan && (!shown || plan.briefing !== shown.briefing) ? plan : null;
  return { fill: null, shown, offer };
}

/** The box's plan named under it while the box holds its departure; a newer plan offered. */
function drawDeparture() {
  const box = $("departure").value.trim().toUpperCase();
  const named = departurePlan && box === departurePlan.icao.toUpperCase();
  $("departure-plan").hidden = !named;
  $("departure-plan-text").textContent = named ? t("departure.from_plan", { plan: departurePlan.plan }) : "";
  $("departure-update").hidden = !departureOffer;
  $("departure-update-text").textContent = !departureOffer
    ? ""
    : departurePlan
      ? t("departure.updated", { plan: departureOffer.plan })
      : t("departure.last", { plan: departureOffer.plan });
}

/**
 * The pilot's last SimBrief plan: its departure put in the box while the box is empty, a newer plan
 * offered once the box holds one. `fresh`: asked again, at most every DEPARTURE_AGAIN_MS unless
 * `now` (a new SimBrief name, the plan line's button), the pilot perhaps back from simbrief.com
 * with a new plan. What SimBrief gave, or null when Live did not answer.
 */
async function suggestDeparture({ fresh = false, now = false } = {}) {
  if (fresh && !now && Date.now() - departureAskedAt < DEPARTURE_AGAIN_MS) return null;
  departureAskedAt = Date.now();
  let got = null;
  try {
    got = await api(now ? "departure?fresh=true&now=true" : fresh ? "departure?fresh=true" : "departure");
    const view = departureView(departurePlan, got, $("departure").value);
    if (view.fill) $("departure").value = view.fill;
    departurePlan = view.shown;
    departureOffer = view.offer;
  } catch (_e) {
    // no suggestion: the box stays as it is
  }
  drawDeparture();
  return got;
}

/** The plan line's button: SimBrief asked at once, the arrow turning meanwhile; a newer plan shows
 * in the yellow notice, else a word says that none came (the user asked, 2026-10-03). */
async function checkDeparture() {
  const button = $("departure-check");
  if (button.classList.contains("is-busy")) return;
  button.classList.add("is-busy");
  let got = null;
  try {
    got = await suggestDeparture({ fresh: true, now: true });
  } finally {
    button.classList.remove("is-busy");
  }
  if (!got?.icao) toast(t("departure.no_plan"));
  else if (!departureOffer) toast(t("departure.no_newer"));
}

/** The newer plan taken: its departure in the box, named under it, its route on the map. */
function loadDeparture() {
  if (!departureOffer) return;
  departurePlan = departureOffer;
  departureOffer = null;
  $("departure").value = departurePlan.icao;
  $("departure-error").hidden = true;
  drawDeparture();
  showSimbriefRoute(); // as the map's own button: the plan just taken, seen where it goes
}

async function prepareDeparture() {
  if (!(await askedWhileBuilding())) return;
  const departure = $("departure").value.trim();
  $("departure-error").hidden = true;
  try {
    await api("prepare", { method: "POST", body: { departure } });
  } catch (error) {
    $("departure-error").hidden = false;
    $("departure-error").textContent =
      error.status === 404
        ? t(departure ? "departure.unknown" : "departure.empty", { code: departure })
        : error.status === 409
          ? t("departure.busy")
          : error.message;
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

/** The legend's lines for the user's own tiles: those the map frames only. */
function drawOwnLegend(kinds) {
  for (const kind of ["osxp", "ortho4xp", "other"]) $(`legend-own-${kind}`).hidden = !kinds.has(kind);
}

async function startMap() {
  if (typeof L === "undefined") return; // Leaflet did not load: the rest of the page works
  try {
    // loaded here, not with the page: the map's modules read OrthoStudio XP's own (static/geo.js)
    const { createLiveMap } = await import("./livemap.js");
    liveMap = createLiveMap($("live-map"), { api, info: $("map-info"), ownKinds: drawOwnLegend });
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

// --- the window's keys and the settings' search, as OrthoStudio XP's ---------------------------

/**
 * `then` once this page is known to be in Live's own window, never in a browser's tab: pywebview
 * writes `window.pywebview` into the page it opens and announces it with `pywebviewready`, not
 * always before the page starts (as OrthoStudio XP's app.js whenInOwnWindow).
 */
function whenInOwnWindow(then) {
  if (window.pywebview) then();
  else window.addEventListener("pywebviewready", then, { once: true });
}

let toastTimer = 0;

/** A word for a moment, as OrthoStudio XP's: the text size just chosen. */
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 4000);
}

let osxpWords = null; // OrthoStudio XP's words (static/i18n.js), which its find bar and zoom speak
let textSizeTools = null; // its text size (static/textsize.js), once loaded
let findTools = null; // its find bar (static/find.js), once loaded
let zoomRefresh = null; // writes the zoom's level again in the page's language, once bound
let textSizeRefresh = null; // writes the text size's choices again, once bound

/**
 * OrthoStudio XP's find bar (Cmd+F, Ctrl+F), zoom (Cmd+plus, Cmd+minus, Cmd+0, and its control at
 * the foot) and text size (its own control at the foot), as they are (static/find.js,
 * static/zoom.js, static/textsize.js): Live's window has none of them, and a browser keeps its own
 * find and zoom; the text size works in a browser too. Loaded here rather than with the page, as
 * the map is: they speak OrthoStudio XP's own words, told the page's language. The user asked for
 * the foot of OrthoStudio XP's page in Live's (2026-10-03).
 */
async function bindPageTools() {
  try {
    const [words, find, zoom, textSize, embed] = await Promise.all([
      import("../static/i18n.js"),
      import("../static/find.js"),
      import("../static/zoom.js"),
      import("../static/textsize.js"),
      import("../static/embed.js"),
    ]);
    osxpWords = words;
    findTools = find;
    textSizeTools = textSize;
    osxpWords.setLanguage(language());
    textSize.applyTextSize(textSize.savedTextSize());
    textSizeRefresh = textSize.bindTextSize($("text-size"));
    find.bindFind();
    whenInOwnWindow(() => {
      find.bindFindKeys();
      zoomRefresh = zoom.bindZoom(toast, $("status-zoom"));
    });
    if (embed.inFrame()) find.bindFindKeys(); // in the app's frame: Cmd+F here, the zoom the frame's
    // in the app's frame, its screens are the frame's tabs, its choices the frame's (embed.js)
    tellFrame = embed.bindFrame({
      // the screens' names as the page's own bar says them, in its language
      tabs: () => SCREENS.map((id) => ({ id, label: $("nav").querySelector(`[data-screen="${id}"]`)?.textContent || id })),
      current: () => shownScreen,
      show: (id) => showScreen(id),
      prefs: framePrefs,
      ownPrefs: () => ({ language: language(), textSize: textSize.savedTextSize() }), // no theme of its own
      busy: () => last?.state || "",
    });
  } catch (_e) {
    // not there: the page works without them
  }
}

/** Whether this page is shown in the app's frame (static/embed.js frameOrigin, which comes with
 * OrthoStudio XP's other parts a moment later): known at once, so that the bars the frame shows
 * for it never show here meanwhile. */
function inAppFrame() {
  try {
    return window.parent !== window && /^http:\/\/127\.0\.0\.1:\d+$/.test(location.ancestorOrigins?.[0] || "");
  } catch (_e) {
    return false;
  }
}

let searching = null; // OrthoStudio XP's settings search (static/settings.js), once loaded

/** The text a reader sees in a question: what is hidden left out, a list's choices in. */
export function shownText(node) {
  let text = "";
  for (const child of node.childNodes) {
    if (child.nodeType === 3) text += ` ${child.nodeValue}`;
    else if (child.nodeType === 1 && !child.hidden) text += shownText(child);
  }
  return text;
}

/**
 * The settings' search, as OrthoStudio XP's (its searchWords and matchesSearch): every word
 * typed, in any order, accents, case and a plural's s aside. The questions left out leave the two
 * columns, so that those found gather at the top.
 */
function drawSearch() {
  const words = searching ? searching.searchWords($("settings-search").value) : [];
  const on = words.length > 0;
  const boxes = document.querySelectorAll("#settings-form .settings-questions > .question");
  let shown = 0;
  for (const box of boxes) {
    const hit = !on || searching.matchesSearch(shownText(box), words);
    if (box.hidden !== !hit) box.hidden = !hit;
    if (hit) shown += 1;
  }
  const note = $("settings-search-note");
  note.hidden = !on;
  note.classList.toggle("is-warn", on && shown === 0);
  note.textContent = !on
    ? ""
    : shown
      ? t("settings.search_found", { n: shown, total: boxes.length })
      : t("settings.search_none");
}

async function bindSearch() {
  $("settings-search").addEventListener("input", drawSearch);
  try {
    const { searchWords, matchesSearch } = await import("../static/settings.js");
    searching = { searchWords, matchesSearch };
  } catch (_e) {
    $("settings-search").closest(".settings-search").hidden = true; // nothing to search with
    return;
  }
  drawSearch();
}

// --- the two screens, as OrthoStudio XP's -------------------------------------------------------

const SCREENS = ["live", "settings"];
let shownScreen = "live";

/** The screen an address names: #live, #settings, #settings/regions (that question in view).
 * Live's by default. */
export function screenFromHash(hash) {
  const m = String(hash || "").match(/^#(\w+)(?:\/([\w-]+))?$/);
  if (!m || !SCREENS.includes(m[1])) return { name: "live", question: null };
  return { name: m[1], question: m[1] === "settings" ? m[2] || null : null };
}

// --- the map as tall as the screen, as OrthoStudio XP's Plan's ------------------------------------

/**
 * How far down the page the map starts, as --map-top, so that its card reaches the foot bar
 * (live.css), as OrthoStudio XP's app.js measureMapTop: .plan-flow measured, not the map, which
 * sticks to the top as the page scrolls. Nothing measured while the settings are shown.
 */
function measureMapTop() {
  const flow = document.querySelector("#screen-live .plan-flow");
  if (!flow || flow.offsetParent === null) return;
  let top = 0;
  for (let el = flow; el; el = el.offsetParent) top += el.offsetTop;
  const now = `${Math.round(top)}px`;
  const root = document.documentElement;
  if (root.style.getPropertyValue("--map-top") !== now) root.style.setProperty("--map-top", now);
}

/** The foot bar's height, which grows when its items wrap, as --statusbar-h (OrthoStudio XP's
 * trackStatusbarHeight): the map's card ends above it. */
function trackStatusbarHeight() {
  const bar = $("statusbar");
  if (!bar) return;
  const set = () => document.documentElement.style.setProperty("--statusbar-h", `${bar.offsetHeight}px`);
  set();
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(set).observe(bar);
}

/**
 * Live's screen (the source, Start, the map, the flight, the textures, the checks) or its
 * settings, one at a time, as OrthoStudio XP's screens: in flight only the first is looked at, and
 * the settings are answered once (a user, 2026-09-27).
 */
function showScreen(name, question = null) {
  if (!SCREENS.includes(name)) name = "live";
  if (shownScreen === "settings" && name !== "settings" && settingsChanged()) {
    toast(t("settings.left_unsaved")); // as OrthoStudio XP says it: nothing applies without Save
  }
  shownScreen = name;
  for (const s of SCREENS) $(`screen-${s}`).hidden = s !== name;
  for (const btn of $("nav").querySelectorAll(".nav-btn")) {
    if (btn.dataset.screen === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  tellFrame?.(); // the app's frame shows these screens as its tabs (embed.js)
  findTools?.findForget(); // an open find bar looks in the screen now shown
  if (name === "live") {
    measureMapTop(); // measured nothing while hidden
    liveMap?.shown();
  }
  if (question) showQuestion(question);
  else window.scrollTo(0, 0);
}

/** A question of the settings in view, as a button pointing at it asks: shown even where the
 * search had left it out, and marked for a moment. */
function showQuestion(question) {
  const box = $(`q-${question}`);
  if (!box) return;
  if (box.hidden) {
    $("settings-search").value = "";
    drawSearch();
  }
  box.scrollIntoView({ block: "start" });
  box.classList.add("is-pointed");
  setTimeout(() => box.classList.remove("is-pointed"), 2000);
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

let tellFrame = null; // gives the app's frame these screens again, in it (embed.js)

/** Everything the page says again in a new language, chosen here or in the app's frame. */
function languageChanged() {
  osxpWords?.setLanguage(language());
  tellLanguage();
  liveMap?.languageChanged();
  applyStatic(document);
  measureMapTop(); // the bar above may wrap otherwise in the new language
  zoomRefresh?.();
  textSizeRefresh?.();
  renderZooms();
  renderProviders($("set-provider").value);
  drawSearch();
  drawDeparture();
  if (last) draw(last);
  tellFrame?.();
}

/** The theme chosen in the app's frame: Live's own page follows the system's. */
function applyTheme(theme) {
  if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

/** What the pilot chose in the app's frame, applied as if chosen here (embed.js). */
function framePrefs({ language: wanted, theme, textSize }) {
  if (wanted && wanted !== language()) {
    setLanguage(wanted, true);
    $("lang-select").value = language();
    languageChanged();
  }
  if (theme !== undefined && theme !== null) applyTheme(theme);
  if (textSize) textSizeTools?.applyTextSize(textSize);
}

function boot() {
  if (inAppFrame()) document.documentElement.classList.add("in-frame"); // its bars, the frame's
  // before any other listener: the click is the text's, not the field's
  document.addEventListener("click", (ev) => {
    if (isTitleClick(ev.target)) ev.preventDefault();
  }, true);
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  applyStatic(document);
  renderZooms();
  drawStart();
  trackStatusbarHeight();
  measureMapTop();
  window.addEventListener("resize", measureMapTop); // the frame sends one when it shows the page
  $("lang-select").addEventListener("change", (event) => {
    setLanguage(event.target.value, true);
    languageChanged();
  });
  $("quit-btn").addEventListener("click", quitLive);
  $("start-btn").addEventListener("click", async () => {
    if (await askedWhileBuilding()) act("start", { fresh: $("fresh").checked });
  });
  $("departure-btn").addEventListener("click", prepareDeparture);
  $("departure").addEventListener("keydown", (event) => {
    if (event.key === "Enter") prepareDeparture();
  });
  $("departure").addEventListener("input", drawDeparture);
  $("departure-load").addEventListener("click", loadDeparture);
  $("departure-check").addEventListener("click", checkDeparture);
  // a new SimBrief plan: looked for when the pilot comes back to the window (from simbrief.com,
  // say), and every two minutes while it is shown
  window.addEventListener("focus", () => suggestDeparture({ fresh: true }));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") suggestDeparture({ fresh: true });
  });
  setInterval(() => {
    if (document.visibilityState === "visible") suggestDeparture({ fresh: true });
  }, DEPARTURE_EVERY_MS);
  $("stop-btn").addEventListener("click", () => act("stop"));
  $("xplane-btn").addEventListener("click", () => act("launch-xplane"));
  $("osxp-btn").addEventListener("click", () => act("osxp"));
  $("set-zooms").addEventListener("change", chooseZoom);
  $("leave-btn").addEventListener("click", () => act("leave"));
  $("uninstall-btn").addEventListener("click", () => act("uninstall"));
  $("fresh").addEventListener("change", () => last && drawState(last));
  $("set-provider").addEventListener("change", chooseProvider);
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
  $("nav").addEventListener("click", (event) => {
    const btn = event.target.closest(".nav-btn");
    if (btn) showScreen(btn.dataset.screen);
  });
  window.addEventListener("hashchange", () => {
    const route = screenFromHash(location.hash);
    showScreen(route.name, route.question);
  });
  const route = screenFromHash(location.hash);
  showScreen(route.name, route.question);
  startMap();
  bindPageTools();
  bindSearch();
  loadProviders();
  suggestDeparture();
  tellLanguage();
  askRegions();
  setInterval(askRegions, 60_000); // read again only when old (orthostudio/live/control.py)
  poll();
}

if (typeof document !== "undefined") boot();
