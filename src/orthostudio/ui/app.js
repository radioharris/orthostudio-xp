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
  homely,
  language,
  reliefName,
  setLanguage,
  setUserHome,
  t,
  tOpt,
} from "./i18n.js";
import { PHOTO_LOOKS, photoValues } from "./colour.js";
import { TEXTURE_MB, ZONES_FORMAT, normalizeZone, parseTile, routeLength, tileName, tilesAlong, validateZonesDocument, zoneTextureKeys } from "./geo.js";
import { createPlanMap, detailLabel, detailName } from "./map.js";
import { colourPreview } from "./preview.js";
import { defaultsKeepingFolders, renderSettingsView, sameValue, settingsSummary } from "./settings.js";
import { bindFind, bindFindKeys, findForget } from "./find.js";
import { bindZoom } from "./zoom.js";
import { countryName, sourceAddressProblem, sourceGroups, sourceGroupTitle, sourceLabel, tilesNotCovered } from "./sources.js";

// ------------------------------------------------------------------ constants

// `location` is read only in a browser: node imports this module in the tests.
const PARAMS = pageParams(typeof location === "undefined" ? "" : location.href);
const MOCK = isOn(PARAMS.get("mock"));
/** Options written after the route (`#plan?mock=1`), kept as the route changes: read once, since
 * moving to another screen rewrites the hash and would otherwise drop them on the next load. */
const HASH_OPTIONS =
  typeof location === "undefined" || !location.hash.includes("?")
    ? ""
    : `?${location.hash.split("?").slice(1).join("?")}`;

/** The page's options, from the address: the query, plus one written after the `#` route.
 *
 * `?mock=1` is the switch, and `#plan?mock=1` a natural way to write it that silently showed the
 * real engine instead (a user, 2026-09-18): both are read, the query first. */
export function pageParams(href) {
  const text = String(href || "");
  const route = text.indexOf("#");
  const query = (part) => {
    const mark = part.indexOf("?");
    return new URLSearchParams(mark >= 0 ? part.slice(mark + 1) : "");
  };
  const params = query(route >= 0 ? text.slice(0, route) : text);
  if (route >= 0) {
    for (const [name, value] of query(text.slice(route))) {
      if (!params.has(name)) params.append(name, value);
    }
  }
  return params;
}

/** Whether an option written in the address is on: `1`, `true`, `yes`, `on`, or the bare name. */
export function isOn(value) {
  return value !== null && ["", "1", "true", "yes", "on"].includes(String(value).toLowerCase());
}
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
  DEM_OVERLAY_UNAVAILABLE: () => t("works.dec_no_overlay"),
  textures_second_pass: () => t("works.dec_second_pass"),
  textures_recovered: () => t("works.dec_recovered"),
};

// ------------------------------------------------------------------ state

const state = {
  screen: "plan",
  status: null,
  /** GET /api/sizes: the store's and the downloaded images' bytes, which the status bar shows when
   * they come (measured apart from the status: no screen waits for them). */
  sizes: null,
  providers: [],
  settings: null,
  schema: null,
  settingsDraft: null,
  tiles: [],
  /** The squares that carry a detail level of their own (`{tile: zl}`), set by the flight
   * plan's two groups; the others take the level of step 1's list. */
  tileZl: {},
  /** The level of step 1's list, kept while the list reads "Several levels". */
  planZl: null,
  // what the pilot last chose in step 1's list, which is not always what the chosen squares
  // share: the flight plan's two ends take this one (2026-09-23)
  zlChosen: null,
  airport: null,
  /** The flight plan drawn on the map: `{points: [{icao, name, lat, lon}]}` or null. */
  route: null,
  plan: null,
  /** Step 3's error: `{message}` (the page's own sentence) or `{err}` (an engine answer). */
  planError: null,
  jobs: [],
  jobId: null,
  job: null,
  log: [],
  source: null,
  library: [],
  /** GET /api/library answered at boot, or failed: the map waits for it, to open on the tiles
   * installed rather than on Europe and then jump (app.js boot). */
  libraryKnown: false,
  /** GET /api/patches: the tiles the saved folder of patches has something for (the Plan). */
  patches: null,
  /** The same for the folder Settings shows, saved or not. */
  settingsPatches: null,
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

/**
 * Make the children of `live` show what those of `fresh` show, changing only what differs: a text,
 * an attribute, a field's value. A screen drawn anew at each change flashed in the Mac's window,
 * whose engine painted every node again, and a number field was replaced under its own arrows (a
 * user, 2026-09-21): what has not changed is not touched now, whatever the change. Children are
 * matched by `id`, else by `data-focus-key`, else by their place among those of the same kind.
 */
export function morphChildren(live, fresh) {
  const keyOf = (n) => (n.nodeType === 1 ? n.id || n.dataset?.focusKey || null : null);
  const kindOf = (n) => (n.nodeType === 1 ? n.tagName : `#${n.nodeType}`);
  const keyed = new Map();
  const unkeyed = new Map();
  for (const n of live.childNodes) {
    const key = keyOf(n);
    if (key) keyed.set(key, n);
    else unkeyed.set(kindOf(n), [...(unkeyed.get(kindOf(n)) || []), n]);
  }
  const wanted = [...fresh.childNodes].map((n) => {
    const key = keyOf(n);
    const match = key ? keyed.get(key) : unkeyed.get(kindOf(n))?.shift();
    if (key) keyed.delete(key);
    return match ? morphNode(match, n) : n;
  });
  // in order, moving only what is out of place; what is left over goes
  let at = live.firstChild;
  for (const n of wanted) {
    if (n === at) at = at.nextSibling;
    else live.insertBefore(n, at);
  }
  while (at) {
    const next = at.nextSibling;
    live.removeChild(at);
    at = next;
  }
}

/** Elements whose handlers act on what they were drawn with: one of them whose attributes change
 * is replaced, and comes in with handlers of its own. */
const MORPH_WHOLE = new Set(["INPUT", "SELECT", "TEXTAREA", "BUTTON", "CANVAS"]);

function sameAttributes(a, b) {
  if (a.attributes.length !== b.attributes.length) return false;
  for (const { name, value } of a.attributes) if (b.getAttribute(name) !== value) return false;
  return true;
}

/**
 * `live` made to show what `fresh` shows, when it can be kept, else `fresh` to put in its place.
 * A control's state lives in its properties (value, ticked), set where it differs, except the value
 * of the field being typed in, which is the user's. An element whose content is drawn later (a
 * canvas) says what it shows in `data-version`, and is kept or replaced whole by it.
 */
function morphNode(live, fresh) {
  if (live.nodeType !== fresh.nodeType) return fresh;
  if (live.nodeType !== 1) {
    if (live.nodeValue !== fresh.nodeValue) live.nodeValue = fresh.nodeValue;
    return live;
  }
  if (live.tagName !== fresh.tagName) return fresh;
  const versioned = live.dataset.version != null || fresh.dataset.version != null;
  if (versioned || MORPH_WHOLE.has(live.tagName)) {
    if (!sameAttributes(live, fresh)) return fresh;
  } else {
    for (const { name } of [...live.attributes]) if (!fresh.hasAttribute(name)) live.removeAttribute(name);
    for (const { name, value } of fresh.attributes) if (live.getAttribute(name) !== value) live.setAttribute(name, value);
  }
  if (!versioned) morphChildren(live, fresh);
  const typing = typeof document !== "undefined" && live === document.activeElement;
  if ("value" in fresh && live.tagName !== "LI" && !typing && live.value !== fresh.value) live.value = fresh.value;
  if ("checked" in fresh && live.checked !== fresh.checked) live.checked = fresh.checked;
  return live;
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

/** A mock node's weight in progress, as the engine's `progress.weight_of`: nothing for a hit,
 * which is work there was none of. Work planned that will not happen still counts. */
function mockWeight(n) {
  if (n.status === "hit") return 0;
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
    // The first tile is built with hand-made patches, so that both halves of the report
    // line can be seen in the mock: which tiles had patches, and which files.
    const patches = i === 0 ? [`${tile.tile}-airport.patch.osm`, "lake-shore.patch.osm"] : [];
    return { tile: tile.tile, provider: tile.provider, zl: tile.zl, ok, pack_dir: packDir, overlay_dsf: null, installed: ok && tile.install, repaired: [], stages: {}, osm: {}, patches, nodes };
  });
  for (const tile of tiles) {
    if (tile.pack_dir) decisions.push({ tile: tile.tile, kind: "pack", path: tile.pack_dir, bytes: 2656881226 + 110000000 * tiles.indexOf(tile), installed: tile.installed });
  }
  // A relief laid over another reaches part of the country only: the first square of such a
  // build gets none, so the line that says what was read can be seen in the mock as well.
  if (doc.relief === "canada" || doc.relief === "south_america") {
    decisions.push({ tile: tiles[0]?.tile, kind: "degraded", code: "DEM_OVERLAY_UNAVAILABLE", count: 1, message: "No elevation data over that cell: the relief laid under it is used alone there." });
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
  if (p === "/api/patches" && method === "GET") {
    // Like the engine: the saved folder, or the one Settings shows (`dir`, empty for the default).
    // The default holds patches for a square of the mock's library and for SBCF's, whose Ortho4XP
    // tree a user sent; a folder named "empty" holds none, "missing" is not there.
    const typed = url.searchParams.get("dir");
    const saved = typed == null ? (await mockFile("settings")).expert?.patches_dir || "" : typed;
    const dir = saved.trim() || "/Users/pilot/.orthostudio/patches";
    if (/missing/i.test(dir)) return { dir, exists: false, tiles: {} };
    if (/empty/i.test(dir)) return { dir, exists: true, tiles: {} };
    return { dir, exists: true, tiles: { "-20-044": ["SBCF.patch.osm"], "+43+005": ["LFML.patch.osm", "harbour.patch.osm"] } };
  }
  if (p === "/api/reveal" && method === "POST") {
    if (!String(body?.path || "").startsWith("/")) throw mockError(403, "SYS_FORBIDDEN_PATH", `${body?.path} is not a folder of OrthoStudio XP, of X-Plane or of a tile of the library.`, "The page only shows the folders OrthoStudio XP works with.");
    return { revealed: body.path };
  }
  if (p === "/api/engine") {
    // Like the engine: who serves, answered at once, even when the status is slow (fail=slow-status).
    const status = await mockFile("status");
    return { version: status.version, api_level: status.api_level, can_quit: status.can_quit, active_job: mockActiveRun()?.doc.id ?? null, engine: { root: "/mock", pid: 0 } };
  }
  if (p === "/api/update") {
    // `?mock=1&update=0.1.10`: that version is out, so the banner can be seen without a release.
    // The link stays on the page: its files carry no address outside it (test_no_external_url),
    // and the engine is the one that knows GitHub's.
    const current = (await mockFile("status")).version;
    const latest = PARAMS.get("update") || null;
    const available = Boolean(latest) && latest !== current;
    return { current, latest, url: available ? `#release-${latest}` : null, available };
  }
  if (p === "/api/simbrief") {
    // A plan like SimBrief's, so the route and its buttons can be tried without an account.
    if (MOCK_FAIL === "simbrief") throw mockError(404, "CFG_SIMBRIEF_USER_UNKNOWN", "SimBrief does not know pilot.", "Check the name in Settings: it is your SimBrief name, or your pilot ID.");
    return {
      from: "LSGG",
      to: "LEPA",
      points: [
        { ident: "LSGG", name: "Geneva", lat: 46.2384, lon: 6.1094 },
        { ident: "SOSAL", name: "", lat: 45.5, lon: 5.4 },
        { ident: "BEBIX", name: "", lat: 44.2, lon: 4.6 },
        { ident: "MTG", name: "Montelimar", lat: 43.1, lon: 4.2 },
        { ident: "LEPA", name: "Palma de Mallorca", lat: 39.5517, lon: 2.7388 },
      ],
    };
  }
  if (p === "/api/status") {
    // fail=slow-status: the status took long on users' Windows, and the page waited for it with
    // the menu alone (2026-09-22)
    if (MOCK_FAIL === "slow-status") await delay(6000);
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
  if (p === "/api/sizes") return { store_bytes: 1830000000, chunks_bytes: 412000000 };
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
    const source = { code, name, max_zl: Number(body.max_zl) || 19, attribution: name, terms_url: "", licence: "", alive: null, extent: null, extent_bounds: null, same_as: null, custom: true, url_template: String(body.url_template).trim() };
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
    const zlOf = (name) => body.tiles_zl?.[name] ?? body.zoom_level;
    plan.tiles = tiles.map((name) => ({ ...structuredClone(template), tile: name, zl: zlOf(name), provider: body.provider }));
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
    if (!mock.disk) mock.disk = { store_bytes: 9.8e9, unused_bytes: 2.4e9, images_bytes: 3.1e9, mapcache_bytes: 12e6, relief_bytes: 1.4e9 };
    if (!mock.library) mock.library = await mockFile("library");
    return { ...mock.disk, tiles: libraryTiles(mock.library).length, building: MOCK_FAIL === "busy" || Boolean(mockActiveRun()) };
  }
  if (p === "/api/clean" && method === "POST") {
    if (MOCK_FAIL === "busy" || mockActiveRun()) {
      throw mockError(409, "SYS_BUSY", "A build is running in OrthoStudio XP, and OrthoStudio XP frees space only between builds.", "Wait for the build to finish, or stop it, then try again.");
    }
    if (!mock.disk) mock.disk = { store_bytes: 9.8e9, unused_bytes: 2.4e9, images_bytes: 3.1e9, mapcache_bytes: 12e6, relief_bytes: 1.4e9 };
    const images = Boolean(body?.images);
    const relief = Boolean(body?.relief);
    const freed = mock.disk.unused_bytes;
    const imagesFreed = images ? mock.disk.images_bytes + mock.disk.mapcache_bytes : 0;
    const reliefFreed = relief ? mock.disk.relief_bytes : 0;
    mock.disk = { ...mock.disk, store_bytes: mock.disk.store_bytes - freed, unused_bytes: 0, ...(images ? { images_bytes: 0, mapcache_bytes: 0 } : {}), ...(relief ? { relief_bytes: 0 } : {}) };
    return { format: "osxp-clean-1", freed_bytes: freed, images_freed_bytes: imagesFreed, relief_freed_bytes: reliefFreed, removed: freed ? 12 : 0 };
  }
  if (p === "/api/library/import-ortho4xp") {
    if (!mock.library) mock.library = await mockFile("library");
    // like the engine: what was found, and where it looked. A folder named "empty" finds nothing,
    // so that the other answer can be seen too.
    const searched = [`${body.folder}/Tiles`];
    if (/empty/i.test(body.folder)) return { entries: [], searched };
    const now = Date.now() / 1000;
    const row = { tile: "+42+009", kind: "ortho", provider: "BI", zl: 16, path: `${body.folder}/Tiles/zOrtho4XP_+42+009`, name: "zOrtho4XP_+42+009", built_by: "ortho4xp", installed: false, keys: null, registered_at: now, updated_at: now, size_bytes: 1650000000, present: true, photo: null, built: null, overlay: null };
    if (!mock.library.some((e) => e.path === row.path)) {
      mock.library.push(row);
      mockSortLibrary();
    }
    const { tile, kind, provider, zl, path: packPath, name, built_by: builtBy } = row;
    return { entries: [{ tile, kind, provider, zl, path: packPath, name, built_by: builtBy }], searched };
  }
  m = p.match(/^\/api\/library\/([^/]+)\/forget$/);
  if (m) {
    // Like the engine: the Library forgets an imported tile, and nothing on the disk changes.
    if (!mock.library) mock.library = await mockFile("library");
    const entry = mockLibraryRow(decodeURIComponent(m[1]), body?.path ?? null);
    if (entry.built_by === "osxp") {
      throw mockError(409, "SYS_PACK_NOT_IMPORTED", `${entry.tile} was built by OrthoStudio XP: Delete takes it away, and the list with it.`, "Use Delete for a tile OrthoStudio XP built.");
    }
    if (entry.installed) {
      throw mockError(409, "SYS_PACK_IN_XPLANE", `${entry.tile} is in X-Plane: taken off the list now, the Library could no longer take it out.`, "Remove it from X-Plane first, then remove it from the list.");
    }
    const before = mock.library.length;
    mock.library = mock.library.filter((e) => e !== entry);
    // its overlay row goes with the tile's last imported pack
    const imported = (e) => e.tile === entry.tile && e.built_by === "ortho4xp";
    if (!libraryTiles(mock.library).some(imported)) mock.library = mock.library.filter((e) => !(imported(e) && e.kind === "overlay"));
    return { tile: entry.tile, forgotten: before - mock.library.length, path: entry.path };
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
/** Ended *and* done (the engine's `progress.FINISHED`): what the bar counts as built. */
const NODE_FINISHED = new Set(["done", "hit"]);
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

/** A node's share of its step's work: 1 once it is done, else how far it got (the engine's
 * `progress._fraction`). One that failed, was skipped or was cancelled ended without finishing. */
function nodeFraction(n) {
  if (NODE_FINISHED.has(n.status)) return 1;
  return NODE_ENDED.has(n.status) || n.status === "running" ? clamp01(n.fraction) : 0;
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
      sum += w * (NODE_FINISHED.has(step.status) ? 1 : clamp01(step.fraction));
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

/**
 * A table wider than its box scrolls sideways (`.table-wrap`). Where the scrollbar takes room (a
 * mouse, macOS set to always show it, Windows), WebKit added it after placing what follows the
 * table, which then overlapped the table until the page moved (a user, 2026-09-22). A table that
 * is wider keeps its scrollbar from the start (`.table-wrap.is-wide`), and what follows is placed
 * again under it. Asked when a screen, the Plan's estimate or the Library is drawn, and when the
 * window's size changes; a hidden table measures nothing and loses the mark.
 */
function markWideTables() {
  for (const wrap of document.querySelectorAll(".table-wrap")) {
    wrap.classList.toggle("is-wide", wrap.scrollWidth > wrap.clientWidth + 1);
  }
}

function showScreen(name, arg) {
  if (!SCREENS.includes(name)) name = "plan";
  if (state.screen === "settings" && name !== "settings" && state.settingsDraft && !sameValue(state.settingsDraft, state.settings)) {
    // an answer changed without Save changes nothing yet: say it rather than let the Plan look stale
    toast(t("settings.left_unsaved"));
  }
  state.screen = name;
  for (const s of SCREENS) $(`screen-${s}`).hidden = s !== name;
  measureMapTop(); // the map can only be measured once its screen is shown
  findForget(); // an open Find looks in the screen now shown, not the one left behind
  for (const btn of $("nav").querySelectorAll(".nav-btn")) {
    if (btn.dataset.screen === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  const hash = (arg ? `#${name}/${arg}` : `#${name}`) + HASH_OPTIONS;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  if (name === "works") loadWorks(arg);
  else followBuildUnderWay();
  if (name === "library") {
    loadLibrary();
    refreshJobList(); // its buttons wait for the builds under way or waiting
  }
  if (name === "settings") renderSettings();
  if (name === "plan") {
    loadPatches();
    renderPlanSettings();
    if (state.tiles.length) planChanged(); // the free disk space may have changed meanwhile
    if (state.libraryKnown) planMap?.show(); // else boot shows it when the library answers
  }
  markWideTables(); // the screen's tables measured now that it shows
}

function routeFromHash() {
  // options may be written after the route (`#plan?mock=1`): they are read elsewhere, not here
  const route = location.hash.split("?")[0];
  const m = route.match(/^#(\w+)(?:\/(.+))?$/);
  showScreen(m ? m[1] : "plan", m ? m[2] : undefined);
}

// ------------------------------------------------------------------ status bar

/** The engine API this page needs (orthostudio.api.app.API_LEVEL); a test keeps the two equal. */
const PAGE_API_LEVEL = 21;

async function loadStatus() {
  try {
    state.status = await api("GET", "/api/status");
    state.engineError = null;
  } catch (err) {
    // The engine answered, but not with a status: every screen then stays as empty as it was
    // drawn, and the only sign used to be a line at the foot of the page. A user on Windows saw
    // greyed fields and nothing else, and could not know what to look at (flusi.info, 2026-09-20).
    $("status-xplane").textContent = errorMessage(err);
    state.engineError = errorMessage(err);
    renderEngineBanner();
    return;
  }
  // Paths are shown with "~" for what lies under it (i18n.js homely).
  setUserHome(state.status?.user_home);
  state.engineOutdated = (Number(state.status?.api_level) || 1) < PAGE_API_LEVEL;
  renderEngineBanner();
  renderStatus();
  loadSizes(); // not awaited: measuring the disk must hold nothing back
}

/** GET /api/engine at boot: the top bar's version and Quit, and whether the engine is older than
 * the page, before the status comes (the status says it all again). */
function renderEngine(e) {
  if (!e || state.status) return; // the status came first: it has said it
  $("brand-version").textContent = e.version ? `v${e.version}` : "";
  $("quit-btn").hidden = !e.can_quit;
  state.engineOutdated = (Number(e.api_level) || 1) < PAGE_API_LEVEL;
  renderEngineBanner();
}

/** The sizes of the status bar, asked whenever the status is: after a build or a library action
 * they change too. Measured apart, since on Windows it can take long (a user, 2026-09-22). */
async function loadSizes() {
  if (state.engineOutdated) return; // an engine before API level 20 has no /api/sizes
  try {
    state.sizes = await api("GET", "/api/sizes");
  } catch (_err) {
    return; // the status bar keeps what it showed: the sizes are a hint, never an error
  }
  renderDiskSizes(); // that line alone: the rest of the bar, the checks' open fold, stays
}

/** The status bar's sizes of the store and of the downloaded images: "…" until measured. */
function renderDiskSizes() {
  const sizes = state.sizes;
  $("status-store").textContent = `${t("status.store")}: ${sizes ? fmtBytes(sizes.store_bytes) : "…"} · ${t("status.chunks")}: ${sizes ? fmtBytes(sizes.chunks_bytes) : "…"}`;
}

/** An engine older than the page (started before an update) cannot answer the new routes, and an
 * engine that answers with an error leaves every screen empty: say which it is and what to do,
 * instead of letting "Not Found" and greyed fields speak. */
const WINDOW_NOTE_KEY = "osxp.windowNoteDone";

/** A system that could hold a window of its own, and lacks what draws one, opens the browser
 * instead. The doctor's `window` check says so at the foot of the page, behind a fold nobody
 * opens (a user asked, 2026-09-20): this says it once, at the top, in the reader's own words,
 * with the one line to run or to read beside it. Put away, it stays away. */
function renderWindowNote() {
  const checks = Array.isArray(state.status?.doctor) ? state.status.doctor : [];
  const check = checks.find((c) => c && c.name === "window");
  const install = check?.details?.browser_only ? check.details.install || "" : "";
  let done = false;
  try {
    done = localStorage.getItem(WINDOW_NOTE_KEY) === "1";
  } catch (_e) {
    // a browser that keeps nothing: the note shows again, which is better than not at all
  }
  if (!install || done) {
    $("window-note")?.remove();
    return;
  }
  if ($("window-note")) return;
  const note = h("p", { id: "window-note", class: "window-note" });
  const away = () => {
    try {
      localStorage.setItem(WINDOW_NOTE_KEY, "1");
    } catch (_e) {
      // ignore
    }
    note.remove();
  };
  note.append(
    t("app.window_browser_only"),
    " ",
    h("code", null, install),
    " ",
    // put away, it must still be findable: the doctor's window check keeps the whole sentence,
    // and nothing said where (a user asked, 2026-09-20)
    t("app.window_browser_only_again"),
    " ",
    h("button", { class: "btn btn-small", onclick: away }, t("app.window_browser_only_ok")),
  );
  $("main").prepend(note);
}

const UPDATE_DISMISSED_KEY = "osxp.updateDismissed";

/**
 * A newer version, said once at the top of the page with a link to its release page, where the
 * notes and the installers are. Nothing is downloaded or installed: a user who did not read the
 * forum stayed on the version he had, with bugs fixed since, and nothing told him (2026-09-21).
 * "Not now" hides it until the next version; the engine asks GitHub at most once a day, and not
 * at all when Settings say no (orthostudio/update.py).
 */
async function loadUpdate() {
  try {
    state.update = await api("GET", "/api/update");
  } catch (_err) {
    return; // no answer, or an engine older than the question: there is nothing to say
  }
  renderUpdateNote();
}

function renderUpdateNote() {
  const u = state.update;
  let dismissed = null;
  try {
    dismissed = localStorage.getItem(UPDATE_DISMISSED_KEY);
  } catch (_e) {
    // a browser that keeps nothing shows the note again next time, which is no harm
  }
  if (!u?.available || !u.url || dismissed === u.latest) {
    if ($("update-note")) {
      $("update-note").remove();
      measureMapTop();
    }
    return;
  }
  if ($("update-note")) return;
  const note = h("p", { id: "update-note", class: "window-note update-note", role: "status" });
  const later = () => {
    try {
      localStorage.setItem(UPDATE_DISMISSED_KEY, u.latest);
    } catch (_e) {
      // ignore: it shows again next time
    }
    note.remove();
    measureMapTop();
  };
  note.append(
    t("app.update_available", { version: u.latest, current: u.current }),
    " ",
    // A link, not a button calling window.open: the window hands a clicked target="_blank" link
    // to the system's browser (pywebview, OPEN_EXTERNAL_LINKS_IN_BROWSER), and only a link.
    h("a", { class: "btn btn-small btn-primary", href: u.url, target: "_blank", rel: "noopener" }, t("app.update_open")),
    " ",
    h("button", { type: "button", class: "btn btn-small", onclick: later }, t("app.update_later")),
  );
  $("main").prepend(note);
  measureMapTop(); // it sits above the map and pushes it down
}

/** The checks the doctor failed, by name. Something OrthoStudio XP needs is not working, and a
 * red pill at the foot of the page was the only sign: Triangle4XP missing makes every build
 * impossible, and nothing said so where the user was looking (a user asked, 2026-09-20). */
function failedChecks() {
  const checks = Array.isArray(state.status?.doctor) ? state.status.doctor : [];
  return checks.filter((c) => c && c.status === "fail").map((c) => c.name);
}

/** Open the checks at the foot and bring them into view: what failed says there what it is. */
function showChecks() {
  const details = $("status-doctor")?.querySelector("details");
  if (!details) return;
  details.open = true;
  details.scrollIntoView({ block: "nearest" });
}

function renderEngineBanner() {
  const unread = state.status?.settings_problems || [];
  const failed = failedChecks();
  const words = state.engineOutdated
    ? t("app.engine_outdated")
    : state.engineError
      ? t("app.engine_error", { reason: state.engineError })
      : unread.length
        ? t("app.settings_unread", { list: unread.join(" · ") })
        : failed.length
          ? t("app.checks_failed", { list: failed.join(" · ") })
          : null;
  let banner = $("engine-outdated");
  if (!words) {
    banner?.remove();
    renderWindowNote();
    measureMapTop();
    return;
  }
  if (!banner) {
    banner = h("p", { id: "engine-outdated", class: "engine-outdated", role: "alert" });
    $("main").prepend(banner);
  }
  clear(banner).append(words);
  if (!state.engineOutdated && !state.engineError && unread.length) {
    banner.append(
      " ",
      h("button", { class: "btn btn-small", onclick: () => showScreen("settings") }, t("app.settings_unread_open")),
    );
  }
  if (state.engineError && !state.engineOutdated) {
    banner.append(
      " ",
      h("button", { class: "btn btn-small", onclick: () => location.reload() }, t("app.engine_error_reload")),
    );
  }
  if (!state.engineOutdated && !state.engineError && !unread.length && failed.length) {
    banner.append(
      " ",
      h("button", { class: "btn btn-small", onclick: showChecks }, t("app.checks_failed_show")),
    );
  }
  measureMapTop(); // the banner sits above the map and pushes it down
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  $("brand-version").textContent = s.version ? `v${s.version}` : "";
  const xp = clear($("status-xplane"));
  const x = s.xplane || {};
  xp.append(`${t("status.xplane")}: `);
  if (x.detected && x.path) {
    const shown = homely(x.path);
    xp.append(h("span", { class: "mono", title: shown }, shown.length > 40 ? `…${shown.slice(-38)}` : shown));
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
  renderDiskSizes();
  $("status-library").textContent = t("status.library", { n: fmtInt(s.library_count ?? 0) });
  // Where the tiles and the downloads go: OrthoStudio XP's folder, or the data folder chosen in
  // Settings, whose disk may not be plugged in.
  const place = clear($("status-home"));
  const data = s.data_dir;
  const where = homely(data?.chosen && data.path ? data.path : s.home || "");
  place.append(where);
  place.title = data?.chosen ? t("status.data_dir", { path: where }) : "";
  if (data?.present === false) place.append(" ", pill(t("status.data_missing"), "fail"));
  // And a button that shows it in the Finder: `.orthostudio` starts with a dot, which hides it
  // there, and a user looked for his tiles in vain (2026-09-22).
  const shown = dataFolderShown(s);
  if (shown) {
    const label = revealLabel(s.platform);
    place.append(h("button", { type: "button", class: "btn btn-small btn-icon reveal-btn status-reveal", title: label, "aria-label": label, onclick: () => revealPath(shown) }, folderIcon()));
  }
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
/**
 * Run `fn` and leave `el` where it was on the screen.
 *
 * The squares chosen sit at the top of step 1, so choosing more pushes everything under them down.
 * Chrome puts the scroll back by itself (scroll anchoring); WebKit, which the app's own window
 * uses, does not, and the page looked as if it had scrolled up under the button just pressed (a
 * user, 2026-09-22).
 */
function keepInPlace(el, fn) {
  const top = el?.getBoundingClientRect?.().top;
  fn();
  if (!el || !Number.isFinite(top)) return;
  const moved = el.getBoundingClientRect().top - top;
  if (Math.abs(moved) > 1) window.scrollBy(0, moved);
}

let sweepBefore = null;
let sweepRemoves = false;

/** A sweep on the map (`map.js`): the squares chosen before it, then those of its rectangle, in
 * one render. Started on a square already chosen, it takes the rectangle's out instead (a user,
 * 2026-09-22); the rectangle followed back takes back what it did, either way. */
function sweepStart(remove = false) {
  sweepBefore = [...state.tiles];
  sweepRemoves = Boolean(remove);
}

/** The squares of the rectangle now; `true` when it holds more than one build takes. */
function sweepTo(names) {
  if (sweepBefore === null) return false;
  const inside = new Set(names);
  let capped = false;
  let wanted;
  if (sweepRemoves) {
    wanted = sweepBefore.filter((name) => !inside.has(name));
  } else {
    const building = tilesInBuilds(activeJobs());
    wanted = [...sweepBefore];
    const seen = new Set(wanted);
    for (const name of names) {
      if (seen.has(name) || building.has(name)) continue;
      if (wanted.length >= MAX_BUILD_TILES) {
        capped = true;
        break;
      }
      seen.add(name);
      wanted.push(name);
    }
  }
  const same = wanted.length === state.tiles.length && wanted.every((n, i) => n === state.tiles[i]);
  if (!same) {
    state.tiles = wanted;
    renderTiles();
    renderZlOptions();
    planChanged();
  }
  return capped;
}

/** How many squares the sweep took in, or took out. */
function sweepEnd() {
  const n = sweepBefore === null ? 0 : Math.abs(state.tiles.length - sweepBefore.length);
  sweepBefore = null;
  return n;
}

/**
 * Add squares to the selection, up to what one build takes.
 *
 * The cap lived in the mouse sweep alone: a flight plan across a continent added its nine
 * hundred squares here, and the estimate then refused the whole selection, leaving the user to
 * take four hundred out by hand (found in review, 2026-09-23). Every way of adding squares goes
 * through this function, so the cap belongs here.
 */
function addTiles(names) {
  const building = tilesInBuilds(activeJobs());
  const skipped = [];
  let capped = 0;
  for (const n of names) {
    if (state.tiles.includes(n) || skipped.includes(n)) continue;
    if (building.has(n)) skipped.push(n);
    else if (state.tiles.length >= MAX_BUILD_TILES) capped += 1;
    else state.tiles.push(n);
  }
  renderTiles();
  renderZlOptions();
  planChanged();
  return { skipped, capped };
}

/** What `addTiles` left out, said under the estimate: squares already building, and the ones a
 * build has no room for. */
function sayTilesInBuild(left) {
  const skipped = left?.skipped || [];
  const capped = left?.capped || 0;
  if (capped) {
    showPlanError(t("plan.tiles_capped", { max: MAX_BUILD_TILES, n: capped }));
  } else if (skipped.length) {
    showPlanError(t("plan.tiles_in_build", { tiles: skipped.join(" ") }));
  }
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
/** The flight plan's own controls, wired only when it is offered. */
function wireFlightPlan() {
  $("plan-route").hidden = !FLIGHT_PLAN;
  if (!FLIGHT_PLAN) return;
  $("route-draw").addEventListener("click", drawRoute);
  $("route-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      drawRoute();
    }
  });
  $("route-ends").addEventListener("click", () => addRouteTiles(routeEndTiles(), Number($("route-ends-zl").value) || routeEndsZl()));
  $("route-all").addEventListener("click", () => addRouteTiles(routeAlongTiles(), Number($("route-all-zl").value) || routeAlongZl()));
  $("route-simbrief").addEventListener("click", routeFromSimbrief);
  $("route-clear").addEventListener("click", clearRoute);
  // the radius applies to both ends of the route: the counts on the buttons follow it
  $("radius-input").addEventListener("input", renderRoute);
}

function toggleTile(name) {
  if (state.tiles.includes(name)) removeTile(name);
  else if (tilesInBuilds(activeJobs()).has(name)) toast(t("plan.tile_in_build", { tile: name }));
  else if (addTiles([name]).capped) toast(t("plan.tiles_capped", { max: MAX_BUILD_TILES, n: 1 }));
}

function renderTiles() {
  const box = clear($("tile-chips"));
  normalizeLevels();
  // A chip says its level only while the squares differ: one flight plan, two levels (2026-09-22).
  const mixed = chosenLevels().length > 1;
  for (const name of state.tiles) {
    const label = mixed ? `${name} · ZL${tileZl(name)}` : name;
    box.append(
      h("span", { class: "chip", dataset: { tile: name } }, label, h("button", { type: "button", "aria-label": t("plan.remove_tile", { tile: name }), onclick: () => removeTile(name) }, "×")),
    );
  }
  $("tile-count").textContent = state.tiles.length ? t("plan.tile_count", { n: state.tiles.length }) : t("plan.tile_none");
  renderSourceOptions(); // the countries' sources that cover the tiles come first
  $("tiles-clear").hidden = !state.tiles.length;
  planMap?.tilesChanged();
  renderTilesBuilt();
  renderTilesPatched();
}

/** Tiles of the chosen squares whose details are unfolded, kept across the redraws. */
const tilesBuiltOpen = new Set();

/**
 * Under the chosen squares, those already built, and what a build now would change (a user asked
 * to see it where he decides to build again, 2026-09-21). Short by default, since several squares
 * are often chosen: one line per tile, and what it was built with folded under it. What a build
 * would change is never folded: it is the one part that asks something of the user, and it has to
 * be read before Build is pressed. Compared: the Plan's own two choices, the imagery source and the
 * detail level, and the square's colours; a build here never touches a tile Ortho4XP built, which
 * gets a tile of OrthoStudio XP's beside it.
 */
function renderTilesBuilt() {
  const box = $("tiles-built");
  if (!box) return;
  clear(box);
  const provider = $("provider-select")?.value;
  const named = (code) => {
    const p = state.providers.find((x) => x.code === code);
    return p ? sourceLabel(p) : code;
  };
  const warned = new Map(); // tile -> what a build would change, for its chip
  for (const name of state.tiles) {
    const rows = state.library.filter((r) => r.tile === name && (r.kind == null || r.kind === "ortho"));
    const e = rows.find((r) => r.installed) || rows[0];
    if (!e) continue;
    if (e.built_by !== "osxp") {
      box.append(h("p", { class: "help tiles-built-line" }, t("plan.built_tile_o4x", { tile: name })));
      continue;
    }
    const open = tilesBuiltOpen.has(name);
    // the very block the Library unfolds: the two places say the same thing, zones and colours
    // included (a user asked for them here too, 2026-09-21)
    const detail = h("div", { class: "tiles-built-detail", hidden: !open }, builtBlock(e));
    const chevron = h("span", { class: "built-chevron", "aria-hidden": "true" }, open ? "▾" : "▸");
    const toggle = h("button", { type: "button", class: "built-toggle", title: t("plan.built_details"), "aria-expanded": open ? "true" : "false" }, t("plan.built_tile", { tile: name }), " ", chevron);
    toggle.addEventListener("click", () => {
      const now = !tilesBuiltOpen.has(name);
      if (now) tilesBuiltOpen.add(name);
      else tilesBuiltOpen.delete(name);
      detail.hidden = !now;
      toggle.setAttribute("aria-expanded", now ? "true" : "false");
      chevron.textContent = now ? "▾" : "▸";
    });
    const changes = [];
    if (provider && e.provider && provider !== e.provider) changes.push(t("plan.built_instead", { now: named(provider), was: named(e.provider) }));
    const zl = tileZl(name);  // its own level, which a flight plan may have given it
    if (zl && e.zl && zl !== Number(e.zl)) changes.push(t("plan.built_instead", { now: `ZL${zl}`, was: `ZL${e.zl}` }));
    // the square's colours, against those the tile was built with: the Library's own test, so the
    // two say the same (a user moved a slider and was told nothing here, 2026-09-21)
    if (photoDiffers(e)) changes.push(t("plan.built_colours"));
    const change = changes.length ? t("plan.built_change", { changes: changes.join(t("plan.built_and")) }) : null;
    if (change) warned.set(name, change);
    box.append(
      h("div", { class: "tiles-built-item" },
        h("p", { class: "tiles-built-line" }, toggle),
        detail,
        change ? h("p", { class: "tiles-built-warn" }, h("span", null, change)) : null),
    );
  }
  // Its chip in the warning's colour, what it says on hover: the square a build would change is
  // found at a glance among the chosen ones (a user asked, 2026-09-22).
  for (const chip of $("tile-chips").querySelectorAll(".chip")) {
    const change = warned.get(chip.dataset.tile);
    chip.classList.toggle("is-warn", Boolean(change));
    if (change) chip.title = change;
    else chip.removeAttribute("title");
  }
}

/**
 * Under the chosen squares, those a build will read hand-made patches for, and which files (a
 * user took "Patches: none" in a report for his patch not being found, when it was for another
 * square, 2026-09-21). From GET /api/patches: read when the page opens, when the Plan shows (a
 * patch may have been dropped into the folder meanwhile) and after Settings are saved.
 */
function renderTilesPatched() {
  const box = $("tiles-patched");
  if (!box) return;
  clear(box);
  const found = state.patches?.tiles || {};
  for (const name of state.tiles) {
    const files = found[name];
    if (files?.length) box.append(h("p", { class: "help tiles-patched-line" }, t("plan.patched", { tile: name, files: files.join(", ") })));
  }
}

/** The patches of the saved folder, for the Plan. An engine older than the route says nothing. */
async function loadPatches() {
  try {
    state.patches = await api("GET", "/api/patches");
  } catch (_err) {
    state.patches = null;
  }
  renderTilesPatched();
}

/** The folder Settings last asked about (`null`: none yet), so a redraw asks only when it changes. */
let settingsPatchesDir = null;

/** The patches of the folder Settings shows, saved or not: listed again when the field changes. */
function loadSettingsPatches() {
  const dir = String(state.settingsDraft?.expert?.patches_dir ?? "").trim();
  if (dir === settingsPatchesDir) return;
  settingsPatchesDir = dir;
  api("GET", `/api/patches?dir=${encodeURIComponent(dir)}`).then(
    (found) => {
      if (settingsPatchesDir !== dir) return; // the field changed again meanwhile
      state.settingsPatches = found;
      if (state.screen === "settings") renderSettings();
    },
    () => {},
  );
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
  keepInPlace(input, () => sayTilesInBuild(addTiles(raw)));
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
  keepInPlace($("latlon-add"), () => sayTilesInBuild(addTiles([tileName(lat, lon)])));
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
  return saved ? t("plan.xplane_saved_missing", { path: homely(saved) }) : t("plan.xplane_missing");
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

/**
 * What went wrong with step 1's two ways, an airport or a flight plan, said right under them. In
 * step 3's box, far below the button, a user pressed Draw and saw nothing happen (2026-09-22).
 */
function showWayError(message) {
  const box = $("way-error");
  box.textContent = message || "";
  box.hidden = !message;
}

async function addTilesFromIcao() {
  const code = $("icao-input").value.trim().toUpperCase();
  if (!code) return;
  let airport = state.airport && state.airport.icao === code ? state.airport : null;
  if (!airport) {
    try {
      airport = await api("GET", `/api/airports/${encodeURIComponent(code)}`);
    } catch (err) {
      showWayError(airportTrouble(err, code));
      return;
    }
  }
  showWayError(null);
  const r = Math.max(1, Number($("radius-input").value) || 15);
  const names = tilesAround(airport.lat, airport.lon, r);
  keepInPlace($("icao-add"), () => sayTilesInBuild(addTiles(names)));
  toast(t("plan.icao_added", { icao: airport.icao, name: airport.name || "", n: names.length, r }));
}

// ------------------------------------------------------------------ Plan: the flight plan

const ROUTE_KEY = "osxp.route";

/** The airports of a typed route: "LSGG LFMN", spaces, commas or arrows between them. */
export function routeCodes(text) {
  return String(text || "")
    .toUpperCase()
    .split(/[^A-Z0-9]+/)
    .filter((code) => code.length >= 3 && code.length <= 4);
}

/** The squares around both ends of the route, at the radius the page shows. */
function routeEndTiles() {
  const points = state.route?.points || [];
  if (points.length < 2) return [];
  const r = Math.max(1, Number($("radius-input").value) || 15);
  const first = points[0];
  const last = points[points.length - 1];
  return [...new Set([...tilesAround(first.lat, first.lon, r), ...tilesAround(last.lat, last.lon, r)])];
}

/** Every square the route crosses, the ends included. */
function routeAllTiles() {
  const points = state.route?.points || [];
  if (points.length < 2) return [];
  return [...new Set([...tilesAlong(points), ...routeEndTiles()])];
}

/** The squares along the route, without those of the departure and the arrival: the two groups
 * are chosen apart, so that each takes the level the pilot gives it (a user, 2026-09-22). */
function routeAlongTiles() {
  const ends = new Set(routeEndTiles());
  return routeAllTiles().filter((name) => !ends.has(name));
}

/** The level of each group, shown once its squares are chosen: the one they carry now, and the one
 * a change here gives them. */
function renderRouteLevels(maxZl, lat) {
  for (const id of ["route-ends-zl", "route-all-zl"]) {
    const sel = $(id);
    if (!sel) continue;
    const group = id === "route-ends-zl" ? routeEndTiles() : routeAlongTiles();
    const chosen = group.filter((name) => state.tiles.includes(name));
    sel.hidden = !chosen.length;
    if (!chosen.length) {
      // emptied while it is hidden: it kept the level of an earlier selection, and the button
      // beside it reads its value first, so it offered that one again (found in review,
      // 2026-09-23)
      clear(sel);
      continue;
    }
    const levels = [...new Set(chosen.map(tileZl))];
    const fallback = id === "route-all-zl" ? routeAlongZl(maxZl) : routeEndsZl(maxZl);
    clear(sel);
    sel.append(...zlOptions(maxZl, lat, { short: true }));
    sel.value = String(levels.length === 1 ? levels[0] : fallback);
    const label = id === "route-ends-zl" ? t("plan.route_ends_zl") : t("plan.route_all_zl");
    sel.setAttribute("aria-label", label);
  }
}

function renderRoute() {
  const found = $("route-found");
  const points = state.route?.points || [];
  found.hidden = points.length < 2;
  if (found.hidden) return;
  const ends = routeEndTiles();
  const all = routeAllTiles();
  setText(
    $("route-what"),
    t("plan.route_what", {
      from: points[0].ident,
      to: points[points.length - 1].ident,
      km: fmtInt(Math.round(routeLength(points))),
    }),
  );
  setText($("route-ends"), t("plan.route_ends", { n: ends.length }));
  setText($("route-all"), t("plan.route_all", { n: all.length - ends.length }));
  renderZlOptions(); // the two groups' levels follow the route and the squares chosen
}

/**
 * What to say when the engine could not answer about an airport.
 *
 * It tells a code it does not hold (404) from a machine with no airport database at all (503,
 * "set the X-Plane folder in Settings"), and the page said "unknown ICAO code" to both, sending
 * a user hunting for a typo that was not there (found in review, 2026-09-23). The page keeps its
 * own words for a code that really is unknown, since they are translated and the engine's are
 * about a latitude.
 */
function airportTrouble(err, code) {
  if (err instanceof ApiError && err.status !== 404) {
    const d = errorDetail(err);
    if (d?.code) return codeWords(d).filter(Boolean).join(" ");
    return errorMessage(err);
  }
  return t("plan.icao_unknown", { icao: code });
}

/** Read the codes, ask the engine where those airports are, and draw the line. */
async function drawRoute() {
  const codes = routeCodes($("route-input").value);
  if (codes.length < 2) {
    showWayError(t("plan.route_short"));
    return;
  }
  // A pasted route carries waypoints and DCT between its airports: what the engine does not know
  // as an airport is left out, and the line says which two ends were kept.
  const points = [];
  const missing = [];
  let trouble = null;  // the engine could not answer at all: that is what to say, not "unknown"
  for (const code of codes) {
    try {
      const airport = await api("GET", `/api/airports/${encodeURIComponent(code)}`);
      points.push({ ident: airport.icao, name: airport.name || "", lat: airport.lat, lon: airport.lon });
    } catch (err) {
      missing.push(code);
      if (trouble === null && err instanceof ApiError && err.status !== 404) trouble = airportTrouble(err, code);
    }
  }
  if (points.length < 2) {
    if (trouble) showWayError(trouble);
    else showWayError(missing.length ? t("plan.route_unknown", { icao: missing[0] }) : t("plan.route_short"));
    return;
  }
  showWayError(null);
  setRoute(points);
}

/** The last flight plan of the SimBrief name set in Settings, drawn as it was filed. */
async function routeFromSimbrief() {
  if (!(state.settings?.essential?.simbrief_user || "").trim()) {
    showWayError(t("plan.route_simbrief_none"));
    return;
  }
  let line;
  try {
    line = await api("GET", "/api/simbrief");
  } catch (err) {
    const d = errorDetail(err);
    showWayError(d?.code ? codeWords(d).filter(Boolean).join(" ") : errorMessage(err));
    return;
  }
  const points = (line?.points || []).filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));
  if (points.length < 2) {
    showWayError(t("plan.route_short"));
    return;
  }
  showWayError(null);
  $("route-input").value = `${line.from} ${line.to}`;
  setRoute(points.map((p) => ({ ident: p.ident, name: p.name || "", lat: p.lat, lon: p.lon })));
  toast(t("plan.route_simbrief_ok", { from: line.from, to: line.to }));
}

function setRoute(points) {
  state.route = points && points.length >= 2 ? { points } : null;
  try {
    if (state.route) localStorage.setItem(ROUTE_KEY, JSON.stringify(state.route));
    else localStorage.removeItem(ROUTE_KEY);
  } catch (_e) {
    // a browser that keeps nothing: the route simply goes when the page is read again
  }
  renderRoute();
  planMap?.routeChanged(Boolean(state.route));
}

function clearRoute() {
  $("route-input").value = "";
  showWayError(null);
  setRoute(null);
}

/** The route of the last visit, so a reload does not lose the line (nothing is asked again). */
function restoreRoute() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(ROUTE_KEY) || "null");
  } catch (_e) {
    saved = null;
  }
  const points = (saved?.points || []).filter(
    (p) => p && typeof p.ident === "string" && Number.isFinite(p.lat) && Number.isFinite(p.lon),
  );
  if (points.length < 2) return;
  state.route = { points };
  $("route-input").value = [points[0].ident, points[points.length - 1].ident].join(" ");
  renderRoute();
}

/** A group of the route: its squares are chosen, and they carry the level shown beside its
 * button, so that the ends and the route may differ. */
function addRouteTiles(names, zl) {
  if (!names.length) return;
  let left = { skipped: [], capped: 0 };
  keepInPlace($("route-found"), () => {
    left = addTiles(names);
    for (const name of names) {
      if (state.tiles.includes(name)) state.tileZl[name] = zl;
    }
    renderTiles();
    renderZlOptions();
    renderTilesBuilt();
    planChanged();
    planMap?.planChanged();
  });
  sayTilesInBuild(left);
  toast(t("plan.route_added", { n: names.length - left.skipped.length - left.capped }));
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
  // the licence beside the credit: EOX's Sentinel-2 is non-commercial and the page said nothing
  // of it, while the tiles it builds are folders people pass around (found in review, 2026-09-23)
  const credit = p ? p.attribution || "" : "";
  const licence = p ? p.licence || "" : "";
  $("provider-attribution").textContent = credit && licence ? `${credit} ${licence}.` : credit;
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
      setDraft(structuredClone(state.settings));
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

/** The level of step 1's list: what a square takes unless it carries one of its own. */
function planZl() {
  return Number(state.planZl) || 16;
}

/** What the squares along a route take unless the pilot says otherwise (a user, 2026-09-22).
 *
 * A route crosses country flown over at altitude, where the ground is scenery and not a place to
 * look at; its two ends are where one lands, and those keep step 1's level. A long route is also
 * a lot of squares, and each level up is four times the imagery. */
const ROUTE_ALONG_ZL = 14;

/** The highest level the chosen imagery source offers. */
function sourceMaxZl() {
  const p = currentProvider();
  return Math.min(19, p ? p.max_zl : 19);
}

/** The level of the squares along a route, never above what the source offers. */
function routeAlongZl(top = sourceMaxZl()) {
  return Math.min(ROUTE_ALONG_ZL, top);
}

/** What the departure and arrival take unless the pilot says otherwise: the level chosen in step
 * 1's list, and not the one the chosen squares happen to share.
 *
 * Pressing "along the route" on an empty selection put every square at ZL14, so step 1's list
 * followed them there, and the two ends then landed at ZL14 as well: the one thing this feature
 * exists to prevent, and it depended on the order the two buttons were pressed in (2026-09-23).
 */
function routeEndsZl(top = sourceMaxZl()) {
  const chosen = Number(state.zlChosen) || Number(state.settings?.essential?.zoom_level) || planZl();
  return Math.min(chosen, top);
}

/** The level a square will be built at: its own (the flight plan's groups), else step 1's. */
function tileZl(name) {
  return Number(state.tileZl[name]) || planZl();
}

/** The levels of the chosen squares, without repeats. */
function chosenLevels() {
  return [...new Set(state.tiles.map(tileZl))];
}

/**
 * One rule, so that no level is ever hidden: the levels of the squares that are gone are dropped,
 * and when every chosen square is at the same level, that level becomes step 1's and no square
 * carries its own any more. Levels differ only while the flight plan's two groups differ.
 */
function normalizeLevels() {
  for (const name of Object.keys(state.tileZl)) {
    if (!state.tiles.includes(name)) delete state.tileZl[name];
  }
  const levels = chosenLevels();
  if (state.tiles.length && levels.length === 1) {
    state.planZl = levels[0];
    for (const name of state.tiles) delete state.tileZl[name];
  }
}

/** Every level the source gives, in plain words, sized at a latitude.
 *
 * `short` leaves the ground size out ("Standard · ZL16"): beside a button of the flight plan the
 * whole sentence sent the list to a line of its own (a user, 2026-09-22). */
function zlOptions(maxZl, lat, { short = false } = {}) {
  const out = [];
  for (let zl = 12; zl <= Math.min(19, maxZl); zl += 1) {
    const label = short ? t("detail.short", { name: detailName(zl), zl }) : detailLabel(zl, lat);
    out.push(h("option", { value: zl }, label));
  }
  return out;
}

/** Detail levels in plain words, sized at the first tile's latitude (else the map centre's).
 *
 * The list shows the level of the chosen squares; when they differ, the flight plan having given
 * its ends and its route two levels, it reads "Several levels" and each chip says its own. */
function renderZlOptions() {
  const sel = $("zl-select");
  const top = sourceMaxZl();
  const lat = state.tiles.length ? tileLat(state.tiles[0]) + 0.5 : planMap?.mapLatitude() ?? 45;
  // a source that stops lower brings every level down with it, the squares' own included
  // what the squares carry wins over what the list still shows: a level chosen for one group
  // of the route left the list on the level of before, which then took it back (2026-09-22)
  const wanted = Number(state.planZl) || Number(sel.value) || state.settings?.essential?.zoom_level || 16;
  state.planZl = Math.min(Number(wanted), top);
  for (const name of Object.keys(state.tileZl)) {
    state.tileZl[name] = Math.min(state.tileZl[name], top);
  }
  normalizeLevels();
  clear(sel);
  sel.append(...zlOptions(top, lat));
  const levels = chosenLevels();
  if (state.tiles.length && levels.length > 1) {
    sel.append(h("option", { value: "", disabled: true }, t("plan.zl_several")));
    sel.value = "";
  } else {
    sel.value = String(planZl());
  }
  $("zl-help").textContent = t("plan.zl_help", { lat: fmtNum(lat, 1) });
  renderRouteLevels(top, lat);
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
  const own = Object.fromEntries(state.tiles.filter((n) => state.tileZl[n]).map((n) => [n, state.tileZl[n]]));
  return {
    tiles: [...state.tiles],
    provider: $("provider-select").value,
    zoom_level: planZl(),
    // the squares of a flight plan's ends, or of its route, when the two levels differ
    ...(Object.keys(own).length ? { tiles_zl: own } : {}),
    zones,
    // The squares' own colours travel with the zones: a request that carried its zones alone
    // built them without their colours (a user, 2026-09-18).
    tiles_settings: planMap ? planMap.tilesSettings([...state.tiles]) : {},
  };
}

/** The most tiles one build takes (orthostudio.api.models.MAX_TILES); a test keeps the two equal.
 * Beyond it the Plan says so in plain words and asks nothing: the engine's answer was a list
 * error that a user took for a limit of the program (64 then, 2026-09-22). */
/**
 * Whether the flight plan is offered. It waits for 0.1.15.
 *
 * Choosing squares along a route is a whole feature of its own -- a field, two buttons, two
 * detail levels, SimBrief, the line on the map -- and it arrived in the same release as three
 * user faults that people are waiting on. A release carrying both is one nobody can check: most
 * of what the reviews found before 0.1.14 was cut landed in this feature, and none of it in the
 * faults. So it is closed here rather than taken out: its code, its tests and its words stay,
 * the page does not offer it, and turning it back on is this one line (2026-09-23).
 */
const FLIGHT_PLAN = false;

const MAX_BUILD_TILES = 500;

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
    if (state.tiles.length > MAX_BUILD_TILES) {
      state.plan = null;
      state.planStale = false;
      showPlanError(t("plan.too_many_tiles", { max: MAX_BUILD_TILES, n: state.tiles.length }), null, "estimate");
      renderPlanPanel();
      return;
    }
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
  renderTileColours();
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
  markWideTables(); // the per-tile table is often wider than step 3's column
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
  // A build that failed or was stopped offered nothing but a Retry buried in an error card, and
  // only for the handful of codes that carry action "retry" (a user, 2026-09-20).
  v.again = h("button", { type: "button", class: "btn btn-small", title: t("works.again_help"), onclick: retryJob }, t("works.again"));
  const head = h("div", { class: "job-head" }, h("h2", null, t("works.job", { id: job.id })), v.pill, v.meta, h("span", { class: "spacer" }), v.again, v.stop);

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
  if (relief === "usgs") return t("works.relief_usgs");
  if (relief === "usgs1") return t("works.relief_usgs1");
  if (relief === "canada") return t("works.relief_canada");
  if (relief === "south_america") return t("works.relief_anadem");
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
  // What was read, not only what was chosen. A user built Banff with "Canada's lidar relief",
  // watched it through without an error, and got Copernicus: the lidar has not flown there, the
  // composite quietly falls back, and this line named his choice back at him (2026-09-20).
  const missing = decisionCounts(job).get("DEM_OVERLAY_UNAVAILABLE") || 0;
  const gap = !missing
    ? ""
    : missing === 1
      ? t("works.relief_gap_one")
      : t("works.relief_gap", { n: fmtInt(missing) });
  const chosen = reliefWords(job.relief);
  const relief = [
    chosen && gap ? `${chosen} (${gap})` : chosen,
    job.relief_own ? t("works.relief_own") : "",
  ].filter(Boolean).join(" + ");
  const parts = [`${job.provider || ""} ZL${job.zoom_level ?? job.zl ?? ""}`, relief, job.install ? t("works.install") : t("works.no_install")];
  setText(v.meta, parts.filter(Boolean).join(" · "));
  const waiting = job.status === "queued";
  v.stop.hidden = !active;
  setText(v.stop, waiting ? t("works.unqueue") : t("works.stop"));
  // Nothing to redo in a build that finished with everything built.
  v.again.hidden = active || (job.status === "done" && !(job.errors || []).length);

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
  // Whether the hand-made patches were read, and which: a build said nothing about them, so a
  // tile built with its patches looked exactly like one built without (a user, 2026-09-20).
  const patched = (rep.tiles || []).filter((tile) => (tile.patches || []).length);
  const patchWords = patched.length
    ? h("span", { title: patched.map((tile) => `${tile.tile}\n  ${tile.patches.join("\n  ")}`).join("\n") },
        patched.map((tile) => `${tile.tile} (${fmtInt(tile.patches.length)})`).join(", "))
    : t("works.patches_none");
  const summary = kv([
    [t("works.patches"), patchWords, patched.length ? null : "help"],
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
    h("div", { class: "report-grid" },
      h("h3", { class: "report-title" }, t("works.report")),
      h("h3", { class: "report-title-decisions" }, t("works.decisions")),
      h("div", { class: "report-summary" }, summary),
      h("div", { class: "report-steps" }, table),
      h("div", { class: "report-decisions" }, decisions)),
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
    renderTilesBuilt(); // the chosen squares a build has just made, or a delete has just taken
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
  // The Plan offers to go back to the colours of a tile on the disk: a build that just ended, or
  // a tile deleted, changes what it may offer.
  renderTileColours();
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
export function freeSpacePlan(disk, images, relief) {
  const unused = Math.max(0, Number(disk?.unused_bytes) || 0);
  const pictures = images ? Math.max(0, Number(disk?.images_bytes) || 0) + Math.max(0, Number(disk?.mapcache_bytes) || 0) : 0;
  // The relief is its own choice: a square of it weighs 40 MB to 400 MB and costs far less to
  // fetch again than its imagery (a user found 1.4 GB of it left after emptying, 2026-09-18).
  const heights = relief ? Math.max(0, Number(disk?.relief_bytes) || 0) : 0;
  return { unused, pictures, heights, total: unused + pictures + heights };
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
    ...row(t("disk.relief"), fmtBytes(d.relief_bytes || 0), t("disk.relief_help")),
  );
  const reveal = $("disk-reveal");
  const data = state.status?.data_dir;
  reveal.hidden = !dataFolderShown(state.status);
  const action = revealLabel(state.status?.platform);
  setText(reveal, data?.chosen ? t("disk.reveal_data", { action }) : t("disk.reveal", { action }));
  const plan = freeSpacePlan(d, $("disk-images").checked, $("disk-relief").checked);
  button.disabled = Boolean(d.building) || plan.total <= 0 || state.diskBusy;
  let said = d.building ? t("disk.busy_note") : plan.total <= 0 ? t("disk.nothing") : "";
  if (data?.present === false) said = t("disk.data_missing", { path: homely(data.path) });
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
  if (plan.heights > 0) lines.push(t("disk.confirm_relief", { size: fmtBytes(plan.heights) }));
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
  const relief = $("disk-relief").checked;
  const plan = freeSpacePlan(state.disk, images, relief);
  if (plan.total <= 0 || !(await confirmFreeSpace(plan))) return;
  const errors = clear($("disk-errors"));
  state.diskBusy = true;
  renderDisk();
  try {
    const res = await api("POST", "/api/clean", { images, relief });
    const freed = (Number(res?.freed_bytes) || 0) + (Number(res?.images_freed_bytes) || 0) + (Number(res?.relief_freed_bytes) || 0);
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

/** The relief a tile stands on, in words: the base, what was laid over it, and what was asked for
 * and not laid -- Canada's lidar chosen where it never flew (a user at Banff, 2026-09-20). */
export function reliefSentence(facts) {
  const laidCodes = facts?.relief_laid || [];
  const base = facts?.relief ? reliefName(facts.relief) : "—";
  const laid = laidCodes.map(reliefName);
  const missing = (facts?.relief_asked || []).filter((a) => !laidCodes.includes(a)).map(reliefName);
  const over = laid.length ? t("library.built_relief_laid", { base, over: laid.join(", ") }) : base;
  return missing.length ? t("library.built_relief_missing", { base: over, asked: missing.join(", ") }) : over;
}

function coloursWords(photo) {
  const values = ["brightness", "contrast", "saturation"].map((k) => Number(photo?.[k]) || 0);
  if (!values.some(Boolean)) return t("library.built_colours_plain");
  const signed = (v) => `${v > 0 ? "+" : ""}${fmtNum(v, 2)}`;
  return t("library.built_colours_set", { brightness: signed(values[0]), contrast: signed(values[1]), saturation: signed(values[2]) });
}

function zonesWords(zones) {
  if (!zones?.length) return t("library.built_none");
  const byLevel = new Map();
  for (const z of zones) byLevel.set(z.zl, (byLevel.get(z.zl) || 0) + 1);
  const parts = [...byLevel].sort((a, b) => a[0] - b[0]).map(([zl, n]) => t("library.built_zone_at", { n: fmtInt(n), zl }));
  return `${fmtInt(zones.length)}: ${parts.join(", ")}`;
}

/**
 * What a tile of the Library was built with: `{head, lines}`, lines as [label, value] (a user asked
 * where to see it, 2026-09-21). `e.built` is `{facts, at}` from the engine; the facts are empty for a
 * pack written before 0.1.10, which kept only its imagery, detail and colours, and that is said
 * rather than guessed. The date is the manifest's own time: the manifest carries none, so that two
 * identical builds stay identical.
 */
export function builtLines(e, providers = []) {
  if (!e?.built) return { head: t("library.built_unknown"), lines: [] };
  const facts = e.built.facts || {};
  const old = !facts.version;
  const date = fmtDate(e.built.at);
  const head = old ? t("library.built_when_old", { date }) : t("library.built_when", { date, version: facts.version });
  const source = providers.find((p) => p.code === e.provider);
  const zl = Number(e.zl) || 0;
  const level = zl ? tOpt(`detail.${zl}`) : null;
  const lines = [
    [t("library.built_imagery"), source ? sourceLabel(source) : String(e.provider || "—")],
    [t("library.built_detail"), zl ? [level, `ZL${zl}`].filter(Boolean).join(" · ") : "—"],
  ];
  if (!old) lines.push([t("library.built_relief"), reliefSentence(facts)]);
  lines.push([t("library.built_colours"), coloursWords(e.photo)]);
  if (!old) {
    lines.push([t("library.built_zones"), zonesWords(facts.zones)]);
    lines.push([t("library.built_patches"), (facts.patches || []).length ? facts.patches.join(", ") : t("library.built_none")]);
  }
  return { head, lines };
}

/** One line for the map's tooltip over a built tile: what it was built with, the tile's own name
 * first. The row X-Plane shows wins when a tile was built into two folders. */
export function builtSummary(name, library = state.library, providers = state.providers, withName = true) {
  const rows = library.filter((r) => r.tile === name && (r.kind == null || r.kind === "ortho"));
  const e = rows.find((r) => r.installed) || rows[0];
  if (!e) return null;
  if (e.built_by !== "osxp") return withName ? `${name} · ${t("library.by_ortho4xp")}` : t("library.by_ortho4xp");
  const source = providers.find((p) => p.code === e.provider);
  const facts = e.built?.facts || {};
  const parts = [withName ? name : null, source ? sourceLabel(source) : e.provider, e.zl ? `ZL${e.zl}` : null, facts.relief ? reliefSentence(facts) : null];
  return parts.filter(Boolean).join(" · ");
}

/** The unfolded part of a Library row: the head line, then the facts. */
function builtBlock(e) {
  const { head, lines } = builtLines(e, state.providers);
  return h("div", { class: "library-built-body" }, h("p", { class: "library-built-head" }, head), lines.length ? kv(lines) : null);
}

/** Rows whose "built with" part is open, kept across the redraws of the table, which come every
 * few seconds while a build installs its tiles. */
const libraryOpen = new Set();

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
  // An imported tile still in X-Plane whose files are gone keeps its way out of X-Plane: it has no
  // Delete, and is taken off the list only once out of X-Plane.
  if (e.installed && (present || !byOsxp)) {
    xplaneButton = h("button", { type: "button", class: "btn btn-small", title: inBuild ? t("library.in_build_help") : t("library.remove_help"), disabled: busy || inBuild, onclick: () => libraryAction(e, "uninstall") }, t("library.remove"));
  } else if (present) {
    xplaneButton = h("button", { type: "button", class: "btn btn-small btn-primary", title: inBuild ? t("library.in_build_help") : t("library.add_help"), disabled: busy || inBuild, onclick: () => libraryAction(e, "install") }, t("library.add"));
  }
  // A tile OrthoStudio XP did not build has no Delete, and had nothing in its place: the reason
  // is said in the lead and in the "Built by" column, and a user still looked for the button
  // (2026-09-20). It is now said where the button would be.
  const deleteButton = byOsxp
    ? h("button", { type: "button", class: "btn btn-small btn-danger", title: building ? t("library.delete_wait") : t("library.delete_help"), disabled: busy || building, onclick: () => deleteLibraryTile(e) }, t("library.delete"))
    // the way back from an import: off the list, and nothing on the disk touched (a user asked
    // for it, 2026-09-21), in place of a "not deletable here" that offered nothing. Not while
    // X-Plane shows the tile: the Library could no longer take it out.
    : h("button", { type: "button", class: "btn btn-small", title: e.installed ? t("library.forget_installed") : t("library.forget_help"), disabled: busy || Boolean(e.installed), onclick: () => forgetLibraryTile(e) }, t("library.forget"));
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
  // The tile on the disk was built with other colours than the square asks for now: the map
  // shows what you would get, this says what you have (a user, 2026-09-18).
  let colourMark = null;
  if (byOsxp && present && photoDiffers(e)) {
    colourMark = pill(t("library.colours_old"), "warn");
    colourMark.title = t("library.colours_old_help");
  }
  const overlayMark = overlayPill(e, busy || inBuild);
  const label = revealLabel(state.status?.platform);
  const revealButton = present && e.path
    ? h("button", { type: "button", class: "btn btn-small btn-icon reveal-btn", title: label, "aria-label": `${label}: ${e.tile}`, onclick: () => revealPath(e.path) }, folderIcon())
    : null;
  // What the tile was built with, one click away and only for a tile OrthoStudio XP built: an
  // imported one's settings are Ortho4XP's, which nothing here can read.
  const open = libraryOpen.has(key);
  const detail = byOsxp ? h("tr", { class: "library-built", hidden: !open }, h("td", { colspan: 8 }, builtBlock(e))) : null;
  let builtCell = t("library.by_ortho4xp");
  if (byOsxp) {
    const chevron = h("span", { class: "built-chevron", "aria-hidden": "true" }, open ? "▾" : "▸");
    const toggle = h("button", { type: "button", class: "built-toggle", title: t("library.built_toggle"), "aria-expanded": open ? "true" : "false" }, t("library.by_osxp"), " ", chevron);
    toggle.addEventListener("click", () => {
      const now = !libraryOpen.has(key);
      if (now) libraryOpen.add(key);
      else libraryOpen.delete(key);
      detail.hidden = !now;
      toggle.setAttribute("aria-expanded", now ? "true" : "false");
      chevron.textContent = now ? "▾" : "▸";
    });
    builtCell = toggle;
  }
  const row = h("tr", { dataset: { key }, "aria-busy": busy ? "true" : null },
    h("td", { title: e.path || null }, h("span", { class: "tile-name" }, e.tile), missing ? [" ", missing] : null, inBuildPill ? [" ", inBuildPill] : null, colourMark ? [" ", colourMark] : null, overlayMark ? [" ", overlayMark] : null),
    imageryCell(e),
    h("td", null, e.installed ? pill(t("app.yes"), "ok") : pill(t("app.no"), "cancelled")),
    h("td", { class: "num" }, fmtBytes(e.size_bytes)),
    h("td", null, builtCell),
    h("td", { class: "library-action" }, revealButton),
    h("td", { class: "library-action" }, xplaneButton),
    h("td", { class: "library-action" }, deleteButton));
  return detail ? [row, detail] : [row];
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
  if (path) xplane.textContent = t("library.xplane", { path: homely(path) });
  // New buttons replace the old ones: a focused button's successor, same row and column, keeps it.
  const cell = body.contains(document.activeElement) ? document.activeElement.closest("td") : null;
  const kept = cell ? { key: cell.parentElement.dataset.key, column: cell.cellIndex } : null;
  clear(body);
  $("library-building").hidden = !activeJobs().length;
  const rows = libraryTiles(state.library);
  renderOverlayNotices(rows);
  if (!rows.length) {
    body.append(h("tr", null, h("td", { colspan: 8, class: "placeholder" }, t("library.empty"))));
    markWideTables();
    return;
  }
  for (const e of rows) body.append(...libraryRow(e));
  if (kept) libraryRowByKey(kept.key)?.cells[kept.column]?.querySelector("button:not(:disabled)")?.focus();
  markWideTables();
}

/** Whether the pack on the disk was built with other colours than its square asks for now.
 *
 * ``row.photo`` is what the pack recorded (absent for one built before the colours, or with the
 * plain ones); the square's answer comes from the map's document. Both are rounded the same way,
 * so a 0 on one side and a missing section on the other agree. */
function photoDiffers(row) {
  return valuesKey(row.photo) !== valuesKey(wantedPhoto(row.tile));
}

/** The three colour values, rounded, as one string: what tells two answers apart. */
function valuesKey(values) {
  const n = (v) => Number(v || 0).toFixed(3);
  const p = values || {};
  return `${n(p.brightness)}:${n(p.contrast)}:${n(p.saturation)}`;
}

/** The colours a square asks for now: its own answer, or the one Settings gives. */
function wantedPhoto(tile) {
  const own = planMap && planMap.tilePhoto ? planMap.tilePhoto(tile) : null;
  return own?.look ? photoValues(own.look, own) : settingsPhoto();
}

/** The colours the pack of ``tile`` on the disk was built with, or ``null`` when there is none.
 *
 * The installed pack answers first: it is the one X-Plane shows. A pack that recorded nothing
 * answers the plain colours, which is what the Library's mark reads into it too. */
function builtPhoto(tile) {
  const rows = libraryTiles(state.library).filter((e) => e.tile === tile && e.present && e.built_by === "osxp");
  const row = rows.find((e) => e.installed) || rows[0];
  if (!row) return null;
  return { brightness: 0, contrast: 0, saturation: 0, ...(row.photo || {}) };
}

/** The choice that gives these values: a look of the menu when one matches, else "my own". */
function photoChoiceOf(values) {
  for (const [look, preset] of Object.entries(PHOTO_LOOKS)) {
    if (valuesKey(preset) === valuesKey(values)) return { ...values, look };
  }
  return { ...values, look: "custom" };
}

/** The chosen squares whose tile on the disk was built with other colours than they ask for now:
 * `[{tile, choice}]`, so the Plan can offer to take those colours back (a user, 2026-09-18). */
function tilesBuiltOtherwise(names) {
  const out = [];
  for (const tile of names) {
    const built = builtPhoto(tile);
    if (built && valuesKey(built) !== valuesKey(wantedPhoto(tile))) {
      out.push({ tile, choice: photoChoiceOf(built) });
    }
  }
  return out;
}

/** The rows of tiles, not the "built with" parts unfolded under them: focus comes back to a row by
 * its place among the tiles. */
function libraryRows() {
  return [...$("library-body").rows].filter((r) => !r.classList.contains("library-built"));
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
  SYS_PACK_IN_XPLANE: () => [t("library.err_in_xplane"), t("library.forget_installed")],
  SYS_PACK_NOT_IMPORTED: () => [t("library.err_not_imported"), t("library.err_not_imported_remedy")],
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

/** "Remove from the list" (tiles imported from Ortho4XP): the Library forgets the tile, and its
 * files stay where Ortho4XP put them. */
function forgetLibraryTile(e) {
  const req = libraryRequest(e, "forget");
  return runLibraryChange(e, () => api("POST", req.path, req.body), () => toast(t("library.forgotten", { tile: e.tile })));
}

/** How long the folder dialog may take to show before the page says it is on its way (the File
 * Explorer's took seconds on a user's Windows). */
const FOLDER_OPENING_TOAST_MS = 800;

/** A folder dialog is being asked for or shown: a click on another Choose… button waits for it. */
let folderAsked = false;

/** A folder chosen in the platform's own dialog, opened by the engine (POST /api/choose-folder):
 * its path, or null when the user cancelled or no dialog could open (a toast says why, and
 * `onMissing` runs: the field to type the path in can then be shown).
 *
 * While the dialog opens, a second click says so instead of asking the engine again: nothing
 * showed for seconds on Windows, and the click after it answered "SYS_BUSY: A folder dialog is
 * already open" (a user, 2026-09-15). */
/** Where the Settings screen takes its colour sample: the map's centre, and the chosen source.
 *
 * ``null`` while the map has not drawn yet. In the mock mode the page draws its own image, so
 * that the preview works with no network (``ui.md`` 2.4). */
function photoSampleUrl(provider, at = null) {
  const centre = at || (planMap && planMap.mapCenter ? planMap.mapCenter() : null);
  if (!centre) return null;
  const where = { lat: centre.lat, lon: centre.lon, tile: tileName(centre.lat, centre.lon) };
  if (MOCK) return { ...where, url: `mock-photo:${centre.lat.toFixed(3)},${centre.lon.toFixed(3)}` };
  const q = new URLSearchParams({
    provider: provider || "BI",
    lat: centre.lat.toFixed(5),
    lon: centre.lon.toFixed(5),
  });
  return { ...where, url: `/api/photo-sample?${q}` };
}

async function chooseFolder(prompt, start = null, onMissing = null) {
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
    if (errorDetail(err)?.code === "SYS_NO_FOLDER_DIALOG") onMissing?.();
    return null;
  } finally {
    clearTimeout(slow);
    folderAsked = false;
  }
}

/** The Ortho4XP folder imported last in this session. */
let ortho4xpFolder = null;

/** Where the import's folder dialog opens: the folder imported last, else the Ortho4XP folder of
 * the newest imported tile (…/Ortho4XP/Tiles/zOrtho4XP_…), else wherever the dialog likes. */
export function ortho4xpStart(library = state.library) {
  if (ortho4xpFolder) return ortho4xpFolder;
  const rows = libraryTiles(library).filter((e) => e.built_by === "ortho4xp").sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  for (const e of rows) {
    const m = String(e.path || "").match(/^(.+)[\\/]Tiles[\\/][^\\/]+$/);
    if (m) return m[1];
  }
  return null;
}

/** The field to type the folder in: only where no folder dialog can open (a Linux without zenity
 * or kdialog, an engine older than the page). */
function showImportPath() {
  $("import-path-field").hidden = false;
  $("import-path").focus();
}

/**
 * "Import my Ortho4XP tiles…", one button. A user pressed Import with the field still empty and
 * nothing happened at all, and could not tell which folder was meant (2026-09-21); the button then
 * opened the folder dialog when no folder was typed, and "Choose…" beside it did the same: two
 * buttons for one thing (the same user). It now asks for the folder, then imports it; the answer
 * counts the tiles, not the overlays pack beside them, names the folder, and says where it looked
 * when it found none. A cancel changes nothing.
 */
async function importOrtho4xp(ev) {
  ev.preventDefault();
  const out = $("import-result");
  let dir = null;
  if (!$("import-path-field").hidden) {
    dir = $("import-path").value.trim();
    if (!dir) {
      out.textContent = t("library.import_type_first");
      $("import-path").focus();
      return;
    }
  } else if (state.engineOutdated) {
    showImportPath();
    out.textContent = t("library.import_type_first");
    return;
  } else {
    dir = await chooseFolder(t("library.import_prompt"), ortho4xpStart(), showImportPath);
    if (!dir) return;
  }
  out.textContent = t("app.loading");
  try {
    const res = await api("POST", "/api/library/import-ortho4xp", { folder: dir });
    ortho4xpFolder = dir;
    const entries = Array.isArray(res) ? res : res?.entries || []; // an older engine sent the list
    const tiles = entries.filter((e) => e.kind == null || e.kind === "ortho").length;
    const where = (Array.isArray(res?.searched) && res.searched.length ? res.searched : [dir]).map(homely).join(", ");
    out.textContent = tiles ? t("library.imported", { n: fmtInt(tiles), where: homely(dir) }) : t("library.import_none", { where });
    await loadLibrary();
    loadStatus();
  } catch (err) {
    out.textContent = errorMessage(err);
  }
}

// ------------------------------------------------------------------ Settings: questions in plain words

/** The Settings screen (settings.js): questions, presets, For experts; the draft is saved by Save. */
/** Put `next` in the draft of Settings, keeping the draft itself: the parts of the screen that stay
 * drawn keep their handlers, which write into that object (morphChildren). */
function setDraft(next) {
  const draft = state.settingsDraft;
  if (!draft || typeof draft !== "object" || !next || typeof next !== "object") {
    state.settingsDraft = next;
    return;
  }
  for (const key of Object.keys(draft)) delete draft[key];
  Object.assign(draft, next);
}

function renderSettings(message, kind) {
  if (!state.schema || !state.settingsDraft) return;
  loadSettingsPatches();
  renderSettingsView(
    { root: $("settings-form"), presets: $("settings-presets"), questions: $("settings-questions"), experts: $("settings-expert-fields"), note: $("settings-search-note") },
    {
      dom: { h, clear, morph: morphChildren },
      draft: state.settingsDraft,
      search: state.settingsSearch,
      schema: state.schema,
      providers: state.providers,
      xplane: state.status?.xplane || null,
      dataDir: state.status?.data_dir || null,
      home: state.status?.home || null,
      platform: state.status?.platform || null,
      reveal: state.status?.platform ? { label: revealLabel(state.status.platform), open: revealPath } : null,
      chooseFolder: state.engineOutdated ? null : chooseFolder,
      photoSample: photoSampleUrl,
      patches: state.settingsPatches || null,
      changed: (text, level) => renderSettings(text, level),
    },
  );
  renderSettingsStatus(message, kind);
}

/** "Changes not saved yet", or the message of the last change: the line beside Save. */
function renderSettingsStatus(message, kind) {
  const dirty = !sameValue(state.settingsDraft, state.settings);
  const line = $("settings-status");
  const text = message || (dirty ? t("settings.unsaved") : "");
  if (line.textContent !== text) line.textContent = text;
  if (line.classList.contains("is-fail") !== (kind === "fail")) line.classList.toggle("is-fail");
}

/** The Plan says in a few words what the build will use, and where to change it. */
function renderPlanSettings() {
  const parts = settingsSummary(state.settings);
  $("plan-settings-summary").textContent = parts.length ? t("plan.settings_summary", { list: parts.join(" · ") }) : "";
  renderPlanXplane();
}

/** Step 1: the colours of the squares chosen, with the three sliders and the preview.
 *
 * The three levels are Settings, the square, then the zones (``map-zones.md`` 3). This control
 * writes the **squares'**: one square selected changes that one, six change the six -- which is
 * also "one colour for this build" (a user, 2026-09-18). */
function renderTileColours() {
  // a colour moved changes what a build of a tile already built would make: said with the rest
  renderTilesBuilt();
  const box = clear($("tile-colours"));
  const select = $("tile-colours-select");
  const help = $("tile-colours-help");
  const names = state.tiles;
  const ready = Boolean(planMap && planMap.zonesLoaded && planMap.zonesLoaded());
  select.disabled = !names.length || !ready;
  const shared = names.length && ready ? planMap.tilesPhoto(names) : { mixed: false, photo: null };
  const options = [
    ["", t("plan.colours_settings")],
    ["as_delivered", t("settings.q.colours_as_delivered")],
    ["softer", t("settings.q.colours_softer")],
    ["much_softer", t("settings.q.colours_much_softer")],
    ["custom", t("settings.q.colours_custom")],
  ];
  clear(select);
  if (shared.mixed) select.append(h("option", { value: "~" }, t("plan.colours_mixed")));
  for (const [value, text] of options) select.append(h("option", { value }, text));
  const photo = shared.photo;
  select.value = shared.mixed ? "~" : photo?.look || "";
  select.onchange = () => {
    if (select.value === "~") return;
    const look = select.value || null;
    planMap.setTilesPhoto(names, look ? { ...(photo || zeroPhoto()), look } : null);
    renderTileColours();
    renderPlanSettings();
  };
  setText(help, names.length ? t("plan.colours_help", { n: names.length }) : t("plan.colours_none"));
  // What carries its own colours, said even when nothing is chosen: a map repainted from an old
  // visit must never be a mystery, and the button is always within reach (a user, 2026-09-18).
  const own = ready ? planMap.ownColours() : { squares: 0, zones: 0, free: 0 };
  if (own.squares || own.zones) {
    let line;
    if (own.squares && own.zones) line = t("plan.colours_own_both", { squares: own.squares, zones: own.zones });
    else if (own.squares) line = t("plan.colours_own_squares", { n: own.squares });
    else line = t("plan.colours_own_zones", { n: own.zones });
    box.append(h("div", { class: "colours-own" },
      h("p", { class: "help" }, line),
      own.free
        ? h("button", { type: "button", class: "btn btn-small btn-accent", onclick: () => {
            const given = planMap.resetColours();
            renderTileColours();
            renderPlanSettings();
            if (given) toast(t("plan.colours_reset_done", { n: given }));
          } }, t("plan.colours_reset"))
        : null));
  }
  // Going back to what a tile already holds: the Library marks the difference, this undoes it
  // without hunting for the numbers again (a user, 2026-09-18).
  const otherwise = ready ? tilesBuiltOtherwise(names) : [];
  if (otherwise.length) {
    box.append(h("div", { class: "colours-own" },
      h("p", { class: "help" }, otherwise.length === 1
        ? t("plan.colours_built_one", { tile: otherwise[0].tile })
        : t("plan.colours_built_many", { n: otherwise.length })),
      h("button", { type: "button", class: "btn btn-small btn-accent", onclick: () => {
          planMap.setEachTilePhoto(Object.fromEntries(otherwise.map((e) => [e.tile, e.choice])));
          renderTileColours();
          renderPlanSettings();
          toast(t("plan.colours_built_done", { n: otherwise.length }));
        } }, t("plan.colours_built_take"))));
  }
  if (!names.length || !ready || shared.mixed) return;
  if (photo?.look === "custom") box.append(photoSliders(photo, (next) => {
    planMap.setTilesPhoto(names, next);
    renderTileColours();
  }));
  // No thumbnail here: the map itself is repainted with these colours, which says it better
  // (a user, 2026-09-18). Settings keeps its two images, having no map.
  box.append(h("p", { class: "help" }, t("plan.colours_on_map")));
}

/** A colour choice with nothing set: what a square starts from when it takes its own. */
function zeroPhoto() {
  return { look: null, brightness: 0, contrast: 0, saturation: 0 };
}

/** The colours Settings answers, for a square that names none. */
function settingsPhoto() {
  const essential = state.settings?.essential || {};
  const expert = state.settings?.expert || {};
  return photoValues(essential.photo_look, {
    brightness: expert.photo_brightness,
    contrast: expert.photo_contrast,
    saturation: expert.photo_saturation,
  });
}

/** The three sliders of "my own values", for a square or a zone; ``onchange`` gets the choice. */
function photoSliders(photo, onchange) {
  const box = h("div", { class: "colour-values" });
  for (const [key, label] of [
    ["brightness", t("settings.x.photo_brightness")],
    ["contrast", t("settings.x.photo_contrast")],
    ["saturation", t("settings.x.photo_saturation")],
  ]) {
    const id = `photo-${key}-${Math.random().toString(36).slice(2, 8)}`;
    const shown = h("output", { class: "colour-value", for: id });
    const min = key === "saturation" ? "-1" : "-0.5";
    const slider = h("input", { type: "range", id, min, max: "0.5", step: "0.05" });
    slider.value = String(photo[key] ?? 0);
    shown.textContent = fmtNum(Number(slider.value), 2);
    slider.addEventListener("input", () => {
      shown.textContent = fmtNum(Number(slider.value), 2);
    });
    slider.addEventListener("change", () => {
      onchange({ ...photo, look: "custom", [key]: Number(slider.value) });
    });
    box.append(h("div", { class: "colour-row" },
      h("label", { class: "sub-question-title", for: id }, label), slider, shown));
  }
  return box;
}

async function saveSettings(ev) {
  ev.preventDefault();
  const err = $("settings-error");
  err.hidden = true;
  try {
    // Settings are where the *next* plan starts, and nothing more: the Plan on screen is this
    // build's and is never written over. Saving used to put the settings' source and detail
    // level back into it, so a user who chose Bing there, changed only the relief here and
    // saved, built with the source of the settings and was never told; and narrowing that to
    // the settings a save had really changed still moved the plan he had set up under him
    // (a user, 2026-09-20 and the day after).
    state.settings = await api("PUT", "/api/settings", state.settingsDraft);
    setDraft(structuredClone(state.settings));
    planChanged();  // the cost again: it was worked out with the settings of before
    // The X-Plane folder may have changed: the Settings line, step 3 and the status bar follow it,
    // and step 3 no longer shows a refusal made with the settings of before.
    await loadStatus();
    showPlanError(null);
    renderSettings(t("settings.saved"));
    setTimeout(() => {
      if (state.screen === "settings" && sameValue(state.settingsDraft, state.settings)) $("settings-status").textContent = "";
    }, 3000);
    renderProviders();  // the list of sources, never the one the Plan shows
    renderPlanSettings();
    loadPatches(); // the folder of patches may be another one now
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
  setDraft(structuredClone(state.settings));
  $("settings-error").hidden = true;
  renderSettings();
}

/** The default value of every setting, to save or not. */
function defaultSettings() {
  if (!state.schema) return;
  setDraft(defaultsKeepingFolders(state.schema, state.settingsDraft));
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

/**
 * Whether this page is OrthoStudio XP's own window rather than a tab in a browser.
 *
 * pywebview writes `window.pywebview` into the page it opens (`webview/js/api.js`), and it is
 * not there yet when the page boots: it announces itself with `pywebviewready`. Asked once at
 * boot, this answered no in the window itself, and neither Cmd+F nor the zoom was ever bound
 * (measured in a real WKWebView, 2026-09-20).
 */
export function inOwnWindow() {
  return typeof window !== "undefined" && Boolean(window.pywebview);
}

/** Run `then` if this is the window, as soon as the window says so. Never in a browser. */
export function whenInOwnWindow(then) {
  if (inOwnWindow()) then();
  else window.addEventListener("pywebviewready", then, { once: true });
}

/**
 * Whether a click lands on a field's title: a label naming a field outside it. A label hands its
 * clicks to its field, the web's way of making the target bigger: a user who double-clicked a
 * setting's name to copy it saw the caret jump into the field, or a check box tick (2026-09-21).
 * A title is text, as in the Mac's and Windows' own windows, and such a click is left to the text.
 * A label around its own box ("On", a question's choice, "Also delete the downloaded images") is
 * that box's target, and keeps it.
 */
export function isTitleClick(target) {
  const label = target?.closest?.("label");
  const control = label?.control;
  return Boolean(control) && !label.contains(control);
}

async function boot() {
  trackStatusbarHeight();
  // before any other listener: the click is the text's, not the field's
  document.addEventListener("click", (ev) => {
    if (isTitleClick(ev.target)) ev.preventDefault();
  }, true);
  window.addEventListener("resize", measureMapTop);
  window.addEventListener("resize", markWideTables);
  setLanguage(detectLanguage());
  $("lang-select").value = language();
  try {
    const theme = localStorage.getItem("osxp.theme");
    if (theme) applyTheme(theme);
  } catch (_e) {
    // ignore
  }
  applyStatic(document);
  bindFind(); // the bar's own field and buttons: the same page runs in a browser
  whenInOwnWindow(() => {
    // No tab to close in a window of its own.
    const stopped = $("stopped").querySelector("p");
    stopped.dataset.i18n = "quit.stopped_text_window";
    stopped.textContent = t("quit.stopped_text_window");
    bindFindKeys();
    bindZoom((text) => toast(text));
  });
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
  wireFlightPlan();
  $("sources-open").addEventListener("click", openSources);
  $("sources-close").addEventListener("click", () => $("sources-dialog").close());
  $("source-try").addEventListener("click", trySource);
  $("sources-form").addEventListener("submit", addSource);
  $("provider-select").addEventListener("change", () => {
    renderTilesBuilt();
    renderProviderAttribution();
    renderSourceCoverage();
    renderZlOptions();
    planChanged();
    planMap?.planChanged();
  });
  $("zl-select").addEventListener("change", () => {
    // Chosen here, the level is every chosen square's: the flight plan's two levels give way to it.
    state.planZl = Number($("zl-select").value) || planZl();
    state.zlChosen = state.planZl;  // what the pilot asked for, which the ends keep
    state.tileZl = {};
    renderTiles();
    renderZlOptions();
    renderTilesBuilt();
    planChanged();
    planMap?.planChanged();
  });
  for (const [id, tiles] of [["route-ends-zl", routeEndTiles], ["route-all-zl", routeAlongTiles]]) {
    $(id).addEventListener("change", (ev) => {
      const zl = Number(ev.target.value);
      if (!zl) return;
      for (const name of tiles()) if (state.tiles.includes(name)) state.tileZl[name] = zl;
      renderTiles();
      renderZlOptions();
      renderTilesBuilt();
      planChanged();
      planMap?.planChanged();
    });
  }
  $("estimate-btn").addEventListener("click", estimate);
  $("build-install-btn").addEventListener("click", () => build(true));
  $("build-only-btn").addEventListener("click", () => build(false));
  $("import-form").addEventListener("submit", importOrtho4xp);
  $("disk-free").addEventListener("click", freeSpace);
  $("jobs-clear").addEventListener("click", clearJobs);
  $("disk-images").addEventListener("change", renderDisk);
  $("disk-relief").addEventListener("change", renderDisk);
  $("settings-search").addEventListener("input", (e) => {
    state.settingsSearch = e.target.value;
    renderSettings();
  });
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
    route: () => state.route,
    toggleTile,
    // a zone drawn outside the chosen tiles offers to add its own (a user, 2026-09-18)
    chooseTiles: (names) => sayTilesInBuild(addTiles(names)),
    providers: () => state.providers,
    planProvider: () => $("provider-select").value,
    planZl,
    tileZl,
    sweep: { start: sweepStart, to: sweepTo, end: sweepEnd },
    /** The squares of the route's departure and arrival, which the map draws in its colour. */
    routeEnds: () => new Set(state.route ? routeEndTiles() : []),
    library: () => state.library,
    builtSummary,
    building: () => buildingOnMap(),
    engineOutdated: () => Boolean(state.engineOutdated),
    photoSliders,
    onZonesChanged: () => {
      planChanged();
      // The Library marks a tile whose square now asks for other colours: it must be drawn again
      // when the squares change, and when the document arrives after it (a user saw a tile that
      // had lost its mark, 2026-09-18).
      renderLibrary();
    },
  });
  renderTiles();
  renderPlanPanel();
  if (FLIGHT_PLAN) restoreRoute();
  startPresence();
  // The screen shows at once and fills in as the engine answers. It used to wait for every
  // answer, and where one was slow (Windows, a big cache behind an antivirus) users saw the menu
  // alone until they clicked it (2026-09-22).
  routeFromHash();
  // Who serves, answered at once: the version, Quit, and the banner of an engine older than the
  // page do not wait for the status. An engine without the route (before API level 14) says
  // nothing here, and the status says it.
  api("GET", "/api/engine").then(renderEngine, () => {});
  const providers = api("GET", "/api/providers").then((p) => {
    state.providers = Array.isArray(p) ? p : p.providers || [];
  });
  const settings = api("GET", "/api/settings").then((s) => {
    state.settings = s;
    setDraft(structuredClone(s));
  });
  const schema = api("GET", "/api/settings/schema").then((s) => {
    state.schema = s;
  });
  // The Plan's source and detail level, and Settings, as soon as what they show is known,
  // whatever the rest takes.
  const early = Promise.allSettled([providers, settings, schema]).then(() => {
    renderProviders(state.settings?.essential?.provider || "BI");
    renderPlanSettings();
    if (state.screen === "settings") renderSettings();
  });
  const results = await Promise.allSettled([
    loadStatus(),
    planMap.load(),
    api("GET", "/api/library")
      .then((l) => {
        state.library = Array.isArray(l) ? l : [];
        renderTilesBuilt(); // the squares chosen before the library arrived
      })
      .finally(() => {
        // The map opens on the tiles installed, as it always did, rather than on Europe first.
        state.libraryKnown = true;
        if (state.screen === "plan") planMap.show();
      }),
    providers,
    settings,
    schema,
    api("GET", "/api/jobs").then((j) => {
      state.jobs = Array.isArray(j) ? j : [];
    }),
    early,
  ]);
  for (const r of results) if (r.status === "rejected") toast(errorMessage(r.reason), "fail");
  routeFromHash(); // the screen shown before the answers, drawn again with them
  // A build started before the page was opened (or reloaded) shows on the map too, and the tiles
  // of those waiting cannot be chosen again.
  if (!state.jobId && state.status?.active_job) watchJob(state.status.active_job);
  jobsChanged();
  loadUpdate(); // not awaited: a slow GitHub must never hold the page back
  loadPatches();
}

/** The status bar is pinned at the bottom (a user asked): its height, which grows when its items
 * wrap, keeps Settings' sticky Save, the toast and scrolling to a field clear of it. */
/**
 * How far down the page the map starts, as --map-top, so its height can leave room for what is
 * above it and for the status bar below it.
 *
 * "100vh - 120px" counted neither, and the map ended 29px under the status bar on every screen,
 * in the browser as in the window: whatever the legend held in those pixels was cut off (a user,
 * 2026-09-20). Measured rather than guessed, because the head above wraps in some languages and
 * a banner can appear over it.
 *
 * .plan-flow is measured, not the map: .map-col sticks to the top as the page scrolls, which
 * moves it, and the height must not follow. Called where the page changes above the map rather
 * than watched: a ResizeObserver on a screen still hidden never reported it being shown
 * (measured in the browser, 2026-09-20).
 */
function measureMapTop() {
  const flow = document.querySelector(".plan-flow");
  if (!flow || flow.offsetParent === null) return; // hidden: the stylesheet's fallback stands
  let top = 0;
  for (let el = flow; el; el = el.offsetParent) top += el.offsetTop;
  const now = `${Math.round(top)}px`;
  const root = document.documentElement;
  if (root.style.getPropertyValue("--map-top") !== now) root.style.setProperty("--map-top", now);
}

function trackStatusbarHeight() {
  const bar = $("statusbar");
  const set = () => document.documentElement.style.setProperty("--statusbar-h", `${bar.offsetHeight}px`);
  set();
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(set).observe(bar);
}

if (typeof document !== "undefined" && document.getElementById("main")) boot();
