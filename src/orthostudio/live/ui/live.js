// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's page: it asks /api/live/status every second and shows the stream's state,
// what X-Plane is doing, what is being built, the checks and the settings. The streaming itself
// runs in the engine (orthostudio/live/control.py); this page only starts it, stops it and shows it.

import { DEFAULT_LOOKS, LOOK_VALUES, lookImageData } from "./look.js";
import { applyStatic, countryName, detectLanguage, language, setLanguage, t } from "./strings.js";

const POLL_MS = 1000;
const MAP_DETAILS_KEY = "osxp.live.mapDetails"; // the bar over the map, shown or hidden
const API_LEVEL = 2; // orthostudio/live/control.py API
const SHARP_ZOOM = 16; // a source stopping below it shows blurrier ortho up close

const $ = (id) => document.getElementById(id);

let last = null; // the last status
let logSeq = 0;
let providers = []; // GET /api/live/providers
let saved = null; // the settings as saved: what "Undo my changes" goes back to
let namedXplane = ""; // the X-Plane folder named, in OrthoStudio XP's settings: the same for Build
let otherXplanes = []; // the other X-Planes of this computer
let liveMap = null; // the map (livemap.js), when Leaflet has loaded

// --- talking to the engine ---------------------------------------------------------------------

async function api(path, options) {
  return fetchJson(`api/live/${path}`, options);
}

/** OrthoStudio XP's own paths that Live's server answers too (/api/update…, the new version's
 * banner: static/update.js), asked as Build's page asks them. */
function osxpRequest(method, path, body) {
  return fetchJson(path, { method, body });
}

async function fetchJson(url, { method = "GET", body } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(url, init);
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

/** The other programs streaming ortho that run (XPME, AutoOrtho, another one Live does not name:
 * scenery.ANOTHER_STREAMER), and that they must go. */
function streamersRunning(given) {
  const names = given.map((n) => (n === "another streamer" ? t("checks.another_streamer") : n));
  const last = names[names.length - 1];
  const text = names.length === 1
    ? t("checks.streamer_running", { name: last })
    : t("checks.streamers_running", { names: `${names.slice(0, -1).join(", ")} ${t("checks.and")} ${last}` });
  return text[0].toUpperCase() + text.slice(1);
}

/** A text saying there is no mesh: a folder of another tool's mesh gone when one is, as the
 *  start says it; another tool's mesh offered only when none is chosen. */
function noMesh(key, checks) {
  const other = checks?.other_meshes;
  if (other?.folders.some((f) => f.unused === "gone") && key === "checks.mesh_missing") return "checks.other_mesh_gone";
  return other ? key : `${key}_other`;
}

/** What keeps a stream from starting, in words, or null. */
function blocking(checks) {
  if (!checks.supported) return t("checks.unsupported");
  if (!checks.xplane) return t("checks.xplane_missing", { problem: checks.xplane_problem || "" });
  if (checks.folder_problem) return t("checks.folder_problem", { problem: checks.folder_problem });
  if (checks.ortho_streamers?.length) return streamersRunning(checks.ortho_streamers);
  if (checks.installing) return t("checks.installing");
  if (!checks.mesh_found && !checks.region_ready) return t(noMesh("checks.mesh_missing", checks), { path: checks.mesh });
  if (checks.xplane_running && !checks.installed) return t("checks.xplane_running_late");
  return null;
}

/** The line under a start's bar, as the plugin's loading window says it (live_gate.c's show(), from
 * the same count, streamer.start_count). */
export function holdText(start, mbs) {
  const seconds = number(start.seconds);
  // every texture built, the files of the squares round the start still being made (sq=)
  if (start.left === 0 && (start.squares || 0) > 0) {
    return t(start.squares === 1 ? "hold.ground_one" : "hold.ground", { n: number(start.squares), seconds });
  }
  // once every photo the start waits for is in, that instead of a rate of 0.0, which read as
  // stuck while the count went down (the user's Shadow, 2026-10-11)
  const photosIn = start.left > 0 && (start.building || 0) >= start.left;
  // the night textures alone left (streamer.Arrival.count)
  const nightsOnly = (start.night || 0) > 0 && start.night >= start.left;
  return t(nightsOnly ? "hold.night" : photosIn ? "hold.photos_in" : "hold.text", {
    left: number(start.left),
    seconds,
    mbs: number(mbs, 1),
  });
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
    $("hold-text").textContent = holdText(start, status.stream.mbs);
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
  $("season-note").textContent = seasonText(running, status.settings);
  // the photos and their zooms: chosen before Start, and kept until Live stops
  $("set-provider").disabled = !idle;
  $("set-provider").title = idle ? "" : t("source.running");
  drawZooms();
  $("start-btn").hidden = !idle;
  $("start-btn").disabled = Boolean(blocking(checks));
  $("delete-here").hidden = !idle;
  $("stop-btn").hidden = !["starting", "streaming"].includes(state);
  // X-Plane sees the pack and loads the plugin only if they are there when it starts
  $("xplane-btn").disabled = !(state === "streaming" && !checks.xplane_running);
  $("xplane-btn").hidden = checks.xplane_running || !["idle", "starting", "streaming"].includes(state);
  $("leave-btn").hidden = state !== "leaving";
  $("uninstall-btn").hidden = !(idle && checks.installed && !checks.xplane_running);
  // a departure prepared: Live started for it when idle, told of it while it streams
  $("departure-btn").disabled = !(state === "streaming" || (idle && !blocking(checks)));
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
  button.className = "btn live-go";
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
    ? t(notice.installed ? "flight.notice_installed" : noMesh("flight.notice", status.checks), { region: t(`region.${notice.region}`) })
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
    // a region's name, else the folder's (another tool's mesh)
    const named = (id) => (t(`region.${id}`) === `region.${id}` ? id : t(`region.${id}`));
    const names = (c.mesh_regions || []).map(named).join(", ") || c.mesh;
    add(c.mesh_found || c.region_ready ? "done" : "failed",
      t(c.mesh_found ? "checks.mesh" : noMesh("checks.mesh_missing", c), { path: names }), !c.mesh_found);
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
  // a folder of another tool's mesh also checked in X-Plane above Live: X-Plane draws it there
  for (const folder of c.other_meshes?.folders || []) {
    if (folder.in_xplane.length) add("warn", t("checks.other_mesh_in_xplane", { packs: folder.in_xplane.join(", ") }));
  }

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

/** Disk space: what Live takes now, of the two, and whether they can be deleted. « Stop Live to
 * delete » only while Live runs (starting or streaming): while it deletes, stops or waits for
 * X-Plane to quit there is nothing to stop, and the state above says what it does. */
function drawDisk(status) {
  const used = diskUsed(status);
  $("disk-now").textContent = t("disk.now", {
    gb: gb(used.total), textures: gb(used.textures), imagery: gb(used.imagery),
  });
  const running = status.state === "starting" || status.state === "streaming";
  $("delete-btn").disabled = status.state !== "idle";
  $("delete-help").textContent = running ? t("disk.delete_streaming") : "";
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
  show.className = "btn reveal-btn";
  show.textContent = revealLabel(platform);
  show.addEventListener("click", () => act("reveal", { path }));
  line.replaceChildren(document.createTextNode(`${text} `), show);
}

/** Live's folder now, and the Finder's button beside it. */
function drawFolder(status) {
  const path = status.checks.folder;
  pathLine($("folder-now"), t("folder.now", { path }), path, status.platform);
  $("set-folder").placeholder = status.checks.folder_default;
  drawOtherMeshes(status.checks.other_meshes);
}

/** Why Live takes none of a folder's meshes (the engine's ``unused``), said under its path. */
const OTHER_UNUSED = { gone: "other_mesh.gone", no_mesh: "other_mesh.none", above: "other_mesh.above", behind: "other_mesh.behind" };
/** The packs left aside that the pilot may have meant, said under the path; the others (a region
 *  Live has already, ground with no photos to lay) only in the name's hover. */
const OTHER_ACT_ON = new Set(["photos", "no_terrain", "unreadable"]);
const refusedToSay = (f) => f.refused.filter((r) => OTHER_ACT_ON.has(r.why));
/** A folder Live cannot use, said as a warning: not there, or no mesh Live can use in it. */
const otherUnusable = (f) => f.unused === "gone" || f.unused === "no_mesh";

/** Packs left aside, a few by name and why, the rest counted: X-Plane's folder chosen lists every
 *  tile of Custom Scenery. Null: none. */
function leftAside(refused) {
  if (!refused.length) return null;
  const shown = refused.slice(0, 5);
  const names = shown.map((r) => `${r.name} (${t(`other_mesh.why_${r.why}`)})`).join(", ");
  const more = refused.length - shown.length;
  return more ? t("other_mesh.refused_more", { names, count: more }) : t("other_mesh.refused", { names });
}

/** What a folder of another tool's mesh gives Live, its box in the list, every fact the engine's
 *  (orthostudio.live.scenery.other_mesh_said), nothing guessed here. Beside its name (count), how
 *  many squares it gives and how many of them are in front of Live, nothing when it gives none.
 *  Under its path (note), only what the pilot acts on: why Live takes none of its meshes (not
 *  there, no mesh Live can use, folders higher in the list giving all its squares, or the squares
 *  it gives all behind Live's mesh, the normal case of a mesh put in front of Live square by
 *  square), and the packs left aside he may have meant. In its name's hover (title), its name,
 *  the meshes and roads it gives, and every pack left aside. A warning only for a folder Live
 *  cannot use. {count, note, warn, title} */
export function otherMeshSaid(f) {
  const front = number(f.front.length);
  let count = "";
  if (f.squares === 1) count = t("other_mesh.squares_one", { front });
  else if (f.squares) count = t("other_mesh.squares", { n: number(f.squares), front });
  const hover = [f.name];
  if (f.meshes.length) {
    hover.push(t(f.next_start ? "other_mesh.meshes_next" : "other_mesh.meshes", { names: f.meshes.join(", ") }));
    hover.push(f.overlays.length ? t("other_mesh.overlays", { names: f.overlays.join(", ") }) : t("other_mesh.no_roads"));
  }
  hover.push(leftAside(f.refused));
  const unused = OTHER_UNUSED[f.unused];
  const note = [unused && t(unused), leftAside(refusedToSay(f))];
  return {
    count,
    note: note.filter(Boolean).join(" "),
    warn: otherUnusable(f),
    title: hover.filter(Boolean).join("\n"),
  };
}

/** What the title of other meshes says beside it, folded or open, and what needs the pilot there,
 *  a word each, which opens the list by itself (foldOpen): a folder not there or giving no mesh, a
 *  pack left aside he may have meant (each folder's own notes, otherMeshSaid). A button refused is
 *  none: said under the field, the list open since it holds them. Every word known at each status
 *  (foldSection's `known`, by default). Its badge: how many folders, and how many of their squares
 *  are in front of Live, in the warning's colour where a folder cannot be used; none without a
 *  folder. {badges: [{text, kind}], needs} */
export function othersSummary(other) {
  const folders = other?.folders || [];
  const needs = [];
  for (const f of folders) {
    if (otherUnusable(f)) needs.push(`${f.unused}:${f.folder}`);
    if (refusedToSay(f).length) needs.push(`refused:${f.folder}`);
  }
  if (!folders.length) return { badges: [], needs };
  const front = number(folders.reduce((n, f) => n + f.front.length, 0));
  const text = folders.length === 1 ? t("other_mesh.badge_one", { front })
    : t("other_mesh.badge", { n: number(folders.length), front });
  return { badges: [{ text, kind: folders.some(otherUnusable) ? "warn" : null }], needs };
}

/** The folders of other tools' meshes, top first, the first that has a square giving it
 *  (orthostudio/live/control.py _other_meshes_said): a box each, its name, what it gives and its
 *  ↑ ↓ on a line, its path under them, what the pilot acts on, then its three buttons, each saved
 *  by the engine in one place (otherAct); under them, what holds for the whole list. Drawn again
 *  only when they change: a button keeps its click. */
function drawOtherMeshes(other) {
  const list = $("other-meshes");
  const folders = other?.folders || [];
  const key = JSON.stringify([language(), folders]);
  folds.others?.draw(othersSummary(other));
  drawOtherFront(other);
  if (!h || list.dataset.drawn === key) return; // h: static/dom.js, loaded on its own (bindDom)
  list.dataset.drawn = key;
  list.hidden = !folders.length;
  list.replaceChildren(...folders.map((f, i) => {
    const act = (name, body = {}) => () => otherAct(name, { folder: f.folder, ...body });
    const none = !f.squares; // the squares it gives, the engine's count: none to put in front or back
    const said = otherMeshSaid(f);
    return h("li", { class: "other-mesh" },
      h("div", { class: "other-mesh-head" },
        h("span", { class: "other-mesh-name", title: said.title }, f.name),
        said.count ? h("span", { class: "other-mesh-count" }, said.count) : null,
        h("span", { class: "other-mesh-order" },
          regionButton("↑", act("move", { up: true }), { disabled: i === 0, title: t("other_mesh.up") }),
          regionButton("↓", act("move", { up: false }), { disabled: i === folders.length - 1, title: t("other_mesh.down") }))),
      h("p", { class: "other-mesh-path", title: f.folder }, f.folder),
      said.note ? h("p", { class: said.warn ? "other-mesh-note is-warn" : "other-mesh-note" }, said.note) : null,
      h("div", { class: "other-mesh-actions" },
        regionButton(t("other_mesh.all_front"), act("all", { front: true }), { disabled: none }),
        regionButton(t("other_mesh.all_behind"), act("all", { front: false }), { disabled: none }),
        regionButton(t("other_mesh.remove"), act("remove"), { title: t("other_mesh.remove_help") })));
  }));
}

/** Under the list, a line each: with two folders or more, that the higher one gives a square
 *  both have; that a change waits for Live's next start; while a folder is not there and a square
 *  in front comes from no folder there (front_unseen, the engine's count), that its Remove leaves
 *  its squares in front of Live, Live not knowing which it gave (orthostudio/live/api.py
 *  other_remove). Empty: no folder. */
export function otherFrontSaid(other) {
  const folders = other?.folders || [];
  const said = [];
  if (folders.length > 1) said.push(t("other_mesh.higher_wins"));
  if (folders.length) said.push(t("other_mesh.next_start"));
  if (other?.front_unseen > 0 && folders.some((f) => f.unused === "gone")) said.push(t("other_mesh.gone_remove"));
  return said;
}

function drawOtherFront(other) {
  const line = $("other-mesh-front");
  line.textContent = otherFrontSaid(other).join("\n"); // a line each (live.css)
  line.hidden = !line.textContent;
}

/** A button of the folders of other tools' meshes, or a folder added: saved by the engine, in one
 *  place each (orthostudio/live/api.py other_changed), which answers the list and the squares in
 *  front as saved; the map and the list asked anew. False when refused, said under the field: a
 *  folder listed already in the page's words, said again in a new language (data-i18n,
 *  applyStatic), the engine's own words otherwise. */
async function otherAct(act, body) {
  const error = $("other-mesh-error");
  error.hidden = true;
  try {
    await api(`other/${act}`, { method: "POST", body });
  } catch (e) {
    const known = e.status === 409 && act === "add" ? "other_mesh.already" : null;
    if (known) error.dataset.i18n = known;
    else delete error.dataset.i18n;
    error.textContent = known ? t(known) : e.message;
    error.hidden = false;
    return false;
  }
  liveMap?.otherChanged();
  try {
    draw(await api("status"));
  } catch (_e) {
    // the loop says it
  }
  return true;
}

/** A folder typed (Enter) or chosen (Choose…): added at the bottom of the list, the field emptied
 *  unless something else was typed in it meanwhile. */
async function addOtherMesh(folder) {
  const field = $("set-other-mesh");
  if (folder.trim() && (await otherAct("add", { folder })) && field.value === folder) field.value = "";
}

/** A square of another tool's mesh clicked on the map: the page sends the square alone, and the
 *  engine puts it in front of Live where it is behind, back where it is in front, as saved now
 *  (orthostudio/live/settings.py flip_front), so that a second click before the first one's
 *  answer flips it back; the toast says where it is as saved, the map and the list draw it. */
async function toggleFront({ lat, lon }) {
  const tile = tileName(lat, lon);
  try {
    const got = await api("map/other", { method: "POST", body: { square: tile } });
    toast(t(got.other_mesh_front.includes(tile) ? "map.other_put_front" : "map.other_put_behind", { tile }));
  } catch (error) {
    toast(error.message);
  }
  liveMap?.otherChanged();
}

/** The X-Plane Live works with, the others found, and whether it can be changed now. */
function drawXplane(status) {
  const { xplane, xplane_running: running } = status.checks;
  const line = $("xplane-now");
  if (xplane) {
    pathLine(line, t("folder.now", { path: xplane }), xplane, status.platform);
  } else {
    delete line.dataset.path;
    line.textContent = t("xplane.not_found");
  }
  $("set-xplane").placeholder = xplane ? t("xplane.placeholder") : "";
  $("xplane-others").hidden = !otherXplanes.length;
  $("xplane-others").textContent = t("xplane.others", { paths: otherXplanes.join(", ") });
  const busy = status.state !== "idle" || running; // the pack is in use
  $("set-xplane").disabled = busy;
  $("xplane-choose").disabled = busy;
  $("xplane-busy").hidden = !busy;
}

/** The field follows the folder named unless something else is being typed in it. */
function fillXplaneFolder(got, { typed = false } = {}) {
  const field = $("set-xplane");
  const untouched = field.value.trim() === namedXplane;
  namedXplane = got.named || "";
  otherXplanes = got.others;
  if (typed || untouched) field.value = namedXplane;
  if (last) drawXplane(last);
}

/** Build's Settings ask the same question, and may have answered it since. */
async function readXplaneFolder() {
  try {
    fillXplaneFolder(await api("xplane-folder"));
  } catch (_e) {
    // the Settings go on without the list; a save says what is wrong
  }
}

/** Saved before Live's own settings: its folder is checked against this X-Plane. */
async function saveXplaneFolder() {
  const path = $("set-xplane").value.trim();
  if (path === namedXplane) return;
  const body = { path: path || null };
  fillXplaneFolder(await api("xplane-folder", { method: "PUT", body }), { typed: true });
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

// --- the two long lists of the mesh's card, which fold away ------------------------------------

let keptTools = null; // what the page keeps from one visit to the next (static/kept.js), once loaded
let legendTools = null; // the legend's chevron (static/legend.js setChevron), once loaded
let sharedLoading = null;

/** OrthoStudio XP's way of keeping the page's choices for the viewer (static/kept.js), for the map
 * and the lists that fold, and the legend's chevron they fold with (static/legend.js), loaded once,
 * each on its own; without kept.js, they keep nothing beyond the visit. */
function loadShared() {
  sharedLoading ??= Promise.all([
    import("../static/kept.js").then((tools) => { keptTools = tools; }, () => {}),
    import("../static/legend.js").then((tools) => { legendTools = tools; }, () => {}),
  ]);
  return sharedLoading;
}

/** The choice kept for a list that folds, {open, seen}: open or folded by the pilot, and what
 * needed him as he chose; null when he never chose, or for words of no such choice. */
export function foldKept(text) {
  try {
    const kept = JSON.parse(text);
    if (typeof kept?.open === "boolean" && Array.isArray(kept.seen)) return kept;
  } catch (_e) {
    // nothing kept
  }
  return null;
}

/** Whether a list that folds is open: folded by default, open by itself while something needs the
 * pilot (`needs`, a word each: an update ready, a folder not there); once he opened or folded it,
 * as he left it (`kept`, foldKept), unless something needs him that did not as he chose. */
export function foldOpen(kept, needs) {
  if (!kept) return needs.length > 0;
  return kept.open || needs.some((need) => !kept.seen.includes(need));
}

/** The choice kept, what needed him then cut to what still needs him (`needs`, every word known):
 * a need gone then back (next month's update of the same region, a second failed install) opens
 * the list again. The same choice when nothing went. */
export function foldSeen(kept, needs) {
  if (!kept || kept.seen.every((need) => needs.includes(need))) return kept;
  return { open: kept.open, seen: kept.seen.filter((need) => needs.includes(need)) };
}

const folds = {}; // the lists that fold, by name (foldSection), made at the page's start
const showFolds = () => Object.values(folds).forEach((fold) => fold.show());

/**
 * A list of the settings folded away under its title (index.html `<name>-head`, its badges
 * `<name>-badges`, its chevron `<name>-fold`, its body `<name>-body`), kept for the viewer under
 * `osxp.live.fold.<name>` as the legend keeps its groups (static/kept.js). A click on the title's
 * line or on its chevron, a real button, opens or folds it and keeps that with what needs the pilot
 * then (foldOpen); a double click or a drag on the title is left to its text, which can be
 * selected. Opened for the visit alone, not kept: pointed at (`open`, showQuestion), or while a
 * search is typed (`searched`, drawSearch), until his click or the search emptied. `words()`: the
 * chevron's hover, {show, hide}, in the page's language. `draw` at each status: its badges and what
 * needs the pilot now, every word known unless `known` is false (foldSeen).
 */
function foldSection(name, words) {
  const key = `osxp.live.fold.${name}`;
  const button = $(`${name}-fold`);
  const badges = $(`${name}-badges`);
  let needs = [];
  // a click of this visit, or the choice kept as foldSeen cut it (draw), as kept: without
  // static/kept.js, kept for the visit
  let chosen = null;
  let lent = false; // opened for the visit alone, not kept
  let searchOn = false; // a search typed in the settings
  const kept = () => chosen ?? foldKept(keptTools?.pageKept(key));
  const isOpen = () => lent || foldOpen(kept(), needs);
  const keep = (choice) => {
    chosen = choice;
    keptTools?.pageKeep(key, JSON.stringify(choice));
  };
  const show = () => {
    const open = isOpen();
    $(`${name}-body`).classList.toggle("is-folded", !open);
    legendTools?.setChevron(button, open, words());
  };
  $(`${name}-head`).addEventListener("click", (ev) => {
    if (!button.contains(ev.target) && (ev.detail > 1 || String(window.getSelection?.() || ""))) return;
    const open = !isOpen();
    lent = false;
    keep({ open, seen: needs });
    show();
  });
  show();
  return {
    show,
    open() {
      lent = true;
      show();
    },
    searched(on) {
      if (on === searchOn) return;
      searchOn = on;
      lent = on;
      show();
    },
    draw({ badges: said, needs: now, known = true }) {
      needs = now;
      const was = kept();
      const seen = known ? foldSeen(was, now) : was;
      if (seen !== was) keep(seen);
      const drawn = JSON.stringify(said);
      if (badges.dataset.drawn !== drawn) { // the badges, OrthoStudio XP's pills (drawPill)
        badges.dataset.drawn = drawn;
        badges.replaceChildren(...said.map(({ text, kind }) => {
          const pill = document.createElement("span");
          drawPill(pill, [text, kind]);
          return pill;
        }));
      }
      show();
    },
  };
}

// --- the mesh: OrthoStudio XP's regions -------------------------------------------------------

const REGION_ORDER = ["eur", "na", "sa", "afr", "asi", "aus_pac"];
let regionsDrawn = ""; // what the list shows: drawn again only when it changes, keeping the focus
let removeArmed = null; // the region whose Delete was clicked once: a second click deletes it
let disarmTimer = null;
const ARM_S = 5; // the armed Delete goes back to « Delete » after this, or at a click elsewhere

/** A region's Delete clicked once: its button asks « Sure? » for ARM_S seconds, or until a click
 * anywhere else (it had no way back but a reload, 2026-11-19). */
function armRemove(id) {
  removeArmed = id;
  clearTimeout(disarmTimer);
  disarmTimer = setTimeout(disarmRemove, ARM_S * 1000);
  if (last) draw(last);
}

function disarmRemove() {
  clearTimeout(disarmTimer);
  if (removeArmed === null) return;
  removeArmed = null;
  if (last) draw(last);
}

function regionButton(label, onClick, { danger = false, disabled = false, title = null } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = danger ? "btn btn-danger" : "btn";
  button.textContent = label;
  button.disabled = disabled;
  if (title) { // a sign alone (↑, ↓), or a word that needs its why: said on hover and read out
    button.title = title;
    button.setAttribute("aria-label", title);
  }
  button.addEventListener("click", onClick);
  return button;
}

/** The line under the regions saying why a request was refused; none: cleared. */
function regionsError(error) {
  $("regions-error").hidden = !error;
  $("regions-error").textContent = error ? error.message : "";
}

async function regionAct(path, method = "POST", body = undefined) {
  regionsError(null);
  try {
    const regions = await api(path, { method, body });
    if (last) draw({ ...last, regions });
    liveMap?.regionsChanged();
  } catch (error) {
    regionsError(error);
  }
}

/** An installed region's update waiting whole, the disk's word (r.waiting), unless a newer one is
 * listed to download. */
const updateReady = (r) => Boolean(r.waiting) && !((r.changed_bytes || 0) > 0);

/** An install that ended short: failed or stopped (osxpregions.py Installer's phases). */
const installStopped = (install) => ["failed", "cancelled"].includes(install?.phase);

/** An install under way, its bar running: not done, waiting whole for Live's next start, nor
 * stopped (installStopped). */
const installRunning = (install) =>
  Boolean(install) && !installStopped(install) && !["done", "waiting"].includes(install.phase);

/**
 * What the regions' title says beside it, folded or open, and what needs the pilot there, a word
 * each, which opens the list by itself (foldOpen): none installed, an update to download or one
 * ready for Live's next start, the last start that could not put it in place, an install running
 * or stopped, the list of regions not come. Every word known once the host's list came (`known`,
 * foldSection: its regions say what they download), the updates said by it alone. Its badges: how
 * many regions are installed, then how many updates, in the accent's colour, the warning's for
 * one held back; the first in the warning's colour where the list did not come or an install
 * failed. A region read as its row reads it (regionRow). {badges: [{text, kind}], needs, known}
 */
export function regionsSummary(regions) {
  const mine = new Set((regions?.ours || []).map((r) => r.id));
  const install = regions?.install;
  const needs = [];
  if (!mine.size) needs.push("none");
  if (regions?.error) needs.push("list");
  let updates = 0;
  let held = false;
  for (const r of regions?.list || []) {
    if (!mine.has(r.id)) continue;
    const ready = updateReady(r);
    if (ready || r.changed) {
      updates += 1;
      needs.push(`${ready ? "ready" : "update"}:${r.id}`);
    }
    if (ready && r.held) {
      held = true;
      needs.push(`held:${r.id}`);
    }
  }
  if (installRunning(install)) needs.push(`install:${install.region}`);
  else if (installStopped(install)) needs.push(`${install.phase}:${install.region}`);
  const n = mine.size;
  const installed = n === 0 ? t("regions.badge_none") : n === 1 ? t("regions.badge_one")
    : t("regions.badge_installed", { n: number(n) });
  const badges = [{ text: installed, kind: regions?.error || install?.phase === "failed" ? "warn" : null }];
  if (updates) {
    const text = updates === 1 ? t("regions.badge_update") : t("regions.badge_updates", { n: number(updates) });
    badges.push({ text, kind: held ? "warn" : "running" });
  }
  const known = (regions?.list || []).some((r) => r.download !== undefined);
  return { badges, needs, known };
}

/** What a region's row shows: its name and size, its state, its buttons, and the line under it
 * (the install's progress, or an update waiting whole). Plain data: the rows are drawn again only
 * when it changes, so that a button under the pointer is not replaced while it is clicked (the
 * bytes of a download change every second: they go to the line, never to the row's shape). */
function regionRow(id, mine, r, install, running, idle, xplane, folder) {
  const busy = Boolean(install) && install.region === id;
  const name = t(`region.${id}`);
  const ready = Boolean(mine) && updateReady(r);
  let size = "";
  let state = "";
  if (mine) {
    size = mine.size ? t("regions.size", { gb: gb(mine.size) }) : "";
    if (!(busy && running)) { // while it runs, its line says it all
      state = ready ? t("regions.ready")
        : r.changed ? t("regions.changed", { gb: gb(r.changed_bytes) })
        : t("regions.installed");
    }
  } else if (r.download) {
    size = t("regions.sizes", { download: gb(r.download), disk: gb(r.unpacked) });
  }
  const buttons = [];
  // its Delete armed: a short « Sure? » on the button, what it deletes on a line under the row (the
  // whole sentence on the button widened the buttons' column and moved every row, 2026-11-19): its
  // installed mesh, the kept old one taking its place where it keeps one, an update ready going
  // with the new mesh (osxpregions.py remove)
  const armed = mine && removeArmed === id;
  let sure = null;
  if (armed && r.kept) {
    sure = t("regions.confirm_remove_new", { of: t(`region_of.${id}`) });
    if (ready) sure += ` ${t("regions.confirm_remove_ready")}`;
  } else if (armed) {
    sure = t("regions.confirm_remove", { name });
  }
  if (busy && running) {
    buttons.push({ act: "cancel", label: t("regions.cancel") });
  } else if (mine) {
    if (r.changed) buttons.push({ act: "update", label: t("regions.update"), disabled: running || !idle || folder });
    buttons.push({ act: armed ? "remove" : "arm", danger: true, disabled: !idle || running || xplane,
      label: armed ? t("regions.remove_sure") : t("regions.remove") });
  } else if (r.download) {
    buttons.push({ act: "install", label: t("regions.install"), disabled: running || folder });
  }
  // the line: the install of this region while it runs, or why it ended short (failed, stopped),
  // or its update waiting whole. None once it is done, the row saying « Installed » (a line « used
  // from the next start » was read as « restart the app », 2026-11-18), nor while it waits: the
  // disk says that
  const line = busy && (running || installStopped(install)) ? { install: true }
    : ready ? { text: [t("regions.waiting"), ...(r.held ? [t("regions.held", { why: r.held })] : [])].join(" "),
      warn: Boolean(r.held) } : null;
  // its kept old mesh, under it: its size and its own Delete, which deletes it alone, by the rule
  // of every deletion (Live stopped, no install of it, X-Plane closed: control.py _deleting)
  const kept = mine && r.kept ? {
    text: r.kept.size ? t("regions.kept_size", { gb: gb(r.kept.size) }) : t("regions.kept"),
    button: { act: "remove_kept", label: t("regions.remove"), danger: true, disabled: !idle || running || xplane },
  } : null;
  // the button that goes on after a stop, which its line names
  const again = t(mine ? "regions.update" : "regions.install");
  return { id, size, state, buttons, line, bar: busy && running, kept, sure, again };
}

/** A region's question (the region-ask <dialog>, :func:`ask`): its title, its lines and its two
 * buttons, the first focused, the careful one; without ``yes``, one that only says. */
function askRegion({ title, lines, yes = null, no, danger = false }) {
  return ask($("region-ask"), () => {
    $("region-ask-title").textContent = title;
    paragraphs($("region-ask-text"), lines);
    $("region-ask-no").textContent = no;
    $("region-ask-yes").hidden = !yes;
    $("region-ask-yes").textContent = yes || "";
    $("region-ask-yes").className = danger ? "btn btn-danger-solid" : "btn";
  });
}

let roomAsked = false; // the room of an update being asked: one at a time

/**
 * Update. Where the update makes the region whole beside it (r.whole), the room it lacks, asked
 * at the click (GET regions/<id>/room, the install's own count: orthostudio/live/osxpregions.py
 * _room_for_update): short, one question says so and nothing starts but what it offers, deleting
 * the region's kept old mesh first, only where that makes every disk enough and X-Plane is closed
 * (it reads the region), never the region itself. Then whether to keep the one of before,
 * « Don't keep » the default: keeping it is a rename, which takes no room.
 */
async function updateRegion(id, button) {
  if (roomAsked) return;
  roomAsked = true;
  try {
    await updateAsked(id, button);
  } finally {
    roomAsked = false;
  }
}

async function updateAsked(id, button) {
  const regions = last?.regions || {};
  const r = (regions.list || []).find((x) => x.id === id) || {};
  const mine = (regions.ours || []).find((x) => x.id === id) || {};
  const of = t(`region_of.${id}`);
  let makeRoom = false;
  let keepOld = false;
  if (r.whole) {
    regionsError(null);
    const label = button?.textContent;
    // a stale stage is read whole: seconds, maybe more, which the button says; the count is
    // most often a few milliseconds, and the button's words changed and back flickered
    const slow = button ? setTimeout(() => {
      button.disabled = true;
      button.textContent = t("regions.room_asking");
    }, 300) : null;
    let room = null;
    try {
      room = (await api(`regions/${id}/room`)).room;
    } catch (error) {
      regionsError(error);
      return;
    } finally {
      clearTimeout(slow);
      if (button) { // the room known: the questions come next, the button as it was
        button.disabled = false;
        button.textContent = label;
      }
    }
    if (room) {
      const xplane = Boolean(last?.checks?.xplane_running);
      const gbFree = { gb: gb(room.free) };
      const sizes = { of, need: gb(room.need), folder: last?.checks?.folder || "",
        free: room.cleared ? t("regions.room_free_cleared", gbFree) : t("regions.room_free", gbFree) };
      const lines = [room.live ? t("regions.room_text_live", sizes) : t("regions.room_text", sizes)];
      if (!room.kept && room.shared) lines.push(t("regions.room_shared")); // its Delete frees nothing yet
      if (!room.kept) lines.push(t("regions.room_none"));
      else lines.push(t("regions.room_kept"), ...(xplane ? [t("regions.room_xplane")] : []));
      const offer = room.kept && !xplane;
      const go = await askRegion({ title: t("regions.room_title"), lines, yes: offer ? t("regions.room_yes") : null,
        no: offer ? t("regions.ask_cancel") : t("regions.ask_ok"), danger: true });
      if (go !== "yes") return;
      makeRoom = true;
    }
    const lines = [mine.size ? t("regions.keep_text_size", { gb: gb(mine.size) }) : t("regions.keep_text")];
    if (r.kept && !makeRoom) lines.push(t("regions.keep_replaces")); // deleted already otherwise
    if (last?.settings?.mesh === "old") lines.push(t("regions.keep_old_setting")); // kept, it is flown
    const answer = await askRegion({ title: t("regions.keep_title", { of }), lines,
      yes: t("regions.keep_yes"), no: t("regions.keep_no") });
    if (!answer) return; // closed: nothing done
    keepOld = answer === "yes";
  }
  await regionAct(`regions/${id}/install`, "POST", { keep_old: keepOld, make_room: makeRoom });
}

/** Delete on a region's kept old mesh, which deletes it alone: Live warns first that it cannot be
 * downloaded again (the host keeps no release of before). */
async function removeKept(id) {
  const go = await askRegion({ title: t("regions.kept_ask_title", { of: t(`region_of.${id}`) }), lines: [t("regions.kept_ask_text")],
    yes: t("regions.remove"), no: t("regions.kept_ask_no"), danger: true });
  if (go === "yes") await regionAct(`regions/${id}/kept`, "DELETE");
}

function regionAction(id, act) {
  if (act === "cancel") return () => regionAct("regions/cancel");
  if (act === "install") return () => regionAct(`regions/${id}/install`);
  if (act === "update") return (ev) => updateRegion(id, ev.currentTarget);
  if (act === "remove_kept") return () => removeKept(id);
  if (act === "remove") {
    return () => {
      disarmRemove();
      regionAct(`regions/${id}`, "DELETE");
    };
  }
  return () => armRemove(id); // "arm": a second click deletes
}

/** OrthoStudio XP's regions: each with what it downloads and takes on the disk, installed or not,
 * its zips changed since, and the install under way with its bar. */
function drawRegions(status) {
  const regions = status.regions || { ours: [], list: [], install: null };
  folds.regions?.draw(regionsSummary(regions));
  const ours = new Map((regions.ours || []).map((r) => [r.id, r]));
  const listed = new Map((regions.list || []).map((r) => [r.id, r]));
  const ids = REGION_ORDER.filter((id) => ours.has(id) || listed.has(id));
  const install = regions.install;
  const running = installRunning(install);
  const idle = status.state === "idle";
  $("regions-loading").hidden = listed.size > 0 || Boolean(regions.error);
  $("regions-error").hidden = !regions.error;
  if (regions.error) $("regions-error").textContent = t("regions.error", { error: regions.error });
  const xplane = Boolean(status.checks?.xplane_running); // deleting waits for it to quit: it reads the region
  const folder = Boolean(status.checks?.folder_problem); // Live's folder unfit: nothing to install into
  const rows = ids.map((id) => regionRow(id, ours.get(id), listed.get(id) || { id, installed: ours.has(id) },
    install, running, idle, xplane, folder));
  // where they are, folder by folder: one may sit on another disk, linked from Live's folder
  const byFolder = new Map();
  for (const r of ours.values()) {
    if (!r.folder) continue;
    if (!byFolder.has(r.folder)) byFolder.set(r.folder, []);
    byFolder.get(r.folder).push(t(`region.${r.id}`));
  }
  const places = [...byFolder].map(([folder, names]) => `${folder} (${names.join(", ")})`);
  // « Mesh: new / old », for every region at once, while one keeps its old mesh; as saved each
  // time it shows, Live setting it back to New once none keeps one (control.py _mesh_follows_kept)
  const keeps = rows.some((row) => row.kept);
  if (keeps && $("mesh-choice").hidden) {
    const mesh = status.settings?.mesh || "new";
    fillMesh(mesh);
    if (saved) saved = { ...saved, mesh };
  }
  $("mesh-choice").hidden = !keeps;
  const shape = JSON.stringify([language(), rows, places]);
  if (shape !== regionsDrawn) {
    regionsDrawn = shape;
    $("regions-ours").replaceChildren(...rows.map((row) => {
      const li = document.createElement("li");
      li.className = "region";
      li.dataset.region = row.id;
      const head = document.createElement("div");
      head.className = "region-head";
      const label = document.createElement("span");
      label.className = "region-label"; // the name, under it its size, then its state
      const name = document.createElement("span");
      name.className = "region-name";
      name.textContent = t(`region.${row.id}`);
      const size = document.createElement("span");
      size.className = "region-size";
      size.textContent = row.size;
      const state = document.createElement("span");
      state.className = "region-state";
      state.textContent = row.state;
      label.append(name, size, state);
      const actions = document.createElement("span");
      actions.className = "region-actions";
      actions.append(...row.buttons.map((b) => {
        const button = regionButton(b.label, regionAction(row.id, b.act), { danger: b.danger, disabled: b.disabled });
        if (b.act === "remove") button.dataset.armed = ""; // its click deletes, never disarms
        return button;
      }));
      head.append(label, actions);
      li.append(head);
      if (row.sure) {
        const sure = document.createElement("p");
        sure.className = "question-help";
        sure.textContent = row.sure;
        li.append(sure);
      }
      if (row.kept) {
        const kept = document.createElement("div");
        kept.className = "region-kept";
        const text = document.createElement("span");
        text.textContent = row.kept.text;
        const b = row.kept.button;
        const act = document.createElement("span");
        act.className = "region-actions";
        act.append(regionButton(b.label, regionAction(row.id, b.act), { danger: b.danger, disabled: b.disabled }));
        kept.append(text, act);
        li.append(kept);
      }
      if (row.line) {
        const line = document.createElement("p");
        line.className = "question-help region-progress";
        if (row.line.install) line.id = "region-progress";
        else line.textContent = row.line.text;
        line.classList.toggle("is-warn", Boolean(row.line.warn));
        li.append(line);
      }
      if (row.bar) {
        const bar = document.createElement("div");
        bar.className = "progress";
        const fill = document.createElement("span");
        fill.id = "region-bar";
        bar.append(fill);
        li.append(bar);
      }
      return li;
    }));
    $("regions-where").hidden = places.length === 0;
    $("regions-where").textContent = places.length ? t("regions.where", { places: places.join(" · ") }) : "";
  }
  // "none installed" once the list came (or could not): not while it is asked for
  $("regions-ours-none").hidden = ids.length > 0 || !$("regions-loading").hidden;
  // why the buttons are grey, by the rules of regionRow, install_region and _deleting
  const why = folder ? t("checks.folder_problem", { problem: status.checks.folder_problem })
    : ours.size && !idle ? t("regions.wait_live")
    : ours.size && xplane ? t("regions.wait_xplane") : "";
  if ($("regions-why").textContent !== why) $("regions-why").textContent = why;
  $("regions-why").hidden = !why;
  if (install && $("region-progress")) {
    const line = $("region-progress");
    line.classList.toggle("is-warn", install.phase === "failed");
    if (install.phase === "deleting") line.textContent = t("regions.deleting"); // room made first
    else if (install.phase === "clearing") line.textContent = t("regions.clearing"); // stale stages
    else if (running) {
      // the pace while files come; none once only unpacking is left
      const pace = install.mbs >= 0.05 ? install.mbs : 0;
      line.textContent = t(pace ? "regions.progress_pace" : "regions.progress", { done: gb(install.done_bytes),
        total: gb(install.total_bytes), mbs: number(pace, 1), unpacked: install.unpacked, zips: install.zips });
    } else {
      const button = rows.find((row) => row.id === install.region)?.again;
      line.textContent = install.phase === "failed" ? t("regions.failed", { error: install.error, button })
        : t("regions.cancelled", { button });
    }
    if ($("region-bar")) {
      const share = install.total_bytes ? install.done_bytes / install.total_bytes : 0;
      $("region-bar").style.width = `${Math.round(share * 1000) / 10}%`;
    }
  }
}

/** The « Soft banks » answer, with what it downloads for the regions installed: their river masks
 * they have not got (each region's rivers_bytes, orthostudio/live/control.py); its words alone once
 * they have them, kept whatever the answer. */
function drawRivers(status) {
  const bytes = (status.regions?.list || []).reduce((sum, r) => sum + (r.rivers_bytes || 0), 0);
  const text = bytes > 0 ? t("rivers.soft_note_download", { size: amount(bytes) }) : t("rivers.soft_note");
  if ($("rivers-soft-note").textContent !== text) $("rivers-soft-note").textContent = text;
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
  drawXplane(status);
  drawLogFile(status);
  drawRegions(status);
  drawRivers(status);
  liveMap?.update({ ...status, providers });
  if (status.decals) decalList = status.decals;
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
 * airports never came (16,384 px textures). Live was out with none other, so none other is shown. */
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

/** The list of combinations, ``current`` chosen. */
function renderZooms(current) {
  const select = $("set-zooms");
  const value = current || select.value || "16/18";
  select.replaceChildren(...ZOOM_CHOICES.map(([zoom, airports, key]) => {
    const option = document.createElement("option");
    option.value = `${zoom}/${airports}`;
    option.textContent = t(key);
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
  drawMemory();
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
    drawMemory();
    drawZooms();
  }
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
  drawQuality();
}

/** Under the zooms too: X-Plane's texture quality below Maximum (Live builds the zoom chosen
 *  whatever it is). At High X-Plane shows each texture one size smaller, measured 2026-11-13: the
 *  note says which ZL; below High it was not measured, so the note gives no number. */
function drawQuality() {
  const q = last?.checks?.xplane_quality;
  const note = $("zoom-quality");
  const below = Number.isInteger(q) && q < 4;
  note.hidden = !below;
  if (!below) return;
  const zoom = zoomsChosen()[0];
  note.textContent = t(q === 3 ? "zoom.quality_high" : "zoom.quality_low", { zl: zoom - 1, zoom });
}

// ---------------------------------------------------------------- the season

/** The line of Live's screen: the season the stream builds in, else what the next start takes. */
function seasonText(running, settings) {
  if (running?.season) return t("state.season", { season: t(`season.${running.season}`).toLocaleLowerCase() });
  const chosen = settings?.season || "auto";
  return chosen === "auto"
    ? t("state.season_auto")
    : t("state.season", { season: t(`season.${chosen}`).toLocaleLowerCase() });
}

let seasonLooks = null; // each season's colours as the sliders hold them, saved with the form
let seasonEdited = "autumn"; // the season whose sliders are shown
let seasonPlace = 0; // which of the places Live keeps the preview shows (GET look/sample)
let seasonPhoto = null; // its image, once loaded; false: Live keeps no photo yet
let seasonFrame = 0;

/** The season answers of the settings: the switch, the season chosen without it, the sliders. */
function fillSeason(settings) {
  const auto = (settings.season || "auto") === "auto";
  $("set-season-auto").checked = auto;
  $("set-season").value = auto ? "summer" : settings.season; // greyed while the date decides
  seasonLooks = structuredClone(settings.seasons || DEFAULT_LOOKS);
  if (!auto) seasonEdited = settings.season;
  drawSeason();
}

function seasonChosen() {
  return $("set-season-auto").checked ? "auto" : $("set-season").value;
}

/** The sliders of the season shown, and its preview. */
function drawSeason() {
  $("set-season").disabled = $("set-season-auto").checked;
  for (const tab of $("season-tabs").querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.season === seasonEdited));
  }
  const box = $("season-sliders");
  box.replaceChildren();
  const look = seasonLooks?.[seasonEdited] || DEFAULT_LOOKS[seasonEdited];
  for (const [key, min, max] of LOOK_VALUES) {
    const id = `season-${key}`;
    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", id, min: String(min), max: String(max), step: "0.01" });
    slider.value = String(look[key] ?? 0);
    const shown = document.createElement("output");
    shown.className = "colour-value";
    shown.htmlFor = id;
    const show = () => {
      const v = Math.round(Number(slider.value) * 100);
      shown.textContent = `${v > 0 ? "+" : ""}${v} %`;
    };
    show();
    slider.addEventListener("input", () => {
      seasonLooks[seasonEdited] = { ...seasonLooks[seasonEdited], [key]: Number(slider.value) };
      show();
      paintSeason(true);
    });
    const label = document.createElement("label");
    label.className = "sub-question-title";
    label.htmlFor = id;
    label.textContent = t(`season.v_${key}`);
    label.title = t(`season.v_${key}_help`);
    const row = document.createElement("div");
    row.className = "colour-row";
    row.append(label, slider, shown);
    box.append(row);
  }
  $("season-after-label").textContent = t(`season.${seasonEdited}`);
  paintSeason();
}

/** The preview: a photo Live keeps, as delivered and in the season shown; at the next frame while
 * a slider moves (``soon``), at once otherwise: a hidden page has no frames to wait for. */
function paintSeason(soon = false) {
  if (seasonPhoto === null) {
    loadSeasonPhoto();
    return;
  }
  const quiet = seasonPhoto === false;
  $("season-preview").hidden = quiet;
  $("season-place").hidden = quiet;
  $("season-preview-note").textContent = t(quiet ? "season.preview_none" : "season.preview_note");
  if (quiet || seasonPhoto === undefined) return; // none, or on its way
  cancelAnimationFrame(seasonFrame);
  const paint = () => {
    const side = $("season-before").width;
    for (const [id, look] of [["season-before", null], ["season-after", seasonLooks?.[seasonEdited]]]) {
      const ctx = $(id).getContext("2d", { willReadFrequently: true });
      ctx.drawImage(seasonPhoto, 0, 0, side, side);
      if (look) ctx.putImageData(lookImageData(ctx.getImageData(0, 0, side, side), look), 0, 0);
    }
  };
  if (soon) seasonFrame = requestAnimationFrame(paint);
  else paint();
}

function loadSeasonPhoto() {
  const image = new Image();
  seasonPhoto = undefined; // on its way
  image.addEventListener("load", () => {
    seasonPhoto = image;
    paintSeason();
  });
  image.addEventListener("error", () => {
    seasonPhoto = false;
    paintSeason();
  });
  image.src = `api/live/look/sample?index=${seasonPlace}`;
}

function bindSeason() {
  $("set-season-auto").addEventListener("change", drawSeason);
  $("set-season").addEventListener("change", () => {
    seasonEdited = $("set-season").value;
    drawSeason();
  });
  $("season-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    seasonEdited = tab.dataset.season;
    drawSeason();
  });
  $("season-default").addEventListener("click", () => {
    seasonLooks[seasonEdited] = { ...DEFAULT_LOOKS[seasonEdited] };
    drawSeason();
  });
  $("season-place").addEventListener("click", () => {
    seasonPlace += 1;
    loadSeasonPhoto();
  });
}

const LIVE_DECAL = "maquify_2_green_key.dcl"; // orthostudio.live.settings.LIVE_DECAL
let decalList = []; // OrthoStudio XP's decals, from the status (orthostudio.decals.DECALS)
let decalsDrawn = "";

/** The decals the select offers (the status's list, OrthoStudio XP's), none first, then chosen. */
function fillDecal(decals, chosen) {
  const select = $("set-decal");
  const shape = JSON.stringify([language(), decals]);
  if (shape !== decalsDrawn) {
    decalsDrawn = shape;
    select.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = t("decal.none");
    select.append(none);
    for (const file of decals) {
      const option = document.createElement("option");
      option.value = file;
      const name = file.replace(/\.dcl$/, "");
      option.textContent = file === LIVE_DECAL ? t("decal.default", { name }) : name;
      select.append(option);
    }
  }
  select.value = chosen ?? LIVE_DECAL;
}

function fillSettings(settings) {
  renderZooms(`${settings.max_zoom}/${settings.max_zoom_airports}`);
  fillDecal(decalList, settings.decal);
  $("set-disk").value = String(settings.disk_gb);
  $("set-folder").value = settings.folder || "";
  $("set-simbrief").value = settings.simbrief_user || "";
  for (const input of document.querySelectorAll('input[name="app_home"]')) {
    input.checked = input.value === (settings.app_home || "ask");
  }
  for (const input of document.querySelectorAll('input[name="overlays"]')) {
    input.checked = input.value === (settings.overlays || "auto");
  }
  for (const input of document.querySelectorAll('input[name="own_tiles"]')) {
    input.checked = input.value === (settings.own_tiles || "first");
  }
  for (const input of document.querySelectorAll('input[name="night"]')) {
    input.checked = input.value === nightChoice(settings);
  }
  for (const input of document.querySelectorAll('input[name="rivers_fade"]')) {
    input.checked = input.value === (settings.rivers_fade ? "soft" : "even");
  }
  fillMesh(settings.mesh);
  drawSimbrief();
  drawMemory();
  drawZooms();
  fillSeason(settings);
  if (providers.length) renderProviders(settings.provider);
}

/** The « Mesh » answer, new or old. */
function fillMesh(mesh) {
  for (const input of document.querySelectorAll('input[name="mesh"]')) input.checked = input.value === (mesh || "new");
}

/** The "Night lights" answer of settings saved: off, or how bright. */
function nightChoice(settings) {
  return settings?.night === false ? "off" : (settings?.night_light || "normal");
}

/** The "Night lights" answer the form holds. */
function nightChosen() {
  return document.querySelector('input[name="night"]:checked')?.value || nightChoice(saved);
}

/** The answers the form holds, as Save sends them. The source and its zooms are not among them:
 * Live's screen saves them as soon as they are chosen. */
function formSettings() {
  return {
    disk_gb: Number($("set-disk").value),
    folder: $("set-folder").value.trim(),
    simbrief_user: $("set-simbrief").value.trim(),
    app_home: document.querySelector('input[name="app_home"]:checked')?.value || saved?.app_home || "ask",
    overlays: document.querySelector('input[name="overlays"]:checked')?.value || saved?.overlays || "auto",
    own_tiles: document.querySelector('input[name="own_tiles"]:checked')?.value || saved?.own_tiles || "first",
    night: nightChosen() !== "off",
    night_light: nightChosen() === "off" ? (saved?.night_light || "normal") : nightChosen(),
    rivers_fade: (document.querySelector('input[name="rivers_fade"]:checked')?.value
      || (saved?.rivers_fade ? "soft" : "even")) === "soft",
    // only while its switch shows: hidden, Live's own value stays (control.py _mesh_follows_kept)
    ...($("mesh-choice").hidden ? {} : {
      mesh: document.querySelector('input[name="mesh"]:checked')?.value || saved?.mesh || "new",
    }),
    decal: $("set-decal").options.length ? $("set-decal").value : (saved?.decal ?? LIVE_DECAL),
    season: seasonChosen(),
    seasons: seasonLooks || saved?.seasons,
  };
}

/** Whether an answer was changed without Save. */
function settingsChanged() {
  if (!saved) return false;
  if ($("set-xplane").value.trim() !== namedXplane) return true;
  const now = formSettings();
  // the seasons' sliders are an object: compared as written
  const text = (value) => (typeof value === "object" ? JSON.stringify(value) : String(value ?? ""));
  return Object.keys(now).some((key) => text(now[key]) !== text(saved[key]));
}

/** Soft banks saved where Even was: the river masks of the regions on the listed mesh download at
 * once (orthostudio/live/control.py rivers_chosen), which the pilot accepts first, seeing their
 * size (each region's rivers_bytes; one still to update brings its own with its Update). False:
 * cancelled, nothing saved. */
async function riversAccepted(body) {
  if (!body.rivers_fade || saved?.rivers_fade) return true;
  const bytes = (last?.regions?.list || []).filter((r) => !r.whole)
    .reduce((sum, r) => sum + (r.rivers_bytes || 0), 0);
  if (bytes <= 0) return true;
  const size = amount(bytes);
  const answer = await askRegion({ title: t("rivers.ask_title", { size }), lines: [t("rivers.ask_text", { size })],
    yes: t("rivers.ask_yes"), no: t("regions.ask_cancel") });
  return answer === "yes";
}

async function saveSettings(event) {
  event.preventDefault();
  const body = formSettings();
  $("settings-error").hidden = true;
  $("settings-status").textContent = "";
  if (!(await riversAccepted(body))) return;
  try {
    const before = saved?.simbrief_user;
    await saveXplaneFolder();
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

async function chooseFolder(field, prompt, now) {
  const start = field.value.trim() || now || null;
  try {
    const got = await api("choose-folder", { method: "POST", body: { prompt, start } });
    if (got.path) field.value = got.path;
    return got.path;
  } catch (error) {
    $("settings-error").hidden = false;
    $("settings-error").textContent = error.message;
    return null;
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
  if (!textures && !imagery) return;
  await act("delete", { textures, imagery });
  // the map without them at once, not at the next change of tab (the user, 2026-11-01)
  liveMap?.builtChanged();
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

/**
 * A question in one of the page's modal <dialog>s, whose form closes it with the value of the
 * button clicked: that value, or "" when closed with Escape, nothing done then; one at a time ("" while
 * one is open). ``fill`` writes its words first. The one way of the quit's, the build's and the
 * regions' questions.
 */
function ask(dialog, fill) {
  if (dialog.open) return Promise.resolve("");
  fill?.();
  dialog.returnValue = "";
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close();
  };
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue), { once: true });
    dialog.showModal();
  });
}

/** Lines into a dialog's text, a paragraph each. */
function paragraphs(box, lines) {
  box.replaceChildren(...lines.map((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    return p;
  }));
}

/** OrthoStudio XP's question before quitting (app.js confirmQuit), with Live's lines. */
async function confirmQuit(lines) {
  return (await ask($("quit-confirm"), () => paragraphs($("quit-confirm-text"), lines))) === "quit";
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
  return (await ask($("build-running"))) === "go";
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
    const prepared = await api("prepare", { method: "POST", body: { departure } });
    liveMap?.showDeparture(prepared.at); // the map on it: the pilot sees where Live builds
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

async function startMap() {
  if (typeof L === "undefined") return; // Leaflet did not load: the rest of the page works
  // what the page keeps from one visit to the next (static/kept.js); without it, the map still
  // shows, its bar too
  await loadShared();
  try {
    // loaded here, not with the page: the map's modules read OrthoStudio XP's own (static/geo.js)
    const { createLiveMap } = await import("./livemap.js");
    liveMap = createLiveMap($("live-map"), {
      api, info: $("map-info"), legend: $("map-legend"), onOther: toggleFront,
    });
  } catch (_e) {
    liveMap = null;
    return;
  }
  $("map-follow").addEventListener("change", (event) => liveMap.setFollow(event.target.checked));
  // the bar over the map: shown unless hidden once, kept by the page's server (static/kept.js)
  const details = keptTools?.pageKept(MAP_DETAILS_KEY) !== "0";
  $("map-details").checked = details;
  liveMap.setDetails(details);
  $("map-details").addEventListener("change", (event) => {
    liveMap.setDetails(event.target.checked);
    keptTools?.pageKeep(MAP_DETAILS_KEY, event.target.checked ? "1" : "0");
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
let updateBanner = null; // the new version's banner (static/update.js), once loaded
let textSizeTools = null; // its text size (static/textsize.js), once loaded
let findTools = null; // its find bar (static/find.js), once loaded
let zoomRefresh = null; // writes the zoom's level again in the page's language, once bound
let textSizeRefresh = null; // writes the text size's choices again, once bound
let h = null; // its one way to make an element (static/dom.js), once loaded (bindDom)

/**
 * OrthoStudio XP's one way to make an element (static/dom.js), which the folders of other tools'
 * meshes are drawn with, loaded at the page's start on its own: another of OrthoStudio XP's modules
 * failing to load (bindPageTools) never hides that list and its buttons. Drawn at the next status.
 */
async function bindDom() {
  try {
    ({ h } = await import("../static/dom.js"));
  } catch (_e) {
    // not there: the list waits for it
  }
}

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
    const [words, find, zoom, textSize, embed, update] = await Promise.all([
      import("../static/i18n.js"),
      import("../static/find.js"),
      import("../static/zoom.js"),
      import("../static/textsize.js"),
      import("../static/embed.js"),
      import("../static/update.js"),
    ]);
    osxpWords = words;
    findTools = find;
    textSizeTools = textSize;
    osxpWords.setLanguage(language());
    textSize.applyTextSize(textSize.savedTextSize());
    // the new version's banner, as on Build's page: its words are OrthoStudio XP's
    updateBanner = update.bindUpdate({
      request: osxpRequest,
      place: $("main"),
      resized: measureMapTop,
      failed: (err) => toast(err.message),
    });
    updateBanner.load(); // not awaited: a slow GitHub must never hold the page back
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
      shown: () => {
        if (shownScreen === "settings") readXplaneFolder();
      },
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
  for (const fold of Object.values(folds)) fold.searched(on); // its words found, shown
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
  if (name === "settings") readXplaneFolder();
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
  folds[question]?.open(); // the regions' list, folded or not, open for the visit
  box.scrollIntoView({ block: "start" });
  box.classList.add("is-pointed");
  setTimeout(() => box.classList.remove("is-pointed"), 2000);
}

function tellLanguage() {
  // the plugin's words in X-Plane follow the window's language
  api("language", { method: "POST", body: { language: language() } }).catch(() => {});
}

async function askRegions() {
  // the host's list of OrthoStudio XP's regions, read by the engine in the background
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
  updateBanner?.render();
  tellLanguage();
  liveMap?.languageChanged();
  applyStatic(document);
  showFolds(); // their chevrons, which applyStatic named « show » again
  measureMapTop(); // the bar above may wrap otherwise in the new language
  zoomRefresh?.();
  textSizeRefresh?.();
  renderZooms();
  renderProviders($("set-provider").value);
  if ($("set-decal").options.length) fillDecal(decalList, $("set-decal").value); // its none, Live's
  if (seasonLooks) drawSeason();
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
  // seen before the button's own click: a click on the armed Delete deletes, any other disarms it
  document.addEventListener("click", (ev) => {
    if (removeArmed !== null && !ev.target.closest?.("[data-armed]")) disarmRemove();
  }, true);
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  applyStatic(document);
  folds.regions = foldSection("regions", () => ({ show: t("regions.fold_show"), hide: t("regions.fold_hide") }));
  folds.others = foldSection("others", () => ({ show: t("other_mesh.fold_show"), hide: t("other_mesh.fold_hide") }));
  loadShared().then(showFolds); // as the viewer left them
  renderZooms();
  drawMemory();
  trackStatusbarHeight();
  measureMapTop();
  window.addEventListener("resize", measureMapTop); // the frame sends one when it shows the page
  $("lang-select").addEventListener("change", (event) => {
    setLanguage(event.target.value, true);
    languageChanged();
  });
  $("quit-btn").addEventListener("click", quitLive);
  $("start-btn").addEventListener("click", async () => {
    if (await askedWhileBuilding()) act("start");
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
  $("set-provider").addEventListener("change", chooseProvider);
  $("set-simbrief").addEventListener("input", drawSimbrief);
  $("simbrief-osxp").addEventListener("click", () => {
    $("set-simbrief").value = last?.checks?.osxp_simbrief || "";
    drawSimbrief();
  });
  $("folder-choose").addEventListener("click", () => chooseFolder($("set-folder"), t("folder.prompt"), last?.checks.folder));
  // a folder of another tool's mesh added at the bottom of the list, typed then Enter (not the
  // form's Save), or chosen in the system's dialog
  $("set-other-mesh").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addOtherMesh($("set-other-mesh").value);
  });
  $("other-mesh-choose").addEventListener("click", async () => {
    const folders = last?.checks.other_meshes?.folders || [];
    const chosen = await chooseFolder($("set-other-mesh"), t("other_mesh.prompt"), folders[folders.length - 1]?.folder);
    if (chosen) addOtherMesh(chosen);
  });
  $("xplane-choose").addEventListener("click", () => chooseFolder($("set-xplane"), t("xplane.prompt"), last?.checks.xplane));
  $("delete-btn").addEventListener("click", openDelete);
  $("delete-here").addEventListener("click", openDelete);
  $("delete-dialog").addEventListener("close", deleteClosed);
  $("settings-form").addEventListener("submit", saveSettings);
  bindSeason();
  $("settings-undo").addEventListener("click", () => {
    if (saved) fillSettings(saved);
    $("set-xplane").value = namedXplane;
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
  bindDom();
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
