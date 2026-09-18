// OrthoStudio XP web page — docs/specs/ui.md. Native ES module: state, API client (real or mock),
// rendering functions, SSE handling. No framework, no dependency, no external URL.

import {
  applyStatic,
  codeText,
  detectLanguage,
  fmtBytes,
  fmtKB,
  fmtDate,
  fmtDuration,
  fmtGB,
  fmtInt,
  fmtMB,
  fmtMbps,
  fmtNum,
  fmtRange,
  language,
  setLanguage,
  t,
  tOpt,
} from "./i18n.js";
import { TEXTURE_MB, ZONES_FORMAT, normalizeZone, tileName, validateZonesDocument, zoneTextureKeys } from "./geo.js";
import { createPlanMap, detailLabel } from "./map.js";
import { defaultsKeepingFolders, renderSettingsView, sameValue, settingsSummary } from "./settings.js";
import { countryName, sourceAddressProblem, sourceGroups, sourceGroupTitle, sourceLabel, tilesNotCovered } from "./sources.js";

// ------------------------------------------------------------------ constants

// `location` is read only in a browser: node imports this module in the tests.
const PARAMS = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
const MOCK = PARAMS.get("mock") === "1";
/**
 * `?mock=1&fail=...` shows an error path of the mock: `zones` refuses every PUT /api/zones;
 * `zone-conflict` changes the zones behind the page's back before its first save (409);
 * `zone-problem` answers GET /api/zones with a zone the engine flags and an entry it cannot
 * read; `disk` gives an estimate with less than twice the space needed free; `xplane` says that
 * X-Plane is running (the Library refuses to add, remove or delete a tile); `busy` refuses a
 * delete as if a build were running; `clean` deletes a tile but cannot free its cache space (the
 * delete's `warning`); `no-xplane` knows no X-Plane 12 folder until one is saved in Settings
 * (Estimate and Build are refused with `XP_DIR_NOT_FOUND`, as on a computer without X-Plane);
 * `no-data-disk` keeps the data on a disk that is not plugged in until Settings save another
 * folder (Estimate and Build are refused with `CFG_DATA_DIR_MISSING`). Saving a data folder whose
 * name says exFAT or FAT32 is refused, as on such a disk.
 */
const MOCK_FAIL = PARAMS.get("fail");
/** `?mock=1&speed=10` runs the mock builds ten times faster (the tests use it); 1 by default. */
const MOCK_SPEED = Math.min(100, Math.max(0.1, Number(PARAMS.get("speed")) || 1));
const STATIC = "static/";
/** Browsers refuse a keepalive request whose body passes 64 KiB: below this, a save may use it. */
export const KEEPALIVE_MAX_BYTES = 60000;

export const STEPS = ["data", "terrain", "coast", "imagery", "assembly", "install"];

/** Node role → user step: the engine's `ROLE_STAGE` (orthostudio.api.stages; a test keeps the two equal).
 * The journal gives each node event its `role` and `stage`; the report's nodes carry `role`. */
export const ROLE_STEP = {
  osm: "data",
  coastline: "data",
  dem: "data",
  vectors: "data",
  mesh: "terrain",
  masks: "coast",
  textures: "imagery",
  xp12: "assembly",
  dsf: "assembly",
  overlay: "assembly",
  pack: "assembly",
  install: "install",
};

/** Rule name → user step, for the node ids of the P2b contract (`+43+005/tile.dsf#2`) that older
 * mock files and journals still carry. */
export const NODE_STEP = {
  "tile.textures": "imagery",
  "xp12.rasters": "assembly",
  "tile.dsf": "assembly",
  "tile.overlay": "assembly",
  "tile.pack": "assembly",
  "tile.install": "install",
};

const TILE_RE = /^[+-]\d{2}[+-]\d{3}$/;
const STEP_KEYS = {
  data: () => t("step.data"),
  terrain: () => t("step.terrain"),
  coast: () => t("step.coast"),
  imagery: () => t("step.imagery"),
  assembly: () => t("step.assembly"),
  install: () => t("step.install"),
};
const STATE_KEYS = {
  queued: () => t("state.pending"),
  pending: () => t("state.pending"),
  running: () => t("state.running"),
  done: () => t("state.done"),
  built: () => t("state.done"),
  hit: () => t("state.hit"),
  failed: () => t("state.failed"),
  skipped: () => t("state.skipped"),
  cancelled: () => t("state.cancelled"),
  waiting: () => t("state.waiting"),
  stopped: () => t("state.stopped"),
  not_started: () => t("state.not_started"),
  finished: () => t("state.finished"),
};
const SEV_KEYS = {
  blocking: () => t("sev.blocking"),
  degraded: () => t("sev.degraded"),
  info: () => t("sev.info"),
};
// What the engine actually reports today, in the order the panel shows it. The vector-stage
// decisions (lakes treated as sea, runways rejected, airports dropped) come back when the
// vectors are OrthoStudio XP's own in P4; showing them as permanent zeros would be a lie.
const DECISION_KEYS = {
  textures_total: () => t("works.dec_textures"),
  textures_built: () => t("works.dec_built"),
  textures_hits: () => t("works.dec_cached"),
  IMG_TILE_PARENT_FALLBACK: () => t("works.dec_parent_fallback"),
  IMG_TILE_PLACEHOLDER: () => t("works.dec_placeholder"),
  TEX_MISSING: () => t("works.dec_tex_missing"),
};
/** Codes the report lists only when they happened, in plain words (the code in the tooltip). */
const EXTRA_DECISION_KEYS = {
  SYS_UPSTREAM_FAILED: () => t("works.dec_upstream"),
  textures_second_pass: () => t("works.dec_second_pass"),
  textures_recovered: () => t("works.dec_recovered"),
};

// ------------------------------------------------------------------ state

const state = {
  screen: "plan",
  status: null,
  providers: [],
  settings: null,
  schema: null,
  settingsDraft: null,
  tiles: [],
  airport: null,
  plan: null,
  /** Step 3's error: `{message}` (the page's own sentence) or `{err}` (an engine answer). */
  planError: null,
  jobs: [],
  jobId: null,
  job: null,
  log: [],
  source: null,
  library: [],
  busy: false,
  jobsClearing: false,
};

/** The map, the zones and the drawing tools of the Plan (map.js); created by boot() so that the
 * zones load with the rest. The tile selection stays in `state.tiles`. */
let planMap = null;

// ------------------------------------------------------------------ DOM helpers

const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

function pill(text, kind) {
  return h("span", { class: `pill pill-${kind}` }, text);
}

let toastTimer = 0;
function toast(message, kind = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast${kind === "fail" ? " toast-fail" : ""}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 4000);
}

// ------------------------------------------------------------------ API client

class ApiError extends Error {
  constructor(status, detail) {
    super(`HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/** The error object of a failed call: the engine wraps it as {"error": {code, message, remedy,
 * context, ...}} (api/app.py), the mock and FastAPI's own errors do not. */
function errorDetail(err) {
  if (!(err instanceof ApiError)) return null;
  const d = err.detail;
  if (d && typeof d === "object" && d.error && typeof d.error === "object") return d.error;
  return d && typeof d === "object" ? d : null;
}

/**
 * [message, remedy] of an engine error: the page's words for the codes it knows, except for the
 * ZONE_* errors, whose message names the zone and says what is wrong with it (the page's generic
 * line would point at a zone the list may not show). Unknown codes keep the engine's words.
 */
export function codeWords(err) {
  const e = err && typeof err === "object" ? err : {};
  const own = [e.message || "", e.remedy || ""];
  if (String(e.code || "").startsWith("ZONE_") && e.message) return own;
  return codeText(e.code, e.context) || own;
}

function errorMessage(err) {
  if (err instanceof ApiError) {
    const d = errorDetail(err);
    if (d) {
      if (d.code) return `${d.code}: ${codeWords(d)[0]}`;
      if (typeof d.detail === "string") return d.detail;
      if (Array.isArray(d.detail)) {
        return d.detail.map((x) => `${(x.loc || []).join(".")}: ${x.msg}`).join("; ");
      }
    }
    if (err.status === 409) return t("err.conflict");
    return t("app.error_network", { status: err.status });
  }
  return err && err.message ? err.message : t("err.unknown");
}

/** True when a body of UTF-8 text stays under KEEPALIVE_MAX_BYTES (checked cheaply first). */
export function keepaliveAllowed(text) {
  if (typeof text !== "string" || text.length >= KEEPALIVE_MAX_BYTES) return false;
  return text.length * 3 < KEEPALIVE_MAX_BYTES || new TextEncoder().encode(text).byteLength < KEEPALIVE_MAX_BYTES;
}

/** options: `headers` (added to the request), `keepalive` (asked for; granted to small bodies only,
 * since a larger one would make the browser refuse the request outright). */
async function api(method, path, body, options = {}) {
  if (MOCK) return mockApi(method, path, body, options);
  const init = { method, headers: { Accept: "application/json", ...(options.headers || {}) } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
    if (options.keepalive && keepaliveAllowed(init.body)) init.keepalive = true;
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = null;
    try {
      detail = await res.json();
    } catch (_e) {
      detail = null;
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

// ------------------------------------------------------------------ mock API (?mock=1)

const mock = { cache: new Map(), jobs: new Map(), nextId: 1, library: null, zones: null, revisions: 0, conflicted: false, pastCleared: false, settingsSaved: false };

/** `fail=no-xplane`: the X-Plane 12 folder the mock knows, none until Settings saved one. */
function mockXplaneDir() {
  return mock.settingsSaved ? mock.cache.get("settings")?.essential?.xplane_dir || null : null;
}

/** `fail=no-data-disk`: the data folder saved before, on a disk that is not plugged in. */
const MOCK_AWAY_DISK = "/Volumes/SSD/OrthoStudio";

/** The data folder the mock's settings hold: null for OrthoStudio XP's own folder. */
function mockDataDir() {
  if (mock.settingsSaved) return mock.cache.get("settings")?.essential?.data_dir || null;
  return MOCK_FAIL === "no-data-disk" ? MOCK_AWAY_DISK : null;
}

/** Like the engine: nothing is estimated or built while the data folder's disk is unplugged. */
function mockCheckDataDisk() {
  const dir = mockDataDir();
  if (MOCK_FAIL !== "no-data-disk" || dir !== MOCK_AWAY_DISK) return;
  throw new ApiError(422, { error: { code: "CFG_DATA_DIR_MISSING", severity: "blocking", action: "stop", message: `The data folder ${dir} was not found.`, remedy: "Plug in the disk it is on, or choose another folder in Settings.", context: { path: dir } } });
}

/** Like the engine (api/specs.py make_specs): nothing is estimated or built without X-Plane 12. */
function mockCheckXplane() {
  if (MOCK_FAIL !== "no-xplane" || mockXplaneDir()) return;
  throw new ApiError(422, { error: { code: "XP_DIR_NOT_FOUND", severity: "blocking", action: "settings", message: "X-Plane 12 was not found on this computer. OrthoStudio XP takes the relief, roads, forests and buildings from it, and adds the tiles to it.", remedy: "Choose the X-Plane 12 folder in Settings.", context: { path: "<not detected>" } } });
}

/** Textures the zones add to the selected tiles, counted like the page's sizes (map.js). */
function mockZoneTextures(zones, tiles, provider, zl) {
  let n = 0;
  for (const tile of tiles) {
    const keys = new Set();
    for (const z of zones || []) {
      const source = z.provider || provider;
      if (z.zl === zl && source === provider) continue;
      for (const k of zoneTextureKeys(z.polygon, z.zl, tile)) keys.add(`${source}|${z.zl}|${k}`);
    }
    n += keys.size;
  }
  return n;
}

async function mockFile(name) {
  if (!mock.cache.has(name)) {
    const res = await fetch(`${STATIC}mock/${name}.json`);
    if (!res.ok) throw new ApiError(res.status, null);
    mock.cache.set(name, await res.json());
  }
  return structuredClone(mock.cache.get(name));
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A new revision of the mock's zones document (opaque to the page, 64 hex digits like a sha256). */
function mockRevision() {
  mock.revisions += 1;
  return mock.revisions.toString(16).padStart(64, "0");
}

function headerValue(headers, name) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers || {})) if (key.toLowerCase() === wanted) return value;
  return null;
}

/** The engine's ZONE_INVALID answer (422) for a problem of geo.js validateZonesDocument. */
function mockZoneInvalid(problem) {
  return new ApiError(422, { error: { code: "ZONE_INVALID", severity: "blocking", message: `Zone ${problem.zone} is invalid: ${problem.reason}.`, remedy: "Fix or delete the zone on the map (or in the zones file); the whole zones document is refused until every zone is valid.", context: { zone: problem.zone, reason: problem.reason } } });
}

/** The zones of a plan or job request, refused like the engine refuses them (422 ZONE_INVALID). */
async function mockCheckZones(zones) {
  if (zones == null) return;
  const problem = validateZonesDocument({ format: ZONES_FORMAT, zones }, await mockFile("providers"));
  if (problem) throw mockZoneInvalid(problem);
}

/** GET /api/zones of the mock: mock/zones.json; with `fail=zone-problem`, plus a zone the engine
 * flags (it stays in the list) and an entry it could not read (left out, `zone: null`). */
async function mockZonesDocument() {
  const doc = await mockFile("zones");
  if (MOCK_FAIL !== "zone-problem") return doc;
  const zone = { id: "berre-19", name: "Étang de Berre", zl: 19, provider: "USGS", polygon: [[5.05, 43.45], [5.2, 43.45], [5.2, 43.55], [5.05, 43.55]] };
  const { reason } = validateZonesDocument({ format: ZONES_FORMAT, zones: [zone] }, await mockFile("providers")) || { reason: "zl 19 is above the maximum of USGS (18)" };
  doc.zones.push(zone);
  const unreadable = doc.zones.length;
  doc.problems = [
    { zone: zone.id, index: unreadable - 1, code: "ZONE_INVALID", reason, message: `Zone ${zone.id} is invalid: ${reason}.` },
    { zone: null, index: unreadable, code: "ZONE_INVALID", reason: `entry ${unreadable} is not a JSON object`, message: `Zone entry ${unreadable} of zones.json could not be read: it is not a JSON object.` },
  ];
  return doc;
}

/** The engine's error envelope ({"error": {...}}), as the mock answers it. */
/** The engine's refusal of an address that cannot be a source (`CFG_VALUE_INVALID`). */
function mockCheckSourceAddress(url) {
  const problem = sourceAddressProblem(url);
  if (problem) throw mockError(422, "CFG_VALUE_INVALID", problem === "scheme" ? "The address of the source does not start with https or http." : "The address of the source does not say where the tile's numbers go.", "Give the address of one tile with {x}, {y} and {zoom} (or {quadkey}) in place of its numbers.");
}

/** The engine's refusal of a source that does not cover every tile (`CFG_PROVIDER_OUT_OF_COVERAGE`). */
async function mockCheckCoverage(body) {
  const code = body?.provider;
  const p = [...(await mockFile("providers")), ...(mock.sources || [])].find((x) => x.code === code);
  const missing = tilesNotCovered(p, body?.tiles || []);
  if (!missing.length) return;
  throw new ApiError(422, { error: { code: "CFG_PROVIDER_OUT_OF_COVERAGE", severity: "blocking", action: "stop", message: `Imagery source ${code} covers ${p.extent} only, not tile(s) ${missing.join(" ")}.`, remedy: "Choose a source that covers these tiles: Bing Maps and Esri cover the whole world.", context: { provider: code, extent: p.extent, tiles: missing.join(" ") } } });
}

function mockError(status, code, message, remedy) {
  return new ApiError(status, { error: { code, severity: "blocking", action: "stop", message, remedy, context: {} } });
}

/** X-Plane runs in the mock when mock/status.json says so, or with `fail=xplane`. */
function mockXplaneRunning() {
  return MOCK_FAIL === "xplane" || Boolean(mock.cache.get("status")?.xplane?.running);
}

/** The mock's library in the engine's order: latitude, longitude, kind, path. */
function mockSortLibrary() {
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const key = (e) => {
    const m = String(e.tile).match(/^([+-]\d{2})([+-]\d{3})$/);
    return m ? [Number(m[1]), Number(m[2])] : [0, 0];
  };
  mock.library.sort((a, b) => {
    const [la, oa] = key(a);
    const [lb, ob] = key(b);
    return la - lb || oa - ob || cmp(String(a.kind), String(b.kind)) || cmp(String(a.path), String(b.path));
  });
}

/** The tile pack a library route acts on, found like the engine's `_find_pack` (a tile, or a pack
 * name: `zOrthoStudio_`, or `zOrtho4XP_` for an imported tile; the newest row first), narrowed by
 * `path` when the request gives one. */
const PACK_NAME_RE = /^(zOrthoStudio_|zOrtho4XP_)/;
function mockLibraryRow(name, path) {
  const tile = name.replace(PACK_NAME_RE, "");
  if (!TILE_RE.test(tile)) {
    throw mockError(422, "CFG_LATLON_INVALID", `'${name}' is neither a tile nor a tile pack name.`, "Use +43+005 or zOrthoStudio_+43+005.");
  }
  const rows = libraryTiles(mock.library)
    .filter((e) => e.tile === tile && (!PACK_NAME_RE.test(name) || e.name === name) && (path == null || e.path === path))
    .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  if (!rows.length) {
    throw mockError(422, "SYS_WORKING_DIR_INVALID", `No pack of ${tile} in the library.`, "Build the tile first, or import an Ortho4XP folder.");
  }
  return rows[0];
}

/** The shared overlays pack stays in X-Plane while one OrthoStudio XP tile is: its rows follow. */
function mockSyncOverlays() {
  const inXplane = mock.library.some((e) => (e.kind == null || e.kind === "ortho") && e.built_by === "osxp" && e.installed);
  for (const e of mock.library) if (e.kind === "overlay" && e.built_by === "osxp") e.installed = inXplane;
}

// ------------------------------------------------------------------ mock jobs (?mock=1)
//
// A mock job runs like the engine's: from POST /api/jobs, whether a page listens or not. Its
// journal gets the entries of mock/job_events.json (the engine's flat shape, `seq` and `ts` added
// when written) and a `stats` entry every second; GET /api/jobs/{id} answers the engine's state
// (`tiles[].stages[stage].nodes`); the event stream replays the journal, then follows it.

function round(x, digits) {
  const k = 10 ** digits;
  return Math.round(x * k) / k;
}

function mockJobId(n) {
  const d = new Date();
  const two = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}-${(0xa000 + n).toString(16)}`;
}

/** The nodes of a tile: those of the one tile of mock/job.json, their ids renamed. */
function mockTileNodes(template, name, level, install) {
  const src = template.tiles[0];
  const srcLevel = `/${template.provider}${template.zl}/`;
  const nodes = [];
  for (const stage of STEPS) {
    for (const n of src.stages[stage]?.nodes || []) {
      if (n.role === "install" && !install) continue;
      nodes.push({ node: n.node.replace(src.tile, name).replace(srcLevel, `/${level}/`), role: n.role, stage, status: "pending", key: null, hit: null, wall_s: 0, fraction: 0, error: null, cause: null, weight_s: n.weight_s ?? 1, started: false });
    }
  }
  return nodes;
}

/** The engine's `Job._handle` for one journal entry: the node's status, key, time and fraction. */
function mockEngineApply(doc, entry) {
  if (entry.event === "stats") {
    doc.stats = entry.stats;
    return;
  }
  if (!["started", "progress", "done", "failed"].includes(entry.event) || !entry.node) return;
  const tile = doc.tiles.find((x) => x.tile === entry.tile);
  if (!tile) return;
  let n = tile.nodes.find((x) => x.node === entry.node);
  if (!n) {
    n = { node: entry.node, role: entry.role, stage: entry.stage, status: "pending", key: null, hit: null, wall_s: 0, fraction: 0, error: null, cause: null, weight_s: entry.weight_s ?? 1, started: false };
    tile.nodes.push(n);
  }
  if (entry.event === "started") {
    n.status = "running";
    n.key = entry.key;
    n.started = true;
  } else if (entry.event === "progress") {
    n.fraction = entry.fraction;
  } else if (entry.event === "done") {
    Object.assign(n, { status: entry.hit ? "hit" : "done", key: entry.key, hit: entry.hit, wall_s: entry.wall_s, fraction: 1 });
  } else {
    const cancelled = entry.error?.code === "SYS_CANCELLED";
    n.status = entry.skipped ? "skipped" : cancelled ? "cancelled" : "failed";
    n.error = cancelled && !entry.skipped ? null : entry.error;
    n.cause = entry.cause;
  }
}

function mockTileStatus(tile, jobStatus) {
  if (tile.nodes.some((n) => n.status === "failed")) return "failed";
  const target = tile.nodes.filter((n) => n.role === (tile.install ? "install" : "pack"));
  if (target.length && target.every((n) => n.status === "done" || n.status === "hit")) return "done";
  if (jobStatus === "cancelled") return "cancelled";
  if (jobStatus === "done" || jobStatus === "failed") return tile.nodes.some((n) => n.status === "skipped") ? "failed" : jobStatus;
  return tile.nodes.some((n) => n.status !== "pending") ? "running" : "pending";
}

/** A mock node's weight in progress, as the engine's `progress.weight_of`: nothing for a hit, or
 * a node skipped or cancelled before it started. */
function mockWeight(n) {
  if (n.status === "hit" || ((n.status === "skipped" || n.status === "cancelled") && !n.started)) return 0;
  return Math.max(0, n.weight_s);
}

/** The node as the engine's journal and state show it (`weight_s` included). */
function mockNodeView(n) {
  return { node: n.node, role: n.role, status: n.status, key: n.key, hit: n.hit, wall_s: n.wall_s, fraction: n.fraction, weight_s: round(mockWeight(n), 3) };
}

/** GET /api/jobs/{id} of a mock job: the engine's `Job.state()`. */
function mockJobState(run) {
  const doc = run.doc;
  const tiles = doc.tiles.map((tile) => {
    const stages = {};
    for (const stage of STEPS) {
      const nodes = tile.nodes.filter((n) => n.stage === stage);
      if (!nodes.length) {
        stages[stage] = { status: stage === "install" && !tile.install ? "skipped" : "pending", fraction: 0, wall_s: 0, nodes: [] };
        continue;
      }
      const views = nodes.map(mockNodeView);
      // The page's own rules stand for the engine's here (they are tested against the engine).
      const totals = stepTotals({ nodes: Object.fromEntries(views.map((n) => [n.node, { ...n, weight: n.weight_s }])) });
      let status = totals.status;
      // Once the job is over nothing runs or waits (the engine's `stage_status` with the job's).
      if (!jobActive(doc) && (status === "running" || status === "waiting")) status = doc.status === "cancelled" ? "cancelled" : "skipped";
      stages[stage] = { status, fraction: round(totals.fraction, 4), wall_s: round(totals.wall_s, 3), nodes: views };
    }
    const errors = tile.nodes.filter((n) => n.error && !n.cause).map((n) => ({ ...n.error, node: n.node, stage: n.stage, tile: tile.tile }));
    return { tile: tile.tile, provider: tile.provider, zl: tile.zl, status: mockTileStatus(tile, doc.status), stages, errors };
  });
  const active = jobActive(doc);
  return structuredClone({
    ...mockJobSummary(run),
    request: doc.request,
    tiles,
    errors: tiles.flatMap((x) => x.errors),
    stats: doc.stats,
    eta: active && doc.stats ? { low_s: doc.stats.eta_low_s, high_s: doc.stats.eta_high_s } : null,
    last_seq: doc.last_seq,
    report: doc.report,
    decisions: doc.decisions,
  });
}

/** GET /api/jobs of a mock job: the engine's `Job.summary()`. */
function mockJobSummary(run) {
  const d = run.doc;
  return { id: d.id, status: d.status, created_at: d.created_at, started_at: d.started_at, finished_at: d.finished_at, install: d.install, tiles: d.tiles.map((x) => x.tile), provider: d.provider, zl: d.zl, ok: d.status === "done" };
}

/** The job's stats: counts, the whole job's elapsed time and progress (never decreasing), a range
 * of the time left once there is something to go by, and the phase (map data first). */
function mockStats(run) {
  const nodes = run.doc.tiles.flatMap((x) => x.nodes);
  const count = (...statuses) => nodes.filter((n) => statuses.includes(n.status)).length;
  let weights = 0;
  let sum = 0;
  for (const n of nodes) {
    const w = mockWeight(n);
    weights += w;
    sum += w * nodeFraction(n);
  }
  run.peak = Math.max(run.peak, weights ? sum / weights : 0);
  const elapsed = mockElapsed(run);
  const p = run.peak;
  let low = null;
  if (p >= 1 || !jobActive(run.doc)) low = 0;
  else if (p > 0.04 && elapsed > 2) low = round((elapsed * (1 - p)) / p, 1);
  return {
    running: count("running"),
    pending: count("pending"),
    done: count("done", "hit"),
    failed: count("failed", "skipped", "cancelled"),
    hits: count("hit"),
    elapsed_s: round(elapsed, 3),
    progress: round(p, 4),
    eta_low_s: low,
    eta_high_s: low == null ? null : round(low * 1.5, 1),
    // The engine downloads the map data inside the build (a tile goes on once its own is in):
    // its `data` phase belonged to the engines that downloaded every tile's first.
    phase: "build",
  };
}

/** Seconds of the mock job's own clock since it started (`MOCK_SPEED` times the real ones); 0 while
 * it waits in the queue. */
function mockElapsed(run) {
  return run.t0 == null ? 0 : ((Date.now() - run.t0) * MOCK_SPEED) / 1000;
}

/** Write one entry in the mock job's journal, like `Job._append`, and tell the listeners. */
function mockAppend(run, fields) {
  const entry = { seq: run.doc.last_seq + 1, ts: round(mockElapsed(run), 3), ...fields };
  run.doc.last_seq = entry.seq;
  mockEngineApply(run.doc, entry);
  mockRecordBuilt(run, entry);
  run.journal.push(entry);
  for (const listener of run.listeners) listener(entry);
  return entry;
}

/** Like the engine's pack and install nodes: a tile a mock build packs joins the library (once it
 * was read), and is in X-Plane once its install is done, with its overlay row. */
function mockRecordBuilt(run, entry) {
  if (!mock.library || entry.event !== "done" || (entry.role !== "pack" && entry.role !== "install")) return;
  const now = Date.now() / 1000;
  const name = `zOrthoStudio_${entry.tile}`;
  let row = mock.library.find((e) => e.kind === "ortho" && e.built_by === "osxp" && e.name === name);
  let overlay = mock.library.find((e) => e.kind === "overlay" && e.built_by === "osxp" && e.tile === entry.tile);
  if (!row) {
    row = { tile: entry.tile, kind: "ortho", provider: run.doc.provider, zl: run.doc.zl, path: `/Users/pilot/.orthostudio/tiles/${name}`, name, built_by: "osxp", installed: false, keys: null, registered_at: now, updated_at: now, size_bytes: 1400000000, present: true, overlay: null };
    mock.library.push(row);
  }
  if (!overlay) {
    overlay = { tile: entry.tile, kind: "overlay", provider: "", zl: null, path: "/Users/pilot/.orthostudio/tiles/yOrthoStudio_Overlays", name: "yOrthoStudio_Overlays", built_by: "osxp", installed: false, keys: null, registered_at: now, updated_at: now, size_bytes: null, present: true, overlay: null };
    mock.library.push(overlay);
  }
  Object.assign(row, { provider: run.doc.provider, zl: run.doc.zl, updated_at: now, present: true });
  if (entry.role === "install") row.installed = overlay.installed = true;
  mockSortLibrary();
}

/**
 * When each entry of mock/job_events.json happens, in ms from the job's start. Map data first,
 * one tile after the other: the first tile of a job of several already has it (its `osm` row turns
 * into a hit), the others download it. Then the tiles build side by side, a little apart; the last
 * tile of a job of several misses a texture (`only: "failing"` entries), so its pack and install
 * are skipped. A retry (`reuse`: the nodes the job before it built) gets those nodes as hits.
 */
export function mockTimeline(doc, script, reuse = new Set()) {
  const out = [];
  const level = `${doc.provider}${doc.zl}`;
  const fill = (e, name) => {
    const { delay_ms: _delay, only: _only, ...entry } = JSON.parse(JSON.stringify(e).replaceAll("{tile}", name).replaceAll("{level}", level));
    return entry;
  };
  const names = doc.tiles.map((x) => x.tile);
  const failing = reuse.size === 0 && names.length > 1 ? names[names.length - 1] : null;
  const declared = (i, node) => doc.tiles[i].nodes.some((n) => n.node === node);
  // Like the engine: the downloads run one after the other on their lane, and a tile's build goes
  // on once its own map data is in, without waiting for the other tiles' downloads.
  let lane = 300;
  const ready = names.map((name, i) => {
    if (!declared(i, `${name}/osm`)) return 300;
    if ((i === 0 && names.length > 1) || reuse.has(`${name}/osm`)) {
      for (const e of script.osm_hit) out.push({ at: 300, entry: fill(e, name) });
      return 300;
    }
    for (const e of script.osm) {
      lane += e.delay_ms;
      out.push({ at: lane, entry: fill(e, name) });
    }
    return lane;
  });
  names.forEach((name, i) => {
    let t = Math.max(ready[i] + 100, 700 + i * 900);
    for (const e of script.tile) {
      t += e.delay_ms;
      if ((e.only === "ok" && name === failing) || (e.only === "failing" && name !== failing)) continue;
      const entry = fill(e, name);
      if (entry.node && !declared(i, entry.node)) continue;
      if (entry.node && reuse.has(entry.node)) {
        // Already built by the job before: the scheduler finds it in the store (started, then a hit).
        if (entry.event === "started") {
          out.push({ at: t, entry }, { at: t, entry: { event: "done", tile: entry.tile, stage: entry.stage, node: entry.node, role: entry.role, key: entry.key, hit: true, wall_s: 0, weight_s: 0 } });
        }
        continue;
      }
      out.push({ at: t, entry });
    }
  });
  return out.sort((a, b) => a.at - b.at);
}

/** The report and decisions of a finished mock job, in the engine's shapes (api.md 5.5). */
function mockOutcome(run) {
  const doc = run.doc;
  const reportStatus = { done: "built", hit: "hit", failed: "failed", skipped: "skipped", cancelled: "failed" };
  const decisions = [];
  const tiles = doc.tiles.map((tile, i) => {
    const target = tile.nodes.filter((n) => n.role === (tile.install ? "install" : "pack"));
    const ok = target.length > 0 && target.every((n) => n.status === "done" || n.status === "hit");
    const packDir = ok ? `/Users/pilot/.orthostudio/tiles/zOrthoStudio_${tile.tile}` : null;
    const nodes = tile.nodes.filter((n) => n.status !== "pending").map((n) => ({ id: n.node, role: n.role, rule: n.role, key: n.key, status: reportStatus[n.status] || n.status, wall_s: n.wall_s, error: n.error, cause: n.cause }));
    const counts = { hit: 0, built: 0, failed: 0, skipped: 0 };
    const seconds = Object.fromEntries(STEPS.map((s) => [s, 0]));
    for (const n of tile.nodes) {
      if (n.status === "hit") counts.hit += 1;
      else if (n.status === "done") counts.built += 1;
      else if (n.status === "failed" || n.status === "skipped") counts[n.status] += 1;
      seconds[n.stage] = round(seconds[n.stage] + (n.wall_s || 0), 2);
    }
    decisions.push({ tile: tile.tile, kind: "nodes", ...counts }, { tile: tile.tile, kind: "stage_time", seconds });
    const skipped = tile.nodes.filter((n) => n.status === "skipped");
    if (skipped.length) decisions.push({ tile: tile.tile, kind: "degraded", code: "SYS_UPSTREAM_FAILED", count: skipped.length, message: skipped[0].error?.message || "" });
    const textures = tile.nodes.find((n) => n.role === "textures");
    if (textures && textures.status !== "pending") {
      const total = 213 + 7 * i;
      const missing = textures.status === "failed" ? textures.error?.context?.count ?? 1 : 0;
      const hits = textures.status === "hit" ? total : 18;
      decisions.push({ tile: tile.tile, kind: "textures", total, built: total - hits - missing, hits, missing, parent_fallback: i === 0 ? 3 : 0, placeholders: 0 });
    }
    return { tile: tile.tile, provider: tile.provider, zl: tile.zl, ok, pack_dir: packDir, overlay_dsf: null, installed: ok && tile.install, repaired: [], stages: {}, osm: {}, nodes };
  });
  for (const tile of tiles) {
    if (tile.pack_dir) decisions.push({ tile: tile.tile, kind: "pack", path: tile.pack_dir, bytes: 2656881226 + 110000000 * tiles.indexOf(tile), installed: tile.installed });
  }
  const all = tiles.flatMap((x) => x.nodes);
  const report = {
    schema: 1,
    ok: tiles.every((x) => x.ok),
    elapsed_s: round(mockElapsed(run), 3),
    built: all.filter((n) => n.status === "built").length,
    hits: all.filter((n) => n.status === "hit").length,
    failed: all.filter((n) => n.status === "failed" || n.status === "skipped").length,
    cancelled: false,
    store_root: "/Users/pilot/.orthostudio/store",
    out_dir: "/Users/pilot/.orthostudio/tiles",
    tiles,
  };
  return { report, decisions };
}

function mockFinish(run, status = null) {
  for (const timer of run.timers) clearTimeout(timer);
  clearInterval(run.ticker);
  run.timers = [];
  const doc = run.doc;
  const nodes = doc.tiles.flatMap((x) => x.nodes);
  doc.status = status || (nodes.some((n) => n.status === "failed" || n.status === "skipped") ? "failed" : "done");
  doc.finished_at = doc.started_at == null ? Date.now() / 1000 : round(doc.started_at + mockElapsed(run), 3);
  mockAppend(run, { event: "stats", stats: mockStats(run) });
  const { report, decisions } = mockOutcome(run);
  // Like the engine: a cancelled build has no report, hence no pack decisions.
  doc.report = doc.status === "cancelled" ? null : report;
  doc.decisions = doc.report ? decisions : decisions.filter((d) => d.kind !== "pack");
  mockAppend(run, { event: "finished", status: doc.status, report: doc.report, decisions: doc.decisions, error: null });
  mockLaunchNext();
}

/** Like the engine's queue: once nothing runs, the job queued first starts. */
function mockLaunchNext() {
  const runs = [...mock.jobs.values()];
  if (runs.some((run) => run.doc.status === "running")) return;
  const next = runs.find((run) => run.doc.status === "queued");
  if (next) mockLaunch(next);
}

/** 1 for the queued mock job that starts next, 2 for the one after it; 0 for a job not waiting. */
function mockQueuePosition(run) {
  return [...mock.jobs.values()].filter((x) => x.doc.status === "queued").indexOf(run) + 1;
}

/** The engine's refusal of a tile already in a build, running or queued (`SYS_TILE_IN_BUILD`). */
function mockCheckTilesFree(tiles, why = "a tile is in one build at a time.") {
  const taken = new Map();
  for (const run of mock.jobs.values()) {
    if (!jobActive(run.doc)) continue;
    for (const x of run.doc.tiles) {
      // Like the engine: a tile the running build has finished is free again.
      if (mockTileStatus(x, run.doc.status) === "done" || taken.has(x.tile)) continue;
      taken.set(x.tile, run.doc.id);
    }
  }
  const busy = [...new Set(tiles || [])].filter((name) => taken.has(name)).sort();
  if (!busy.length) return;
  const jobs = [...new Set(busy.map((name) => taken.get(name)))].sort();
  const message = `${busy.join(" ")} ${busy.length === 1 ? "is" : "are"} in a build already, running or queued (${jobs.join(" ")}): ${why}`;
  throw new ApiError(409, { error: { schema: 1, code: "SYS_TILE_IN_BUILD", domain: "SYS", severity: "blocking", action: "stop", message, remedy: "Wait for that build to end (see Works), or cancel it, then try again.", context: { tiles: busy, jobs }, cause: null } });
}

/** Start a mock job for a POST /api/jobs body, or queue it (`queued`: a build runs); `reuse` holds
 * the nodes a retried job built. */
async function mockStartJob(body, reuse = new Set(), queued = false) {
  const template = await mockFile("job");
  const script = await mockFile("job_events");
  const provider = body.provider || template.provider;
  const zl = Number(body.zoom_level ?? template.zl);
  const install = Boolean(body.install);
  const now = Date.now();
  const doc = {
    id: mockJobId(mock.nextId++),
    status: "queued",
    created_at: now / 1000,
    started_at: null,
    finished_at: null,
    install,
    provider,
    zl,
    request: { ...template.request, tiles: [...(body.tiles || [])], provider, zoom_level: zl, zones: body.zones || [], install },
    tiles: (body.tiles || []).map((name) => ({ tile: name, provider, zl, install, nodes: mockTileNodes(template, name, `${provider}${zl}`, install) })),
    stats: null,
    last_seq: 0,
    report: null,
    decisions: [],
  };
  const run = { doc, journal: [], listeners: new Set(), timers: [], ticker: 0, t0: null, peak: 0, script, reuse };
  mock.jobs.set(doc.id, run);
  if (!queued) mockLaunch(run);
  return run;
}

/** A mock job starts, like the engine's job thread: its clock, its first entries, its timeline. */
function mockLaunch(run) {
  const doc = run.doc;
  run.t0 = Date.now();
  doc.status = "running";
  doc.started_at = run.t0 / 1000;
  mockAppend(run, { event: "log", message: `job ${doc.id} started`, tile: null, stage: null });
  mockAppend(run, { event: "stats", stats: mockStats(run) });
  const timeline = mockTimeline(doc, run.script, run.reuse);
  for (const { at, entry } of timeline) run.timers.push(setTimeout(() => mockAppend(run, entry), at / MOCK_SPEED));
  run.ticker = setInterval(() => mockAppend(run, { event: "stats", stats: mockStats(run) }), 1000 / MOCK_SPEED);
  run.timers.push(setTimeout(() => mockFinish(run), ((timeline.length ? timeline[timeline.length - 1].at : 0) + 600) / MOCK_SPEED));
}

/** The mock job running, else the one queued first: the engine's `JobManager.active()`. */
function mockActiveRun() {
  const runs = [...mock.jobs.values()];
  return runs.find((run) => run.doc.status === "running") || runs.find((run) => jobActive(run.doc)) || null;
}

/** Stop a mock job like the engine: its running nodes end with SYS_CANCELLED, then `finished`. */
function mockCancel(run) {
  for (const timer of run.timers) clearTimeout(timer);
  run.timers = [];
  for (const tile of run.doc.tiles) {
    for (const n of tile.nodes) {
      if (n.status !== "running") continue;
      mockAppend(run, { event: "failed", tile: tile.tile, stage: n.stage, node: n.node, role: n.role, error: { schema: 1, code: "SYS_CANCELLED", domain: "SYS", severity: "info", action: "none", message: `Node ${n.node} cancelled.`, remedy: "Run the build again: finished nodes are reused.", context: {}, cause: null }, skipped: false, cause: null, weight_s: round(mockWeight(n), 3) });
    }
  }
  mockFinish(run, "cancelled");
}

/** Exported for the tests, which run it under node. */
export async function mockApi(method, path, body, options = {}) {
  const url = new URL(path, location.href);
  const p = url.pathname.replace(/^.*\/api\//, "/api/");
  await delay(60);
  if (p === "/api/quit" && method === "POST") {
    const running = mockActiveRun();
    if (running && !body?.force) throw mockError(409, "SYS_BUSY", `A build is running (${running.doc.id}): quitting OrthoStudio XP stops it.`, "Wait for the build to finish, or confirm that it may stop.");
    // Like the engine: the queued builds first, so that none starts when the running one stops.
    const queued = [...mock.jobs.values()].filter((run) => run.doc.status === "queued" && run !== running);
    for (const run of queued) mockCancel(run);
    if (running) mockCancel(running);
    return { stopping: true, cancelled: running ? running.doc.id : null, queued_cancelled: queued.map((run) => run.doc.id) };
  }
  if (p === "/api/choose-folder" && method === "POST") {
    // The mock opens no dialog: a folder as the engine would answer (fail=no-dialog: none on this system).
    if (MOCK_FAIL === "no-dialog") throw mockError(501, "SYS_NO_FOLDER_DIALOG", "No folder dialog could be opened on this computer.", "Type the folder's path in the field.");
    // fail=slow-dialog: the File Explorer's took seconds to show on a user's Windows
    if (MOCK_FAIL === "slow-dialog") await new Promise((resolve) => setTimeout(resolve, 2500));
    return { path: /ortho4xp/i.test(body?.prompt || "") ? "/Users/pilot/Ortho4XP" : "/Users/pilot/X-Plane 12" };
  }
  if (p === "/api/reveal" && method === "POST") {
    if (!String(body?.path || "").startsWith("/")) throw mockError(403, "SYS_FORBIDDEN_PATH", `${body?.path} is not a folder of OrthoStudio XP, of X-Plane or of a tile of the library.`, "The page only shows the folders OrthoStudio XP works with.");
    return { revealed: body.path };
  }
  if (p === "/api/status") {
    const status = await mockFile("status");
    if (MOCK_FAIL === "xplane") status.xplane.running = true;
    if (MOCK_FAIL === "no-xplane") {
      const dir = mockXplaneDir();
      status.xplane = { path: dir, detected: Boolean(dir), running: false };
      const check = status.doctor.find((c) => c.name === "xplane");
      if (check && !dir) Object.assign(check, { status: "warn", summary: "X-Plane 12 not found: choose its folder in Settings, or give --xplane (OrthoStudio XP takes the relief, roads, forests and buildings from it, and adds the tiles to it)" });
    }
    const dataDir = mockDataDir();
    status.data_dir = dataDir
      ? { path: dataDir, chosen: true, present: !(MOCK_FAIL === "no-data-disk" && dataDir === MOCK_AWAY_DISK) }
      : { path: status.home, chosen: false, present: true };
    // Like the engine: tiles, not rows (a tile's overlay row is not a tile of its own).
    if (mock.library) status.library_count = new Set(libraryTiles(mock.library).map((e) => e.tile)).size;
    status.active_job = mockActiveRun()?.doc.id ?? null;
    return status;
  }
  if (p === "/api/providers") return [...(await mockFile("providers")), ...structuredClone(mock.sources || [])];
  if (p === "/api/sources/test" && method === "POST") {
    mockCheckSourceAddress(body?.url_template);
    const works = !String(body.url_template).includes("fail");
    return { ok: works, status: 200, image: works ? "jpeg" : null, bytes: works ? 23456 : 120, url: String(body.url_template).replace("{zoom}", "15").replace("{x}", "16988").replace("{y}", "11589"), error: null };
  }
  if (p === "/api/sources" && method === "POST") {
    mockCheckSourceAddress(body?.url_template);
    mock.sources = mock.sources || [];
    const taken = new Set([...(await mockFile("providers")).map((x) => x.code), ...mock.sources.map((x) => x.code)]);
    const base = String(body.name || "").replace(/[^A-Za-z0-9]/g, "").slice(0, 24) || "Source";
    let code = base;
    for (let n = 2; taken.has(code); n += 1) code = `${base}_${n}`;
    const name = String(body.name).trim();
    const source = { code, name, max_zl: Number(body.max_zl) || 19, attribution: name, terms_url: "", alive: null, extent: null, extent_bounds: null, same_as: null, custom: true, url_template: String(body.url_template).trim() };
    mock.sources.push(source);
    return structuredClone(source);
  }
  const source = p.match(/^\/api\/sources\/([^/]+)$/);
  if (source && method === "DELETE") {
    const code = decodeURIComponent(source[1]);
    if (!(mock.sources || []).some((x) => x.code === code)) throw mockError(404, "CFG_PROVIDER_UNKNOWN", `${code} is not a source you added.`, "Only the sources you added can be removed.");
    const zones = (mock.zones?.zones || []).filter((z) => z.provider === code).map((z) => z.name || z.id);
    if (zones.length) throw new ApiError(409, { error: { code: "SYS_SOURCE_IN_USE", severity: "blocking", action: "stop", message: `The source ${code} is the imagery of zone(s) ${zones.join(", ")}.`, remedy: "Give those zones another source (the Plan, step 2), then remove it again.", context: { zones } } });
    mock.sources = mock.sources.filter((x) => x.code !== code);
    const settings = mock.cache.get("settings");
    const reset = settings?.essential?.provider === code ? "BI" : null;
    if (reset) settings.essential.provider = reset;
    return { removed: code, settings_provider: reset };
  }
  if (p === "/api/settings/schema") return mockFile("settings_schema");
  if (p === "/api/settings") {
    if (method === "PUT") {
      const wanted = String(body?.essential?.data_dir || "").trim() || null;
      if (wanted && wanted !== mockDataDir()) {
        if (mockActiveRun()) throw mockError(409, "SYS_BUSY", "A build is running or waiting, and the data folder changes between builds.", "Wait for the builds to finish, or cancel them, then save again.");
        if (/exfat|fat32/i.test(wanted)) {
          const reason = "its disk cannot hard-link files (exFAT or FAT32): each texture would be written three times";
          throw new ApiError(422, { error: { code: "CFG_DATA_DIR_INVALID", severity: "blocking", action: "stop", message: `The data folder ${wanted} cannot be used: ${reason}.`, remedy: "Choose a folder on a disk formatted APFS or Mac OS Extended (Mac), NTFS (Windows) or ext4 (Linux).", context: { path: wanted, reason, why: "links" } } });
        }
      }
      const saved = structuredClone(body);
      if (saved.essential) saved.essential.data_dir = wanted;
      mock.cache.set("settings", saved);
      mock.settingsSaved = true;
      return structuredClone(saved);
    }
    const settings = await mockFile("settings");
    if (MOCK_FAIL === "no-xplane" && !mock.settingsSaved) settings.essential.xplane_dir = null;
    if (MOCK_FAIL === "no-data-disk" && !mock.settingsSaved) settings.essential.data_dir = MOCK_AWAY_DISK;
    return settings;
  }
  if (p === "/api/airports") {
    const q = (url.searchParams.get("q") || "").trim().toUpperCase();
    const all = await mockFile("airports");
    if (!q) return [];
    return all
      .filter((a) => a.icao.startsWith(q) || a.name.toUpperCase().includes(q))
      .slice(0, 10);
  }
  let m = p.match(/^\/api\/airports\/([A-Za-z0-9]+)$/);
  if (m) {
    const all = await mockFile("airports");
    const a = all.find((x) => x.icao === m[1].toUpperCase());
    if (!a) throw new ApiError(404, { code: "CFG_LATLON_INVALID", message: "unknown ICAO" });
    return a;
  }
  if (p === "/api/plan") {
    await delay(700);
    mockCheckDataDisk();
    await mockCheckZones(body.zones);
    mockCheckXplane();
    await mockCheckCoverage(body);
    const plan = await mockFile("plan");
    const tiles = body.tiles || [];
    const template = plan.tiles[0];
    plan.tiles = tiles.map((name) => ({ ...structuredClone(template), tile: name, zl: body.zoom_level, provider: body.provider }));
    const k = Math.max(1, tiles.length);
    for (const key of ["requests", "mb", "seconds_low", "seconds_high"]) plan.network[key] *= k;
    for (const key of ["seconds", "textures", "cached"]) plan.compute[key] *= k;
    plan.disk.dds_gb *= k;
    const extra = mockZoneTextures(body.zones, tiles, body.provider, body.zoom_level);
    plan.compute.textures += extra;
    plan.compute.seconds += extra * 0.2;
    plan.network.requests += extra * 256;
    plan.network.mb += extra * 4;
    plan.disk.dds_gb += (extra * TEXTURE_MB) / 1000;
    // The engine's verdict (orthostudio.estimate): images and downloads needed, ok when more than twice free.
    plan.disk.needed_gb = Math.round((plan.disk.dds_gb + plan.network.mb / 1000) * 100) / 100;
    if (MOCK_FAIL === "disk") plan.disk.free_gb = Math.round(plan.disk.needed_gb * 15) / 10;
    plan.disk.ok = plan.disk.free_gb > 2 * plan.disk.needed_gb;
    if (!plan.disk.ok) plan.warnings.push("SYS_DISK_FULL");
    return plan;
  }
  if (p === "/api/zones") {
    if (!mock.zones) mock.zones = await mockZonesDocument();
    if (method === "PUT") {
      if (MOCK_FAIL === "zone-conflict" && !mock.conflicted) {
        // Another window saves first, once: the page's revision is stale now.
        mock.conflicted = true;
        const first = mock.zones.zones[0];
        if (first) first.name = `${first.name} (edited elsewhere)`.slice(0, 80);
        mock.zones.revision = mockRevision();
      }
      // Like the engine: a save must name the revision it replaces; nothing is written otherwise.
      if (headerValue(options.headers, "If-Match") !== `"${mock.zones.revision}"`) {
        throw new ApiError(409, { error: { code: "ZONE_CONFLICT", severity: "blocking", message: "The zones were changed since this page read them (another window or an edit of the file): nothing was written.", remedy: "Load the zones again, then make the change again.", context: { revision: mock.zones.revision } } });
      }
      const problem = MOCK_FAIL === "zones"
        ? { zone: body?.zones?.[0]?.id ?? "", reason: "refused on purpose by the mock (?fail=zones)" }
        : validateZonesDocument(body, await mockFile("providers"));
      if (problem) throw mockZoneInvalid(problem);
      mock.zones = { format: ZONES_FORMAT, revision: mockRevision(), zones: body.zones.map(normalizeZone), problems: [] };
    }
    return structuredClone(mock.zones);
  }
  if (p === "/api/jobs" && method === "GET") {
    const past = mock.pastCleared ? [] : await mockFile("jobs");
    const live = [...mock.jobs.values()].map(mockJobSummary).reverse();
    return [...live, ...past];
  }
  if (p === "/api/jobs" && method === "POST") {
    mockCheckDataDisk();
    await mockCheckZones(body.zones);
    mockCheckXplane();
    await mockCheckCoverage(body);
    const busy = Boolean(mockActiveRun());
    if (busy && !body.queue) {
      throw mockError(409, "SYS_BUSY", "A build is already running.", "Wait for it to finish, or cancel it.");
    }
    mockCheckTilesFree(body.tiles);
    const run = await mockStartJob(body, new Set(), busy);
    return { job_id: run.doc.id, status: run.doc.status, queue_position: mockQueuePosition(run) };
  }
  if (p === "/api/jobs/clear" && method === "POST") {
    // Like the engine: the finished jobs leave the list for good, a running one stays.
    const removed = [];
    for (const [id, run] of mock.jobs) {
      if (jobActive(run.doc)) continue;
      mock.jobs.delete(id);
      removed.unshift(id);
    }
    if (!mock.pastCleared) removed.push(...(await mockFile("jobs")).map((j) => j.id));
    mock.pastCleared = true;
    return { removed };
  }
  m = p.match(/^\/api\/jobs\/([^/]+)$/);
  if (m) {
    if (mock.jobs.has(m[1])) return mockJobState(mock.jobs.get(m[1]));
    const done = await mockFile("job_done");
    if (!mock.pastCleared && done.id === m[1]) return done;
    throw mockError(404, "SYS_WORKING_DIR_INVALID", `No job ${m[1]}.`, "List the jobs with GET /api/jobs.");
  }
  m = p.match(/^\/api\/jobs\/([^/]+)\/(cancel|retry)$/);
  if (m) {
    const run = mock.jobs.get(m[1]);
    const done = run ? null : await mockFile("job_done");
    if (!run && (mock.pastCleared || done.id !== m[1])) throw mockError(404, "SYS_WORKING_DIR_INVALID", `No job ${m[1]}.`, "List the jobs with GET /api/jobs.");
    const status = run ? run.doc.status : done.status;
    if (m[2] === "cancel") {
      if (!run || !jobActive(run.doc)) throw mockError(409, "SYS_BUSY", `Job ${m[1]} is already ${status}.`, "Nothing to cancel.");
      mockCancel(run);
      return { job_id: run.doc.id, status: run.doc.status, cancel_requested: true };
    }
    const busy = Boolean(mockActiveRun());
    if (busy && !body?.queue) {
      throw mockError(409, "SYS_BUSY", "A build is already running.", "Wait for it to finish, or cancel it.");
    }
    // A retry is a new job with the same tiles: what the job before built comes back as hits.
    const source = run ? mockJobState(run) : done;
    const reuse = new Set();
    for (const tile of source.tiles) {
      for (const stage of Object.values(tile.stages || {})) {
        for (const n of stage.nodes || []) if (n.status === "done" || n.status === "hit") reuse.add(n.node);
      }
    }
    const request = { tiles: source.tiles.map((x) => x.tile), provider: source.provider, zoom_level: source.zl ?? source.zoom_level, install: source.install, zones: source.request?.zones || [] };
    mockCheckTilesFree(request.tiles);
    const next = await mockStartJob(request, reuse, busy);
    return { job_id: next.doc.id, status: next.doc.status, retry_of: m[1], queue_position: mockQueuePosition(next) };
  }
  if (p === "/api/library/overlays" && method === "POST") {
    if (!mock.library) mock.library = await mockFile("library");
    if (mockXplaneRunning()) throw mockError(409, "XP_RUNNING", "X-Plane is running.", "Quit X-Plane, then try again.");
    mockCheckTilesFree(body?.tiles || [], "the end of that build decides what X-Plane shows of it: its pack stays as it is.");
    const changed = [];
    const states = {};
    for (const row of mock.library) {
      if (row.kind !== "ortho" || !row.overlay || !(body.tiles || []).includes(row.tile)) continue;
      const before = row.overlay.state;
      if (body.use === "others" && (before === "double" || before === "own")) row.overlay.state = row.overlay.others.length ? "left" : "missing";
      if (body.use === "own" && (before === "left" || before === "missing")) row.overlay.state = row.overlay.others.length ? "double" : "own";
      if (row.overlay.state !== before) changed.push(row.tile);
      states[row.tile] = structuredClone(row.overlay);
    }
    return { changed, states };
  }
  if (p === "/api/library" && method === "GET") {
    if (!mock.library) mock.library = await mockFile("library");
    return structuredClone(mock.library);
  }
  if (p === "/api/disk" && method === "GET") {
    if (!mock.disk) mock.disk = { store_bytes: 9.8e9, unused_bytes: 2.4e9, images_bytes: 3.1e9, mapcache_bytes: 12e6 };
    if (!mock.library) mock.library = await mockFile("library");
    return { ...mock.disk, tiles: libraryTiles(mock.library).length, building: MOCK_FAIL === "busy" || Boolean(mockActiveRun()) };
  }
  if (p === "/api/clean" && method === "POST") {
    if (MOCK_FAIL === "busy" || mockActiveRun()) {
      throw mockError(409, "SYS_BUSY", "A build is running in OrthoStudio XP, and OrthoStudio XP frees space only between builds.", "Wait for the build to finish, or stop it, then try again.");
    }
    if (!mock.disk) mock.disk = { store_bytes: 9.8e9, unused_bytes: 2.4e9, images_bytes: 3.1e9, mapcache_bytes: 12e6 };
    const images = Boolean(body?.images);
    const freed = mock.disk.unused_bytes;
    const imagesFreed = images ? mock.disk.images_bytes + mock.disk.mapcache_bytes : 0;
    mock.disk = { ...mock.disk, store_bytes: mock.disk.store_bytes - freed, unused_bytes: 0, ...(images ? { images_bytes: 0, mapcache_bytes: 0 } : {}) };
    return { format: "osxp-clean-1", freed_bytes: freed, images_freed_bytes: imagesFreed, removed: freed ? 12 : 0 };
  }
  if (p === "/api/library/import-ortho4xp") {
    if (!mock.library) mock.library = await mockFile("library");
    const now = Date.now() / 1000;
    const row = { tile: "+42+009", kind: "ortho", provider: "BI", zl: 16, path: `${body.folder}/Tiles/zOrtho4XP_+42+009`, name: "zOrtho4XP_+42+009", built_by: "ortho4xp", installed: false, keys: null, registered_at: now, updated_at: now, size_bytes: 1650000000, present: true, overlay: null };
    if (!mock.library.some((e) => e.path === row.path)) {
      mock.library.push(row);
      mockSortLibrary();
    }
    const { tile, kind, provider, zl, path: packPath, name, built_by: builtBy } = row;
    return [{ tile, kind, provider, zl, path: packPath, name, built_by: builtBy }];
  }
  m = p.match(/^\/api\/library\/([^/]+)\/(install|uninstall|delete)$/);
  if (m) {
    if (!mock.library) mock.library = await mockFile("library");
    const action = m[2];
    const customScenery = `${(await mockFile("status")).xplane?.path || "X-Plane 12"}/Custom Scenery`;
    if (action === "delete" && (MOCK_FAIL === "busy" || mockActiveRun())) {
      throw mockError(409, "SYS_BUSY", "A build is running: the library cannot be changed now.", "Wait for it to finish, or cancel it.");
    }
    // Like the engine: the row's path picks one pack when two builds of a tile, in two output folders, share a name.
    const entry = mockLibraryRow(decodeURIComponent(m[1]), body?.path ?? null);
    if (action !== "delete" && entry.built_by === "osxp") {
      mockCheckTilesFree([entry.tile], "the end of that build decides what X-Plane shows of it: its pack stays as it is.");
    }
    if (action === "delete" && entry.built_by !== "osxp") {
      throw mockError(409, "SYS_PACK_NOT_OSXP", `${entry.tile} in ${entry.path} was built by Ortho4XP: OrthoStudio XP deletes only the tiles it built.`, "Nothing was deleted. Uninstall takes the tile out of X-Plane without deleting anything; to delete the tile for good, delete its folder by hand.");
    }
    // Refused while X-Plane runs, a delete too, even of a tile X-Plane does not show.
    if (mockXplaneRunning()) {
      throw mockError(409, "XP_RUNNING", "X-Plane is running; the scenery cannot be installed now.", "Quit X-Plane, then run Install again.");
    }
    const overlaysBefore = mock.library.some((e) => e.kind === "overlay" && e.built_by === "osxp" && e.installed);
    if (action === "delete") {
      // The tile's pack goes, and its row of the shared overlays pack with it.
      mock.library = mock.library.filter((e) => e !== entry && !(e.kind === "overlay" && e.tile === entry.tile && e.built_by === entry.built_by));
    } else entry.installed = action === "install";
    mockSyncOverlays();
    if (action === "delete") {
      // `fail=clean`: the tile is gone, but the store could not give its space back (yet).
      const warning = MOCK_FAIL === "clean" ? "The tile is deleted, but OrthoStudio XP could not free the space its cache holds (mock: fail=clean). Run osxp clean later, when no build is running." : null;
      const freed = entry.present === false || warning ? 0 : Math.round((entry.size_bytes || 0) * 0.93);
      return { format: "osxp-delete-1", name: entry.name, tile: entry.tile, removed_from_xplane: Boolean(entry.installed), pack_deleted: entry.present !== false, freed_bytes: freed, custom_scenery: entry.installed ? customScenery : null, warning };
    }
    if (action === "install") {
      const overlay = entry.built_by === "osxp" ? `${customScenery}/yOrthoStudio_Overlays` : null;
      return { format: "osxp-install-1", tile: entry.tile, pack: entry.path, target: `${customScenery}/${entry.name}`, overlay_target: overlay, custom_scenery: customScenery, link: true };
    }
    const overlaysAfter = mock.library.some((e) => e.kind === "overlay" && e.built_by === "osxp" && e.installed);
    return { format: "osxp-uninstall-1", name: entry.name, removed: true, pack: entry.path, overlay_parked: null, overlay_pack_removed: overlaysBefore && !overlaysAfter, pack_deleted: false, custom_scenery: customScenery };
  }
  throw new ApiError(404, { detail: `mock: no route for ${method} ${p}` });
}

/** The event stream of a mock job, like the engine's: its journal so far, then each new entry
 * (a moment later, as over the network), until closed. Exported for the tests. */
export async function mockSubscribe(jobId, handlers) {
  const run = mock.jobs.get(jobId);
  if (!run) return { close() {} };
  let closed = false;
  const deliver = (entry) => {
    if (!closed) handlers[entry.event]?.(structuredClone(entry));
  };
  const past = run.journal.slice();
  setTimeout(() => {
    for (const entry of past) deliver(entry);
  }, 0);
  const listener = (entry) => setTimeout(() => deliver(entry), 0);
  run.listeners.add(listener);
  return {
    close() {
      closed = true;
      run.listeners.delete(listener);
    },
  };
}

// ------------------------------------------------------------------ job state helpers

/** Node statuses that end a node: the engine counts such a node whole in its stage's fraction. */
const NODE_ENDED = new Set(["done", "hit", "failed", "skipped", "cancelled"]);
/** Job statuses of a build still to come or under way: Stop, Remaining and the ticking clock. */
const JOB_ACTIVE = new Set(["queued", "pending", "running"]);
/** The log keeps its last lines only. */
const LOG_MAX = 500;
const LOG_LEVELS = new Set(["info", "warning", "error"]);

export function jobActive(job) {
  return Boolean(job) && JOB_ACTIVE.has(job.status);
}

/**
 * The tiles of a running build as the Plan's map shows them (a user asked to see there what is
 * being done): `working` while one of its steps runs now, `queued` while nothing of it runs and it
 * is not finished (its turn has not come, or it waits between two steps), `failed` once a step
 * failed. A finished tile leaves the map's build layer: installed, it is green. Empty when the
 * build is over.
 */
export function buildingTiles(job) {
  const out = new Map();
  if (!jobActive(job)) return out;
  for (const tile of Array.isArray(job.tiles) ? job.tiles : []) {
    if (!tile || typeof tile.tile !== "string" || !TILE_RE.test(tile.tile)) continue;
    // a step without rows (the install of a build that does not install) says nothing
    const steps = Object.values(tile.steps || {}).filter((st) => Object.keys(st?.nodes || {}).length);
    if (tile.status === "failed" || steps.some((st) => st.status === "failed")) out.set(tile.tile, "failed");
    else if (steps.some((st) => st.status === "running")) out.set(tile.tile, "working");
    else if (tile.status !== "done" && !(steps.length && steps.every((st) => NODE_ENDED.has(st.status)))) out.set(tile.tile, "queued");
  }
  return out;
}

/**
 * The tiles in a build among `jobs` (summaries of GET /api/jobs, or a job's state), each with its
 * build's id. A tile is in one build at a time: the page does not choose it again (a user asked),
 * and the engine refuses a second build of it (SYS_TILE_IN_BUILD). A tile the build has finished
 * is free again (a user was refused one, installed while the rest of its build ran): a job's
 * state says which (`buildingTiles` leaves them out, a failed one stays), a summary does not.
 */
export function tilesInBuilds(jobs) {
  const out = new Map();
  for (const job of jobs || []) {
    if (!jobActive(job)) continue;
    const tiles = Array.isArray(job.tiles) ? job.tiles : [];
    const unfinished = tiles.some((x) => x && typeof x === "object") ? buildingTiles(job) : null;
    for (const x of tiles) {
      const name = typeof x === "string" ? x : x?.tile;
      if (typeof name !== "string" || out.has(name) || (unfinished && !unfinished.has(name))) continue;
      out.set(name, job.id);
    }
  }
  return out;
}

function clamp01(x) {
  return typeof x === "number" && Number.isFinite(x) ? Math.min(1, Math.max(0, x)) : 0;
}

function finite(x) {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

function nodeName(nodeId) {
  return String(nodeId || "").split("/").pop().replace(/#\d+$/, "");
}

function tileOfNode(nodeId) {
  const m = String(nodeId || "").match(/^[+-]\d{2}[+-]\d{3}(?=\/)/);
  return m ? m[0] : null;
}

/** The user step of a node: its `role` when the engine gives it, else the last segment of its id,
 * a role (`+46+006/BI16/dsf#2` → dsf) or a rule name of the P2b contract (`+43+005/tile.dsf`). */
export function stepOfNode(nodeId, role) {
  if (typeof role === "string" && Object.hasOwn(ROLE_STEP, role)) return ROLE_STEP[role];
  if (!nodeId) return null;
  const last = nodeName(nodeId);
  if (Object.hasOwn(NODE_STEP, last)) return NODE_STEP[last];
  return Object.hasOwn(ROLE_STEP, last) ? ROLE_STEP[last] : null;
}

function emptyStep() {
  return { status: "pending", fraction: 0, message: "", wall_s: 0, nodes: {} };
}

/** A node's share of its step's work: 1 once it ended, its fraction while it runs, else 0 (the
 * engine's `progress._fraction`). */
function nodeFraction(n) {
  if (NODE_ENDED.has(n.status)) return 1;
  return n.status === "running" ? clamp01(n.fraction) : 0;
}

/**
 * Status, fraction and time of a step from its nodes, by the engine's rules (`osxp.api.jobs`
 * `stage_status` and `osxp.api.progress` `weighted_progress`, docs/specs/api.md 5.4): failed,
 * cancelled, skipped; done once every node ended (hit when all were hits); pending while none runs
 * and none did real work (hits alone do not start a step); running while one runs; waiting when
 * some did real work and the others have not started. The fraction weighs each node by the
 * `weight_s` the engine sends with it (0 for a hit, or a node skipped or cancelled before it
 * started), by count when nothing weighs. An older engine sends no weights: every node the same,
 * as it computed then.
 */
export function stepTotals(step) {
  const nodes = Object.values(step?.nodes || {});
  const statuses = nodes.map((n) => n.status);
  let status = "pending";
  if (statuses.includes("failed")) status = "failed";
  else if (statuses.includes("cancelled")) status = "cancelled";
  else if (statuses.includes("skipped")) status = "skipped";
  else if (nodes.length && statuses.every((x) => x === "done" || x === "hit")) status = statuses.every((x) => x === "hit") ? "hit" : "done";
  else if (statuses.includes("running")) status = "running";
  else if (statuses.includes("done")) status = "waiting";
  const weighted = nodes.length > 0 && nodes.every((n) => finite(n.weight) != null);
  let total = 0;
  let sum = 0;
  let count = 0;
  let wall = 0;
  for (const n of nodes) {
    const f = nodeFraction(n);
    const w = weighted && n.status !== "hit" ? Math.max(0, n.weight) : 0;
    total += w;
    sum += w * f;
    count += f;
    wall += finite(n.wall_s) || 0;
  }
  const fraction = !nodes.length ? 0 : total > 0 ? sum / total : count / nodes.length;
  return { status, fraction, wall_s: wall };
}

/** A stage of GET /api/jobs/{id} as the page keeps it: the engine's status and fraction, and its
 * nodes by id so that the events can move them. An older engine sends no weights and named the
 * statuses by older rules (a step with rows done and rows waiting was "pending"): the status then
 * follows the current rules from its nodes, as the page's own recompute does, so that a read and
 * the next event agree. */
function stepFromStage(stage) {
  const st = stage && typeof stage === "object" ? stage : {};
  const step = emptyStep();
  for (const n of Array.isArray(st.nodes) ? st.nodes : []) {
    if (!n || typeof n !== "object" || typeof n.node !== "string" || !tileOfNode(n.node)) continue;
    step.nodes[n.node] = {
      role: typeof n.role === "string" ? n.role : nodeName(n.node),
      status: typeof n.status === "string" ? n.status : "pending",
      fraction: clamp01(n.fraction),
      hit: typeof n.hit === "boolean" ? n.hit : null,
      wall_s: finite(n.wall_s) || 0,
      weight: finite(n.weight_s) ?? finite(n.weight),
    };
  }
  const totals = stepTotals(step);
  const rows = Object.values(step.nodes);
  const known = rows.length > 0;
  const weighted = known && rows.every((n) => n.weight != null);
  step.status = typeof st.status === "string" && (weighted || !known) ? st.status : known ? totals.status : "pending";
  step.fraction = typeof st.fraction === "number" ? clamp01(st.fraction) : known ? totals.fraction : Number(step.status === "done" || step.status === "hit");
  step.wall_s = finite(st.wall_s) ?? totals.wall_s;
  step.message = typeof st.message === "string" ? st.message : "";
  return step;
}

/** Step statuses whose bar shows a share of the step (the others show it full, or how far its
 * rows got). */
const PARTIAL_STEPS = new Set(["pending", "waiting", "running"]);

/** A step moved by an event: its status, fraction and time follow its nodes. What the page
 * recomputes between two reads of the engine never moves a bar back (without weights it cannot
 * weigh the rows as the engine does, and the rows no event touched keep the weights of the last
 * read); the next read may. */
function refreshStep(step) {
  const totals = stepTotals(step);
  const before = clamp01(step.fraction);
  step.status = totals.status;
  step.wall_s = totals.wall_s;
  step.fraction = PARTIAL_STEPS.has(totals.status) ? Math.max(before, totals.fraction) : totals.fraction;
  if (totals.status !== "running") step.message = "";
}

/**
 * The page's copy of a job from GET /api/jobs/{id}: the API speaks `stages` and `zl`, the page keeps
 * `steps` (with their nodes, so that events move them) and `zoom_level`. The engine's stages always
 * replace the steps: its state wins at every read. `seq` is the last journal entry the state
 * reflects (`last_seq`), `statsAt` when its stats arrived.
 */
export function normalizeJob(job, nowMs = Date.now()) {
  if (!job || typeof job !== "object") return job;
  if (job.zoom_level == null && job.zl != null) job.zoom_level = job.zl;
  job.tiles = (Array.isArray(job.tiles) ? job.tiles : []).map((tile) => {
    if (typeof tile === "string") return { tile, status: "pending", steps: Object.fromEntries(STEPS.map((s) => [s, emptyStep()])) };
    const out = tile && typeof tile === "object" ? tile : { tile: "?", status: "pending" };
    const source = out.stages && typeof out.stages === "object" ? out.stages : null;
    const steps = {};
    for (const s of STEPS) {
      if (source) steps[s] = stepFromStage(source[s]);
      else {
        // An answer of the P2b contract: steps without nodes.
        const old = out.steps?.[s];
        steps[s] = old && typeof old === "object" ? { ...emptyStep(), ...old, nodes: old.nodes && typeof old.nodes === "object" ? old.nodes : {} } : emptyStep();
      }
    }
    out.steps = steps;
    return out;
  });
  job.seq = finite(job.last_seq);
  job.statsAt = job.stats && typeof job.stats === "object" ? nowMs : null;
  job.errors = (Array.isArray(job.errors) ? job.errors : []).filter((e) => e && typeof e === "object");
  for (const e of job.errors) {
    if (e.step == null && STEPS.includes(e.stage)) e.step = e.stage;
  }
  return job;
}

/** The step an event moves (created when the job does not list it), and its node. */
function findStep(job, data) {
  const rawNode = data.node ?? data.node_id ?? null;
  const nodeId = typeof rawNode === "string" && tileOfNode(rawNode) ? rawNode : null;
  const tileName = typeof data.tile === "string" && TILE_RE.test(data.tile) ? data.tile : tileOfNode(nodeId);
  const stepName = STEPS.includes(data.stage) ? data.stage : STEPS.includes(data.step) ? data.step : stepOfNode(nodeId, data.role);
  if (!tileName || !stepName) return null;
  if (!Array.isArray(job.tiles)) job.tiles = [];
  let tile = job.tiles.find((x) => x.tile === tileName);
  if (!tile) {
    tile = { tile: tileName, status: "pending", steps: Object.fromEntries(STEPS.map((s) => [s, emptyStep()])) };
    job.tiles.push(tile);
  }
  if (!tile.steps) tile.steps = {};
  const step = tile.steps[stepName] || (tile.steps[stepName] = emptyStep());
  if (!step.nodes) step.nodes = {};
  // An event without a node (the P2b contract allowed it) moves one node standing for its step.
  const id = nodeId || `${tileName}/${stepName}`;
  if (!Object.hasOwn(step.nodes, id)) {
    step.nodes[id] = { role: typeof data.role === "string" ? data.role : nodeName(id), status: "pending", fraction: 0, hit: null, wall_s: 0, weight: finite(data.weight_s) };
  }
  return { tile, step, stepName, node: step.nodes[id], nodeId: id };
}

function uiAction(err) {
  const a = err.action;
  if (a === "retry" || a === "settings" || a === "none") return a;
  const code = err.code || "";
  if (/^(TEX_MISSING|IMG_TILE_MISSING|NET_|OSM_MIRROR|DEM_DOWNLOAD)/.test(code)) return "retry";
  if (/^(CFG_|XP_|DSF_GLOBAL_SCENERY)/.test(code)) return "settings";
  return "none";
}

/** One error card per failure: an entry replayed or read again does not add a second. */
function addJobError(job, err, where) {
  if (!err || typeof err !== "object" || !err.code) return false;
  if (!Array.isArray(job.errors)) job.errors = [];
  const tile = where.tile ?? err.context?.tile ?? null;
  const step = where.step ?? null;
  const node = where.node ?? null;
  const same = (e) => e.code === err.code && (e.tile ?? null) === tile && (e.step ?? e.stage ?? null) === step && (!node || !e.node || e.node === node);
  if (job.errors.some(same)) return false;
  job.errors.push({ ...err, severity: err.severity || "blocking", action: uiAction(err), tile, step, node });
  return true;
}

/** A line of the log: when it was written (epoch seconds; the engine's `ts` counts from the job's
 * creation), its level, its tile and its text. */
export function logEntry(job, data) {
  const ts = finite(data?.ts);
  const created = finite(job?.created_at) ?? finite(job?.started_at);
  const at = ts == null ? null : ts > 1e9 ? ts : created == null ? null : created + ts;
  return {
    at,
    ts,
    level: LOG_LEVELS.has(data?.level) ? data.level : "info",
    tile: typeof data?.tile === "string" ? data.tile : null,
    message: typeof data?.message === "string" ? data.message : "",
  };
}

/**
 * Apply one journal entry (the data of an SSE message) to the page's copy of a job; true when the
 * job changed. The fields are the engine's (`tile`, `stage`, `node`, `role`, ...), those of the P2b
 * contract (`step`, `node_id`) are read too. An entry the job already reflects (its `seq` is not
 * above the job's `seq`, the `last_seq` of the state read last) changes nothing, except a log line,
 * which that state does not carry.
 */
export function applyEvent(job, event, data, nowMs = Date.now()) {
  if (!job || !data || typeof data !== "object") return false;
  const seq = finite(data.seq);
  if (event === "log") {
    if (seq != null && finite(job.logSeq) != null && seq <= job.logSeq) return false;
    if (seq != null) job.logSeq = seq;
    if (!Array.isArray(job.log)) job.log = [];
    job.log.push(logEntry(job, data));
    if (job.log.length > LOG_MAX) job.log.splice(0, job.log.length - LOG_MAX);
    return true;
  }
  if (seq != null && finite(job.seq) != null && seq <= job.seq) return false;
  if (seq != null) job.seq = seq;
  switch (event) {
    case "started":
    case "progress":
    case "done":
    case "failed": {
      const f = findStep(job, data);
      if (!f) return event === "failed" ? addJobError(job, data.error, {}) : false;
      const { tile, step, node } = f;
      // Each node entry carries the weight the engine gives the node now (0 once it does not run).
      if (finite(data.weight_s) != null) node.weight = data.weight_s;
      // As the engine's Job._handle: a started node runs unless it ended with a result; a progress
      // moves its fraction; a done keeps what a first pass built when a second one finds it.
      if (event === "started") {
        if (node.status === "done" || node.status === "hit") return false;
        node.status = "running";
        node.fraction = 0;
        if (tile.status === "pending" || tile.status == null) tile.status = "running";
        if (job.status === "pending" || job.status === "queued") job.status = "running";
      } else if (event === "progress") {
        if (typeof data.fraction === "number") node.fraction = clamp01(data.fraction);
        if (typeof data.message === "string" && data.message && node.status === "running") {
          step.message = data.message;
          step.messageNode = f.nodeId;
        }
      } else if (event === "done") {
        if (!(data.hit && node.status === "done")) {
          node.status = data.hit ? "hit" : "done";
          node.hit = Boolean(data.hit);
          if (typeof data.wall_s === "number") node.wall_s = data.wall_s;
        }
        node.fraction = 1;
      } else {
        const err = data.error && typeof data.error === "object" ? data.error : {};
        const skipped = data.skipped === true || Boolean(data.cause || err.cause);
        node.status = skipped ? "skipped" : err.code === "SYS_CANCELLED" ? "cancelled" : "failed";
        if (typeof data.wall_s === "number") node.wall_s = data.wall_s;
        if (node.status === "failed") {
          tile.status = "failed";
          addJobError(job, err, { tile: tile.tile, step: f.stepName, node: f.nodeId });
        }
      }
      // The line of a node that ended says nothing of the step any more (no download rate of an OSM
      // download that is over while the elevation of the same step runs).
      if (event !== "started" && event !== "progress" && step.messageNode === f.nodeId) {
        step.message = "";
        step.messageNode = null;
      }
      refreshStep(step);
      return true;
    }
    case "stats": {
      // The engine sends {"event": "stats", "stats": {...}}; the P2b mock sent the numbers flat.
      const stats = { ...(data.stats && typeof data.stats === "object" ? data.stats : data) };
      delete stats.seq;
      delete stats.ts;
      delete stats.event;
      job.stats = stats;
      job.statsAt = nowMs;
      // The engine sends stats once a job runs: a job that waited in the queue has started.
      if (job.status === "queued" && finite(stats.elapsed_s) > 0) job.status = "running";
      return true;
    }
    case "finished":
      if (typeof data.status === "string") job.status = data.status;
      if (data.report && typeof data.report === "object") job.report = data.report;
      if (Array.isArray(data.decisions)) job.decisions = data.decisions;
      if (data.error && typeof data.error === "object") addJobError(job, data.error, {});
      for (const tile of job.tiles || []) {
        if (tile.status === "running") tile.status = job.status === "done" ? "done" : job.status;
      }
      return true;
    default:
      return false;
  }
}

/**
 * Whole-job progress, 0 to 1: the engine's `stats.progress` (every tile and step, weighted, never
 * decreasing). An older engine sends none: then the steps' fractions, each weighing its node count
 * (a step without nodes one; the install step of a build that does not install nothing).
 */
export function jobProgress(job) {
  if (!job) return 0;
  const p = finite(job.stats?.progress);
  if (p != null) return clamp01(p);
  if (job.status === "done") return 1;
  let weights = 0;
  let sum = 0;
  for (const tile of job.tiles || []) {
    for (const s of STEPS) {
      const step = tile.steps?.[s];
      if (!step) continue;
      const n = Object.keys(step.nodes || {}).length;
      if (!n && step.status === "skipped") continue;
      const w = Math.max(1, n);
      weights += w;
      sum += w * (NODE_ENDED.has(step.status) ? 1 : clamp01(step.fraction));
    }
  }
  return weights ? sum / weights : 0;
}

/**
 * Seconds since the job started. While it runs: the engine's `stats.elapsed_s` plus the time since
 * those stats arrived. It counts the whole job when the stats carry `progress` or `phase`; an older
 * engine restarted it at each phase, so the clock since `started_at` is used instead. Once the job
 * ended: `finished_at - started_at`, else the report's or the stats' figure.
 */
export function jobElapsed(job, nowMs = Date.now()) {
  if (!job) return null;
  const st = job.stats && typeof job.stats === "object" ? job.stats : {};
  const wholeJob = "progress" in st || "phase" in st;
  const started = finite(job.started_at);
  if (!jobActive(job)) {
    const finished = finite(job.finished_at);
    if (started != null && finished != null) return Math.max(0, finished - started);
    return finite(job.report?.elapsed_s) ?? finite(st.elapsed_s);
  }
  const elapsed = finite(st.elapsed_s);
  if (wholeJob && elapsed != null) {
    const since = finite(job.statsAt) != null ? Math.max(0, (nowMs - job.statsAt) / 1000) : 0;
    return elapsed + since;
  }
  if (started != null) return Math.max(0, nowMs / 1000 - started);
  return elapsed;
}

/** [low, high] seconds left for the whole job, or [null, null] while the engine does not know. */
export function etaRange(stats) {
  if (!stats || typeof stats !== "object") return [null, null];
  const lo = finite(stats.eta_low_s);
  const hi = finite(stats.eta_high_s);
  if (lo != null || hi != null) return [lo ?? hi, hi ?? lo];
  const eta = finite(stats.eta_s);
  return eta == null ? [null, null] : [eta * 0.8, eta * 1.5];
}

/** Tiles whose map data (their `osm` node) is downloaded before the build: every tile with such a
 * node that is not a hit. */
export function dataPhaseTiles(job) {
  let n = 0;
  for (const tile of job?.tiles || []) {
    const nodes = Object.values(tile.steps?.data?.nodes || {});
    if (nodes.some((node) => node.role === "osm" && node.status !== "hit")) n += 1;
  }
  return n;
}

/** What the log view does to show `log` when it shows `shown` (the same line objects): drop `drop`
 * lines at the top (the log keeps its last lines only), then append `add`. */
export function logDelta(shown, log) {
  const last = shown.length ? shown[shown.length - 1] : null;
  const i = last == null ? -1 : log.lastIndexOf(last);
  if (i === -1) return { drop: shown.length, add: log.slice() };
  return { drop: Math.max(0, shown.length - (i + 1)), add: log.slice(i + 1) };
}

/**
 * The size and the X-Plane count of a finished job's report, each tile once: the report's tile
 * when it says (`pack_bytes`, `installed`), else the job's `pack` decision for that tile. The engine
 * gives both for the same tiles, and adding them counted every installed tile twice.
 */
export function reportTotals(job) {
  const rep = job?.report && typeof job.report === "object" ? job.report : {};
  const reportTiles = Array.isArray(rep.tiles) ? rep.tiles.filter((x) => x && typeof x === "object") : [];
  const packs = new Map();
  const pack = (name) => {
    if (!packs.has(name)) packs.set(name, { bytes: null, installed: null });
    return packs.get(name);
  };
  for (const tile of reportTiles) {
    const p = pack(String(tile.tile ?? tile.pack_dir ?? ""));
    if (finite(tile.pack_bytes) != null) p.bytes = tile.pack_bytes;
    if (typeof tile.installed === "boolean") p.installed = tile.installed;
  }
  for (const d of Array.isArray(job?.decisions) ? job.decisions : []) {
    if (!d || d.kind !== "pack") continue;
    const p = pack(String(d.tile ?? d.path ?? ""));
    if (p.bytes == null && finite(d.bytes) != null) p.bytes = d.bytes;
    if (p.installed == null && typeof d.installed === "boolean") p.installed = d.installed;
  }
  let bytes = null;
  let installed = 0;
  for (const p of packs.values()) {
    if (p.bytes != null) bytes = (bytes || 0) + p.bytes;
    if (p.installed) installed += 1;
  }
  return { tiles: reportTiles.length || (job?.tiles || []).length, installed, bytes };
}

/**
 * Counts of the report's Decisions list by code. A tile's textures decision gives its texture
 * counts, missing ones included; a decision with a code (`degraded`) counts that code for its tile.
 * An error adds its code only for a tile no decision counted it for (the textures decision already
 * counts the textures a TEX_MISSING error is about: both made "Missing textures" count twice).
 */
export function decisionCounts(job) {
  const counts = new Map();
  const add = (code, n) => {
    if (finite(n) != null) counts.set(code, (counts.get(code) || 0) + n);
  };
  const counted = new Set();
  const key = (tile, code) => `${tile ?? ""}|${code}`;
  for (const d of Array.isArray(job?.decisions) ? job.decisions : []) {
    if (!d || typeof d !== "object") continue;
    if (d.kind === "textures") {
      add("textures_total", d.total);
      add("textures_built", d.built);
      add("textures_hits", d.hits);
      add("IMG_TILE_PARENT_FALLBACK", d.parent_fallback);
      add("IMG_TILE_PLACEHOLDER", d.placeholders);
      add("TEX_MISSING", d.missing);
      // Listed only when a transient failure sent chunks to the second pass (textures.py).
      if (finite(d.second_pass) && d.second_pass > 0) {
        add("textures_second_pass", d.second_pass);
        add("textures_recovered", d.recovered ?? 0);
      }
      for (const code of ["IMG_TILE_PARENT_FALLBACK", "IMG_TILE_PLACEHOLDER", "TEX_MISSING"]) counted.add(key(d.tile, code));
    } else if (d.code && d.kind !== "pack") {
      add(d.code, d.count ?? 1);
      counted.add(key(d.tile, d.code));
    }
  }
  for (const e of Array.isArray(job?.errors) ? job.errors : []) {
    if (!e || !e.code || counted.has(key(e.tile, e.code))) continue;
    add(e.code, e.code === "TEX_MISSING" ? finite(e.context?.count) ?? e.count ?? 1 : e.count ?? 1);
  }
  return counts;
}

// ------------------------------------------------------------------ SSE

/** The journal entries the page applies, by SSE event name. */
const JOB_EVENTS = ["started", "progress", "done", "failed", "stats", "log", "finished"];

function subscribe(jobId, handlers) {
  if (MOCK) return mockSubscribe(jobId, handlers);
  const es = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
  for (const [name, fn] of Object.entries(handlers)) {
    es.addEventListener(name, (ev) => {
      let data = null;
      try {
        data = ev.data ? JSON.parse(ev.data) : {};
      } catch (_e) {
        data = { message: ev.data };
      }
      fn(data);
    });
  }
  return Promise.resolve(es);
}

/** Replay the journal of a finished job to show its log (the stream ends by itself then). */
async function loadJournalLog(jobId) {
  if (MOCK) return;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/events`, {
      headers: { Accept: "text/event-stream" },
    });
    if (!res.ok) return;
    const text = await res.text();
    const job = state.job;
    if (state.jobId !== jobId || !job) return;
    const lines = [];
    let logSeq = null;
    for (const block of text.split("\n\n")) {
      let name = null;
      let payload = null;
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) payload = line.slice(5).trim();
      }
      if (name !== "log" || !payload) continue;
      try {
        const d = JSON.parse(payload);
        lines.push(logEntry(job, d));
        if (typeof d.seq === "number") logSeq = d.seq;
      } catch (_e) {
        // a truncated journal line: skip it
      }
    }
    if (lines.length) {
      job.log = lines.slice(-LOG_MAX);
      job.logSeq = logSeq;
      scheduleRenderJob();
    }
  } catch (_e) {
    // the journal may have been swept; the rest of the job still shows
  }
}

/** The watch under way: a later watchJob() replaces it, and the answers of the former are dropped. */
let watchToken = null;

async function watchJob(jobId) {
  if (state.source) {
    state.source.close();
    state.source = null;
  }
  const token = {};
  watchToken = token;
  state.jobId = jobId;
  state.job = null;
  renderJobList(); // the list marks the job shown: it was drawn before state.jobId changed
  let job;
  try {
    job = normalizeJob(await api("GET", `/api/jobs/${encodeURIComponent(jobId)}`));
  } catch (err) {
    if (watchToken === token) {
      toast(errorMessage(err), "fail");
      renderJob();
    }
    return;
  }
  if (watchToken !== token) return;
  state.job = job;
  renderJob();
  if (!jobActive(job)) {
    await loadJournalLog(jobId);
    return;
  }

  // The engine's state wins: read again after a node ends. One read at a time; the entries that
  // arrive meanwhile are applied again to its answer (those it already reflects change nothing).
  let pending = null;
  let again = false;
  const refresh = async () => {
    if (pending) {
      again = true;
      return;
    }
    pending = [];
    try {
      const fresh = normalizeJob(await api("GET", `/api/jobs/${encodeURIComponent(jobId)}`));
      if (watchToken === token && state.job) {
        fresh.log = state.job.log;
        fresh.logSeq = state.job.logSeq;
        for (const entry of pending) if (entry.event !== "log") applyEvent(fresh, entry.event, entry.data);
        const statusChanged = fresh.status !== state.job.status;
        state.job = fresh;
        scheduleRenderJob();
        if (statusChanged) refreshJobList();
      }
    } catch (_e) {
      // keep the local state
    }
    pending = null;
    if (again && watchToken === token) {
      again = false;
      refresh();
    }
  };
  const handler = (event) => (data) => {
    if (watchToken !== token || !state.job) return;
    if (pending) pending.push({ event, data });
    const before = state.job.status;
    const changed = applyEvent(state.job, event, data);
    if (changed) scheduleRenderJob();
    // A job that waited in the queue started: the list, step 4 and the map follow.
    if (event !== "finished" && state.job.status !== before) refreshJobList();
    if (event === "finished") {
      // Closed even when a read of the job already had it: the stream ends after `finished`, and an
      // EventSource left open would reconnect every two seconds.
      if (state.source) {
        state.source.close();
        state.source = null;
      }
      refresh();
      refreshJobList();
      loadLibrary(); // the tiles built are in the Library, and green on the map once installed
      loadStatus(); // and the status bar's sizes of the store and of the downloaded images
    } else if (changed && (event === "done" || event === "failed")) {
      refresh();
      // a tile just went into X-Plane: the map's green follows the build, tile by tile
      if (event === "done" && data?.role === "install") loadLibrarySoon();
    }
  };
  const source = await subscribe(jobId, Object.fromEntries(JOB_EVENTS.map((name) => [name, handler(name)])));
  if (watchToken !== token || !jobActive(state.job)) {
    source.close();
    return;
  }
  state.source = source;
}

// ------------------------------------------------------------------ navigation

const SCREENS = ["plan", "works", "library", "settings"];

function showScreen(name, arg) {
  if (!SCREENS.includes(name)) name = "plan";
  if (state.screen === "settings" && name !== "settings" && state.settingsDraft && !sameValue(state.settingsDraft, state.settings)) {
    // an answer changed without Save changes nothing yet: say it rather than let the Plan look stale
    toast(t("settings.left_unsaved"));
  }
  state.screen = name;
  for (const s of SCREENS) $(`screen-${s}`).hidden = s !== name;
  for (const btn of $("nav").querySelectorAll(".nav-btn")) {
    if (btn.dataset.screen === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  const hash = arg ? `#${name}/${arg}` : `#${name}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  if (name === "works") loadWorks(arg);
  else followBuildUnderWay();
  if (name === "library") {
    loadLibrary();
    refreshJobList(); // its buttons wait for the builds under way or waiting
  }
  if (name === "settings") renderSettings();
  if (name === "plan") {
    renderPlanSettings();
    if (state.tiles.length) planChanged(); // the free disk space may have changed meanwhile
    planMap?.show();
  }
}

function routeFromHash() {
  const m = location.hash.match(/^#(\w+)(?:\/(.+))?$/);
  showScreen(m ? m[1] : "plan", m ? m[2] : undefined);
}

// ------------------------------------------------------------------ status bar

/** The engine API this page needs (orthostudio.api.app.API_LEVEL); a test keeps the two equal. */
const PAGE_API_LEVEL = 16;

async function loadStatus() {
  try {
    state.status = await api("GET", "/api/status");
  } catch (err) {
    $("status-xplane").textContent = errorMessage(err);
    return;
  }
  state.engineOutdated = (Number(state.status?.api_level) || 1) < PAGE_API_LEVEL;
  renderEngineBanner();
  renderStatus();
}

/** An engine older than the page (started before an update) cannot answer the new routes: say
 * how to fix it instead of letting "Not Found" and a blank map speak. */
function renderEngineBanner() {
  let banner = $("engine-outdated");
  if (!state.engineOutdated) {
    banner?.remove();
    return;
  }
  if (!banner) {
    banner = h("p", { id: "engine-outdated", class: "engine-outdated", role: "alert" });
    $("main").prepend(banner);
  }
  banner.textContent = t("app.engine_outdated");
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  $("brand-version").textContent = s.version ? `v${s.version}` : "";
  const xp = clear($("status-xplane"));
  const x = s.xplane || {};
  xp.append(`${t("status.xplane")}: `);
  if (x.detected && x.path) {
    xp.append(h("span", { class: "mono", title: x.path }, x.path.length > 40 ? `…${x.path.slice(-38)}` : x.path));
    if (x.running) xp.append(" ", pill(t("status.xplane_running"), "warn"));
  } else xp.append(pill(t("status.xplane_missing"), "fail"));
  renderPlanXplane();

  const doc = clear($("status-doctor"));
  const checks = Array.isArray(s.doctor) ? s.doctor : s.doctor?.checks || [];
  const n = { ok: 0, warn: 0, fail: 0 };
  for (const c of checks) if (c.status in n) n[c.status] += 1;
  const summary = h("summary", null, `${t("status.doctor")}: `, pill(t("status.doctor_ok", { ok: n.ok }), "ok"));
  if (n.warn) summary.append(" ", pill(t("status.doctor_warn", { warn: n.warn }), "warn"));
  if (n.fail) summary.append(" ", pill(t("status.doctor_fail", { fail: n.fail }), "fail"));
  const list = h("ul", null, checks.map((c) => h("li", null, pill(c.status, c.status === "ok" ? "ok" : c.status === "fail" ? "fail" : c.status === "warn" ? "warn" : "cancelled"), h("span", { class: "name" }, c.name), h("span", null, c.summary || ""))));
  doc.append(h("details", null, summary, h("div", { class: "popover" }, list)));

  $("quit-btn").hidden = !s.can_quit;
  $("status-store").textContent = `${t("status.store")}: ${fmtBytes(s.store_bytes)} · ${t("status.chunks")}: ${fmtBytes(s.chunks_bytes)}`;
  $("status-library").textContent = t("status.library", { n: fmtInt(s.library_count ?? 0) });
  // Where the tiles and the downloads go: OrthoStudio XP's folder, or the data folder chosen in
  // Settings, whose disk may not be plugged in.
  const place = clear($("status-home"));
  const data = s.data_dir;
  const where = data?.chosen && data.path ? data.path : s.home || "";
  place.append(where);
  place.title = data?.chosen ? t("status.data_dir", { path: where }) : "";
  if (data?.present === false) place.append(" ", pill(t("status.data_missing"), "fail"));
}

// ------------------------------------------------------------------ the file manager, quitting

/** "Show in Finder", "Show in File Explorer", "Open the folder": the platform the engine runs on. */
export function revealLabel(platform) {
  if (platform === "mac") return t("reveal.mac");
  if (platform === "win") return t("reveal.win");
  return t("reveal.lin");
}

/** Show a folder (or a file) of OrthoStudio XP, of X-Plane or of a tile in the file manager. */
async function revealPath(path) {
  if (!path) return;
  try {
    await api("POST", "/api/reveal", { path });
  } catch (err) {
    toast(errorMessage(err), "fail");
  }
}

function confirmQuit(active, queued = 0) {
  const dialog = $("quit-confirm");
  if (dialog.open) return Promise.resolve(false);
  const lines = [t("quit.text")];
  if (active) lines.push(t("quit.running"));
  if (queued > 0) lines.push(t("quit.queued", { n: queued }));
  clear($("quit-confirm-text")).append(...lines.map((line) => h("p", null, line)));
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

/** "Quit": asks (saying that a running build stops), then stops the engine and says so. */
async function quitOsxp() {
  let active = state.status?.active_job || null;
  let queued = 0;
  try {
    active = (await api("GET", "/api/status"))?.active_job || null;
    state.jobs = await api("GET", "/api/jobs");
    queued = state.jobs.filter((j) => j.status === "queued").length;
  } catch (_e) {
    // the engine may already be gone: the question stays the same
  }
  if (!(await confirmQuit(active, queued))) return;
  try {
    await api("POST", "/api/quit", { force: Boolean(active) });
  } catch (err) {
    toast(errorMessage(err), "fail");
    return;
  }
  clearInterval(jobsTimer);
  $("stopped").hidden = false;
}

// ------------------------------------------------------------------ presence

/** The app started from its icon stops a while after its last page closed, unless a build runs or
 * waits (orthostudio/api/presence.py): nothing shows it once the page is gone, and a user on
 * Windows had to end it in the Task Manager (2026-09-17). An open page says so every 30 s, and at
 * once when it shows again; a background tab's timers slow down to about once a minute, which the
 * engine allows for. */
const PRESENCE_MS = 30000;
const PRESENCE_RETRY_MS = 3000;
let presenceTimer = 0;

async function sayPresent(retry = true) {
  if (MOCK || !$("stopped").hidden) return;
  try {
    await api("POST", "/api/presence", {});
  } catch (err) {
    if (err instanceof ApiError) return; // it answered: an older engine's 404 is the banner's business
    // no answer: the engine may have stopped while the page could not speak (a tab put to sleep
    // in the background); asked once more before saying so
    if (retry) setTimeout(() => sayPresent(false), PRESENCE_RETRY_MS);
    else showEngineGone();
  }
}

/** The engine no longer answers: the page says it stopped, as after "Quit", and how to start it. */
function showEngineGone() {
  clearInterval(jobsTimer);
  clearInterval(presenceTimer);
  const text = $("stopped").querySelector("p");
  text.dataset.i18n = "quit.gone_text";
  text.textContent = t("quit.gone_text");
  $("stopped").hidden = false;
}

function startPresence() {
  if (MOCK) return;
  sayPresent();
  clearInterval(presenceTimer);
  presenceTimer = setInterval(() => sayPresent(), PRESENCE_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") sayPresent();
  });
}

// ------------------------------------------------------------------ Plan: tiles

function tileLat(name) {
  const m = name.match(/^([+-]\d{2})([+-]\d{3})$/);
  return m ? Number(m[1]) : 0;
}

/** Add tiles to the selection; returns those left out because they are in a build, under way or
 * waiting (a user asked that such a tile cannot be chosen again: it would be built twice). */
function addTiles(names) {
  const building = tilesInBuilds(activeJobs());
  const skipped = [];
  for (const n of names) {
    if (state.tiles.includes(n) || skipped.includes(n)) continue;
    if (building.has(n)) skipped.push(n);
    else state.tiles.push(n);
  }
  renderTiles();
  renderZlOptions();
  planChanged();
  return skipped;
}

/** The tiles `addTiles` left out, said under the estimate. */
function sayTilesInBuild(skipped) {
  if (skipped.length) showPlanError(t("plan.tiles_in_build", { tiles: skipped.join(" ") }));
}

function removeTile(name) {
  state.tiles = state.tiles.filter((x) => x !== name);
  renderTiles();
  renderZlOptions();
  planChanged();
}

/** Step 1's trash (user request, 2026-09-14): the whole selection at once, without asking, unlike
 * the zones' and the jobs' trash. Nothing on the disk changes: the tiles are only unselected, the
 * built ones stay installed, and the selection was not saved anyway. */
function clearTiles() {
  const n = state.tiles.length;
  if (!n) return;
  state.tiles = [];
  renderTiles();
  renderZlOptions();
  planChanged();
  $("tiles-panel").focus({ preventScroll: true }); // the trash is hidden now
  toast(t("plan.tiles_cleared", { n }));
}

/** A click on the map: the chips and the map show one selection. A tile in a build, under way or
 * waiting, is not chosen: a toast says why. */
function toggleTile(name) {
  if (state.tiles.includes(name)) removeTile(name);
  else if (tilesInBuilds(activeJobs()).has(name)) toast(t("plan.tile_in_build", { tile: name }));
  else addTiles([name]);
}

function renderTiles() {
  const box = clear($("tile-chips"));
  for (const name of state.tiles) {
    box.append(
      h("span", { class: "chip" }, name, h("button", { type: "button", "aria-label": t("plan.remove_tile", { tile: name }), onclick: () => removeTile(name) }, "×")),
    );
  }
  $("tile-count").textContent = state.tiles.length ? t("plan.tile_count", { n: state.tiles.length }) : t("plan.tile_none");
  renderSourceOptions(); // the countries' sources that cover the tiles come first
  $("tiles-clear").hidden = !state.tiles.length;
  planMap?.tilesChanged();
}

function addTilesFromText() {
  const input = $("tiles-text");
  const raw = input.value.split(/[\s,;]+/).filter(Boolean);
  const bad = raw.filter((x) => !TILE_RE.test(x));
  if (bad.length) {
    showPlanError(t("plan.tile_invalid", { value: bad.join(" ") }));
    return;
  }
  showPlanError(null);
  sayTilesInBuild(addTiles(raw));
  input.value = "";
}

function addTileFromLatLon() {
  const lat = Number($("lat-input").value);
  const lon = Number($("lon-input").value);
  if ($("lat-input").value === "" || $("lon-input").value === "" || Number.isNaN(lat) || Number.isNaN(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    showPlanError(t("plan.latlon_invalid"));
    return;
  }
  showPlanError(null);
  sayTilesInBuild(addTiles([tileName(lat, lon)]));
}

/** Every 1° cell intersecting the bounding box of the circle (spec 2.1). */
export function tilesAround(lat, lon, radiusKm) {
  const dlat = radiusKm / 111.2;
  const dlon = radiusKm / (111.2 * Math.max(0.05, Math.cos((lat * Math.PI) / 180)));
  const out = [];
  for (let la = Math.floor(lat - dlat); la <= Math.floor(lat + dlat); la += 1) {
    for (let lo = Math.floor(lon - dlon); lo <= Math.floor(lon + dlon); lo += 1) {
      if (la < -90 || la > 89) continue;
      const wrapped = ((((lo + 180) % 360) + 360) % 360) - 180;
      out.push(tileName(la, wrapped));
    }
  }
  return out;
}

/** Step 3's error: `message`, a sentence of the page's own, or `err`, what the engine answered. */
/** Step 3's message line: `from` "estimate" for a refusal of the live estimate, which the next
 * estimate that succeeds takes away (the others stay until the user acts again). */
function showPlanError(message, err, from = null) {
  state.planError = message ? { message, from } : err ? { err, from } : null;
  renderPlanError();
}

/**
 * An engine error is a card, as in Works: the page's words for its code, the remedy, and a button
 * to Settings when the fix is there (a user on a computer without X-Plane read "Global Scenery is
 * not installed in <not detected>" and nothing on what to do). Anything else is a line.
 */
function renderPlanError() {
  const box = clear($("plan-error"));
  const shown = state.planError;
  box.hidden = !shown;
  if (!shown) return;
  const d = shown.err ? errorDetail(shown.err) : null;
  if (d && d.code) box.append(errorCard({ ...d, severity: d.severity || "blocking", action: uiAction(d) }));
  else box.append(h("p", { class: "error-inline" }, shown.message || errorMessage(shown.err)));
}

/** Codes whose fix is the X-Plane 12 folder: their Settings button goes straight to its field. */
const XPLANE_FOLDER_CODES = new Set(["XP_DIR_NOT_FOUND", "XP_GLOBAL_SCENERY_NOT_FOUND", "DSF_GLOBAL_SCENERY_MISSING"]);

/** Codes whose fix is the data folder: their Settings button goes straight to its field. */
const DATA_FOLDER_CODES = new Set(["CFG_DATA_DIR_MISSING", "CFG_DATA_DIR_INVALID"]);

function openSettingsFor(err) {
  if (XPLANE_FOLDER_CODES.has(err?.code)) chooseXplaneFolder();
  else if (DATA_FOLDER_CODES.has(err?.code)) focusSettingsField("q-data-dir");
  else showScreen("settings");
}

/** Settings, the focus in the X-Plane 12 folder field (its first question). */
function chooseXplaneFolder() {
  focusSettingsField("q-xplane-dir");
}

function focusSettingsField(id) {
  showScreen("settings");
  const field = $(id);
  if (!field) return;
  field.focus();
  field.scrollIntoView?.({ block: "center" });
}

/**
 * What step 3 says before any estimate when OrthoStudio XP knows no X-Plane 12 folder (the
 * status has none): the folder saved in Settings is not found, or none is on this computer.
 * Null when X-Plane is known, or while the status is not read.
 */
export function xplaneNotice(status, settings) {
  const x = status?.xplane;
  if (!x || (x.detected && x.path)) return null;
  const saved = settings?.essential?.xplane_dir;
  return saved ? t("plan.xplane_saved_missing", { path: saved }) : t("plan.xplane_missing");
}

function renderPlanXplane() {
  const text = xplaneNotice(state.status, state.settings);
  $("plan-xplane").hidden = !text;
  $("plan-xplane-text").textContent = text || "";
}

// -- ICAO combobox

let icaoTimer = 0;
let icaoResults = [];
let icaoActive = -1;

function renderIcaoList() {
  const list = clear($("icao-list"));
  const input = $("icao-input");
  list.hidden = icaoResults.length === 0;
  input.setAttribute("aria-expanded", String(!list.hidden));
  icaoResults.forEach((a, i) => {
    const li = h("li", { role: "option", id: `icao-opt-${i}`, "aria-selected": String(i === icaoActive), onmousedown: (ev) => { ev.preventDefault(); pickAirport(a); } },
      h("span", { class: "icao" }, a.icao), h("span", { class: "name" }, a.name || ""));
    list.append(li);
  });
  if (icaoActive >= 0) input.setAttribute("aria-activedescendant", `icao-opt-${icaoActive}`);
  else input.removeAttribute("aria-activedescendant");
}

function pickAirport(a) {
  state.airport = a;
  $("icao-input").value = a.icao;
  icaoResults = [];
  icaoActive = -1;
  renderIcaoList();
}

function onIcaoInput() {
  const q = $("icao-input").value.trim();
  state.airport = null;
  clearTimeout(icaoTimer);
  if (q.length < 2) {
    icaoResults = [];
    renderIcaoList();
    return;
  }
  icaoTimer = setTimeout(async () => {
    try {
      icaoResults = await api("GET", `/api/airports?q=${encodeURIComponent(q)}`);
    } catch (_e) {
      icaoResults = [];
    }
    icaoActive = icaoResults.length ? 0 : -1;
    renderIcaoList();
  }, 200);
}

function onIcaoKey(ev) {
  if (ev.key === "ArrowDown" && icaoResults.length) {
    icaoActive = (icaoActive + 1) % icaoResults.length;
    renderIcaoList();
    ev.preventDefault();
  } else if (ev.key === "ArrowUp" && icaoResults.length) {
    icaoActive = (icaoActive - 1 + icaoResults.length) % icaoResults.length;
    renderIcaoList();
    ev.preventDefault();
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    if (icaoActive >= 0 && icaoResults[icaoActive]) pickAirport(icaoResults[icaoActive]);
    else addTilesFromIcao();
  } else if (ev.key === "Escape") {
    icaoResults = [];
    renderIcaoList();
  }
}

async function addTilesFromIcao() {
  const code = $("icao-input").value.trim().toUpperCase();
  if (!code) return;
  let airport = state.airport && state.airport.icao === code ? state.airport : null;
  if (!airport) {
    try {
      airport = await api("GET", `/api/airports/${encodeURIComponent(code)}`);
    } catch (_e) {
      showPlanError(t("plan.icao_unknown", { icao: code }));
      return;
    }
  }
  showPlanError(null);
  const r = Math.max(1, Number($("radius-input").value) || 15);
  const names = tilesAround(airport.lat, airport.lon, r);
  sayTilesInBuild(addTiles(names));
  toast(t("plan.icao_added", { icao: airport.icao, name: airport.name || "", n: names.length, r }));
}

// ------------------------------------------------------------------ Plan: provider, zoom

function currentProvider() {
  return state.providers.find((p) => p.code === $("provider-select").value) || null;
}

/** Step 1's source list, its source selected: `wanted` (the saved one), else the one selected
 * stays, else the saved one. */
function renderProviders(wanted = null) {
  renderSourceOptions(wanted);
  renderProviderAttribution();
  renderZlOptions();
  planMap?.planChanged();
}

/**
 * The options of step 1's list, grouped since a user found the countries' sources mixed with the
 * others (2026-09-14): the whole world first (Bing Maps, then Esri), then the countries' sources
 * that cover every tile chosen, the user's own, and the other countries' ("Netherlands · PDOK
 * 2020"). Drawn again when the tiles change.
 */
function renderSourceOptions(wanted = null) {
  const sel = $("provider-select");
  const current = wanted || sel.value || state.settings?.essential?.provider || "BI";
  const groups = sourceGroups(state.providers, state.tiles, current);
  clear(sel);
  for (const key of ["world", "tiles", "mine", "other"]) {
    if (!groups[key].length) continue;
    const options = groups[key].map((p) => {
      const dead = p.alive === false;
      return h("option", { value: p.code, disabled: dead && p.code !== current }, dead ? `${sourceLabel(p)} · ${t("plan.provider_dead")}` : sourceLabel(p));
    });
    sel.append(h("optgroup", { label: sourceGroupTitle(key, state.tiles.length > 0) }, ...options));
  }
  if (state.providers.some((p) => p.code === current)) sel.value = current;
  renderSourceCoverage();
}

/** Under step 1's list: the tiles its source does not cover, before the estimate refuses them. */
function renderSourceCoverage() {
  const p = currentProvider();
  const missing = tilesNotCovered(p, state.tiles);
  const note = $("provider-coverage");
  note.hidden = !missing.length;
  setText(note, missing.length ? t("plan.source_coverage", { source: sourceLabel(p), country: countryName(p.extent), tiles: missing.join(" ") }) : "");
}

function renderProviderAttribution() {
  const p = currentProvider();
  $("provider-attribution").textContent = p ? p.attribution || "" : "";
}

// ------------------------------------------------------------------ Plan: the user's own sources

/** "My sources…" under step 1's list (user request, 2026-09-14): sources OrthoStudio XP does not
 * ship, which the user adds at their own risk and tries on one tile first. */
function openSources() {
  const dialog = $("sources-dialog");
  if (dialog.open) return;
  renderSourcesList();
  setSourceResult("");
  dialog.showModal();
}

function setSourceResult(text, fail = false) {
  const el = $("source-result");
  setText(el, text);
  el.classList.toggle("is-fail", fail);
}

function renderSourcesList() {
  const mine = state.providers.filter((p) => p.custom);
  const ul = clear($("sources-list"));
  if (!mine.length) {
    ul.append(h("li", { class: "placeholder" }, t("sources.none")));
    return;
  }
  for (const p of mine) {
    ul.append(h("li", { class: "source-row" },
      h("div", { class: "source-text" }, h("b", null, p.name || p.code), " ", h("code", null, p.code), h("div", { class: "source-url" }, p.url_template || "")),
      h("button", { type: "button", class: "btn btn-small btn-danger", onclick: () => removeSource(p) }, t("sources.remove"))));
  }
}

/** Where a source is tried: the middle of the first tile chosen, else of the map, else Geneva. */
function sourceTryPoint() {
  const m = /^([+-]\d{2})([+-]\d{3})$/.exec(state.tiles[0] || "");
  if (m) return { lat: Number(m[1]) + 0.5, lon: Number(m[2]) + 0.5 };
  const c = planMap?.mapCenter();
  if (!c) return { lat: 46.2, lon: 6.1 };
  return { lat: Math.max(-85, Math.min(85, c.lat)), lon: ((((c.lon + 180) % 360) + 360) % 360) - 180 };
}

/** The dialog's fields; null, and the reason said, when the address cannot be one. */
function sourceForm(needName) {
  const form = { name: $("source-name").value.trim(), url_template: $("source-url").value.trim(), max_zl: Math.min(22, Math.max(1, Math.round(Number($("source-zl").value)) || 19)) };
  const problem = sourceAddressProblem(form.url_template);
  if (needName && !form.name) setSourceResult(t("sources.fill"), true);
  else if (problem === "scheme") setSourceResult(t("sources.bad_scheme"), true);
  else if (problem === "place") setSourceResult(t("sources.bad_place"), true);
  else return form;
  return null;
}

async function trySource() {
  const form = sourceForm(false);
  if (!form) return;
  setSourceResult(t("sources.trying"));
  try {
    const r = await api("POST", "/api/sources/test", { url_template: form.url_template, max_zl: form.max_zl, ...sourceTryPoint() });
    if (r.ok) setSourceResult(t("sources.try_ok", { kind: String(r.image).toUpperCase(), size: fmtKB(r.bytes) }));
    else setSourceResult(t("sources.try_failed", { status: r.status || r.error || "—" }), true);
  } catch (err) {
    setSourceResult(errorMessage(err), true);
  }
}

async function addSource(ev) {
  ev.preventDefault();
  const form = sourceForm(true);
  if (!form) return;
  try {
    const added = await api("POST", "/api/sources", form);
    $("sources-form").reset();
    await reloadProviders(added.code);
    renderSourcesList();
    setSourceResult(t("sources.added", { name: added.name }));
  } catch (err) {
    setSourceResult(errorMessage(err), true);
  }
}

async function removeSource(p) {
  try {
    const r = await api("DELETE", `/api/sources/${encodeURIComponent(p.code)}`);
    if (r.settings_provider) {
      state.settings = await api("GET", "/api/settings");
      state.settingsDraft = structuredClone(state.settings);
    }
    await reloadProviders(r.settings_provider);
    renderSourcesList();
    setSourceResult(t("sources.removed", { name: p.name || p.code }));
  } catch (err) {
    const d = errorDetail(err);
    if (d?.code === "SYS_SOURCE_IN_USE") setSourceResult(t("sources.in_use", { zones: (d.context?.zones || []).join(", ") }), true);
    else setSourceResult(errorMessage(err), true);
  }
}

/** The sources read again after one was added or removed: every list follows, `select` chosen in
 * step 1 when given. */
async function reloadProviders(select = null) {
  const rows = await api("GET", "/api/providers");
  state.providers = Array.isArray(rows) ? rows : rows.providers || [];
  renderProviders(select);
  if (state.screen === "settings") renderSettings();
  planChanged();
}

/** Detail levels in plain words, sized at the first tile's latitude (else the map centre's). */
function renderZlOptions() {
  const sel = $("zl-select");
  const p = currentProvider();
  const maxZl = p ? p.max_zl : 19;
  const lat = state.tiles.length ? tileLat(state.tiles[0]) + 0.5 : planMap?.mapLatitude() ?? 45;
  const wanted = Number(sel.value) || state.settings?.essential?.zoom_level || 16;
  clear(sel);
  for (let zl = 12; zl <= Math.min(19, maxZl); zl += 1) {
    sel.append(h("option", { value: zl }, detailLabel(zl, lat)));
  }
  sel.value = String(Math.min(wanted, Math.min(19, maxZl)));
  $("zl-help").textContent = t("plan.zl_help", { lat: fmtNum(lat, 1) });
}

// ------------------------------------------------------------------ Plan: estimate + build

/** The page sends what it shows: the tiles, and the zones of the list that reach them (map-zones.md 5).
 * Null, with the reason shown, when the zones could not be loaded even on a second try. */
async function planRequest() {
  const zones = planMap ? await planMap.zonesForRequest([...state.tiles]) : [];
  if (!zones) {
    showPlanError(t("plan.zones_unavailable"));
    return null;
  }
  return {
    tiles: [...state.tiles],
    provider: $("provider-select").value,
    zoom_level: Number($("zl-select").value),
    zones,
  };
}

async function persistSettings() {
  if (!state.settings) return;
  state.settings.essential.provider = $("provider-select").value;
  state.settings.essential.zoom_level = Number($("zl-select").value);
  state.settings = await api("PUT", "/api/settings", state.settings);
  state.settingsDraft = structuredClone(state.settings);
}

/** How long the Plan waits after a change before working out the cost: a few clicks on the map
 * make one estimate (one takes 0.1 to 0.5 s for one to six tiles on an M4 Pro). */
const ESTIMATE_DELAY_MS = 400;
let estimateTimer = 0;
let estimateSeq = 0;
let estimateRun = null;

/**
 * The Plan changed (its tiles, source, level, zones or settings): its cost is worked out again by
 * itself a moment later, since a user found pressing Estimate before Build tedious (2026-09-14).
 * The figures of before stay, dimmed, until the new ones arrive.
 */
function planChanged() {
  clearTimeout(estimateTimer);
  estimateTimer = 0;
  estimateSeq += 1; // an estimate under way for the Plan of before is dropped when it answers
  if (!state.tiles.length) {
    state.plan = null;
    state.planStale = false;
    estimateRun = null;
    if (state.planError?.from === "estimate") showPlanError(null);
    renderPlanPanel();
    return;
  }
  state.planStale = true;
  renderPlanPanel();
  estimateTimer = setTimeout(estimate, ESTIMATE_DELAY_MS);
}

/** Work out the cost of the Plan as it is now; resolves once it is known or refused. An answer
 * that arrives after a newer change is dropped. */
function estimate() {
  clearTimeout(estimateTimer);
  estimateTimer = 0;
  const seq = ++estimateSeq;
  estimateRun = (async () => {
    if (!state.tiles.length) return;
    state.planStale = true;
    renderPlanPanel();
    const req = await planRequest(); // null: the zones could not be loaded, and step 3 says so
    let plan = null;
    let refusal = null;
    if (req) {
      try {
        plan = await api("POST", "/api/plan", req);
      } catch (err) {
        refusal = err;
      }
    }
    if (seq !== estimateSeq) return;
    state.plan = plan;
    state.planStale = false;
    if (plan) {
      planMap?.zonesAccepted();
      if (state.planError?.from === "estimate") showPlanError(null);
    } else if (refusal) {
      planMap?.noteError(refusal);
      showPlanError(null, refusal, "estimate");
      xplaneRefused(refusal);
    }
    renderPlanPanel();
  })();
  return estimateRun;
}

/** Refused for want of X-Plane: the status is read again, so that step 3's notice and the status
 * bar say what the engine found now (a drive plugged in or out since the page loaded). */
function xplaneRefused(err) {
  if (XPLANE_FOLDER_CODES.has(errorDetail(err)?.code)) loadStatus();
}

async function build(install) {
  if (!state.tiles.length || state.buildStarting) return;
  // Both buttons wait until the engine answered, the estimate being worked out meanwhile included:
  // a second click never asks for the same build twice.
  state.buildStarting = true;
  renderBuildActions();
  try {
    // The cost of the last change first: a build refused by the estimate, or one the disk cannot
    // hold, does not start (step 4 says why).
    if (state.planStale || estimateTimer) await (estimateTimer || !estimateRun ? estimate() : estimateRun);
    if (!state.plan || diskVerdict(state.plan.disk).ok === false) return;
    const req = await planRequest();
    if (!req) return;
    await persistSettings();
    // Asked while another build runs, it waits for its turn (a user asked for a queue).
    const res = await api("POST", "/api/jobs", { ...req, install, queue: true });
    planMap?.zonesAccepted();
    // The job has its tiles now: the next selection starts empty. Kept, "add three tiles" built
    // the six of the last job again with them (they stay green on the map once installed).
    state.tiles = [];
    renderTiles();
    renderZlOptions();
    planChanged();
    if (res.queue_position > 0) {
      // It waits: the Plan stays, where more can be chosen; the map shows its tiles as waiting.
      toast(t("plan.queued", { n: res.queue_position }));
      await refreshJobList();
    } else {
      toast(t("plan.started"));
      showScreen("works", res.job_id);
    }
  } catch (err) {
    planMap?.noteError(err);
    const d = errorDetail(err);
    if (d?.code === "SYS_TILE_IN_BUILD") {
      // Another window queued them: the map learns it, and the user takes them out of the selection.
      showPlanError(t("plan.tiles_in_build_refused", { tiles: (d.context?.tiles || []).join(" ") }));
      refreshJobList();
    } else if (d?.code === "SYS_BUSY" && !state.engineOutdated) {
      showPlanError(t("plan.busy_deleting")); // a build asked for with queue waits: a delete refuses it
    } else if (err instanceof ApiError && err.status === 409) {
      showPlanError(t("plan.job_running"));
    } else {
      showPlanError(null, err);
      xplaneRefused(err);
    }
  } finally {
    state.buildStarting = false;
    renderBuildActions();
  }
}

/** Step 4 stays visible; its buttons wait for an estimate (consequences before action, 7.0.4). */
/**
 * Step 4: Build is there as soon as tiles are chosen, without pressing anything first. It waits
 * only when the estimate was refused or the disk cannot hold the build, and says why; otherwise the
 * line under the buttons sums the estimate up, as it follows the Plan (consequences before action).
 */
function renderBuildActions() {
  const tiles = state.tiles.length > 0;
  const plan = state.planStale ? null : state.plan;
  const disk = plan ? diskVerdict(plan.disk) : null;
  const refused = tiles && !state.planStale && !state.plan;
  const short = disk?.ok === false;
  const blocked = !tiles || refused || short || Boolean(state.buildStarting);
  $("build-install-btn").disabled = blocked;
  $("build-only-btn").disabled = blocked;
  let note = t("plan.estimating");
  if (!tiles) note = t("step4.need_tiles");
  else if (refused) note = t("step4.refused");
  else if (short) note = t("step4.disk_short", { need: fmtGB(disk.needed), free: fmtGB(disk.free) });
  else if (plan) note = t("step4.summary", { download: fmtMB(plan.network?.mb), time: fmtRange(plan.network?.seconds_low, plan.network?.seconds_high), disk: fmtGB(disk.needed) });
  setText($("build-note"), note);
  $("build-note").classList.toggle("is-fail", short || refused);
  // With a build under way or waiting, this one waits for its turn.
  const queue = $("build-queue");
  queue.hidden = !tiles || !activeJobs().length;
  setText(queue, queue.hidden ? "" : t("step4.queue_note"));
}

const COST_ICON_PATHS = {
  network: "M12 20v-4M4 10a12 12 0 0 1 16 0M7 13.5a7 7 0 0 1 10 0M10 16.5a3 3 0 0 1 4 0",
  compute: "M4 5h16v11H4zM9 20h6M12 16v4",
  disk: "M4 12a8 8 0 1 1 16 0 8 8 0 0 1-16 0zM12 12h.01",
};

/** Inline SVG icon (static markup, no user data). */
function costIcon(kind) {
  const markup = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" d="${COST_ICON_PATHS[kind]}"/></svg>`;
  return document.createRange().createContextualFragment(markup).firstElementChild;
}

function kv(rows) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v, cls] of rows) dl.append(h("dt", null, k), h("dd", { class: cls || null }, v));
  return dl;
}

function warningEntries(plan) {
  const out = [];
  for (const w of plan.warnings || []) {
    if (typeof w === "string") out.push({ code: w });
    else if (w && typeof w === "object") out.push(w);
  }
  return out;
}

function errorCard(err, { onRetry, tile } = {}) {
  const tpl = $("tpl-error-card").content.firstElementChild.cloneNode(true);
  const sev = err.severity || "info";
  tpl.classList.add(`sev-card-${sev}`);
  tpl.querySelector(".error-code").textContent = err.code || "";
  const sevEl = tpl.querySelector(".sev");
  sevEl.className = `sev sev-${sev}`;
  sevEl.textContent = SEV_KEYS[sev] ? SEV_KEYS[sev]() : sev;
  const where = [err.tile || tile, err.step ? STEP_KEYS[err.step]?.() : null].filter(Boolean).join(" · ");
  tpl.querySelector(".error-where").textContent = where;
  const [message, remedy] = codeWords(err);
  tpl.querySelector(".error-msg").textContent = message;
  tpl.querySelector(".error-remedy").textContent = remedy;
  const actions = tpl.querySelector(".error-actions");
  const action = uiAction(err);
  if (action === "retry" && onRetry) actions.append(h("button", { type: "button", class: "btn btn-small", onclick: onRetry }, t("works.retry")));
  else if (action === "settings") actions.append(h("button", { type: "button", class: "btn btn-small", onclick: () => openSettingsFor(err) }, t("works.settings")));
  else actions.remove();
  return tpl;
}

/**
 * The disk verdict of an estimate, the engine's own: `needed` is `needed_gb` (the images and the
 * downloads), `ok` is `disk.ok`, true when more than twice the need is free (orthostudio.estimate
 * `Estimate.disk_ok`). An answer without them gets the same rule from `dds_gb`; `ok` is null when
 * nothing is known.
 */
export function diskVerdict(disk) {
  const d = disk && typeof disk === "object" ? disk : {};
  const num = (x) => (typeof x === "number" && Number.isFinite(x) ? x : null);
  const free = num(d.free_gb);
  const needed = num(d.needed_gb) ?? num(d.dds_gb);
  let ok = typeof d.ok === "boolean" ? d.ok : null;
  if (ok === null && free !== null && needed !== null) ok = free > 2 * needed;
  return { free, needed, ok };
}

function renderPlanPanel() {
  const panel = clear($("plan-panel"));
  const plan = state.plan;
  renderBuildActions();
  const working = state.planStale && state.tiles.length > 0;
  $("estimate-spinner").hidden = !working;
  setText($("estimate-status"), working ? t("plan.estimating") : "");
  // after a refusal (the engine unreachable a moment, say): the same estimate, again, on demand
  $("estimate-btn").hidden = working || !state.tiles.length || Boolean(plan);
  panel.classList.toggle("is-stale", Boolean(plan) && working);
  if (!plan) {
    if (!state.tiles.length) panel.append(h("p", { class: "placeholder" }, t("plan.empty")));
    return;
  }
  const net = plan.network || {};
  const cpu = plan.compute || {};
  const disk = diskVerdict(plan.disk);
  const diskCls = disk.ok === null ? null : disk.ok ? "ok" : "fail";
  const diskFree = disk.ok === null ? fmtGB(disk.free) : `${fmtGB(disk.free)} · ${disk.ok ? t("plan.disk_ok") : t("plan.disk_full")}`;
  if (disk.ok === false) {
    panel.append(h("p", { class: "disk-alert", role: "alert" }, t("plan.disk_short", { need: fmtGB(disk.needed), free: fmtGB(disk.free) })));
  }

  panel.append(
    h("div", { class: "costs" },
      h("section", { class: "cost" },
        h("h3", null, costIcon("network"), t("plan.network"), h("span", { class: "sub" }, `· ${t("plan.network_sub")}`)),
        kv([
          [t("plan.requests"), fmtInt(net.requests)],
          [t("plan.download"), fmtMB(net.mb)],
          [t("plan.time"), fmtRange(net.seconds_low, net.seconds_high), "big"],
          [t("plan.throughput"), fmtMbps(net.mbps_measured)],
        ])),
      h("section", { class: "cost" },
        h("h3", null, costIcon("compute"), t("plan.compute"), h("span", { class: "sub" }, `· ${t("plan.compute_sub")}`)),
        kv([
          [t("plan.textures"), fmtInt(cpu.textures)],
          [t("plan.cached"), fmtInt(cpu.cached)],
          [t("plan.time"), fmtDuration(cpu.seconds), "big"],
        ])),
      h("section", { class: "cost" },
        h("h3", null, costIcon("disk"), t("plan.disk")),
        kv([
          [t("plan.disk_write"), fmtGB(disk.needed)],
          [t("plan.disk_free"), diskFree, diskCls],
        ])),
    ),
  );

  // The red line above says more than the engine's SYS_DISK_FULL warning: no second card for it.
  const warnings = warningEntries(plan).filter((w) => !(disk.ok === false && w.code === "SYS_DISK_FULL"));
  if (warnings.length) {
    panel.append(h("h3", { class: "section-title" }, t("plan.warnings")));
    panel.append(h("div", { class: "error-list" }, warnings.map((w) => errorCard({ severity: "degraded", ...w }))));
  }

  const tiles = plan.tiles || [];
  if (tiles.length) {
    const table = h("table", { class: "table" },
      h("thead", null, h("tr", null,
        h("th", { scope: "col" }, t("library.tile")),
        h("th", { scope: "col", class: "num" }, t("plan.hits")),
        h("th", { scope: "col", class: "num" }, t("plan.builds")),
        h("th", { scope: "col", class: "num" }, t("plan.textures")),
        h("th", { scope: "col", class: "num" }, t("plan.requests")),
        h("th", { scope: "col", class: "num" }, t("plan.download")))),
      h("tbody", null, tiles.map((te) => h("tr", null,
        h("td", { class: "tile-name" }, te.tile),
        h("td", { class: "num" }, fmtInt(te.hits)),
        h("td", { class: "num" }, fmtInt(te.builds)),
        h("td", { class: "num" }, fmtInt(te.textures?.total ?? te.textures)),
        h("td", { class: "num" }, fmtInt(te.requests)),
        h("td", { class: "num" }, fmtMB(te.download_mb))))));
    panel.append(h("h3", { class: "section-title" }, t("plan.per_tile")), h("div", { class: "table-wrap plan-tiles" }, table));
  }
  panel.append(h("p", { class: "plan-summary" }, t("plan.summary", {
    tiles: tiles.length || state.tiles.length,
    textures: fmtInt(cpu.textures),
    requests: fmtInt(net.requests),
    mb: fmtMB(net.mb),
    disk: fmtGB(disk.needed),
  })));
}

// ------------------------------------------------------------------ Works

let jobsTimer = 0;

async function loadWorks(jobId) {
  await refreshJobList();
  const wanted = jobId || state.jobId || state.jobs.find((j) => jobActive(j))?.id || state.jobs[0]?.id;
  if (wanted && wanted !== state.jobId) await watchJob(wanted);
  else if (wanted && !state.job) await watchJob(wanted);
  else renderJob();
  clearInterval(jobsTimer);
  jobsTimer = setInterval(() => {
    if (state.screen === "works") refreshJobList();
  }, 10000);
}

async function refreshJobList() {
  try {
    state.jobs = await api("GET", "/api/jobs");
  } catch (err) {
    toast(errorMessage(err), "fail");
    state.jobs = [];
  }
  renderJobList();
  jobsChanged();
}

/** The builds under way or waiting, as the page knows them: the job shown in Works in its own
 * state, which is fresher, and the others of the list. */
function activeJobs() {
  const shown = state.job;
  const others = (state.jobs || []).filter((j) => jobActive(j) && j.id !== shown?.id);
  return jobActive(shown) ? [shown, ...others] : others;
}

/** The Plan map's build layer: what each tile of the build shown is doing, then the tiles of the
 * other builds under way or waiting, which wait. */
function buildingOnMap() {
  const out = buildingTiles(state.job);
  for (const job of activeJobs()) {
    if (job === state.job) continue;
    for (const name of tilesInBuilds([job]).keys()) if (!out.has(name)) out.set(name, "queued");
  }
  return out;
}

/** The builds under way or waiting changed: step 4's note, the map's build layer and the Library's
 * buttons follow, and so does the job the page watches off Works. */
function jobsChanged() {
  renderBuildActions();
  planMap?.jobChanged();
  if (state.screen === "library" && state.library) renderLibrary();
  followBuildUnderWay();
}

/** Off Works, the page watches the build under way, so that the Plan's map shows what is being
 * built: when the build it watched ends, the next one of the queue starts. The list read just
 * after that end may still call the next one queued (its thread starts a moment later): the page
 * then watches the oldest queued job, whose events say when it runs. On Works the job the user
 * looks at stays. */
function followBuildUnderWay() {
  if (state.screen === "works" || state.job?.status === "running") return;
  const jobs = state.jobs || [];
  const queued = jobs.filter((j) => j.status === "queued").sort((a, b) => a.created_at - b.created_at);
  const next = jobs.find((j) => j.status === "running") || queued[0];
  if (next && next.id !== state.jobId) watchJob(next.id);
}

function jobStatusPill(status) {
  const kind = { queued: "running", running: "running", pending: "running", done: "done", failed: "failed", cancelled: "cancelled" }[status] || "cancelled";
  return pill(STATE_KEYS[status] ? STATE_KEYS[status]() : status, kind);
}

function renderJobList() {
  const trash = $("jobs-clear");
  trash.hidden = Boolean(state.engineOutdated) || !state.jobs.some((j) => !jobActive(j));
  trash.disabled = state.jobsClearing;
  const ul = clear($("job-list"));
  for (const j of state.jobs) {
    const tiles = (j.tiles || []).map((x) => (typeof x === "string" ? x : x.tile));
    const btn = h("button", { type: "button", class: "job-item", "aria-current": String(j.id === state.jobId), onclick: () => { history.replaceState(null, "", `#works/${j.id}`); watchJob(j.id); renderJobList(); } },
      h("span", { class: "job-tiles" }, tiles.length > 3 ? `${tiles.slice(0, 3).join(" ")} +${tiles.length - 3}` : tiles.join(" ")),
      jobStatusPill(j.status),
      h("span", { class: "job-meta num" }, `${j.provider || ""} ZL${j.zoom_level ?? j.zl ?? ""} · ${fmtDate(j.started_at || j.created_at)} · ${j.install ? t("works.install") : t("works.no_install")}`));
    ul.append(h("li", null, btn));
  }
}

/** The trash above the job list: the finished jobs leave it and their journals are deleted
 * (POST /api/jobs/clear); a build running or queued stays, and so do the tiles they built. */
async function clearJobs() {
  const finished = state.jobs.filter((j) => !jobActive(j)).length;
  if (!finished || state.jobsClearing) return;
  if (!(await confirmClearJobs(finished, state.jobs.length - finished))) return;
  state.jobsClearing = true;
  renderJobList();
  let removed = null;
  try {
    removed = (await api("POST", "/api/jobs/clear"))?.removed || [];
  } catch (err) {
    toast(errorMessage(err), "fail");
  } finally {
    state.jobsClearing = false;
  }
  await refreshJobList();
  if (state.jobId && !state.jobs.some((j) => j.id === state.jobId)) {
    const next = state.jobs.find((j) => jobActive(j));
    if (next) {
      history.replaceState(null, "", `#works/${next.id}`);
      await watchJob(next.id);
    } else {
      forgetShownJob();
    }
  }
  if (document.activeElement === document.body || $("jobs-clear").hidden) {
    ($("job-list").querySelector("button") || $("jobs-title")).focus({ preventScroll: true });
  }
  if (removed) toast(t("works.cleared", { n: removed.length }));
}

function confirmClearJobs(finished, running) {
  const dialog = $("jobs-clear-confirm");
  if (dialog.open) return Promise.resolve(false);
  const lines = [t("works.clear_count", { n: finished }), t("works.clear_kept")];
  if (running > 0) lines.push(t("works.clear_running"));
  clear($("jobs-clear-text")).append(...lines.map((line) => h("p", null, line)));
  dialog.returnValue = "";
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close();
  };
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "clear"), { once: true });
    dialog.showModal();
  });
}

/** The job shown left the list: stop following it and show none. */
function forgetShownJob() {
  if (state.source) {
    state.source.close();
    state.source = null;
  }
  watchToken = {};
  state.jobId = null;
  state.job = null;
  history.replaceState(null, "", "#works");
  renderJob();
  renderJobList();
}

const percentFormats = new Map();
const clockFormats = new Map();

/** The download rate in the engine message of a step that downloads, in MB/s: Imagery's
 * "… (1392 req/s, 21.6 MB/s)" (build.py textures_progress_message), the OSM layers of Data
 * "+46+006: 2/4 OSM layers (1.4 MB/s)" (sources/osm.py osm_progress_message); null without. */
export function downloadRate(message) {
  const m = /\b(\d+(?:\.\d+)?) MB\/s\)/.exec(String(message || ""));
  return m ? Number(m[1]) : null;
}

/** "42 %" in French, "42%" in English; rounded down, so that 100 % means finished. */
function fmtPercent(fraction) {
  const lang = language();
  if (!percentFormats.has(lang)) percentFormats.set(lang, new Intl.NumberFormat(lang === "fr" ? "fr-FR" : "en-US", { style: "percent", maximumFractionDigits: 0 }));
  return percentFormats.get(lang).format(Math.floor(clamp01(fraction) * 100) / 100);
}

/** The time of day of a log line (every line of a job is from the same build). */
function fmtClock(epochSeconds) {
  const lang = language();
  if (!clockFormats.has(lang)) clockFormats.set(lang, new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-US", { timeStyle: "medium" }));
  return clockFormats.get(lang).format(new Date(epochSeconds * 1000));
}

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

function setAttr(el, name, value) {
  const v = String(value);
  if (el.getAttribute(name) !== v) el.setAttribute(name, v);
}

/** The job panel, built once per job (and language) and then updated in place: rebuilt on every
 * event, it closed the log, lost its scroll, dropped tooltips and the focus of Stop. */
let jobView = null;
let renderTimer = 0;
let lastRender = 0;
let clockTimer = 0;
const RENDER_EVERY_MS = 250;

/** Draw the job panel at most four times a second; it draws the state as it is then. */
function scheduleRenderJob() {
  if (renderTimer) return;
  renderTimer = setTimeout(renderJob, Math.max(0, lastRender + RENDER_EVERY_MS - Date.now()));
}

function renderJob() {
  clearTimeout(renderTimer);
  renderTimer = 0;
  lastRender = Date.now();
  planMap?.jobChanged(); // the tiles being built, on the Plan's map
  const box = $("job-detail");
  const job = state.job;
  if (!job) {
    clear(box).append(h("p", { class: "placeholder" }, t("works.empty")));
    return;
  }
  if (!jobView || jobView.id !== job.id || jobView.lang !== language() || !box.contains(jobView.root)) {
    jobView = buildJobView(box, job, jobView);
  }
  updateJobView(jobView, job);
  if (!clockTimer) {
    clockTimer = setInterval(() => {
      if (state.screen === "works" && state.job && jobView?.id === state.job.id && jobView.root.isConnected && jobActive(state.job)) updateClock(jobView, state.job);
    }, 1000);
  }
}

function logAtBottom(pre) {
  return pre.scrollHeight - pre.scrollTop - pre.clientHeight <= 4;
}

function buildJobView(box, job, previous) {
  // The same job drawn again (another language, the job clicked again): the log stays as it was.
  const same = previous && previous.id === job.id;
  const carry = same ? { open: previous.logDetails.open, follow: previous.logFollow, top: previous.logPre.scrollTop } : null;
  const v = { id: job.id, lang: language(), status: null, cells: new Map(), tileNames: null, errorsKey: null, report: undefined, logLines: [], logFollow: carry ? carry.follow : true, logTop: carry && !carry.follow ? carry.top : null, elapsedShown: null };

  v.pill = h("span", { class: "job-pill" });
  v.meta = h("span", { class: "help num" });
  v.stop = h("button", { type: "button", class: "btn btn-small btn-danger", onclick: cancelJob }, t("works.stop"));
  const head = h("div", { class: "job-head" }, h("h2", null, t("works.job", { id: job.id })), v.pill, v.meta, h("span", { class: "spacer" }), v.stop);

  v.progress = h("b");
  v.elapsed = h("b");
  v.eta = h("b");
  v.rate = h("b");
  v.rateItem = h("span", null, `${t("works.rate")} `, v.rate);
  // The numbers of the header are the whole job's: their labels say so in a tooltip.
  const helped = (label, help, value) => h("span", { title: help }, h("span", { class: "has-help" }, label), " ", value);
  v.etaItem = helped(t("works.eta"), t("works.eta_help"), v.eta);
  const stats = h("div", { class: "job-stats" },
    helped(t("works.progress"), t("works.progress_help"), v.progress),
    helped(t("works.elapsed"), t("works.elapsed_help"), v.elapsed),
    v.etaItem,
    v.rateItem);
  v.barFill = h("span");
  v.bar = h("div", { class: "progress progress-total", role: "progressbar", "aria-label": t("works.progress_help"), "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": 0 }, v.barFill);
  v.phase = h("p", { class: "job-phase", hidden: true });
  v.rows = h("div", { class: "tile-rows" });
  v.errors = h("div", { class: "job-errors" });

  v.logSummary = h("summary");
  v.logPre = h("pre", { tabindex: 0 });
  v.logDetails = h("details", { class: "log" }, v.logSummary, v.logPre);
  v.logPre.addEventListener("scroll", () => {
    if (v.logDetails.open) v.logFollow = logAtBottom(v.logPre);
  });
  v.logDetails.addEventListener("toggle", () => {
    if (v.logDetails.open && v.logFollow) v.logPre.scrollTop = v.logPre.scrollHeight;
  });
  v.reportBox = h("div", { class: "job-report" });

  v.root = h("div", { class: "job-view" }, head, stats, v.bar, v.phase, v.rows, v.errors, v.logDetails, v.reportBox);
  clear(box).append(v.root);
  if (carry?.open) v.logDetails.open = true;
  return v;
}

/** What the build takes its heights from, for the line under a job's title. */
function reliefWords(relief) {
  if (relief === "copernicus") return t("works.relief_cop30");
  if (relief === "file") return t("works.relief_file");
  if (relief === "xplane") return t("works.relief_xplane");
  return "";  // an older engine says nothing, and the line keeps its other parts
}

function updateJobView(v, job) {
  const active = jobActive(job);
  if (v.status !== job.status) {
    clear(v.pill).append(jobStatusPill(job.status));
    v.status = job.status;
  }
  const parts = [`${job.provider || ""} ZL${job.zoom_level ?? job.zl ?? ""}`, reliefWords(job.relief), job.install ? t("works.install") : t("works.no_install")];
  setText(v.meta, parts.filter(Boolean).join(" · "));
  const waiting = job.status === "queued";
  v.stop.hidden = !active;
  setText(v.stop, waiting ? t("works.unqueue") : t("works.stop"));

  const progress = jobProgress(job);
  setText(v.progress, fmtPercent(progress));
  updateClock(v, job);
  v.etaItem.hidden = !active || waiting;
  if (active && !waiting) {
    const [lo, hi] = etaRange(job.stats);
    setText(v.eta, lo == null ? t("works.eta_unknown") : fmtRange(lo, hi));
  }
  const st = job.stats || {};
  const rate = [];
  if (active && st.req_per_s) rate.push(`${fmtInt(st.req_per_s)} req/s`);
  if (active && st.mb_per_s) rate.push(fmtMbps(st.mb_per_s));
  v.rateItem.hidden = !rate.length;
  setText(v.rate, rate.join(" · "));
  const pct = Math.floor(progress * 100);
  setAttr(v.bar, "aria-valuenow", pct);
  setAttr(v.bar, "class", `progress progress-total progress-${active ? "running" : job.status === "done" ? "done" : job.status === "failed" ? "failed" : "cancelled"}`);
  v.barFill.style.width = `${pct}%`;

  const dataPhase = active && st.phase === "data";
  v.phase.hidden = !dataPhase && !waiting;
  if (waiting) {
    setText(v.phase, t("works.queued_note"));
  } else if (dataPhase) {
    const n = dataPhaseTiles(job);
    setText(v.phase, n ? t("works.phase_data", { n: fmtInt(n) }) : t("works.phase_data_any"));
  }

  updateTileRows(v, job);
  updateErrors(v, job, active);
  updateLog(v, job, active);
  if (v.report !== job.report) {
    clear(v.reportBox);
    if (job.report) v.reportBox.append(renderReport(job));
    v.report = job.report;
  }
}

/** Elapsed, ticking every second while the job runs; a figure from the engine that arrives a
 * little late never sets the clock back. */
function updateClock(v, job) {
  let seconds = jobElapsed(job);
  if (jobActive(job) && seconds != null) {
    if (v.elapsedShown != null && seconds < v.elapsedShown) seconds = v.elapsedShown;
    v.elapsedShown = seconds;
  }
  setText(v.elapsed, fmtDuration(seconds));
}

function updateTileRows(v, job) {
  const tiles = job.tiles || [];
  const names = tiles.map((x) => x.tile).join(" ");
  if (names !== v.tileNames) {
    clear(v.rows);
    v.cells.clear();
    v.tileNames = names;
    for (const tile of tiles) {
      const steps = h("div", { class: "steps" });
      for (const s of STEPS) {
        const cell = buildStepCell(tile.tile, s);
        v.cells.set(`${tile.tile}|${s}`, cell);
        steps.append(cell.root);
      }
      v.rows.append(h("div", { class: "tile-row" }, h("span", { class: "tile-name" }, tile.tile), steps));
    }
  }
  for (const tile of tiles) {
    for (const s of STEPS) updateStepCell(v.cells.get(`${tile.tile}|${s}`), job, tile, s);
  }
}

function buildStepCell(tileName, s) {
  const c = { status: null };
  c.dot = h("span", { class: "dot dot-pending" });
  c.fill = h("span");
  c.bar = h("div", { class: "progress step-bar progress-pending", role: "progressbar", "aria-label": `${tileName} ${STEP_KEYS[s]()}`, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": 0 }, c.fill);
  c.state = h("span", { class: "step-state" });
  c.detail = h("span", { class: "step-detail" });
  c.root = h("div", { class: "step step-pending" }, h("div", { class: "step-label" }, c.dot, STEP_KEYS[s]()), c.bar, h("div", { class: "step-text" }, c.state, c.detail));
  return c;
}

/**
 * What the cell of step `s` of a tile shows: its status for the eye (`status`, the class of its dot
 * and bar), the bar's width in whole percent (`pct`), a few plain words (`text`, `detail`) and a
 * tooltip line (`help`). Exported for the tests.
 *
 * - pending: empty; `not_started` once the job ended;
 * - running: the step's fraction, "42 %" (or "running" at 0) and the engine's message;
 * - waiting: the step's fraction, "waiting · 42 %": some of its rows did their work, the others
 *   wait for their turn (the dot does not pulse);
 * - done, hit, failed: full;
 * - skipped, cancelled: how far its rows got (by count: 1 for a row that ended with a result, its
 *   fraction for one stopped while running), dashed;
 * - stopped: a step an older engine still calls running or waiting after the job ended (the
 *   current engine says skipped or cancelled then), shown like cancelled.
 */
export function stepView(job, tile, s) {
  const step = tile?.steps?.[s] || emptyStep();
  const nodes = Object.values(step.nodes || {});
  const active = jobActive(job);
  let status = step.status || "pending";
  if ((status === "running" || status === "waiting") && !active) status = "stopped";
  if (status === "pending" && !active) status = "not_started";
  const word = () => (STATE_KEYS[status] ? STATE_KEYS[status]() : status);
  let width = 0;
  let text = null;
  let detail = "";
  let help = "";
  if (s === "install" && job && !job.install && !nodes.length && ["skipped", "pending", "not_started"].includes(status)) {
    status = "skipped";
    text = t("works.no_install");
  } else if (status === "running") {
    width = clamp01(step.fraction);
    text = width > 0 ? fmtPercent(width) : word();
    // A step that downloads shows its rate (a user asked for it wherever the network works:
    // Imagery, the OSM layers of Data); the engine's whole line stays in the tooltip.
    const rate = downloadRate(step.message);
    detail = rate ? fmtMbps(rate) : step.message || "";
    help = step.message || "";
  } else if (status === "pending" && s === "imagery") {
    // Coast is done and Imagery waits: for the tile's DSF, which lists its images (Assembly), or
    // for the images of another tile (one tile downloads at a time). "pending" alone said nothing
    // for a long while (a user on Windows, 2026-09-15).
    const dsf = Object.values(tile?.steps?.assembly?.nodes || {}).find((n) => n?.role === "dsf");
    const other = (job?.tiles || []).find((x) => x && x.tile !== tile?.tile && x.steps?.imagery?.status === "running");
    if (dsf && (dsf.status === "running" || dsf.status === "waiting")) text = t("works.waits_dsf");
    else if (other) text = t("works.waits_images", { tile: other.tile });
  } else if (status === "waiting") {
    width = clamp01(step.fraction);
    if (width > 0) detail = fmtPercent(width);
    help = t("works.waiting_help");
  } else if (status === "done") {
    width = 1;
    if (step.wall_s >= 1) detail = fmtDuration(step.wall_s);
  } else if (status === "hit") {
    width = 1;
    help = t("works.hit_help");
  } else if (status === "failed") {
    width = 1;
    const err = (job?.errors || []).find((e) => e.tile === tile.tile && (e.step ?? e.stage) === s);
    if (err) help = `${err.code}: ${codeWords(err)[0]}`;
  } else if (status === "skipped" || status === "cancelled" || status === "stopped") {
    width = nodes.length ? nodes.reduce((sum, n) => sum + (n.status === "done" || n.status === "hit" ? 1 : n.status === "running" || n.status === "cancelled" ? clamp01(n.fraction) : 0), 0) / nodes.length : 0;
    if (status === "skipped") help = t("works.skipped_help");
  }
  // The engine rounds what it sends to four decimals: that noise never shows as a percent less.
  const pct = Math.min(100, Math.floor(width * 100 + 0.01));
  return { status, pct, text: text ?? word(), detail, help };
}

/** One step of a tile: a thin bar for every status, then what it is doing in plain words. */
function updateStepCell(c, job, tile, s) {
  if (!c) return;
  const { status, pct, text, detail, help } = stepView(job, tile, s);
  if (c.status !== status) {
    c.root.className = `step step-${status}`;
    c.dot.className = `dot dot-${status}`;
    c.bar.className = `progress step-bar progress-${status}`;
    c.status = status;
  }
  c.fill.style.width = `${pct}%`;
  setAttr(c.bar, "aria-valuenow", pct);
  setAttr(c.bar, "aria-valuetext", detail ? `${text}, ${detail}` : text);
  setText(c.state, text);
  setText(c.detail, detail);
  setAttr(c.root, "title", `${STEP_KEYS[s]()} — ${text}${detail ? ` · ${detail}` : ""}${help && help !== detail ? `\n${help}` : ""}`);
}

function updateErrors(v, job, active) {
  const errors = job.errors || [];
  const key = `${active}|${errors.map((e) => `${e.code}|${e.tile}|${e.step ?? e.stage}|${e.message}`).join("\n")}`;
  if (key === v.errorsKey) return;
  v.errorsKey = key;
  clear(v.errors);
  if (!errors.length) return;
  v.errors.append(
    h("h3", { class: "section-title" }, `${t("works.errors")} (${errors.length})`),
    h("div", { class: "error-list" }, errors.map((e) => errorCard(e, { onRetry: active ? null : retryJob }))),
  );
}

/** The log is never rebuilt: new lines are appended, lines past the last 500 leave the top, it
 * stays open or closed as the user left it, and it follows new lines only when scrolled to the end. */
function updateLog(v, job, active) {
  const log = Array.isArray(job.log) ? job.log : [];
  v.logDetails.hidden = !(log.length || active);
  setText(v.logSummary, t("works.log", { n: fmtInt(log.length) }));
  const { drop, add } = logDelta(v.logLines, log);
  if (!drop && !add.length) return;
  const pre = v.logPre;
  if (drop) {
    const before = pre.scrollHeight;
    for (let i = 0; i < drop && pre.firstChild; i += 1) pre.firstChild.remove();
    v.logLines.splice(0, drop);
    if (!v.logFollow) pre.scrollTop -= before - pre.scrollHeight;
  }
  if (add.length) {
    const lines = document.createDocumentFragment();
    for (const line of add) {
      const time = line.at != null ? fmtClock(line.at) : line.ts != null ? `+${fmtDuration(line.ts)}` : "";
      lines.append(h("span", { class: `lvl-${line.level}` }, `${[time, line.tile, line.message].filter(Boolean).join(" ")}\n`));
    }
    pre.append(lines);
    v.logLines.push(...add);
  }
  if (v.logTop != null) {
    pre.scrollTop = v.logTop;
    v.logTop = null;
  } else if (v.logFollow) pre.scrollTop = pre.scrollHeight;
}

function renderReport(job) {
  const rep = job.report;
  const per = Object.fromEntries(STEPS.map((s) => [s, { hits: 0, built: 0, failed: 0, seconds: 0 }]));
  for (const tile of rep.tiles || []) {
    for (const node of tile.nodes || []) {
      const s = stepOfNode(node.id, node.role);
      if (!s) continue;
      if (node.status === "hit") per[s].hits += 1;
      else if (node.status === "built") per[s].built += 1;
      else if (node.status === "failed") per[s].failed += 1;
      per[s].seconds += node.wall_s || 0;
    }
  }
  const table = h("table", null,
    h("thead", null, h("tr", null, h("th", null, t("works.report_step")), h("th", null, t("works.report_hits")), h("th", null, t("works.report_built")), h("th", null, t("works.report_failed")), h("th", null, t("works.report_time")))),
    h("tbody", null, STEPS.map((s) => h("tr", null, h("td", null, STEP_KEYS[s]()), h("td", null, fmtInt(per[s].hits)), h("td", null, fmtInt(per[s].built)), h("td", null, fmtInt(per[s].failed)), h("td", null, fmtDuration(per[s].seconds))))));

  const counts = decisionCounts(job);
  const decisions = h("ul", { class: "decisions" });
  for (const [code, fn] of Object.entries(DECISION_KEYS)) {
    decisions.append(h("li", null, h("span", null, fn()), h("b", null, fmtInt(counts.get(code) || 0))));
  }
  for (const [code, n] of counts) {
    if (code in DECISION_KEYS) continue;
    const words = Object.hasOwn(EXTRA_DECISION_KEYS, code) ? h("span", { title: code }, EXTRA_DECISION_KEYS[code]()) : h("code", null, code);
    decisions.append(h("li", null, words, h("b", null, fmtInt(n))));
  }
  const totals = reportTotals(job);
  const summary = kv([
    [t("works.report_hits"), fmtInt(rep.hits)],
    [t("works.report_built"), fmtInt(rep.built)],
    [t("works.report_failed"), fmtInt(rep.failed), rep.failed ? "fail" : null],
    [t("works.elapsed"), fmtDuration(rep.elapsed_s)],
    [t("works.size"), totals.bytes != null ? fmtBytes(totals.bytes) : "—"],
    [t("step.install"), job.install ? `${fmtInt(totals.installed)} / ${fmtInt(totals.tiles)}` : t("works.no_install")],
  ]);
  // Built only: the tiles are on the disk, not in X-Plane. A user looked for a way to "import"
  // them, and found the Ortho4XP import instead (2026-09-17).
  const notInstalled = !job.install && rep.failed === 0
    ? h("p", { class: "report-note" },
        t("works.not_installed_yet"),
        " ",
        h("button", { type: "button", class: "btn btn-small", onclick: () => showScreen("library") }, t("works.open_library")))
    : null;
  return h("section", { class: "report" },
    h("h3", null, t("works.report")),
    h("div", { class: "report-grid" }, summary, table, h("div", null, h("h3", { class: "section-title" }, t("works.decisions")), decisions)),
    notInstalled);
}

async function cancelJob() {
  if (!state.jobId) return;
  const waiting = state.job?.status === "queued";
  try {
    await api("POST", `/api/jobs/${encodeURIComponent(state.jobId)}/cancel`);
    toast(waiting ? t("works.unqueued") : t("works.cancelled"));
  } catch (err) {
    toast(errorMessage(err), "fail");
  }
}

async function retryJob() {
  if (!state.jobId) return;
  try {
    const res = await api("POST", `/api/jobs/${encodeURIComponent(state.jobId)}/retry`, { queue: true });
    if (res?.queue_position > 0) {
      // It waits for the build under way: the job shown stays, the list shows the new one.
      toast(t("works.retry_queued"));
      await refreshJobList();
      return;
    }
    toast(t("works.retried"));
    const id = res && res.job_id ? res.job_id : state.jobId;
    history.replaceState(null, "", `#works/${id}`);
    await refreshJobList();
    await watchJob(id);
  } catch (err) {
    const d = errorDetail(err);
    if (d?.code === "SYS_TILE_IN_BUILD") toast(t("works.retry_in_build", { tiles: (d.context?.tiles || []).join(" ") }), "fail");
    else if (d?.code === "SYS_BUSY" && !state.engineOutdated) toast(t("plan.busy_deleting"), "fail");
    else if (err instanceof ApiError && err.status === 409) toast(t("plan.job_running"), "fail");
    else toast(errorMessage(err), "fail");
  }
}

// ------------------------------------------------------------------ Library

/** Rows (`libraryKey`) with a change under way: their buttons stay disabled, across re-renders
 * too, until the engine answers, so a double click never sends a request twice. */
const libraryBusy = new Set();

let libraryTimer = 0;

/** The Library read again in a moment: a build installs its tiles one after the other. */
function loadLibrarySoon() {
  clearTimeout(libraryTimer);
  libraryTimer = setTimeout(loadLibrary, 800);
}

async function loadLibrary() {
  try {
    const rows = await api("GET", "/api/library");
    state.library = Array.isArray(rows) ? rows : [];
    // The status bar counts the same tiles: a build that ends or installs a tile, a tile deleted,
    // show at once there too (a user saw "0 tile(s) in the library" stay after builds, 2026-09-15).
    if (state.status) {
      state.status.library_count = new Set(libraryTiles(state.library).map((e) => e.tile)).size;
      renderStatus();
    }
  } catch (err) {
    // The rows shown stay: "No tile" would be wrong while the engine does not answer.
    toast(errorMessage(err), "fail");
  }
  renderLibrary();
  planMap?.libraryChanged();
  loadDisk(); // a delete or a removal changes what can be freed
}

// ------------------------------------------------------------------ Library: disk space

/** The folder the Library's disk space is measured in: the data folder, when its disk is there. */
export function dataFolderShown(status) {
  const data = status?.data_dir;
  if (data && data.path) return data.present === false ? null : data.path;
  return status?.home || null;
}

/** GET /api/disk: what "Free space" would give back, measured without deleting anything. */
async function loadDisk() {
  if (!$("disk-list")) return;
  try {
    state.disk = await api("GET", "/api/disk");
  } catch (_err) {
    state.disk = null; // an older engine has no such route: the banner asks for a restart
  }
  renderDisk();
}

/** The sizes a confirmation and the panel show: the unused tile data, and with `images` the
 * downloaded image pieces and the map background. Exported for the tests. */
export function freeSpacePlan(disk, images) {
  const unused = Math.max(0, Number(disk?.unused_bytes) || 0);
  const pictures = images ? Math.max(0, Number(disk?.images_bytes) || 0) + Math.max(0, Number(disk?.mapcache_bytes) || 0) : 0;
  return { unused, pictures, total: unused + pictures };
}

function renderDisk() {
  const list = $("disk-list");
  if (!list) return;
  const d = state.disk;
  clear(list);
  const button = $("disk-free");
  const note = $("disk-note");
  if (!d) {
    button.disabled = true;
    setText(note, "");
    return;
  }
  const inUse = Math.max(0, (Number(d.store_bytes) || 0) - (Number(d.unused_bytes) || 0));
  const row = (label, value, help) => [h("dt", { title: help || null }, label), h("dd", { class: "num" }, value)];
  list.append(
    ...row(t("disk.in_use", { n: d.tiles ?? 0 }), fmtBytes(inUse), t("disk.in_use_help")),
    ...row(t("disk.unused"), fmtBytes(d.unused_bytes || 0), t("disk.unused_help")),
    ...row(t("disk.images"), fmtBytes(d.images_bytes || 0), t("disk.images_pieces_help")),
    ...row(t("disk.mapcache"), fmtBytes(d.mapcache_bytes || 0)),
  );
  const reveal = $("disk-reveal");
  const data = state.status?.data_dir;
  reveal.hidden = !dataFolderShown(state.status);
  const action = revealLabel(state.status?.platform);
  setText(reveal, data?.chosen ? t("disk.reveal_data", { action }) : t("disk.reveal", { action }));
  const plan = freeSpacePlan(d, $("disk-images").checked);
  button.disabled = Boolean(d.building) || plan.total <= 0 || state.diskBusy;
  let said = d.building ? t("disk.busy_note") : plan.total <= 0 ? t("disk.nothing") : "";
  if (data?.present === false) said = t("disk.data_missing", { path: data.path });
  setText(note, said);
}

/** The question before freeing space, in the page's modal <dialog>; resolves true for "Free". */
function confirmFreeSpace(plan) {
  const dialog = $("disk-confirm");
  if (dialog.open) return Promise.resolve(false);
  $("disk-confirm-title").textContent = t("disk.confirm_title", { size: fmtBytes(plan.total) });
  const lines = [];
  if (plan.unused > 0) lines.push(t("disk.confirm_unused", { size: fmtBytes(plan.unused) }));
  if (plan.pictures > 0) lines.push(t("disk.confirm_images", { size: fmtBytes(plan.pictures) }));
  lines.push(t("disk.confirm_kept"));
  clear($("disk-confirm-text")).append(...lines.map((line) => h("p", null, line)));
  dialog.returnValue = "";
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close();
  };
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "free"), { once: true });
    dialog.showModal();
  });
}

/** "Free space…": asks, then POST /api/clean, then says how much came back. */
async function freeSpace() {
  if (state.diskBusy || !state.disk) return;
  const images = $("disk-images").checked;
  const plan = freeSpacePlan(state.disk, images);
  if (plan.total <= 0 || !(await confirmFreeSpace(plan))) return;
  const errors = clear($("disk-errors"));
  state.diskBusy = true;
  renderDisk();
  try {
    const res = await api("POST", "/api/clean", { images });
    const freed = (Number(res?.freed_bytes) || 0) + (Number(res?.images_freed_bytes) || 0);
    toast(freed > 0 ? t("disk.freed", { size: fmtBytes(freed) }) : t("disk.nothing"));
  } catch (err) {
    const found = errorDetail(err);
    const d = found && found.code ? found : { code: "SYS_INTERNAL_ERROR", message: errorMessage(err), severity: "blocking" };
    const card = errorCard(d.code === "SYS_BUSY" ? { ...d, action: "none", severity: "blocking" } : d);
    if (d.code === "SYS_BUSY") {
      card.querySelector(".error-msg").textContent = t("disk.err_busy");
      card.querySelector(".error-remedy").textContent = t("disk.err_busy_remedy");
    }
    errors.append(card);
  } finally {
    state.diskBusy = false;
    await loadDisk();
    loadStatus();
  }
}

function entryName(e) {
  return e.name || String(e.path || "").split(/[\\/]/).pop();
}

/** One key per row, even when two builds of the same tile, in two output folders, share the pack name. */
function libraryKey(e) {
  return `${e.tile}|${e.path || entryName(e)}`;
}

/**
 * The rows the Library shows: one per tile pack. The overlay rows (the `yOrthoStudio_Overlays` pack
 * the tiles share) go with their tile in the engine, so they are not rows of their own; a row
 * without `kind` is a tile pack.
 */
export function libraryTiles(rows) {
  return (Array.isArray(rows) ? rows : []).filter((e) => e && (e.kind == null || e.kind === "ortho"));
}

/** The request of a change of a row (`install`, `uninstall`, `delete`): the pack's name, and its
 * path, since two builds of the same tile, in two output folders, share the name. */
export function libraryRequest(e, action) {
  return { path: `/api/library/${encodeURIComponent(entryName(e))}/${action}`, body: { path: e.path ?? null } };
}

/** Title and paragraphs of the delete confirmation: X-Plane is named only when the tile is in
 * it, the size only when the engine knows it. */
export function deleteQuestion(e) {
  const title = t("library.delete_title", { tile: e.tile });
  if (e.present === false) {
    return { title, body: [e.installed ? t("library.delete_missing_xplane") : t("library.delete_missing")] };
  }
  const size = typeof e.size_bytes === "number" && e.size_bytes > 0 ? fmtBytes(e.size_bytes) : null;
  let first;
  if (e.installed) first = size ? t("library.delete_xplane_size", { size }) : t("library.delete_xplane");
  else first = size ? t("library.delete_files_size", { size }) : t("library.delete_files");
  return { title, body: [first, t("library.delete_cache")] };
}

/** The toast after a delete: the space freed, from 1 MB. A tile built a few minutes ago frees
 * next to nothing at once (its cache stays for a build that may still use it, and goes at the
 * next delete or osxp clean): "830 B freed" would only puzzle. With the engine's `warning` (the
 * tile is gone, its cache space could not be freed), no size: that space comes back later. */
export function deletedMessage(tile, freedBytes, warning = null) {
  if (typeof warning === "string") return t("library.deleted_cache_later", { tile });
  return freedBytes >= 1e6 ? t("library.deleted_freed", { tile, size: fmtBytes(freedBytes) }) : t("library.deleted", { tile });
}

/** "BI · Standard" then "ZL16" as secondary text (plain words first); the source's full name
 * and the detail level at the tile's latitude in the tooltip. */
function imageryCell(e) {
  const zl = Number(e.zl) || 0;
  const level = zl ? tOpt(`detail.${zl}`) : null;
  const text = [e.provider, level || (zl ? `ZL${zl}` : "")].filter(Boolean).join(" · ");
  if (!text) return h("td", null, t("library.no_zl"));
  const source = state.providers.find((p) => p.code === e.provider);
  const tip = [source?.name, zl ? detailLabel(zl, tileLat(e.tile) + 0.5) : null].filter(Boolean).join(" — ");
  return h("td", { title: tip || null }, text, level ? [" ", h("span", { class: "library-zl" }, `ZL${zl}`)] : null);
}

/** A small folder, drawn like the page's other icons (the template in index.html). */
function folderIcon() {
  return $("tpl-folder-icon").content.firstElementChild.cloneNode(true);
}

function libraryRow(e) {
  const key = libraryKey(e);
  const busy = libraryBusy.has(key);
  const present = e.present !== false;
  const byOsxp = e.built_by === "osxp";
  const jobs = activeJobs();
  // A tile OrthoStudio XP built, in a build under way or waiting: the end of the build decides what
  // X-Plane shows of it (the engine refuses meanwhile). Tiles are deleted only between builds.
  const inBuild = byOsxp && tilesInBuilds(jobs).has(e.tile);
  const building = jobs.length > 0;
  // Two action columns, so that the buttons line up from row to row. A tile whose files are gone
  // can only be deleted (the engine then forgets it); only OrthoStudio XP deletes the tiles it built.
  let xplaneButton = null;
  if (present && e.installed) {
    xplaneButton = h("button", { type: "button", class: "btn btn-small", title: inBuild ? t("library.in_build_help") : t("library.remove_help"), disabled: busy || inBuild, onclick: () => libraryAction(e, "uninstall") }, t("library.remove"));
  } else if (present) {
    xplaneButton = h("button", { type: "button", class: "btn btn-small btn-primary", title: inBuild ? t("library.in_build_help") : t("library.add_help"), disabled: busy || inBuild, onclick: () => libraryAction(e, "install") }, t("library.add"));
  }
  const deleteButton = byOsxp
    ? h("button", { type: "button", class: "btn btn-small btn-danger", title: building ? t("library.delete_wait") : t("library.delete_help"), disabled: busy || building, onclick: () => deleteLibraryTile(e) }, t("library.delete"))
    : null;
  let missing = null;
  if (!present) {
    missing = pill(t("library.missing"), "warn");
    missing.title = t("library.missing_help");
  }
  let inBuildPill = null;
  if (inBuild) {
    inBuildPill = pill(t("library.in_build"), "running");
    inBuildPill.title = t("library.in_build_help");
  }
  const overlayMark = overlayPill(e, busy || inBuild);
  const label = revealLabel(state.status?.platform);
  const revealButton = present && e.path
    ? h("button", { type: "button", class: "btn btn-small btn-icon reveal-btn", title: label, "aria-label": `${label}: ${e.tile}`, onclick: () => revealPath(e.path) }, folderIcon())
    : null;
  return h("tr", { dataset: { key }, "aria-busy": busy ? "true" : null },
    h("td", { title: e.path || null }, h("span", { class: "tile-name" }, e.tile), missing ? [" ", missing] : null, inBuildPill ? [" ", inBuildPill] : null, overlayMark ? [" ", overlayMark] : null),
    imageryCell(e),
    h("td", null, e.installed ? pill(t("app.yes"), "ok") : pill(t("app.no"), "cancelled")),
    h("td", { class: "num" }, fmtBytes(e.size_bytes)),
    h("td", null, byOsxp ? t("library.by_osxp") : t("library.by_ortho4xp")),
    h("td", { class: "library-action" }, revealButton),
    h("td", { class: "library-action" }, xplaneButton),
    h("td", { class: "library-action" }, deleteButton));
}

/** The names of the overlay packs of other tools, as a user knows them. */
export function overlayPackLabel(name) {
  if (/^yAutoOrtho_Overlays$/i.test(name)) return "AutoOrtho";
  if (/^XPME_Overlays$/i.test(name)) return "XPME";
  if (/^yOrtho4XP_Overlays$/i.test(name)) return "Ortho4XP";
  return String(name);
}

function overlayPacks(e) {
  return [...new Set((e.overlay?.others || []).map(overlayPackLabel))].join(", ");
}

/** A tile's roads, forests and buildings when another pack draws them too, or instead (engine's
 * `overlay_states`): a warning when they come twice or not at all, a button to take them back
 * when they were left to another pack. */
function overlayPill(e, disabled) {
  const state = e.overlay?.state;
  const packs = overlayPacks(e);
  if (state === "double") {
    const mark = pill(t("library.overlay_double"), "warn");
    mark.title = t("library.overlay_double_help", { packs });
    return mark;
  }
  if (state === "missing") {
    const mark = pill(t("library.overlay_missing"), "fail");
    mark.title = t("library.overlay_missing_help");
    return mark;
  }
  if (state === "left") {
    return h("button", { type: "button", class: "pill pill-hit overlay-back", title: t("library.overlay_left_help", { packs }), disabled, onclick: () => setOverlays([e.tile], "own") }, t("library.overlay_left", { packs }));
  }
  return null;
}

/** Above the table: the squares whose roads come twice, or not at all, and the one click that
 * mends each (user request, 2026-09-14). */
function renderOverlayNotices(rows) {
  const box = clear($("library-overlays"));
  const busy = tilesInBuilds(activeJobs());
  const tiles = (state) => rows.filter((e) => e.overlay?.state === state && !busy.has(e.tile));
  const doubles = tiles("double");
  const missing = tiles("missing");
  box.hidden = !doubles.length && !missing.length;
  if (doubles.length) {
    const packs = [...new Set(doubles.flatMap((e) => (e.overlay.others || []).map(overlayPackLabel)))].join(", ");
    box.append(h("div", { class: "overlay-notice" },
      h("p", null, t("library.overlays_double", { n: doubles.length, tiles: doubles.map((e) => e.tile).join(" "), packs })),
      h("button", { type: "button", class: "btn btn-small", onclick: () => setOverlays(doubles.map((e) => e.tile), "others") }, t("library.overlays_leave", { packs }))));
  }
  if (missing.length) {
    box.append(h("div", { class: "overlay-notice overlay-notice-fail" },
      h("p", null, t("library.overlays_missing", { n: missing.length, tiles: missing.map((e) => e.tile).join(" ") })),
      h("button", { type: "button", class: "btn btn-small", onclick: () => setOverlays(missing.map((e) => e.tile), "own") }, t("library.overlays_take_back"))));
  }
}

/** Leave the roads of `tiles` to the other packs (`others`) or take OrthoStudio XP's back (`own`),
 * then read the Library again; a refusal (X-Plane running, a tile in a build) is a card. */
async function setOverlays(tiles, use) {
  const errors = clear($("library-errors"));
  try {
    const res = await api("POST", "/api/library/overlays", { use, tiles });
    const n = (res?.changed || []).length;
    toast(use === "others" ? t("library.overlays_left", { n }) : t("library.overlays_taken_back", { n }));
  } catch (err) {
    errors.append(libraryErrorCard(err, { tile: tiles.join(" ") }));
  }
  await loadLibrary();
}

function renderLibrary() {
  const body = $("library-body");
  // Which X-Plane the "In X-Plane" column speaks of: a user found nothing in the Custom Scenery of
  // the X-Plane he flies, since OrthoStudio XP used another one of his Mac (2026-09-17).
  const xplane = $("library-xplane");
  const path = state.status?.xplane?.path;
  xplane.hidden = !path;
  if (path) xplane.textContent = t("library.xplane", { path });
  // New buttons replace the old ones: a focused button's successor, same row and column, keeps it.
  const cell = body.contains(document.activeElement) ? document.activeElement.closest("td") : null;
  const kept = cell ? { key: cell.parentElement.dataset.key, column: cell.cellIndex } : null;
  clear(body);
  $("library-building").hidden = !activeJobs().length;
  const rows = libraryTiles(state.library);
  renderOverlayNotices(rows);
  if (!rows.length) {
    body.append(h("tr", null, h("td", { colspan: 8, class: "placeholder" }, t("library.empty"))));
    return;
  }
  for (const e of rows) body.append(libraryRow(e));
  if (kept) libraryRowByKey(kept.key)?.cells[kept.column]?.querySelector("button:not(:disabled)")?.focus();
}

function libraryRows() {
  return [...$("library-body").rows];
}

function libraryRowByKey(key) {
  return libraryRows().find((r) => r.dataset.key === key) || null;
}

/** Focus went away with a disabled or re-rendered button: back to the same row (the button of the
 * same column when it is still there), else to the row now in its place (after a delete), else to
 * the title. Never taken from where the user moved it. */
function focusLibraryRow(key, index, column = -1) {
  if (state.screen !== "library") return;
  const active = document.activeElement;
  if (active && active !== document.body) return;
  const rows = libraryRows();
  const row = libraryRowByKey(key) || rows[Math.min(index, rows.length - 1)];
  const enabled = "button:not(:disabled)";
  const button = row?.cells[column]?.querySelector(enabled) || row?.querySelector(enabled);
  if (button) button.focus();
  else $("library-title").focus({ preventScroll: true });
}

/** The Library's words for the refusals it knows: the engine's own speak of installing, paths,
 * orthostudio.toml or osxp clean. */
const LIBRARY_REFUSALS = {
  XP_RUNNING: () => [t("library.err_xp_running"), t("library.err_xp_running_remedy")],
  SYS_BUSY: () => [t("library.err_busy"), t("library.err_busy_remedy")],
  SYS_TILE_IN_BUILD: () => [t("library.err_in_build"), t("library.err_in_build_remedy")],
  SYS_PACK_NOT_OSXP: () => [t("library.err_not_osxp"), t("library.err_not_osxp_remedy")],
  SYS_WRITE_FAILED: () => [t("library.err_write_failed"), t("library.err_write_failed_remedy")],
  SYS_WORKING_DIR_INVALID: () => [t("library.err_gone"), t("library.err_gone_remedy")],
  XP_PACK_CONFLICT: () => [t("library.err_conflict"), t("library.err_conflict_remedy")],
};

/**
 * What the error card of a failed change shows: for a refusal the Library knows, its own words, a
 * blocking card (the change did not happen) and no action button (the remedy is not in Settings);
 * any other error as the engine sent it.
 */
export function libraryCardContent(detail) {
  const words = LIBRARY_REFUSALS[detail?.code]?.() ?? null;
  return { error: words ? { ...detail, severity: "blocking", action: "none" } : detail, words };
}

function libraryErrorCard(err, e) {
  const found = errorDetail(err);
  const d = found && found.code ? found : { code: "SYS_INTERNAL_ERROR", message: errorMessage(err), severity: "blocking" };
  const { error, words } = libraryCardContent(d);
  const card = errorCard(error, { tile: e.tile });
  if (words) {
    const message = card.querySelector(".error-msg");
    message.textContent = words[0];
    card.querySelector(".error-remedy").textContent = words[1];
    // The card has no details area: the engine's own text (paths, reason) stays in the tooltip.
    message.title = [d.message, d.remedy].filter(Boolean).join(" ");
  }
  return card;
}

/**
 * One change of a library row: its buttons are disabled until the engine answers; a success runs
 * `onSuccess`. Either way the library (and with it the map) and the status bar are read again,
 * since a refusal may come after the engine changed something (a deletion stopped halfway, a
 * tile deleted meanwhile); then a refusal (or a network failure) becomes an error card in
 * #library-errors.
 */
async function runLibraryChange(e, request, onSuccess) {
  const key = libraryKey(e);
  if (libraryBusy.has(key)) return;
  const errors = clear($("library-errors"));
  const rows = libraryRows();
  const index = Math.max(0, rows.findIndex((r) => r.dataset.key === key));
  libraryBusy.add(key);
  const row = rows[index]?.dataset.key === key ? rows[index] : null;
  // The button used (after the dialog, the browser gave it the focus back): focus returns to it.
  const used = row?.contains(document.activeElement) ? document.activeElement.closest("td")?.cellIndex ?? -1 : -1;
  if (row) {
    row.setAttribute("aria-busy", "true");
    for (const b of row.querySelectorAll("button")) b.disabled = true;
  }
  let failure = null;
  try {
    const result = await request();
    onSuccess?.(result);
  } catch (err) {
    failure = err;
  }
  libraryBusy.delete(key);
  await loadLibrary();
  loadStatus();
  const card = failure ? libraryErrorCard(failure, e) : null;
  if (card) errors.append(card);
  focusLibraryRow(key, index, used);
  card?.scrollIntoView?.({ block: "nearest" });
}

/** "Add to X-Plane" / "Remove from X-Plane": `POST /api/library/{name}/install|uninstall`. */
function libraryAction(e, action) {
  const req = libraryRequest(e, action);
  return runLibraryChange(e, () => api("POST", req.path, req.body));
}

/** The delete question in the page's modal <dialog>; resolves true for "Delete". Focus starts on
 * "Keep it" (autofocus), Escape keeps the tile, and the browser gives focus back on closing. */
function confirmDelete(e) {
  const dialog = $("library-delete");
  if (dialog.open) return Promise.resolve(false);
  const { title, body } = deleteQuestion(e);
  $("library-delete-title").textContent = title;
  clear($("library-delete-text")).append(...body.map((line) => h("p", null, line)));
  dialog.returnValue = "";
  // Browsers close a modal dialog on Escape by themselves, but may skip it (no key code, repeated
  // presses): closing here keeps the promise that Escape always keeps the tile.
  dialog.onkeydown = (ev) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    dialog.close();
  };
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "delete"), { once: true });
    dialog.showModal();
  });
}

/** "Delete…" (tiles built by OrthoStudio XP): asks first, then deletes and says how much space was freed. */
async function deleteLibraryTile(e) {
  if (libraryBusy.has(libraryKey(e)) || !(await confirmDelete(e))) return;
  const req = libraryRequest(e, "delete");
  await runLibraryChange(e, () => api("POST", req.path, req.body), (res) => toast(deletedMessage(res?.tile || e.tile, res?.freed_bytes, res?.warning)));
}

/** How long the folder dialog may take to show before the page says it is on its way (the File
 * Explorer's took seconds on a user's Windows). */
const FOLDER_OPENING_TOAST_MS = 800;

/** A folder dialog is being asked for or shown: a click on another Choose… button waits for it. */
let folderAsked = false;

/** A folder chosen in the platform's own dialog, opened by the engine (POST /api/choose-folder):
 * its path, or null when the user cancelled or no dialog could open (a toast says why).
 *
 * While the dialog opens, a second click says so instead of asking the engine again: nothing
 * showed for seconds on Windows, and the click after it answered "SYS_BUSY: A folder dialog is
 * already open" (a user, 2026-09-15). */
async function chooseFolder(prompt, start = null) {
  if (folderAsked) {
    toast(t("folder.already_open"));
    return null;
  }
  folderAsked = true;
  const slow = setTimeout(() => toast(t("folder.opening")), FOLDER_OPENING_TOAST_MS);
  try {
    const res = await api("POST", "/api/choose-folder", { prompt, start: start || null });
    return res?.path || null;
  } catch (err) {
    toast(errorMessage(err), "fail");
    return null;
  } finally {
    clearTimeout(slow);
    folderAsked = false;
  }
}

async function importOrtho4xp(ev) {
  ev.preventDefault();
  const dir = $("import-path").value.trim();
  if (!dir) return;
  const out = $("import-result");
  out.textContent = t("app.loading");
  try {
    const res = await api("POST", "/api/library/import-ortho4xp", { folder: dir });
    const n = Array.isArray(res) ? res.length : res?.imported ?? res?.entries?.length ?? 0;
    out.textContent = t("library.imported", { n });
    await loadLibrary();
    loadStatus();
  } catch (err) {
    out.textContent = errorMessage(err);
  }
}

// ------------------------------------------------------------------ Settings: questions in plain words

/** The Settings screen (settings.js): questions, presets, For experts; the draft is saved by Save. */
function renderSettings(message, kind) {
  if (!state.schema || !state.settingsDraft) return;
  renderSettingsView(
    { root: $("settings-form"), presets: $("settings-presets"), questions: $("settings-questions"), experts: $("settings-expert-fields") },
    {
      dom: { h, clear },
      draft: state.settingsDraft,
      schema: state.schema,
      providers: state.providers,
      xplane: state.status?.xplane || null,
      dataDir: state.status?.data_dir || null,
      home: state.status?.home || null,
      platform: state.status?.platform || null,
      reveal: state.status?.platform ? { label: revealLabel(state.status.platform), open: revealPath } : null,
      chooseFolder: state.engineOutdated ? null : chooseFolder,
      changed: (text, level) => renderSettings(text, level),
    },
  );
  const dirty = !sameValue(state.settingsDraft, state.settings);
  $("settings-status").textContent = message || (dirty ? t("settings.unsaved") : "");
  $("settings-status").classList.toggle("is-fail", kind === "fail");
}

/** The Plan says in a few words what the build will use, and where to change it. */
function renderPlanSettings() {
  const parts = settingsSummary(state.settings);
  $("plan-settings-summary").textContent = parts.length ? t("plan.settings_summary", { list: parts.join(" · ") }) : "";
  renderPlanXplane();
}

async function saveSettings(ev) {
  ev.preventDefault();
  const err = $("settings-error");
  err.hidden = true;
  try {
    state.settings = await api("PUT", "/api/settings", state.settingsDraft);
    state.settingsDraft = structuredClone(state.settings);
    // The Plan follows what was saved: its source and level, and the cost worked out again.
    $("zl-select").value = "";
    planChanged();
    // The X-Plane folder may have changed: the Settings line, step 3 and the status bar follow it,
    // and step 3 no longer shows a refusal made with the settings of before.
    await loadStatus();
    showPlanError(null);
    renderSettings(t("settings.saved"));
    setTimeout(() => {
      if (state.screen === "settings" && sameValue(state.settingsDraft, state.settings)) $("settings-status").textContent = "";
    }, 3000);
    renderProviders(state.settings.essential?.provider);
    renderPlanSettings();
  } catch (e) {
    err.hidden = false;
    // the page's words for the code and its remedy: which folder, and what to do (a user on a
    // computer without X-Plane read only "XP_DIR_NOT_FOUND: The X-Plane 12 folder was not found")
    const d = errorDetail(e);
    const detail = d?.code ? codeWords(d).filter(Boolean).join(" ") : errorMessage(e);
    err.textContent = t("settings.invalid", { detail });
  }
}

function resetSettings() {
  state.settingsDraft = structuredClone(state.settings);
  $("settings-error").hidden = true;
  renderSettings();
}

/** The default value of every setting, to save or not. */
function defaultSettings() {
  if (!state.schema) return;
  state.settingsDraft = defaultsKeepingFolders(state.schema, state.settingsDraft);
  renderSettings();
}

// ------------------------------------------------------------------ theme + language

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "dark" || theme === "light") root.dataset.theme = theme;
  else delete root.dataset.theme;
  planMap?.themeChanged();
  try {
    if (theme) localStorage.setItem("osxp.theme", theme);
    else localStorage.removeItem("osxp.theme");
  } catch (_e) {
    // ignore
  }
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  applyTheme(dark ? "light" : "dark");
}

function rerenderAll() {
  applyStatic(document);
  renderStatus();
  renderProviders();
  renderTiles();
  renderPlanPanel();
  renderPlanSettings();
  renderPlanError();
  renderJobList();
  renderJob();
  renderLibrary();
  renderSettings();
  planMap?.rerender();
}

// ------------------------------------------------------------------ boot

async function boot() {
  trackStatusbarHeight();
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  try {
    const theme = localStorage.getItem("osxp.theme");
    if (theme) applyTheme(theme);
  } catch (_e) {
    // ignore
  }
  applyStatic(document);
  $("mock-badge").hidden = !MOCK;

  $("nav").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".nav-btn");
    if (btn) showScreen(btn.dataset.screen);
  });
  window.addEventListener("hashchange", routeFromHash);
  $("lang-select").addEventListener("change", (ev) => {
    setLanguage(ev.target.value, true);
    rerenderAll();
  });
  $("theme-btn").addEventListener("click", toggleTheme);

  $("tiles-text").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      addTilesFromText();
    }
  });
  $("tiles-text").addEventListener("change", addTilesFromText);
  $("latlon-add").addEventListener("click", addTileFromLatLon);
  $("tiles-clear").addEventListener("click", clearTiles);
  $("icao-input").addEventListener("input", onIcaoInput);
  $("icao-input").addEventListener("keydown", onIcaoKey);
  $("icao-input").addEventListener("blur", () => {
    icaoResults = [];
    renderIcaoList();
  });
  $("icao-add").addEventListener("click", addTilesFromIcao);
  $("sources-open").addEventListener("click", openSources);
  $("sources-close").addEventListener("click", () => $("sources-dialog").close());
  $("source-try").addEventListener("click", trySource);
  $("sources-form").addEventListener("submit", addSource);
  $("provider-select").addEventListener("change", () => {
    renderProviderAttribution();
    renderSourceCoverage();
    renderZlOptions();
    planChanged();
    planMap?.planChanged();
  });
  $("zl-select").addEventListener("change", () => {
    planChanged();
    planMap?.planChanged();
  });
  $("estimate-btn").addEventListener("click", estimate);
  $("build-install-btn").addEventListener("click", () => build(true));
  $("build-only-btn").addEventListener("click", () => build(false));
  $("import-form").addEventListener("submit", importOrtho4xp);
  $("import-choose").addEventListener("click", async () => {
    const path = await chooseFolder(t("library.import_prompt"), $("import-path").value.trim() || null);
    if (path) $("import-path").value = path;
  });
  $("disk-free").addEventListener("click", freeSpace);
  $("jobs-clear").addEventListener("click", clearJobs);
  $("disk-images").addEventListener("change", renderDisk);
  $("settings-form").addEventListener("submit", saveSettings);
  $("settings-reset").addEventListener("click", resetSettings);
  $("settings-defaults").addEventListener("click", defaultSettings);
  $("plan-settings-change").addEventListener("click", () => showScreen("settings"));
  $("plan-xplane-choose").addEventListener("click", chooseXplaneFolder);
  $("quit-btn").addEventListener("click", quitOsxp);
  $("disk-reveal").addEventListener("click", () => revealPath(dataFolderShown(state.status)));

  // The expert band remembers whether it was open: a user who works in there should not have to
  // reopen it at every visit, and one who never opens it keeps a short page (2026-09-18).
  const experts = $("settings-experts");
  try {
    if (localStorage.getItem("osxp.experts") === "open") experts.open = true;
  } catch {
    // private window, blocked storage: the band simply starts closed
  }
  experts.addEventListener("toggle", () => {
    try {
      localStorage.setItem("osxp.experts", experts.open ? "open" : "closed");
    } catch {
      // nothing to remember, nothing to report
    }
  });

  planMap = createPlanMap({
    mock: MOCK,
    api,
    toast,
    errorMessage,
    errorDetail,
    h,
    clear,
    tiles: () => state.tiles,
    toggleTile,
    providers: () => state.providers,
    planProvider: () => $("provider-select").value,
    planZl: () => Number($("zl-select").value),
    library: () => state.library,
    building: () => buildingOnMap(),
    engineOutdated: () => Boolean(state.engineOutdated),
    onZonesChanged: () => planChanged(),
  });
  renderTiles();
  renderPlanPanel();
  startPresence();
  const results = await Promise.allSettled([
    loadStatus(),
    planMap.load(),
    api("GET", "/api/library").then((l) => {
      state.library = Array.isArray(l) ? l : [];
    }),
    api("GET", "/api/providers").then((p) => {
      state.providers = Array.isArray(p) ? p : p.providers || [];
    }),
    api("GET", "/api/settings").then((s) => {
      state.settings = s;
      state.settingsDraft = structuredClone(s);
    }),
    api("GET", "/api/settings/schema").then((s) => {
      state.schema = s;
    }),
    api("GET", "/api/jobs").then((j) => {
      state.jobs = Array.isArray(j) ? j : [];
    }),
  ]);
  for (const r of results) if (r.status === "rejected") toast(errorMessage(r.reason), "fail");
  renderProviders(state.settings?.essential?.provider || "BI");
  renderPlanSettings();
  routeFromHash();
  // A build started before the page was opened (or reloaded) shows on the map too, and the tiles
  // of those waiting cannot be chosen again.
  if (!state.jobId && state.status?.active_job) watchJob(state.status.active_job);
  jobsChanged();
}

/** The status bar is pinned at the bottom (a user asked): its height, which grows when its items
 * wrap, keeps Settings' sticky Save, the toast and scrolling to a field clear of it. */
function trackStatusbarHeight() {
  const bar = $("statusbar");
  const set = () => document.documentElement.style.setProperty("--statusbar-h", `${bar.offsetHeight}px`);
  set();
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(set).observe(bar);
}

if (typeof document !== "undefined" && document.getElementById("main")) boot();
