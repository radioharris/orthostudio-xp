// The map of the Plan screen (docs/specs/map-zones.md section 7): the engine's base layer, the
// 1° tile grid, zones drawn from visible buttons (the Ortho4XP gestures are only shortcuts), the
// zone list saved with PUT /api/zones, and the approximate sizes shown before any estimate.
// Leaflet 1.9.4 is vendored and loaded as a classic script before app.js (decision M1); without
// it the inputs of the Plan keep working and the list still edits the saved zones.
//
// Saves are conditional: each PUT carries the revision the page last received (If-Match), so two
// windows, or a window and a hand edit of zones.json, never overwrite each other unseen (409).
//
// State: the tile selection belongs to app.js (the chips), read through ctx.tiles() and toggled
// with ctx.toggleTile(). The zones, their order (index 0 wins, M4), the selected zone and the
// shape being drawn live here.

import { adjustImageData, photoValues } from "./colour.js";
import { fmtGround, fmtMB, t } from "./i18n.js";
import { mockPhoto } from "./preview.js";
import { sourceGroups, sourceGroupTitle, sourceLabel } from "./sources.js";
import {
  MAX_NAME,
  MAX_VERTICES,
  MAX_ZONES,
  TEXTURE_MB,
  ZONE_ZL_MAX,
  ZONE_ZL_MIN,
  ZONES_FORMAT,
  clampLat,
  cleanPolygon,
  colouredRegions,
  decodeBorders,
  insertIndexForZl,
  listedZones,
  metersPerPixel,
  movedInList,
  newZoneId,
  normalizePhoto,
  normalizeZone,
  photoKey,
  parseTile,
  pointInPolygon,
  tilesInBounds,
  polygonBounds,
  polygonIsSimple,
  round9,
  samePolygon,
  snapToTextureCorner,
  textureSquare,
  tileName,
  tileTextureCount,
  tilesTouched,
  wrapLon,
  zoneProblem,
  zoneTextureKeys,
} from "./geo.js";

export const GRID_MIN_ZOOM = 5;
export const LABEL_MIN_ZOOM = 7;
export const ZONE_MIN_ZOOM = 9;
export const DEFAULT_ZONE_ZL = 18;
const SAVE_DELAY_MS = 500;
const MAX_LABELS = 400;
const EUROPE = [[36, -10], [60, 25]];
const WORLD = [[-85.06, -180], [85.06, 180]];
/** Highest detail level the page offers: the default mesh (ZL19). The engine refuses a zone
 * above the mesh (`ZONE_INVALID`, map-zones.md 10); the document format still allows 20. */
const ZONE_ZL_OFFERED = Math.min(ZONE_ZL_MAX, 19);
const HINT_KEY = "osxp.mapHintDone";
/** Country borders (Natural Earth, public domain: vendor/borders/README.md), fetched the first
 * time they are shown. They are accurate to a few hundred metres: zoomed in closer than
 * BORDERS_MAX_ZOOM they would visibly miss the borders on the imagery, so they are hidden. */
const BORDERS_URL = "static/vendor/borders/borders.json";
export const BORDERS_MAX_ZOOM = 10;
const BORDERS_KEY = "osxp.mapBorders";

/** Airports on the map (`GET /api/airports/in`), from the index the app ships: no request leaves
 * the machine, and they show over the aerial imagery, where a runway is not always obvious. A
 * user of the X-Plane.Org page asked for an OSM background to tell whether a square holds the
 * airport he wanted (2026-09-19); this answers the question itself.
 *
 * Below AIRPORTS_MIN_ZOOM there would be thousands of them: the legend says to zoom in. */
const AIRPORTS_KEY = "osxp.mapAirports";

/** The street map: OpenStreetMap rendered by OpenFreeMap, served by the engine
 * (`api/basemap.py`). A user asked for it beside the aerial imagery, as Ortho4XP offers in its
 * preview window, to tell what a square holds before building it (2026-09-19).
 *
 * It is vector, so it needs MapLibre GL, which weighs a megabyte: the renderer is loaded the
 * first time the map is asked for, never on a page that stays on the photo. */
const STREET_KEY = "osxp.mapStreet";
const MAPLIBRE_CSS = "static/vendor/maplibre/maplibre-gl.css";
const MAPLIBRE_JS = "static/vendor/maplibre/maplibre-gl.js";
const MAPLIBRE_BRIDGE = "static/vendor/maplibre/leaflet-maplibre-gl.js";
const BASEMAP_STYLE = "api/basemap/style";
const BASEMAP_ATTRIBUTION = "© OpenFreeMap © OpenMapTiles, data © OpenStreetMap contributors";
export const AIRPORTS_MIN_ZOOM = 8;
export const AIRPORTS_LABEL_ZOOM = 9;
export const AIRPORTS_LIMIT = 200;
/** Where the code sits beside its ring, in pixels: the anchor of the icon and the box the
 * decluttering works with read the same two numbers, so they cannot drift apart. */
const LABEL_DX = 11;
const LABEL_DY = 5;
const LABEL_HEIGHT = 13;
const BORDERS_RETRY_MS = [5000, 15000, 60000, 300000];

/** The delay before reading the borders again after `tries` failed readings in a row: soon at
 * first (the likely cause is OrthoStudio XP being restarted), then every five minutes. */
export function bordersRetryDelay(tries) {
  const i = Math.min(Math.max(Math.trunc(tries) || 1, 1), BORDERS_RETRY_MS.length) - 1;
  return BORDERS_RETRY_MS[i];
}

/** Plain names of the detail levels (section 7.0.3); literal keys for the i18n test. */
const DETAIL_NAMES = {
  12: () => t("detail.12"),
  13: () => t("detail.13"),
  14: () => t("detail.14"),
  15: () => t("detail.15"),
  16: () => t("detail.16"),
  17: () => t("detail.17"),
  18: () => t("detail.18"),
  19: () => t("detail.19"),
  20: () => t("detail.20"),
};

export function detailName(zl) {
  return DETAIL_NAMES[zl] ? DETAIL_NAMES[zl]() : `ZL${zl}`;
}

/** "Very sharp, about 40 cm per pixel · ZL18": the name first, the zoom level as secondary text. */
export function detailLabel(zl, lat) {
  return t("detail.option", { name: detailName(zl), size: fmtGround(metersPerPixel(zl, lat)), zl });
}

function isMac() {
  if (typeof navigator === "undefined") return false;
  const platform = navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || "";
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** Leaflet writes attributions with innerHTML: the provider's text must stay text (no link). */
function escapeHtml(text) {
  const table = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(text).replace(/[&<>"']/g, (c) => table[c]);
}

const latLngs = (polygon) => polygon.map(([lon, lat]) => [lat, lon]);

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (_e) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_e) {
    // storage may be unavailable: the hint comes back next time
  }
}

/** The If-Match header of a save: the revision the page last received, as an entity tag (quoted,
 * `""` when no file was saved yet). No header when the engine sent no revision at all. */
export function ifMatch(revision) {
  return typeof revision === "string" ? { "If-Match": `"${revision}"` } : {};
}

/**
 * GET /api/zones as the page keeps it. `zones`: every zone the engine could read, valid or not
 * (a zone with a problem stays in the list, to be fixed or deleted); a missing or repeated id is
 * replaced, since two rows sharing an id would select, edit and delete each other. `revision`:
 * the string to send back in If-Match, or null. `marks`: zone id → the engine's reason, for the
 * problems naming a zone of the list. `fileProblems`: the messages of the other problems (an
 * unreadable entry, a file that is not JSON), which the next save replaces.
 */
export function readZonesDocument(doc) {
  const zones = [];
  const taken = new Set();
  for (const raw of doc && Array.isArray(doc.zones) ? doc.zones : []) {
    if (!raw || typeof raw !== "object") continue;
    const zone = normalizeZone(raw);
    if (typeof raw.id !== "string" || !raw.id || taken.has(zone.id)) zone.id = newZoneId(zones);
    taken.add(zone.id);
    zones.push(zone);
  }
  const tiles = {};
  const raw = doc && doc.tiles && typeof doc.tiles === "object" ? doc.tiles : {};
  for (const [name, value] of Object.entries(raw)) {
    const photo = normalizePhoto(value && value.photo);
    if (photo.look) tiles[name] = { photo };
  }
  const marks = new Map();
  const fileProblems = [];
  for (const p of doc && Array.isArray(doc.problems) ? doc.problems : []) {
    if (!p || typeof p !== "object") continue;
    if (typeof p.zone === "string" && taken.has(p.zone)) {
      const reason = String(p.reason || p.message || p.code || "");
      marks.set(p.zone, marks.has(p.zone) ? `${marks.get(p.zone)}; ${reason}` : reason);
    } else {
      const text = String(p.message || p.reason || p.code || "");
      if (text) fileProblems.push(text);
    }
  }
  return { zones, tiles, revision: doc && typeof doc.revision === "string" ? doc.revision : null, marks, fileProblems };
}

/**
 * ctx: {mock, api(method, path, body, {headers, keepalive}), toast, errorMessage, errorDetail, h,
 * clear, tiles(), toggleTile(name), providers(), planProvider(), planZl(), tileZl(name),
 * routeEnds(),
 * library(),
 * onZonesChanged()}. api() rejects with an error carrying the HTTP `status` when the engine
 * answered, without one when it could not be reached.
 */
/**
 * The route's points with their longitudes unrolled, so that a leg crossing the antimeridian is
 * drawn the short way. Tokyo to Honolulu is 62 degrees eastward over the Pacific, which is what
 * ``tilesAlong`` counts and what the two buttons offer; drawn from the raw longitudes it went
 * the other way, over Asia and the Atlantic, and the map moved to the Gulf of Guinea to show it
 * (2026-09-23). Leaflet draws a longitude past 180 where it belongs.
 *
 * A leg is brought within half a turn however far the longitudes have run on, so a route that
 * goes round the world more than once keeps going the short way at every leg.
 */
export function routeLine(points) {
  let lon = points[0].lon;
  return points.map((p, i) => {
    if (i) {
      let step = p.lon - lon;
      while (step > 180) step -= 360;
      while (step < -180) step += 360;
      lon += step;
    }
    return [p.lat, lon];
  });
}

/**
 * The route as pieces that all lie inside the world, and where each of its points is drawn.
 *
 * The unrolled line runs past 180, which is how it goes the short way; drawn there, San Francisco
 * to Tokyo puts Tokyo at longitude -220, outside the bounds the map will pan to, so its ring
 * could not be reached and the view could not be fitted to it (found in review, 2026-09-23). The
 * line is cut where it crosses the meridian and continues on the other side, the way a chart
 * draws it, and every point is drawn where the map can go.
 */
export function routePieces(points) {
  const line = routeLine(points);
  const pieces = [];
  const at = [];
  let piece = [];
  let shift = -360 * Math.round(line[0][1] / 360);
  at.push([line[0][0], line[0][1] + shift]);
  piece.push(at[0]);
  for (let i = 1; i < line.length; i += 1) {
    const [lat, lon] = line[i];
    const [prevLat] = line[i - 1];
    let a = line[i - 1][1] + shift;
    let latA = prevLat;
    let b = lon + shift;
    while (b > 180 || b < -180) {
      const edge = b > 180 ? 180 : -180;
      const part = (edge - a) / (b - a);
      const latAt = latA + (lat - latA) * part;
      piece.push([latAt, edge]);
      pieces.push(piece);
      piece = [[latAt, -edge]];
      shift += b > 180 ? -360 : 360;
      a = -edge;
      latA = latAt;
      b = lon + shift;
    }
    at.push([lat, b]);
    piece.push(at[i]);
  }
  pieces.push(piece);
  return { pieces, at };
}

export function createPlanMap(ctx) {
  const { h, clear } = ctx;
  const L = globalThis.L;
  const $ = (id) => document.getElementById(id);
  const mac = isMac();
  const keyMod = mac ? "⌘" : "Ctrl";
  const keyShift = () => (mac ? "⇧" : t("zones.key_shift"));
  const keyBack = () => (mac ? "⌫" : t("zones.key_backspace"));
  const keyDelete = () => (mac ? "⌫" : t("zones.key_delete"));

  const zs = {
    zones: [],
    tiles: {},  // tile name → {photo}: the colours a square carries of its own (map-zones.md 3)
    fileUnreadable: false,  // the saved file holds something this version could not read
    loaded: false, // GET /api/zones answered: the list may be edited and saved
    loading: null, // the GET in flight, shared by every caller
    loadError: null, // the engine could not be reached (network, 5xx): nothing is editable
    revision: null, // sent back as If-Match; null when the engine gave none
    fileProblems: [], // messages of the saved file's problems that name no zone of the list
    saveError: null,
    notice: null, // () => text: a plain line after a conflict
    selected: null,
    showAll: false, // step 2 lists every zone, not only those of the chosen tiles
    draft: null, // {kind: "rect" | "shape", vertices: [[lon, lat], ...]}
    nextZl: DEFAULT_ZONE_ZL,
    nextProvider: "", // "" = the tiles' imagery source
    pending: false,
    saving: false,
    saveTimer: 0,
    unloadGuard: false, // the beforeunload listener is installed
    // zone id → {reason, source}: "document" for the file's problems and the refusals of a save
    // (a successful save clears them), "build" for the refusals of a plan or a job.
    marks: new Map(),
    hintDone: storageGet(HINT_KEY) === "1",
    street: {
      wanted: storageGet(STREET_KEY) === "1", // off: the imagery is what a build will use
      loading: false,
      failed: false,
    },
    airports: {
      wanted: storageGet(AIRPORTS_KEY) === "1", // off unless the user asked: markers over a photo
      rows: [],
      key: "", // the view the rows were read for
      loading: false,
      missing: false, // this machine has no airport index: the legend says so
    },
  };
  const borders = {
    wanted: storageGet(BORDERS_KEY) !== "0", // shown unless the user unticked them
    ready: false, // the lines are in layers.borders
    loading: false,
    failed: false, // the last reading failed: the legend says so, and the file is read again
    tries: 0, // failed readings in a row (bordersRetryDelay)
    timer: 0, // the next reading after a failure
  };
  let map = null;
  let base = null;
  let baseKey = null;
  let zoomControl = null;
  let band = null;
  let bandFrame = 0;
  let lastShortcut = { time: 0, x: 0, y: 0, kind: "" };
  const layers = {};
  // Polygons are never modified in place (an edit replaces the array): cache by identity.
  const touchedCache = new WeakMap();
  const keysCache = new WeakMap();

  // ---------------------------------------------------------------- providers and sizes

  function providerByCode(code) {
    return ctx.providers().find((p) => p.code === code) || null;
  }

  function providerLabel(code) {
    const p = providerByCode(code);
    return p ? p.name || p.code : code;
  }

  function maxZlFor(provider) {
    const p = provider ? providerByCode(provider) : null;
    return p && Number.isInteger(p.max_zl) ? Math.min(ZONE_ZL_OFFERED, p.max_zl) : ZONE_ZL_OFFERED;
  }

  function zoneLat(z) {
    const b = polygonBounds(z.polygon);
    const lat = (b.south + b.north) / 2;
    return Number.isFinite(lat) ? lat : centerLat(); // a zone without vertices, read from a broken file
  }

  /** Latitude of the sizes in the detail labels of new zones: the map centre, else the first tile. */
  function centerLat() {
    if (map) return map.getCenter().lat;
    const first = parseTile(ctx.tiles()[0]);
    return first ? first.lat + 0.5 : 45;
  }

  /** A detail level a zone may hold: the saved file may carry anything else. */
  function usableZl(zl) {
    return Number.isInteger(zl) && zl >= ZONE_ZL_MIN && zl <= ZONE_ZL_MAX;
  }

  /** The levels offered, plus the zone's own when the page does not offer it (a hand-edited file):
   * a select must show the level the zone really has, never the first option instead. */
  function detailOptions(provider, current, lat) {
    const max = maxZlFor(provider);
    const out = [];
    if (!usableZl(current)) out.push(h("option", { value: "", disabled: true }, t("zones.zl_unusable")));
    for (let zl = ZONE_ZL_MIN; zl <= ZONE_ZL_OFFERED; zl += 1) {
      out.push(h("option", { value: String(zl), disabled: zl > max && zl !== current }, detailLabel(zl, lat)));
    }
    if (usableZl(current) && current > ZONE_ZL_OFFERED) out.push(h("option", { value: String(current) }, detailLabel(current, lat)));
    return out;
  }

  /** A zone's source list: the tiles' own first, then grouped as step 1's (sources.js). */
  function sourceOptions(current) {
    const list = ctx.providers();
    const out = [h("option", { value: "" }, t("zones.tile_provider"))];
    const groups = sourceGroups(list, [], current);
    for (const key of ["world", "mine", "other"]) {
      if (!groups[key].length) continue;
      const options = groups[key].map((p) => h("option", { value: p.code, disabled: p.alive === false && p.code !== current }, sourceLabel(p)));
      out.push(h("optgroup", { label: sourceGroupTitle(key, false) }, ...options));
    }
    if (current && !list.some((p) => p.code === current)) out.push(h("option", { value: current }, current));
    return out;
  }

  function zoneTiles(z) {
    let tiles = touchedCache.get(z.polygon);
    if (!tiles) {
      tiles = tilesTouched(z.polygon);
      touchedCache.set(z.polygon, tiles);
    }
    return tiles;
  }

  function zoneKeys(z, tile) {
    let byLevel = keysCache.get(z.polygon);
    if (!byLevel) {
      byLevel = new Map();
      keysCache.set(z.polygon, byLevel);
    }
    const k = `${z.zl}|${tile}`;
    if (!byLevel.has(k)) byLevel.set(k, zoneTextureKeys(z.polygon, z.zl, tile));
    return byLevel.get(k);
  }

  /** A zone at a tile's own level and source adds nothing there: its textures are the tile's.
   * Asked per tile since a flight plan may give its ends and its route two levels (2026-09-22). */
  function addsNothingOn(z, tile) {
    const source = ctx.planProvider();
    return z.zl === ctx.tileZl(tile) && (z.provider || source) === source;
  }

  /** Approximate extra megabytes of one zone, over every tile it covers (section 7.0.4). */
  function zoneMB(z) {
    let n = 0;
    for (const tile of zoneTiles(z)) {
      if (addsNothingOn(z, tile)) continue;
      n += zoneKeys(z, tile).length;
    }
    return n * TEXTURE_MB;
  }

  /** Megabytes of the selected tiles at their level, and of the zones inside them (shared textures once). */
  function selectionMB() {
    const source = ctx.planProvider();
    let own = 0;
    let extra = 0;
    for (const tile of ctx.tiles()) {
      own += tileTextureCount(tile, ctx.tileZl(tile));
      const keys = new Set();
      for (const z of zs.zones) {
        if (!usableZl(z.zl) || addsNothingOn(z, tile) || !zoneTiles(z).includes(tile)) continue;
        for (const k of zoneKeys(z, tile)) keys.add(`${z.provider || source}|${z.zl}|${k}`);
      }
      extra += keys.size;
    }
    return { own: own * TEXTURE_MB, extra: extra * TEXTURE_MB };
  }

  // ---------------------------------------------------------------- load and save

  /** GET /api/zones, one request at a time, shared by every caller (boot, Retry, a plan or job
   * request, a conflict). Never rejects. */
  function load() {
    if (!zs.loading) {
      zs.loading = fetchZones().finally(() => {
        zs.loading = null;
      });
    }
    return zs.loading;
  }

  async function fetchZones() {
    zs.loadError = null;
    zs.notice = null;
    renderStatus();
    let doc = null;
    try {
      doc = await ctx.api("GET", "/api/zones");
    } catch (err) {
      if (err?.status === 404 && !err?.detail?.error) {
        // Not an OrthoStudio XP error but the framework's "Not Found": an engine older than this page.
        zs.loaded = false;
        zs.loadError = t("app.engine_outdated");
      } else if (typeof err?.status !== "number" || err.status >= 500) {
        // Network or engine failure: editing waits until the saved zones are known, since a
        // save would overwrite them unseen.
        zs.loaded = false;
        zs.loadError = ctx.errorMessage(err);
      } else {
        // A refusal instead of the 200 of the contract: nothing to show, which the next save
        // replaces, as for a file that is not JSON.
        doc = { zones: [], revision: null, problems: [{ zone: null, message: ctx.errorMessage(err) }] };
      }
    }
    if (doc) {
      const read = readZonesDocument(doc);
      zs.zones = read.zones;
      zs.tiles = read.tiles;
      zs.revision = read.revision;
      zs.fileProblems = read.fileProblems;
      zs.marks = new Map([...read.marks].map(([id, reason]) => [id, { reason, source: "document" }]));
      zs.saveError = null;
      // Nothing could be read from a file that holds something: saving now would replace it
      // unseen, which is how a user lost his zones the day an older engine met a newer file
      // (2026-09-18). Editing waits until he says to start over.
      zs.fileUnreadable = read.zones.length === 0 && read.fileProblems.length > 0;
      zs.loaded = !zs.fileUnreadable;
    }
    // Every caller replaces the list (or finds it unavailable): an edit waiting to be saved is gone.
    clearTimeout(zs.saveTimer);
    setPending(false);
    if (zs.selected && !zoneById(zs.selected)) zs.selected = null;
    renderAll();
    ctx.onZonesChanged();
  }

  function zoneForApi(z) {
    return { id: z.id, name: z.name, zl: z.zl, provider: z.provider, photo: z.photo, polygon: z.polygon.map((p) => [p[0], p[1]]) };
  }

  /** The colours the chosen squares share, or null when they disagree (the Plan's control). */
  function tilesPhoto(names) {
    const keys = new Set();
    let photo = null;
    for (const name of names) {
      const own = zs.tiles[name]?.photo || null;
      keys.add(photoKey(own));
      if (own?.look) photo = own;
    }
    if (keys.size !== 1) return { mixed: true, photo: null };
    return { mixed: false, photo: keys.has("") ? null : photo };
  }

  /** Give every square and zone its colours back to Settings; answers how many.
   *
   * Installed tiles included: their pack records the colours it was built with, and the Library
   * says when it no longer matches, so the setting has nothing to keep for them. Sparing them
   * only made the button vanish while a pilot was working on an installed tile (a user,
   * 2026-09-18). */
  function resetColours() {
    let given = Object.keys(zs.tiles).length;
    zs.tiles = {};
    for (const z of zs.zones) {
      if (!z.photo?.look) continue;
      z.photo = { ...z.photo, look: null };
      given += 1;
    }
    if (given) changed();
    return given;
  }

  /** How many squares and zones carry colours of their own: the Plan says it even when nothing
   * is chosen, so a map repainted from an old visit is never a mystery (a user, 2026-09-18). */
  function ownColours() {
    const squares = Object.keys(zs.tiles).length;
    const zones = zs.zones.filter((z) => z.photo?.look).length;
    return { squares, zones, free: squares + zones };
  }

  /** Give each named square its own colours in one call (``null`` gives them back to Settings).
   *
   * Unlike ``setTilesPhoto`` the squares differ here: taking the colours back from the tiles
   * already built gives each square what its own pack holds (a user, 2026-09-18). */
  function setEachTilePhoto(byName) {
    for (const [name, photo] of Object.entries(byName)) {
      if (photo && photo.look) zs.tiles[name] = { photo: { ...photo } };
      else delete zs.tiles[name];
    }
    changed();
  }

  /** Give every square of ``names`` these colours (``null`` gives them back to Settings). */
  function setTilesPhoto(names, photo) {
    for (const name of names) {
      if (photo && photo.look) zs.tiles[name] = { photo: { ...photo } };
      else delete zs.tiles[name];
    }
    changed();
  }

  function documentBody() {
    // The squares' own colours travel with the zones: one file, one revision (map-zones.md 3).
    const tiles = {};
    for (const [name, choice] of Object.entries(zs.tiles || {})) {
      if (choice && choice.photo && choice.photo.look) tiles[name] = { photo: choice.photo };
    }
    return { format: ZONES_FORMAT, zones: zs.zones.map(zoneForApi), tiles };
  }

  function scheduleSave() {
    if (!zs.loaded) return;
    setPending(true);
    clearTimeout(zs.saveTimer);
    zs.saveTimer = setTimeout(saveNow, SAVE_DELAY_MS);
  }

  /** PUT the whole list, on condition that the file is still the revision the page read. */
  async function saveNow() {
    clearTimeout(zs.saveTimer);
    if (!zs.loaded || !zs.pending || zs.saving) return;
    setPending(false);
    setSaving(true);
    try {
      // keepalive (small bodies only, see api() in app.js): a save under way when the page
      // closes still reaches the engine.
      const saved = await ctx.api("PUT", "/api/zones", documentBody(), { headers: ifMatch(zs.revision), keepalive: true });
      if (saved && typeof saved.revision === "string") zs.revision = saved.revision;
      const dropped = dropMarks("document"); // the engine accepted every zone of the document
      const news = dropped || Boolean(zs.saveError || zs.notice || zs.fileProblems.length);
      zs.saveError = null;
      zs.notice = null;
      zs.fileProblems = []; // the file is the list now
      if (news) {
        renderList();
        renderZones();
        renderStatus();
      }
    } catch (err) {
      if (isConflict(err)) await reloadAfterConflict();
      else noteError(err, "save");
    } finally {
      setSaving(false);
      if (zs.pending) {
        // A hidden page may be closing: no delay then.
        if (document.visibilityState === "hidden") saveNow();
        else scheduleSave();
      }
    }
  }

  function isConflict(err) {
    const d = ctx.errorDetail(err) || {};
    return d.code === "ZONE_CONFLICT" || (!d.code && (err?.status === 409 || err?.status === 412));
  }

  /** Another window or a hand edit changed the file, and nothing was written: the saved zones
   * replace the list, and the user is told that their last change was not saved. */
  async function reloadAfterConflict() {
    await load();
    if (zs.loaded) {
      zs.notice = () => t("zones.conflict");
      renderStatus();
      ctx.toast(t("zones.conflict"), "fail");
    } else {
      ctx.toast(t("zones.conflict_unloaded"), "fail");
    }
  }

  /** Save at once instead of after the delay: the page is being hidden or closed. */
  function flushSave() {
    if (zs.pending && !zs.saving) saveNow();
  }

  function setPending(value) {
    zs.pending = value;
    guardUnload();
  }

  function setSaving(value) {
    zs.saving = value;
    guardUnload();
  }

  /** Closing the page while a save waits or runs asks first. The listener only exists then: it
   * keeps some browsers from caching the page for the back button. */
  function guardUnload() {
    const busy = zs.pending || zs.saving;
    if (busy === zs.unloadGuard) return;
    zs.unloadGuard = busy;
    if (busy) window.addEventListener("beforeunload", onBeforeUnload);
    else window.removeEventListener("beforeunload", onBeforeUnload);
  }

  function onBeforeUnload(ev) {
    flushSave(); // on the wire before the browser asks, so leaving loses as little as possible
    if (!zs.pending && !zs.saving) return;
    ev.preventDefault();
    ev.returnValue = true; // browsers before Chrome 119 ask only with a return value
  }

  /** Remove the marks of one source; true when there was one. */
  function dropMarks(source) {
    let dropped = false;
    for (const [id, mark] of zs.marks) {
      if (mark.source !== source) continue;
      zs.marks.delete(id);
      dropped = true;
    }
    return dropped;
  }

  /** An engine error of a save (`"save"`) or of a plan or a job (`"build"`): a ZONE_* error marks
   * the zone it names (context.zone) with the engine's reason. True for a ZONE_* error. */
  function noteError(err, source) {
    const d = ctx.errorDetail(err) || {};
    const context = d.context && typeof d.context === "object" ? d.context : {};
    const zoneError = typeof d.code === "string" && d.code.startsWith("ZONE_");
    if (source === "save") {
      zs.saveError = ctx.errorMessage(err);
      ctx.toast(t("zones.save_failed", { detail: zs.saveError }), "fail");
    }
    const id = context.zone == null ? null : String(context.zone);
    if (zoneError && id !== null && zoneById(id)) {
      zs.marks.set(id, { reason: String(context.reason || d.message || d.code), source: source === "save" ? "document" : "build" });
    }
    if (source === "save" || zoneError) {
      renderList();
      renderZones();
      renderStatus();
    }
    return zoneError;
  }

  /** After an edit of its level or source, a marked zone is checked again with the page's copy of
   * the format rules (geo.js): the mark goes when they find nothing, the engine having the last
   * word at the next save or estimate, or shows what they find. */
  function recheck(z) {
    if (!zs.marks.has(z.id)) return;
    const providers = ctx.providers();
    const reason = zoneProblem(z, null, providers.length ? providers : null);
    if (reason) zs.marks.set(z.id, { reason, source: "document" });
    else zs.marks.delete(z.id);
  }

  // ---------------------------------------------------------------- zone edits

  function zoneById(id) {
    return zs.zones.find((z) => z.id === id) || null;
  }

  function changed({ plan = true } = {}) {
    renderList();
    renderZones();
    renderGrid();  // a square that takes its own colours loses its fill at once
    refreshColours();  // the colours of the map follow the squares and the zones
    renderStatus();
    renderLegend();
    renderSizes();
    scheduleSave();
    if (plan) ctx.onZonesChanged();
  }

  function defaultName() {
    const names = new Set(zs.zones.map((z) => z.name));
    let n = zs.zones.length + 1;
    while (names.has(t("zones.default_name", { n }))) n += 1;
    return t("zones.default_name", { n });
  }

  function addZone(polygon) {
    if (zs.zones.length >= MAX_ZONES) {
      ctx.toast(t("zones.too_many", { max: MAX_ZONES }), "fail");
      return;
    }
    const zone = { id: newZoneId(zs.zones), name: defaultName(), zl: zs.nextZl,
      provider: zs.nextProvider || null, photo: normalizePhoto(null), polygon };  // fmt: skip
    zs.zones.splice(insertIndexForZl(zs.zones, zone.zl), 0, zone);
    zs.selected = zone.id;
    changed();
    scrollItemIntoView(zone.id);
  }

  function createTextureZone(lon, lat) {
    const polygon = textureSquare(lon, lat, zs.nextZl);
    const provider = zs.nextProvider || null;
    const same = zs.zones.find((z) => z.zl === zs.nextZl && z.provider === provider && samePolygon(z.polygon, polygon));
    if (same) {
      select(same.id, "map");
      ctx.toast(t("zones.exists", { name: same.name || t("zones.unnamed") }));
      return;
    }
    addZone(polygon);
  }

  /** A zone whose detail level changed goes where a new zone of that level would (sharpest first,
   * insertIndexForZl): a user who set a zone to ZL15 above a ZL18 one hid the ZL18 one entirely.
   * The arrows still reorder by hand afterwards. */
  function placeByZl(z) {
    const from = zs.zones.indexOf(z);
    if (from < 0) return;
    zs.zones.splice(from, 1);
    zs.zones.splice(insertIndexForZl(zs.zones, z.zl), 0, z);
  }

  /** One row up or down in the list as shown, over the zones it leaves out (geo.js movedInList). */
  function moveZone(id, delta) {
    const next = movedInList(zs.zones, listed(), id, delta);
    if (!next) return;
    zs.zones.splice(0, zs.zones.length, ...next);
    changed();
  }

  function removeZone(id) {
    const i = zs.zones.findIndex((z) => z.id === id);
    if (i < 0) return;
    const list = $("zone-list");
    const hadFocus = list.contains(document.activeElement);
    const [gone] = zs.zones.splice(i, 1);
    zs.marks.delete(id);
    if (zs.selected === id) zs.selected = null;
    changed();
    if (hadFocus) {
      const next = zs.zones[i] || zs.zones[i - 1];
      const target = next ? list.querySelector(`[data-focus-key="${CSS.escape(`${next.id}:delete`)}"]`) : $("zones-panel");
      if (target) target.focus();
    }
    ctx.toast(t("zones.deleted", { name: gone.name || t("zones.unnamed") }));
  }

  /** The trash above the list: the zones it shows at once, after asking (a user deleting a dozen
   * zones one × at a time asked for it). Those of other tiles, which the list leaves out, stay: it
   * never deletes what cannot be seen (2026-09-22). The usual debounced PUT saves the list; tiles
   * already built keep their sharper areas until they are built again. */
  async function removeAllZones() {
    const shown = listed().length;
    if (!zs.loaded || !shown || !(await confirmRemoveAll(shown, zs.zones.length - shown))) return;
    const gone = new Set(listed().map((z) => z.id));
    if (!zs.loaded || !gone.size) return; // reloaded or unreachable while the dialog was open
    zs.zones.splice(0, zs.zones.length, ...zs.zones.filter((z) => !gone.has(z.id)));
    for (const id of gone) zs.marks.delete(id);
    if (gone.has(zs.selected)) zs.selected = null;
    changed();
    $("zones-panel").focus({ preventScroll: true }); // the trash is hidden now
    ctx.toast(t("zones.cleared", { n: gone.size }));
  }

  function confirmRemoveAll(n, others) {
    const dialog = $("zones-clear-confirm");
    if (dialog.open) return Promise.resolve(false);
    const lines = [t("zones.clear_count", { n }), ...(others ? [t("zones.clear_others", { n: others })] : []), t("zones.clear_kept")];
    clear($("zones-clear-text")).append(...lines.map((line) => h("p", null, line)));
    dialog.returnValue = "";
    dialog.onkeydown = (ev) => {
      if (ev.key !== "Escape") return;
      ev.preventDefault(); // the map's Escape (cancel a drawing, deselect) ignores it
      dialog.close();
    };
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "clear"), { once: true });
      dialog.showModal();
    });
  }

  /** Lower a detail level to what the imagery source provides, and say so (never silent). */
  function clampZl(zl, provider) {
    const max = maxZlFor(provider);
    if (!usableZl(zl) || zl <= max) return zl; // an unusable level waits for the user's choice
    ctx.toast(t("zones.zl_clamped", { provider: providerLabel(provider), level: detailName(max) }));
    return max;
  }

  function select(id, source) {
    const next = id && zoneById(id) ? id : null;
    const moved = next !== zs.selected;
    zs.selected = next;
    if (moved) {
      const list = $("zone-list");
      // A zone of another tile, clicked on the map, joins the list; deselected (Esc, a click on the
      // map), it leaves it. Not when another row is chosen: the rows would move under the pointer.
      const rows = new Set(listed().map((z) => z.id));
      const shown = [...list.querySelectorAll(".zone-item")].map((li) => li.dataset.zone);
      if (next ? !shown.includes(next) : shown.some((id) => !rows.has(id))) renderList();
      for (const li of list.querySelectorAll(".zone-item")) {
        li.setAttribute("aria-current", String(li.dataset.zone === next));
      }
      renderZones();
    }
    if (!next) return;
    if (source === "map" && moved) scrollItemIntoView(next);
    if (source === "list") revealZone(zoneById(next));
  }

  /** Scroll the list itself, never the page (which would move the map away from the pointer). */
  function scrollItemIntoView(id) {
    const list = $("zone-list");
    const li = list.querySelector(`[data-zone="${CSS.escape(id)}"]`);
    if (!li) return;
    const lr = list.getBoundingClientRect();
    const ir = li.getBoundingClientRect();
    if (ir.top < lr.top) list.scrollTop -= lr.top - ir.top;
    else if (ir.bottom > lr.bottom) list.scrollTop += ir.bottom - lr.bottom;
  }

  function revealZone(zone) {
    if (!map || !zone) return;
    const bounds = L.latLngBounds(latLngs(zone.polygon));
    if (!bounds.isValid() || map.getBounds().intersects(bounds)) return;
    const zoom = Math.min(Math.max(map.getZoom(), ZONE_MIN_ZOOM), map.getBoundsZoom(bounds));
    map.setView(bounds.getCenter(), zoom);
  }

  // ---------------------------------------------------------------- drawing

  function startDraw(kind) {
    if (!zs.loaded) {
      ctx.toast(zs.loadError ? t("zones.load_failed", { detail: zs.loadError }) : t("app.loading"), "fail");
      return;
    }
    zs.draft = { kind, vertices: [] };
    select(null, "draw");
    renderDraft();
  }

  function cancelDraft() {
    zs.draft = null;
    renderDraft();
  }

  function undoPoint() {
    if (!zs.draft) return;
    zs.draft.vertices.pop();
    renderDraft();
  }

  function flashBanner() {
    const box = $("map-banner");
    if (!box) return;
    box.classList.remove("flash");
    void box.offsetWidth; // restart the animation
    box.classList.add("flash");
  }

  /** A point of the shape being drawn (a rectangle corner or a free-shape point). */
  function draftPoint(lon, lat, snapped = false) {
    const d = zs.draft;
    if (!d) return;
    if (!map || map.getZoom() < ZONE_MIN_ZOOM) {
      flashBanner();
      return;
    }
    const point = snapped ? snapToTextureCorner(wrapLon(lon), clampLat(lat), zs.nextZl) : [round9(wrapLon(lon)), round9(clampLat(lat))];
    const last = d.vertices[d.vertices.length - 1];
    if (last && last[0] === point[0] && last[1] === point[1]) return;
    if (d.vertices.length >= MAX_VERTICES) {
      ctx.toast(t("draw.max_points", { max: MAX_VERTICES }), "fail");
      return;
    }
    d.vertices.push(point);
    if (d.kind === "rect" && d.vertices.length === 2) finishRect();
    else renderDraft();
  }

  function finishRect() {
    const [a, b] = zs.draft.vertices;
    const west = Math.min(a[0], b[0]);
    const east = Math.max(a[0], b[0]);
    const south = Math.min(a[1], b[1]);
    const north = Math.max(a[1], b[1]);
    if (east - west < 1e-6 || north - south < 1e-6) {
      zs.draft.vertices.pop();
      ctx.toast(t("draw.flat"), "fail");
      renderDraft();
      return;
    }
    zs.draft = null;
    renderDraft();
    addZone([[west, south], [east, south], [east, north], [west, north]]);
  }

  function finishShape() {
    const d = zs.draft;
    if (!d || d.kind !== "shape") return;
    const polygon = cleanPolygon(d.vertices);
    if (polygon.length < 3) {
      ctx.toast(t("draw.too_few"), "fail");
      flashBanner();
      return;
    }
    if (!polygonIsSimple(polygon)) {
      ctx.toast(t("draw.crossing", { back: keyBack() }), "fail");
      flashBanner();
      return;
    }
    zs.draft = null;
    renderDraft();
    addZone(polygon);
  }

  // ---------------------------------------------------------------- pointer and keys

  /** The Ortho4XP gestures, kept as shortcuts (section 7.0.2); never needed. */
  function shortcut(latlng, ev, mod, shift) {
    // macOS may deliver one Ctrl+click as a contextmenu and a click: act once.
    const kind = `${mod}${shift}`;
    const now = Date.now();
    if (now - lastShortcut.time < 500 && lastShortcut.kind === kind && Math.abs(ev.clientX - lastShortcut.x) < 4 && Math.abs(ev.clientY - lastShortcut.y) < 4) return;
    lastShortcut = { time: now, x: ev.clientX, y: ev.clientY, kind };
    if (!zs.loaded) {
      ctx.toast(zs.loadError ? t("zones.load_failed", { detail: zs.loadError }) : t("app.loading"), "fail");
      return;
    }
    if (map.getZoom() < ZONE_MIN_ZOOM) {
      ctx.toast(t("draw.zoom_in"));
      return;
    }
    const busy = zs.draft && zs.draft.vertices.length > 0;
    if (mod && !shift) {
      if (busy) {
        ctx.toast(t("draw.busy"));
        flashBanner();
        return;
      }
      if (zs.draft) cancelDraft();
      createTextureZone(wrapLon(latlng.lng), clampLat(latlng.lat));
      return;
    }
    if (zs.draft && zs.draft.kind !== "shape") {
      if (busy) {
        ctx.toast(t("draw.busy"));
        flashBanner();
        return;
      }
      zs.draft = null;
    }
    if (!zs.draft) startDraw("shape");
    draftPoint(latlng.lng, latlng.lat, mod);
  }

  function plainClick(latlng) {
    if (zs.draft) {
      draftPoint(latlng.lng, latlng.lat);
      return;
    }
    const zoom = map.getZoom();
    if (zoom >= ZONE_MIN_ZOOM) {
      // Zones are picked when zoomed in only: further out a click reaches the tiles under a
      // region. The first zone of the list is the one on top, as in the build.
      const hit = zs.zones.find((z) => pointInPolygon([latlng.lng, latlng.lat], z.polygon));
      if (hit) {
        select(hit.id, "map");
        return;
      }
      if (zs.selected) {
        select(null, "map");
        return;
      }
    }
    if (zoom < GRID_MIN_ZOOM) {
      ctx.toast(t("map.zoom_for_tiles"));
      return;
    }
    const lat = Math.floor(latlng.lat);
    const lon = Math.floor(wrapLon(latlng.lng));
    if (lat < -90 || lat > 89 || lon < -180 || lon > 179) return;
    ctx.toggleTile(tileName(lat, lon));
  }

  function onMapClick(ev) {
    if (skipClick) {  // the end of a sweep, not a click on the square under the pointer
      skipClick = false;
      return;
    }
    const oe = ev.originalEvent || {};
    if (oe.detail > 1) return; // the second click of a double-click
    const mod = Boolean(oe.ctrlKey || oe.metaKey);
    if (mod || oe.shiftKey) shortcut(ev.latlng, oe, mod, Boolean(oe.shiftKey));
    else plainClick(ev.latlng);
  }

  /** Ctrl+click on macOS opens the context menu instead of clicking (M7). */
  function onContextMenu(ev) {
    if (!map || !ev.ctrlKey) return;
    ev.preventDefault();
    shortcut(map.mouseEventToLatLng(ev), ev, true, ev.shiftKey);
  }

  function onMapDblClick(ev) {
    if (!zs.draft) return;
    if (ev.originalEvent) ev.originalEvent.preventDefault();
    if (zs.draft.kind === "shape") finishShape();
  }

  // What a built tile was built with, while the pointer rests on its green outline (a user asked,
  // 2026-09-21). An element of our own rather than a Leaflet tooltip: those need the outline to be
  // interactive, and an interactive outline would take the clicks that choose the squares.
  let tip = null;

  function hideTip() {
    if (tip) tip.hidden = true;
  }

  function showTip(ev) {
    const lat = Math.floor(clampLat(ev.latlng.lat));
    const lon = Math.floor(wrapLon(ev.latlng.lng));
    const name = tileName(lat, lon);
    const text = installedTiles().includes(name) ? ctx.builtSummary?.(name) : null;
    if (!text) return hideTip();
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "map-tip";
      tip.setAttribute("role", "tooltip");
      map.getContainer().append(tip);
    }
    tip.textContent = text;
    tip.hidden = false;
    const p = ev.containerPoint;
    tip.style.left = `${p.x + 14}px`;
    tip.style.top = `${p.y + 14}px`;
  }

  /**
   * Shift and the mouse held down: every square the pointer sweeps is chosen (a user asked to
   * choose a row of squares without clicking each one, 2026-09-22).
   *
   * ``pending`` holds the press until the pointer has moved a few pixels, so that Shift+click
   * still puts a point of a free shape where it always did; from there the map stops panning and
   * each new square is added at once, which the chips and the map show as it goes.
   */
  let sweep = null;
  let skipClick = false;
  const SWEEP_MOVE_PX = 4;

  function sweepPress(ev, latlng) {
    if (zs.draft || !map || map.getZoom() < GRID_MIN_ZOOM) return;
    // Started on a square already chosen, the rectangle takes squares out rather than in: sweeping
    // back over them is how a user asked to drop them (2026-09-22).
    const under = tileName(Math.floor(latlng.lat), Math.floor(wrapLon(latlng.lng)));
    const removing = ctx.tiles().includes(under);
    sweep = { x: ev.clientX, y: ev.clientY, from: latlng, started: false, capped: false, removing };
    map.dragging.disable(); // the map would follow the pointer; taken back when the button is up
  }

  function sweepTo(latlng, ev) {
    if (!sweep || !map) return;
    if (!sweep.started) {
      const moved = Math.abs(ev.clientX - sweep.x) + Math.abs(ev.clientY - sweep.y);
      if (moved < SWEEP_MOVE_PX) return;
      sweep.started = true;
      ctx.sweep.start(sweep.removing);
      hideTip();
    }
    const box = [
      [wrapLon(sweep.from.lng), clampLat(sweep.from.lat)],
      [wrapLon(latlng.lng), clampLat(latlng.lat)],
    ];
    sweep.capped = ctx.sweep.to(tilesInBounds(box));
    drawSweepBox(sweep.from, latlng, sweep.removing);
  }

  /** The whole squares the rectangle takes, or takes out, drawn while the pointer moves. */
  function drawSweepBox(from, to, removing) {
    layers.draft.clearLayers();
    const south = Math.floor(Math.min(from.lat, to.lat));
    const north = Math.ceil(Math.max(from.lat, to.lat));
    const west = Math.floor(Math.min(from.lng, to.lng));
    const east = Math.ceil(Math.max(from.lng, to.lng));
    layers.draft.addLayer(
      L.rectangle([[south, west], [north, east]], { pane: "osxpDraft", className: `osxp-sweep-box${removing ? " is-removing" : ""}`, interactive: false, weight: 2 }),
    );
  }

  function sweepEnd() {
    if (!sweep) return;
    const { started, capped, removing } = sweep;
    sweep = null;
    map?.dragging.enable();
    layers.draft.clearLayers();
    if (!started) return;
    skipClick = true; // the click that follows the drag would toggle the square under the pointer
    const n = ctx.sweep.end();
    if (!n) return;
    if (removing) ctx.toast(t("map.swept_out", { n }));
    else if (capped) ctx.toast(t("map.swept_max", { n }));
    else ctx.toast(t("map.swept", { n }));
  }

  function onMapMouseMove(ev) {
    if (sweep) {
      sweepTo(ev.latlng, ev.originalEvent || {});
      if (sweep?.started) return;
    }
    if (!zs.draft || !band) {
      showTip(ev);
      return;
    }
    hideTip(); // drawing a zone: the tip would sit in the way
    const oe = ev.originalEvent || {};
    let cursor = ev.latlng;
    if (zs.draft.kind === "shape" && (oe.ctrlKey || oe.metaKey) && oe.shiftKey) {
      const [lon, lat] = snapToTextureCorner(wrapLon(cursor.lng), clampLat(cursor.lat), zs.nextZl);
      cursor = L.latLng(lat, lon);
    }
    cancelAnimationFrame(bandFrame);
    bandFrame = requestAnimationFrame(() => renderBand(cursor));
  }

  function planVisible() {
    const screen = $("screen-plan");
    return Boolean(screen) && !screen.hidden;
  }

  function onKeyDown(ev) {
    if (ev.defaultPrevented || ev.altKey || !planVisible()) return;
    const target = ev.target instanceof Element ? ev.target : null;
    if (target && target.closest("input, textarea, select, [contenteditable]")) return;
    const onMapOrZones = !target || target === document.body || Boolean(target.closest("#map-wrap, #zones-panel"));
    if (ev.key === "Escape") {
      if (zs.draft) {
        cancelDraft();
        ev.preventDefault();
      } else if (zs.selected && onMapOrZones) {
        select(null, "key");
      }
    } else if (ev.key === "Enter") {
      if (zs.draft && zs.draft.kind === "shape" && !(target && target.closest("button, a, summary"))) {
        finishShape();
        ev.preventDefault();
      }
    } else if (ev.key === "Backspace" || ev.key === "Delete") {
      if (!onMapOrZones) return;
      if (zs.draft) {
        undoPoint();
        ev.preventDefault();
      } else if (zs.selected) {
        removeZone(zs.selected);
        ev.preventDefault();
      }
    }
  }

  // ---------------------------------------------------------------- the map

  function show() {
    if (!L || typeof L.map !== "function") {
      setUnavailable();
      return;
    }
    if (map) {
      map.invalidateSize();
      return;
    }
    try {
      createMap();
    } catch (err) {
      console.error(err);
      map = null;
      setUnavailable();
    }
  }

  function setUnavailable() {
    const el = $("plan-map");
    if (!el) return;
    el.dataset.unavailable = "1";
    el.classList.add("is-unavailable");
    clear(el).append(h("p", { class: "placeholder" }, t("map.unavailable")));
  }

  function createMap() {
    const el = $("plan-map");
    const m = L.map(el, {
      zoomControl: false,
      boxZoom: false, // Shift+drag would zoom instead of adding a point
      doubleClickZoom: false, // a double-click finishes a free shape
      minZoom: 3,
      maxZoom: 20,
      maxBounds: WORLD,
      maxBoundsViscosity: 1,
    });
    map = m;
    setInitialView();
    m.attributionControl.setPrefix(false);
    addZoomControl();
    const bordersPane = m.createPane("osxpBorders");
    bordersPane.style.zIndex = "340"; // under the grid and the tiles
    bordersPane.style.pointerEvents = "none";
    m.createPane("osxpColours").style.zIndex = "250"; // over the imagery, under the grid
    m.createPane("osxpGrid").style.zIndex = "350"; // under the zones (overlayPane, 400)
    const labels = m.createPane("osxpLabels");
    labels.style.zIndex = "360";
    labels.style.pointerEvents = "none";
    m.createPane("osxpDraft").style.zIndex = "450";
    const airportsPane = m.createPane("osxpAirports");
    airportsPane.style.zIndex = "365"; // over the labels, under the zones
    airportsPane.style.pointerEvents = "none";
    const routePane = m.createPane("osxpRoute");
    routePane.style.zIndex = "362"; // over the grid, under the airports it leads to
    routePane.style.pointerEvents = "none";
    layers.borders = L.layerGroup();
    layers.airports = L.layerGroup();
    layers.route = L.layerGroup().addTo(m);
    layers.grid = L.layerGroup().addTo(m);
    layers.tiles = L.layerGroup().addTo(m);
    layers.labels = L.layerGroup().addTo(m);
    layers.zones = L.layerGroup().addTo(m);
    layers.draft = L.layerGroup().addTo(m);
    el.classList.toggle("is-mock", Boolean(ctx.mock));
    el.setAttribute("aria-label", t("map.label"));
    m.on("moveend", () => {
      renderGrid();
      renderBanner();
      renderToolOptions(true);
      refreshAirports();
    });
    m.on("zoomend", () => {
      renderBorders();
      drawAirports(); // the codes appear one zoom before they would be unreadable
      renderLegend();
    });
    m.on("click", onMapClick);
    m.on("dblclick", onMapDblClick);
    m.on("mousemove", onMapMouseMove);
    m.on("mouseout", hideTip);
    m.on("mouseout", () => renderBand(null));
    el.addEventListener("contextmenu", onContextMenu);
    el.addEventListener("mousedown", (ev) => {
      if (!ev.shiftKey || ev.button !== 0) return;
      ev.preventDefault(); // no text selection on Shift+click
      sweepPress(ev, m.mouseEventToLatLng(ev));
    });
    document.addEventListener("mouseup", sweepEnd);
    // Leaflet works out where a click landed from the size it last measured, and it measures
    // only when told. Nothing told it: the window resized, a panel opened, the browser's own
    // zoom changed, and every click after that fell somewhere else than where the pointer was
    // (a user drawing a shape, 2026-09-24). Watched, it is told.
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => m.invalidateSize({ animate: false, pan: false })).observe(el);
    }
    setBaseLayer(true);
    renderBorders();
    refreshAirports();
    renderAll();
  }

  // ---------------------------------------------------------------- airports

  /** Read the airports of the view when they are wanted and the zoom is close enough.
   *
   * The engine answers from the index shipped with the app, so this costs no network; the view is
   * rounded into a key so that panning a little does not ask again. */
  function refreshAirports() {
    if (!map) return;
    const state = zs.airports;
    if (!state.wanted || map.getZoom() < AIRPORTS_MIN_ZOOM) {
      if (state.rows.length) {
        state.rows = [];
        state.key = "";
        drawAirports();
      }
      return;
    }
    const b = map.getBounds().pad(0.25);
    const key = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
      .map((v) => v.toFixed(1))
      .join(",");
    if (key === state.key || state.loading) return;
    state.loading = true;
    const query = `west=${b.getWest()}&south=${b.getSouth()}&east=${b.getEast()}&north=${b.getNorth()}`;
    ctx
      // Only the airports carrying a real ICAO code: the others are identifiers of X-Plane's own
      // (`XED0051`), which a pilot cannot read on a map (a user, 2026-09-19).
      .api("GET", `/api/airports/in?${query}&limit=${AIRPORTS_LIMIT}&icao_only=true`)
      .then((rows) => {
        state.rows = Array.isArray(rows) ? rows : [];
        state.key = key;
        state.missing = false; // an X-Plane folder was chosen since, or the index was built
        drawAirports();
        renderLegend();
      })
      .catch(() => {
        // The index is absent on this machine when X-Plane's apt.dat is not there (503). The box
        // was ticked and nothing came, and a user had to ask why (2026-09-20): the legend says it
        // now. Asking again costs one small request on the next move, so a folder chosen in the
        // meantime shows its airports without a reload.
        state.rows = [];
        state.key = "";
        state.missing = true;
        drawAirports();
        renderLegend();
      })
      .finally(() => {
        state.loading = false;
      });
  }

  /** The markers: a small circle and the code, in the airports pane. */
  /**
   * The flight plan of step 1: one line through its airports, a ring at each end and a smaller one
   * at the points between. It is an aid to choosing squares, so it is drawn over the grid and
   * takes no pointer event; nothing of it is built or saved with the tiles.
   */
  function drawRoute() {
    if (!map || !layers.route) return;
    layers.route.clearLayers();
    const points = (ctx.route?.() || {}).points || [];
    if (points.length < 2) return;
    const { pieces, at } = routePieces(points);
    for (const piece of pieces) {
      L.polyline(piece, { pane: "osxpRoute", color: "#ffffff", weight: 4, opacity: 0.55 }).addTo(layers.route);
      L.polyline(piece, { pane: "osxpRoute", color: "#e0572f", weight: 2, opacity: 0.95 }).addTo(layers.route);
    }
    points.forEach((p, i) => {
      const end = i === 0 || i === points.length - 1;
      L.circleMarker(at[i], {
        pane: "osxpRoute",
        radius: end ? 5 : 3,
        weight: 2,
        color: "#e0572f",
        fillColor: end ? "#ffffff" : "#e0572f",
        fillOpacity: 1,
      })
        .bindTooltip(p.name ? `${p.ident} ${p.name}` : p.ident, { direction: "top", opacity: 0.9 })
        .addTo(layers.route);
    });
  }

  function drawAirports() {
    if (!map || !layers.airports) return;
    layers.airports.clearLayers();
    const rows = zs.airports.wanted ? zs.airports.rows : [];
    // The rings say where the airports are; the codes come one zoom later, or a view of half a
    // continent is a wall of text (a user, 2026-09-19).
    const withCode = map.getZoom() >= AIRPORTS_LABEL_ZOOM;
    // Where every ring will be, so a code is never written over a neighbour's: the codes are
    // placed in the order the engine gave them (the ones a pilot names first), and one that
    // would land on a ring or on a code already placed is left out. Its ring stays, and the
    // name is in the tooltip (a user, 2026-09-19).
    const points = rows.map((a) => map.latLngToContainerPoint([Number(a.lat), Number(a.lon)]));
    const ringBoxes = points.map((p) => ({ l: p.x - 6, t: p.y - 6, r: p.x + 6, b: p.y + 6 }));
    const placed = [];
    const clashes = (box, boxes) =>
      boxes.some((o) => !(box.r < o.l || o.r < box.l || box.b < o.t || o.b < box.t));
    for (const [index, a] of rows.entries()) {
      const lat = Number(a.lat);
      const lon = Number(a.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const name = String(a.name || "");
      const code = String(a.icao || "");
      L.circleMarker([lat, lon], {
        pane: "osxpAirports",
        radius: 4,
        weight: 2,
        color: "#ffd166",
        fillColor: "#ffd166",
        fillOpacity: 0.35,
        interactive: false,
      })
        .bindTooltip(name ? `${code} · ${name}` : code, { pane: "osxpAirports" })
        .addTo(layers.airports);
      if (!withCode) continue;
      // 6.7 px a character at 11 px in the monospace face, measured; 13 px of offset, 13 tall
      const p = points[index];
      const box = {
        l: p.x + LABEL_DX,
        t: p.y - LABEL_DY,
        r: p.x + LABEL_DX + code.length * 6.7 + 2,
        b: p.y - LABEL_DY + LABEL_HEIGHT,
      };
      if (clashes(box, placed) || clashes(box, ringBoxes.filter((_, i) => i !== index))) continue;
      placed.push(box);
      L.marker([lat, lon], {
        pane: "osxpAirports",
        interactive: false,
        // The offset is the icon's anchor, not a style: Leaflet writes its own transform on the
        // element to place it, and a transform in the stylesheet is thrown away by it (a user saw
        // "GG" for LSGG, then saw the ring still on the letters, 2026-09-19).
        icon: L.divIcon({
          className: "osxp-airport-label",
          html: escapeHtml(code),
          iconSize: [0, 0],
          iconAnchor: [-LABEL_DX, LABEL_DY],
        }),
      }).addTo(layers.airports);
    }
    if (rows.length && !map.hasLayer(layers.airports)) layers.airports.addTo(map);
    if (!rows.length && map.hasLayer(layers.airports)) map.removeLayer(layers.airports);
  }

  function setAirportsWanted(wanted) {
    zs.airports.wanted = wanted;
    storageSet(AIRPORTS_KEY, wanted ? "1" : "0");
    if (!wanted) {
      zs.airports.rows = [];
      zs.airports.key = "";
      drawAirports();
    } else {
      refreshAirports();
    }
    renderLegend();
  }

  /** The legend's airports line: a checkbox, and why nothing shows, be it the zoom or a machine
   * with no airport index at all. */
  function airportsToggle() {
    const close = map.getZoom() >= AIRPORTS_MIN_ZOOM;
    const missing = zs.airports.wanted && zs.airports.missing;
    let text = t("map.airports");
    if (missing) text = t("map.airports_missing");
    else if (zs.airports.wanted && !close) text = t("map.airports_zoomed");
    const input = h("input", {
      type: "checkbox",
      checked: zs.airports.wanted,
      onchange: (ev) => setAirportsWanted(ev.target.checked),
    });
    return h("li", { class: "legend-toggle" },
      h("label", { title: missing ? t("map.airports_missing_hint") : t("map.airports_hint") },
        input, h("span", { class: "legend-swatch legend-airport", "aria-hidden": "true" }), text));
  }

  // ---------------------------------------------------------------- country borders

  /** On the map when wanted and not zoomed in past BORDERS_MAX_ZOOM; the file is read once it
   * works. After a failure the timer reads it again (a zoom does not hurry it). */
  function renderBorders() {
    if (!map) return;
    const show = borders.wanted && map.getZoom() <= BORDERS_MAX_ZOOM;
    if (show && !borders.ready) {
      if (!borders.timer) loadBorders();
      return;
    }
    if (show && !map.hasLayer(layers.borders)) layers.borders.addTo(map);
    if (!show && map.hasLayer(layers.borders)) map.removeLayer(layers.borders);
  }

  async function loadBorders() {
    if (borders.loading || borders.ready) return;
    clearTimeout(borders.timer);
    borders.timer = 0;
    borders.loading = true;
    try {
      const res = await fetch(BORDERS_URL);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const classes = decodeBorders(await res.json());
      const attribution = escapeHtml(t("map.borders_attribution"));
      for (const [name, lines] of Object.entries(classes)) {
        if (!lines.length) continue;
        const style = { pane: "osxpBorders", className: `osxp-border is-${name}`, interactive: false, attribution };
        layers.borders.addLayer(L.polyline(lines, style));
      }
      borders.ready = true;
      borders.failed = false;
      borders.tries = 0;
    } catch (_e) {
      // The rest of the map does not need them. A reading fails while OrthoStudio XP restarts, for instance:
      // it is tried again after a delay, at once when the box is ticked again or the browser is
      // back online.
      layers.borders.clearLayers();
      borders.failed = true;
      borders.tries += 1;
      borders.timer = setTimeout(() => {
        borders.timer = 0;
        renderBorders();
      }, bordersRetryDelay(borders.tries));
    } finally {
      borders.loading = false;
    }
    renderBorders();
    renderLegend();
  }

  function setBordersWanted(wanted) {
    borders.wanted = wanted;
    storageSet(BORDERS_KEY, wanted ? "1" : "0");
    if (wanted) retryBordersNow();
    renderBorders();
    renderLegend();
  }

  /** A failed reading waits for its delay, unless the user asks again or the network is back. */
  function retryBordersNow() {
    if (!borders.failed || !borders.timer) return;
    clearTimeout(borders.timer);
    borders.timer = 0;
  }

  /** The selected tiles, else the installed ones, else Europe. */
  function setInitialView() {
    const selected = ctx.tiles();
    const names = selected.length ? selected : installedTiles();
    const cells = names.map(parseTile).filter(Boolean);
    if (!cells.length) {
      map.fitBounds(EUROPE);
      return;
    }
    const bounds = L.latLngBounds([cells[0].lat, cells[0].lon], [cells[0].lat + 1, cells[0].lon + 1]);
    for (const c of cells) bounds.extend([c.lat, c.lon]).extend([c.lat + 1, c.lon + 1]);
    map.fitBounds(bounds, { maxZoom: ZONE_MIN_ZOOM, padding: [16, 16] });
  }

  function addZoomControl() {
    if (zoomControl) zoomControl.remove();
    zoomControl = L.control.zoom({ zoomInTitle: t("map.zoom_in"), zoomOutTitle: t("map.zoom_out") }).addTo(map);
  }

  function setNotice(text) {
    const el = $("map-notice");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
  }

  function setBaseLayer(force = false) {
    if (!map) return;
    const code = ctx.planProvider() || "";
    const key = ctx.mock ? "mock" : code;
    if (!force && base && key === baseKey) return;
    if (base) map.removeLayer(base);
    base = null;
    baseKey = key;
    setNotice("");
    if (ctx.mock) base = neutralLayer();
    else if (zs.street.wanted && !zs.street.failed) base = streetLayer();
    else if (code) base = providerLayer(code);
    if (base) base.addTo(map);
  }

  /** The street map, drawn by MapLibre GL inside the Leaflet map.
   *
   * The renderer is fetched the first time it is wanted; until it is there the imagery stays, so
   * nothing blinks and a failure leaves the map as it was. */
  function streetLayer() {
    if (typeof L.maplibreGL !== "function") {
      loadMaplibre();
      return providerLayer(ctx.planProvider() || "");
    }
    const layer = L.maplibreGL({ style: BASEMAP_STYLE, attribution: BASEMAP_ATTRIBUTION });
    const gl = layer.getMaplibreMap?.();
    gl?.on("error", () => {
      // The style or a tile did not come: say it once, and go back to the imagery.
      if (zs.street.failed) return;
      zs.street.failed = true;
      setNotice(t("map.street_failed"));
      setBaseLayer(true);
      renderLegend();
    });
    return layer;
  }

  /** Load MapLibre GL and its Leaflet bridge, once, then draw the street map. */
  function loadMaplibre() {
    if (zs.street.loading || typeof L.maplibreGL === "function") return;
    zs.street.loading = true;
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = MAPLIBRE_CSS;
    document.head.append(css);
    const add = (src) =>
      new Promise((resolve, reject) => {
        const tag = document.createElement("script");
        tag.src = src;
        tag.onload = resolve;
        tag.onerror = () => reject(new Error(src));
        document.head.append(tag);
      });
    add(MAPLIBRE_JS)
      .then(() => add(MAPLIBRE_BRIDGE))
      .then(() => {
        zs.street.loading = false;
        if (zs.street.wanted) setBaseLayer(true);
        renderLegend();
      })
      .catch(() => {
        zs.street.loading = false;
        zs.street.failed = true;
        setNotice(t("map.street_failed"));
        renderLegend();
      });
  }

  function setStreetWanted(wanted) {
    zs.street.wanted = wanted;
    zs.street.failed = false;
    storageSet(STREET_KEY, wanted ? "1" : "0");
    setNotice("");
    setBaseLayer(true);
    refreshColours();
    renderLegend();
  }

  /** The legend's street-map line: a checkbox, and a word while the renderer is coming. */
  function streetToggle() {
    let text = t("map.street");
    if (zs.street.failed) text = t("map.street_failed");
    else if (zs.street.wanted && zs.street.loading) text = t("map.street_loading");
    const input = h("input", {
      type: "checkbox",
      checked: zs.street.wanted,
      onchange: (ev) => setStreetWanted(ev.target.checked),
    });
    return h("li", { class: "legend-toggle" },
      h("label", { title: t("map.street_hint") },
        input, h("span", { class: "legend-swatch legend-street", "aria-hidden": "true" }), text));
  }

  function providerLayer(code) {
    const p = providerByCode(code);
    const counts = { loaded: 0, failed: 0 };
    const layer = L.tileLayer(`api/map/${encodeURIComponent(code)}/{z}/{x}/{y}`, {
      attribution: escapeHtml(p?.attribution || p?.name || code),
      maxZoom: 20,
      maxNativeZoom: Math.min(19, Number.isInteger(p?.max_zl) ? p.max_zl : 19),
      noWrap: true,
      // Leaflet 1.9's _isValidTile ignores noWrap on a wrapping CRS: without bounds, a view at
      // the edge of the world requests x = -1 or 2^z, which the engine rightly refuses (422).
      bounds: WORLD,
    });
    layer.on("tileload", () => {
      counts.loaded += 1;
      if (layer === base) setNotice("");
    });
    // A 204 (no image there) is an error for an <img> too: only a view where nothing loads says so.
    layer.on("tileerror", () => {
      counts.failed += 1;
      if (layer === base && !counts.loaded && counts.failed >= 6) {
        setNotice(ctx.engineOutdated?.() ? t("app.engine_outdated") : t("map.base_failed", { provider: providerLabel(code) }));
      }
    });
    return layer;
  }


  // -- the colours, live on the map ----------------------------------------------------------

  /** A layer that repaints the map with the colours a build would encode (a user asked to see
   * the result on the whole square, not in a thumbnail, 2026-09-18).
   *
   * Each map tile is drawn again through ``colour.js`` -- the arithmetic a test holds equal to
   * the engine's -- and clipped to the region's polygon, so only what carries its own colours is
   * repainted and everything else stays the imagery underneath. */
  function colourLayer(code) {
    const Coloured = L.GridLayer.extend({
      createTile(coords, done) {
        const size = this.getTileSize();
        const canvas = document.createElement("canvas");
        canvas.width = size.x;
        canvas.height = size.y;
        const regions = colouredRegions(zs.zones, zs.tiles);
        const g = canvas.getContext("2d", { willReadFrequently: true });
        if (!g || !regions.length) {
          setTimeout(() => done(null, canvas), 0);
          return canvas;
        }
        const paint = (image) => {
          const origin = { x: coords.x * size.x, y: coords.y * size.y };
          const filtered = new Map();
          for (const region of regions) {
            const path = ringPath(region.ring, coords.z, origin);
            if (!path) continue;
            const key = JSON.stringify(photoValues(region.photo.look, region.photo));
            let painted = filtered.get(key);
            if (!painted) {
              painted = document.createElement("canvas");
              painted.width = size.x;
              painted.height = size.y;
              const pg = painted.getContext("2d", { willReadFrequently: true });
              pg.drawImage(image, 0, 0, size.x, size.y);
              pg.putImageData(
                adjustImageData(pg.getImageData(0, 0, size.x, size.y), photoValues(region.photo.look, region.photo)),
                0,
                0,
              );
              filtered.set(key, painted);
            }
            g.save();
            g.beginPath();
            path.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
            g.closePath();
            g.clip();
            g.drawImage(painted, 0, 0);
            g.restore();
          }
          done(null, canvas);
        };
        if (ctx.mock) {
          const image = new Image();
          image.onload = () => paint(image);
          image.src = mockPhoto(size.x, `${coords.z}/${coords.x}/${coords.y}`);
          return canvas;
        }
        // No crossOrigin: the image comes from this engine, so it never taints the canvas, and
        // asking for CORS made the browser refuse the copy the map layer had already cached
        // without it -- the tile then failed to load and stayed unpainted (a user saw a map
        // repainted in places after a hard reload, 2026-09-18).
        const image = new Image();
        image.onload = () => paint(image);
        image.onerror = () => done(null, canvas);  // no imagery there: nothing to repaint
        image.src = `api/map/${encodeURIComponent(code)}/${coords.z}/${coords.x}/${coords.y}`;
        return canvas;
      },
    });
    return new Coloured({ pane: "osxpColours", maxZoom: 20, noWrap: true, bounds: WORLD });
  }

  /** A ring of [lon, lat] in the pixels of one map tile, or null when it misses the tile. */
  function ringPath(ring, z, origin) {
    const size = 256;
    const points = ring.map((p) => {
      const pt = map.project(L.latLng(p[1], p[0]), z);
      return [pt.x - origin.x, pt.y - origin.y];
    });
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    if (Math.max(...xs) < 0 || Math.min(...xs) > size) return null;
    if (Math.max(...ys) < 0 || Math.min(...ys) > size) return null;
    return points;
  }

  /** Draw the colours again: the regions or their looks changed. */
  function refreshColours() {
    if (!map) return;
    if (colours) {
      map.removeLayer(colours);
      colours = null;
    }
    const code = ctx.planProvider ? ctx.planProvider() : null;
    // Over the street map there is no photo to recolour: the repaint would tint roads and
    // houses, which says nothing about a build.
    if (zs.street.wanted && !zs.street.failed) return;
    if (!colouredRegions(zs.zones, zs.tiles).length) return;
    colours = colourLayer(code || "BI");
    colours.addTo(map);
  }

  /** Mock mode: a neutral canvas grid drawn in the browser, no request (spec section 7.1.7). */
  function neutralLayer() {
    const Neutral = L.GridLayer.extend({
      createTile(coords) {
        const size = this.getTileSize();
        const canvas = document.createElement("canvas");
        canvas.width = size.x;
        canvas.height = size.y;
        const g = canvas.getContext("2d");
        if (!g) return canvas;
        const css = getComputedStyle(document.documentElement);
        const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
        g.fillStyle = token("--bg-3", "#ecebe6");
        g.fillRect(0, 0, size.x, size.y);
        g.strokeStyle = token("--line", "#d8d6cf");
        g.lineWidth = 1;
        g.strokeRect(0.5, 0.5, size.x - 1, size.y - 1);
        g.fillStyle = token("--fg-3", "#8a8780");
        g.font = "10px ui-monospace, Menlo, monospace";
        g.fillText(`${coords.z}/${coords.x}/${coords.y}`, 6, 14);
        return canvas;
      },
    });
    return new Neutral({ attribution: escapeHtml(t("map.mock_attribution")), maxZoom: 20, noWrap: true, bounds: WORLD });
  }

  /** The tiles of the running build and what it does with each (``working``, ``queued``,
   * ``failed``); empty without a build. */
  function buildingNow() {
    const building = ctx.building ? ctx.building() : null;
    return building instanceof Map ? building : new Map();
  }

  let colours = null;  // the live colour layer, when something carries its own
  let buildingKey = "";

  /** Tiles whose own pack is in X-Plane. A tile's overlay row says whether the shared
   * yOrthoStudio_Overlays pack is, which stays while any OrthoStudio XP tile is: it would keep a removed tile
   * green. A row without `kind` is a tile pack. */
  function installedTiles() {
    return ctx
      .library()
      .filter((e) => e && (e.kind == null || e.kind === "ortho") && e.installed && parseTile(e.tile))
      .map((e) => e.tile);
  }

  /** `box` ([[south, west], [north, east]]) drawn `px` pixels inside itself at the map's zoom, or
   * null when the tile is too small on the screen for it. */
  function insetBox(box, px) {
    const nw = map.latLngToLayerPoint([box[1][0], box[0][1]]);
    const se = map.latLngToLayerPoint([box[0][0], box[1][1]]);
    if (se.x - nw.x < 4 * px || se.y - nw.y < 4 * px) return null;
    const inNw = map.layerPointToLatLng(L.point(nw.x + px, nw.y + px));
    const inSe = map.layerPointToLatLng(L.point(se.x - px, se.y - px));
    return [[inSe.lat, inNw.lng], [inNw.lat, inSe.lng]];
  }

  /** 1° grid and tile labels for the viewport only; selected and installed tiles at every zoom. */
  function renderGrid() {
    if (!map) return;
    const routeEnds = ctx.routeEnds?.() ?? new Set();
    layers.grid.clearLayers();
    layers.tiles.clearLayers();
    layers.labels.clearLayers();
    const zoom = map.getZoom();
    const view = map.getBounds();
    const south = Math.max(-85, Math.floor(view.getSouth()));
    const north = Math.min(85, Math.ceil(view.getNorth()));
    const west = Math.max(-180, Math.floor(view.getWest()));
    const east = Math.min(180, Math.ceil(view.getEast()));
    if (zoom >= GRID_MIN_ZOOM) {
      const line = { pane: "osxpGrid", className: "osxp-grid-line", interactive: false, weight: 1 };
      for (let lon = west; lon <= east; lon += 1) layers.grid.addLayer(L.polyline([[south, lon], [north, lon]], line));
      for (let lat = south; lat <= north; lat += 1) layers.grid.addLayer(L.polyline([[lat, west], [lat, east]], line));
    }
    const selected = new Set(ctx.tiles());
    const installed = new Set(installedTiles());
    const building = buildingNow();
    for (const name of new Set([...installed, ...selected, ...building.keys()])) {
      const c = parseTile(name);
      if (!c || c.lat + 1 < south || c.lat > north || c.lon + 1 < west || c.lon > east) continue;
      const box = [[c.lat, c.lon], [c.lat + 1, c.lon + 1]];
      // Installed and chosen: the green outline, the blue one inside it, and a line between and
      // around them (--map-casing, dark on the dark theme), so that they stand out from each other
      // and from the photo. On the same line the green hid the blue, and a thin blue beside the
      // green hardly showed (a user, 2026-09-22). Too small to hold both, the tile shows the green:
      // from far away the map is there to show which tiles are installed (the same user).
      const inner = installed.has(name) && selected.has(name) ? insetBox(box, 4) : null;
      if (inner) {
        for (const b of [box, inner]) {
          layers.tiles.addLayer(L.rectangle(b, { pane: "osxpGrid", className: "osxp-tile-casing", interactive: false, fill: false, weight: 5 }));
        }
      }
      const both = inner ? " is-both" : "";
      if (installed.has(name)) {
        layers.tiles.addLayer(L.rectangle(box, { pane: "osxpGrid", className: `osxp-tile-installed${both}`, interactive: false, fill: false, weight: 3 }));
      }
      if (selected.has(name) && (inner || !installed.has(name))) {
        // The route's departure and arrival in the route's own colour: on a plan across Europe
        // every square was the same blue and the two ends were lost in it (a user, 2026-09-22).
        const end = routeEnds.has(name) ? " is-route-end" : "";
        layers.tiles.addLayer(L.rectangle(inner || box, { pane: "osxpGrid", className: `osxp-tile-selected${both}${end}`, interactive: false, fill: false, weight: 3 }));
      }
      // Over the others: a tile the running build works on pulses, one waiting for its turn is
      // dashed, a failed one dashed red (buildingTiles in app.js).
      if (building.has(name)) {
        layers.tiles.addLayer(L.rectangle(box, { pane: "osxpGrid", className: `osxp-tile-${building.get(name)}`, interactive: false, weight: 3 }));
      }
    }
    if (zoom < LABEL_MIN_ZOOM || (north - south) * (east - west) > MAX_LABELS) return;
    // Each label sits in the north-west corner of the visible part of its tile, when that part
    // can hold it (a sliver of tile at the edge of the view gets none).
    const top = view.getNorth();
    const left = view.getWest();
    for (let lat = south; lat < north && lat <= 89; lat += 1) {
      for (let lon = west; lon < east && lon <= 179; lon += 1) {
        const nw = map.latLngToContainerPoint([Math.min(lat + 1, top), Math.max(lon, left)]);
        const se = map.latLngToContainerPoint([Math.max(lat, view.getSouth()), Math.min(lon + 1, view.getEast())]);
        if (se.x - nw.x < 64 || se.y - nw.y < 22) continue;
        const name = tileName(lat, lon);
        const icon = L.divIcon({
          className: `osxp-tile-label${selected.has(name) ? " is-selected" : ""}`,
          html: h("span", null, name),
          iconSize: null,
          iconAnchor: [-4, -4],
        });
        layers.labels.addLayer(L.marker([Math.min(lat + 1, top), Math.max(lon, left)], { icon, pane: "osxpLabels", interactive: false, keyboard: false }));
      }
    }
  }

  function renderZones() {
    if (!map) return;
    layers.zones.clearLayers();
    // Painted from the end of the list, so the first zone (the one that wins) is on top.
    for (let i = zs.zones.length - 1; i >= 0; i -= 1) {
      const z = zs.zones[i];
      if (z.polygon.length < 3) continue; // read from a broken file: listed, nothing to draw
      const cls = ["osxp-zone", `zl-${z.zl}`];
      if (z.id === zs.selected) cls.push("is-selected");
      if (zs.marks.has(z.id)) cls.push("is-invalid");
      layers.zones.addLayer(L.polygon(latLngs(z.polygon), { className: cls.join(" "), interactive: false, fill: false, weight: 3 }));
    }
    const sel = zoneById(zs.selected);
    if (sel && sel.polygon.length >= 3) {
      layers.zones.addLayer(L.polygon(latLngs(sel.polygon), { className: `osxp-zone-outline zl-${sel.zl}`, interactive: false, fill: false, weight: 2 }));
    }
  }

  function renderDraft() {
    renderBanner();
    if (!map) return;
    layers.draft.clearLayers();
    band = null;
    map.getContainer().classList.toggle("is-drawing", Boolean(zs.draft));
    $("map-wrap")?.classList.toggle("is-drawing", Boolean(zs.draft));
    if (!zs.draft) return;
    const cls = `zl-${zs.nextZl}`;
    const pts = latLngs(zs.draft.vertices);
    if (zs.draft.kind === "shape" && pts.length >= 2) {
      layers.draft.addLayer(L.polyline(pts, { pane: "osxpDraft", className: `osxp-draft ${cls}`, interactive: false, weight: 2 }));
    }
    band = zs.draft.kind === "rect"
      ? L.polygon([], { pane: "osxpDraft", className: `osxp-draft-band is-rect ${cls}`, interactive: false, weight: 2 })
      : L.polyline([], { pane: "osxpDraft", className: `osxp-draft-band ${cls}`, interactive: false, weight: 1 });
    layers.draft.addLayer(band);
    for (const p of pts) {
      layers.draft.addLayer(L.circleMarker(p, { pane: "osxpDraft", className: `osxp-draft-vertex ${cls}`, interactive: false, radius: 4 }));
    }
  }

  function renderBand(cursor) {
    if (!band || !zs.draft) return;
    const v = zs.draft.vertices;
    if (!cursor || !v.length) {
      band.setLatLngs([]);
      return;
    }
    const first = L.latLng(v[0][1], v[0][0]);
    if (zs.draft.kind === "rect") {
      band.setLatLngs([first, L.latLng(first.lat, cursor.lng), cursor, L.latLng(cursor.lat, first.lng)]);
      return;
    }
    const last = L.latLng(v[v.length - 1][1], v[v.length - 1][0]);
    band.setLatLngs(v.length >= 2 ? [last, cursor, first] : [last, cursor]);
  }

  // ---------------------------------------------------------------- the panel and overlays

  function renderAll() {
    refreshColours();
    renderToolOptions();
    renderHelp();
    renderStatus();
    renderList();
    renderDraft();
    renderZones();
    renderGrid();
    renderLegend();
    renderSizes();
    renderHint();
    drawRoute();
  }

  /** The banner over the map says what to do next while drawing (section 7.0.2). */
  function renderBanner() {
    const box = $("map-banner");
    if (!box) return;
    const d = zs.draft;
    box.hidden = !d;
    for (const kind of ["rect", "shape"]) {
      $(`draw-${kind}`)?.setAttribute("aria-pressed", String(Boolean(d) && d.kind === kind));
    }
    renderHint();
    if (!d) return;
    let text;
    if (!map || map.getZoom() < ZONE_MIN_ZOOM) text = t("draw.zoom_in");
    else if (d.kind === "rect") text = d.vertices.length ? t("draw.rect_second") : t("draw.rect_first");
    else text = `${t("draw.shape_hint")} ${t("draw.points", { n: d.vertices.length })}`;
    $("map-banner-text").textContent = text;
    $("draw-finish").hidden = d.kind !== "shape";
    $("draw-finish").disabled = d.vertices.length < 3;
    $("draw-undo").disabled = d.vertices.length === 0;
    $("draw-center").disabled = !map || map.getZoom() < ZONE_MIN_ZOOM;
  }

  function renderHint() {
    const hint = $("map-hint");
    if (!hint) return;
    hint.hidden = !map || Boolean(zs.draft) || zs.hintDone || ctx.tiles().length > 0;
  }

  function dismissHint() {
    zs.hintDone = true;
    storageSet(HINT_KEY, "1");
    renderHint();
  }

  /** Installed tiles, selected tiles, and the detail levels in use (section 7.0.5). */
  function renderLegend() {
    const box = $("map-legend");
    if (!box) return;
    // The legend is rebuilt on zoom and on a toggle: the borders checkbox keeps the focus it had.
    const hadFocus = document.activeElement !== null && box.querySelector(".legend-toggle input") === document.activeElement;
    const row = (swatch, text) => h("li", null, h("span", { class: `legend-swatch ${swatch}`, "aria-hidden": "true" }), text);
    const items = [row("legend-installed", t("map.legend_installed")), row("legend-selected", t("map.legend_selected"))];
    if ((ctx.route?.() || {}).points?.length >= 2) {
      items.push(row("legend-route-end", t("map.legend_route_ends")));
    }
    const states = new Set(buildingNow().values());
    if (states.has("working")) items.push(row("legend-working", t("map.legend_working")));
    if (states.has("queued")) items.push(row("legend-queued", t("map.legend_queued")));
    if (states.has("failed")) items.push(row("legend-failed", t("map.legend_failed")));
    if (map) items.push(bordersToggle(), airportsToggle(), streetToggle());
    const levels = [...new Set([...zs.zones.map((z) => z.zl).filter(usableZl), ...(zs.draft ? [zs.nextZl] : [])])].sort((a, b) => b - a);
    const list = h("ul", null, items);
    clear(box).append(list);
    if (hadFocus) box.querySelector(".legend-toggle input")?.focus();
    if (!levels.length) return;
    box.append(
      h("p", { class: "legend-title" }, t("map.legend_zones")),
      h("ul", null, levels.map((zl) => h("li", { class: `zl-${zl}` }, h("span", { class: "legend-swatch legend-zone", "aria-hidden": "true" }), detailName(zl)))),
    );
  }

  /** The legend's borders line: a checkbox, and why nothing shows when zoomed in close. */
  function bordersToggle() {
    let text = t("map.borders");
    if (borders.wanted && map.getZoom() > BORDERS_MAX_ZOOM) text = t("map.borders_zoomed");
    else if (borders.wanted && borders.failed) text = t("map.borders_failed");
    const input = h("input", {
      type: "checkbox",
      checked: borders.wanted,
      onchange: (ev) => setBordersWanted(ev.target.checked),
    });
    return h("li", { class: "legend-toggle" },
      h("label", { title: t("map.borders_hint") },
        input, h("span", { class: "legend-swatch legend-border", "aria-hidden": "true" }), text));
  }

  function renderSizes() {
    const own = $("selection-size");
    const total = $("zones-total");
    const { own: ownMB, extra } = selectionMB();
    if (own) {
      own.textContent = !ctx.tiles().length ? "" : extra > 0
        ? t("step1.size_extra", { total: fmtMB(ownMB + extra), extra: fmtMB(extra) })
        : t("step1.size", { total: fmtMB(ownMB) });
    }
    if (total) total.textContent = zs.zones.length && ctx.tiles().length ? t("zones.total", { size: fmtMB(extra) }) : "";
  }

  function renderToolOptions(onMove = false) {
    const zlSel = $("zone-zl-select");
    const srcSel = $("zone-provider-select");
    if (!zlSel || !srcSel) return;
    // Rebuilt on every map move for the latitude of the sizes, unless the user is in the select.
    if (onMove && document.activeElement === zlSel) return;
    if (zs.nextProvider && ctx.providers().length && !providerByCode(zs.nextProvider)) zs.nextProvider = "";
    zs.nextZl = Math.min(zs.nextZl, maxZlFor(zs.nextProvider || null));
    clear(zlSel).append(...detailOptions(zs.nextProvider || null, zs.nextZl, centerLat()));
    zlSel.value = String(zs.nextZl);
    if (onMove) return;
    clear(srcSel).append(...sourceOptions(zs.nextProvider || null));
    srcSel.value = zs.nextProvider;
  }

  function renderHelp() {
    const ul = $("zones-help");
    if (!ul) return;
    const shift = keyShift();
    clear(ul).append(
      h("li", null, t("zones.sc_square", { mod: keyMod })),
      h("li", null, t("zones.sc_point", { shift })),
      h("li", null, t("map.sc_sweep", { shift })),
      h("li", null, t("zones.sc_snap", { mod: keyMod, shift })),
      h("li", null, t("zones.sc_finish", { back: keyBack() })),
      h("li", null, t("zones.sc_delete", { del: keyDelete() })),
      h("li", null, t("zones.sc_select")),
    );
  }

  function renderStatus() {
    const box = $("zones-status");
    if (!box) return;
    let message = "";
    let kind = "";
    if (!zs.loaded) {
      message = zs.loadError ? t("zones.load_failed", { detail: zs.loadError }) : t("app.loading");
      kind = zs.loadError ? "error" : "";
    } else if (zs.saveError) {
      message = t("zones.save_failed", { detail: zs.saveError });
      kind = "error";
    } else if (zs.notice) {
      message = zs.notice();
      kind = "warn";
    }
    box.hidden = !message;
    box.classList.toggle("is-error", kind === "error");
    box.classList.toggle("is-warn", kind === "warn");
    $("zones-status-text").textContent = message;
    $("zones-retry").hidden = kind !== "error";
    for (const kind of ["rect", "shape"]) {
      const btn = $(`draw-${kind}`);
      if (btn) btn.disabled = !zs.loaded;
    }
    renderListHead();
    renderProblems();
  }

  /** The zones the list shows (geo.js listedZones): the selected one and those with a problem to
   * fix always, so that a zone just drawn or clicked on the map is never out of sight. */
  function listed({ all = zs.showAll } = {}) {
    return listedZones(zs.zones, ctx.tiles(), {
      all,
      keep: (z) => z.id === zs.selected || zs.marks.has(z.id) || z.polygon.length < 3,
      tilesOf: zoneTiles,
    });
  }

  /** Around the list: the priority sentence and the trash when it has rows, and under it the
   * zones of other tiles it leaves out, with the button that shows them or hides them again. */
  function renderListHead() {
    const shown = listed().length;
    const outside = zs.zones.length - listed({ all: false }).length;
    $("zones-empty").hidden = !zs.loaded || zs.zones.length > 0;
    $("zones-priority").hidden = shown < 2;
    $("zone-list-head").hidden = !zs.loaded || shown === 0;
    $("zones-others").hidden = !zs.loaded || outside === 0;
    const text = $("zones-others-text");
    text.hidden = zs.showAll;
    text.textContent = t("zones.others", { n: outside });
    $("zones-others-toggle").textContent = zs.showAll ? t("zones.others_hide") : t("zones.others_show");
  }

  /** Above the list: the saved file's problems that name no zone of the list (the next save
   * replaces them), and how many zones of the list are marked. */
  function renderProblems() {
    const box = $("zones-problems");
    if (!box) return;
    const marked = zs.zones.filter((z) => zs.marks.has(z.id)).length;
    clear(box);
    box.hidden = !zs.loaded && !zs.fileUnreadable ? true : !zs.fileProblems.length && !marked;
    if (box.hidden) return;
    if (zs.fileUnreadable) {
      box.append(
        h("p", null, t("zones.file_unreadable")),
        h("ul", null, zs.fileProblems.map((text) => h("li", null, text))),
        h("button", { type: "button", class: "btn btn-small", onclick: () => {
          zs.fileUnreadable = false;
          zs.loaded = true;
          renderAll();
        } }, t("zones.file_start_over")),
      );
      return;
    }
    if (zs.fileProblems.length) {
      box.append(h("p", null, t("zones.file_problems")), h("ul", null, zs.fileProblems.map((text) => h("li", null, text))));
    }
    if (marked) box.append(h("p", null, t("zones.marked", { n: marked })));
  }

  /** Focus survives a re-render of the list: controls carry data-focus-key = "<zone id>:<part>". */
  function captureFocus(container) {
    const el = document.activeElement;
    if (!el || !container.contains(el) || !el.dataset || !el.dataset.focusKey) return null;
    let start = null;
    let end = null;
    try {
      start = el.selectionStart ?? null;
      end = el.selectionEnd ?? null;
    } catch (_e) {
      // not a text control
    }
    return { key: el.dataset.focusKey, start, end };
  }

  function restoreFocus(container, saved) {
    if (!saved) return;
    let el = container.querySelector(`[data-focus-key="${CSS.escape(saved.key)}"]`);
    if (!el || el.disabled) {
      const item = container.querySelector(`[data-zone="${CSS.escape(saved.key.split(":")[0])}"]`);
      el = item ? [...item.querySelectorAll("[data-focus-key]")].find((x) => !x.disabled) : null;
    }
    if (!el) return;
    el.focus({ preventScroll: true });
    if (saved.start != null && typeof el.setSelectionRange === "function") {
      try {
        el.setSelectionRange(saved.start, saved.end);
      } catch (_e) {
        // not a text control
      }
    }
  }

  let listRendering = false;

  function renderList() {
    const list = $("zone-list");
    // Removing a focused name field fires its change event, whose handler renders the list: that
    // inner call is skipped (this one reads the zones after it anyway), or the outer removal
    // would fail on a node already gone and the rows would be appended twice.
    if (!list || listRendering) return;
    listRendering = true;
    try {
      const saved = captureFocus(list);
      clear(list);
      const selectedTiles = new Set(ctx.tiles());
      const source = providerByCode(ctx.planProvider());
      const rows = listed();
      rows.forEach((z, i) => list.append(zoneItem(z, i, rows.length, selectedTiles, source)));
      restoreFocus(list, saved);
    } finally {
      listRendering = false;
    }
    renderListHead();
  }

  function zoneItem(z, index, count, selectedTiles, source) {
    const label = z.name || t("zones.unnamed");
    const key = (part) => ({ focusKey: `${z.id}:${part}` });
    const disabled = !zs.loaded;

    const name = h("input", { type: "text", class: "zone-name", maxlength: MAX_NAME, spellcheck: "false", autocomplete: "off", "aria-label": t("zones.name"), placeholder: t("zones.unnamed"), disabled, dataset: key("name") });
    name.value = z.name;
    name.addEventListener("input", () => {
      z.name = name.value.slice(0, MAX_NAME);
      scheduleSave();
    });
    name.addEventListener("change", () => renderList()); // refresh the accessible names of the buttons

    const detail = h("select", { class: "zone-zl", "aria-label": t("zones.detail_of", { name: label }), disabled, dataset: key("zl") }, detailOptions(z.provider, z.zl, zoneLat(z)));
    detail.value = usableZl(z.zl) ? String(z.zl) : "";
    detail.addEventListener("change", () => {
      z.zl = Number(detail.value);
      placeByZl(z);
      recheck(z);
      changed();
    });

    const imagery = h("select", { class: "zone-provider", "aria-label": t("zones.source_of", { name: label }), disabled, dataset: key("provider") }, sourceOptions(z.provider));
    imagery.value = z.provider || "";
    imagery.addEventListener("change", () => {
      z.provider = imagery.value || null;
      const zl = z.zl;
      z.zl = clampZl(z.zl, z.provider);
      if (z.zl !== zl) placeByZl(z);
      recheck(z);
      changed();
    });

    // Colours of this zone's photos, or the tile's answer (a user asked for colours per zone,
    // 2026-09-18). The names are the ones of the Settings question.
    const colours = h("select", { class: "zone-colours", "aria-label": t("zones.colours_of", { name: label }), disabled, dataset: key("colours") },
      [["", t("zones.colours_tile")],
       ["as_delivered", t("settings.q.colours_as_delivered")],
       ["softer", t("settings.q.colours_softer")],
       ["much_softer", t("settings.q.colours_much_softer")],
       ["custom", t("settings.q.colours_custom")]].map(([value, text]) => h("option", { value }, text)));
    colours.value = z.photo?.look || "";
    colours.addEventListener("change", () => {
      z.photo = { ...normalizePhoto(z.photo), look: colours.value || null };
      changed();
      renderList();  // the sliders of "my own values" appear or go
    });
    // The three numbers, right under the choice, as in Settings and in step 1 (a user, 2026-09-18)
    const sliders = z.photo?.look === "custom" ? ctx.photoSliders(z.photo, (next) => {
      z.photo = next;
      changed();
    }) : null;

    const button = (part, text, title, onclick, off) =>
      h("button", { type: "button", class: "btn btn-small btn-icon", "aria-label": title, title, disabled: disabled || off, dataset: key(part), onclick }, text);
    const up = button("up", "↑", t("zones.move_up", { name: label }), () => moveZone(z.id, -1), index === 0);
    const down = button("down", "↓", t("zones.move_down", { name: label }), () => moveZone(z.id, 1), index === count - 1);
    const del = button("delete", "×", t("zones.delete", { name: label }), () => removeZone(z.id), false);

    const notes = h("div", { class: "zone-notes" });
    if (usableZl(z.zl)) {
      const mb = zoneMB(z);
      notes.append(h("span", { class: "zone-size num" }, mb > 0 ? t("zones.size", { size: fmtMB(mb) }) : t("zones.size_none")));
    }
    const touched = zoneTiles(z);
    if (touched.length > 1) notes.append(h("span", { class: "pill pill-region", title: touched.join(" ") }, t("zones.region", { n: touched.length })));
    if (!touched.some((n) => selectedTiles.has(n))) {
      // A zone builds nothing on its own: a build makes whole tiles. Rather than only saying so,
      // the row adds the tiles the zone falls in, in one click (a user asked what happens to a
      // zone drawn without tiles, 2026-09-18). Never on its own: a region can hold many tiles,
      // and each one is a download.
      notes.append(h("span", { class: "zone-hint" }, t("zones.outside")));
      notes.append(h("button", {
        type: "button", class: "btn btn-small btn-accent", disabled, title: touched.join(" "),
        onclick: () => ctx.chooseTiles(touched),
      }, t("zones.outside_add", { n: touched.length })));
    }
    // the level of the chosen squares this zone falls in, which a flight plan may vary
    const inside = touched.filter((n) => selectedTiles.has(n));
    const tilesZl = inside.length ? Math.max(...inside.map(ctx.tileZl)) : Number(ctx.planZl());
    if (usableZl(z.zl) && Number.isInteger(tilesZl) && z.zl < tilesZl) {
      // Less sharp than the tiles: the area comes out blurrier than the rest of the tile.
      notes.append(h("span", { class: "zone-hint zone-hint-warn" }, t("zones.below_tiles", { level: detailName(tilesZl) })));
    }
    if (!z.provider && source && Number.isInteger(source.max_zl) && z.zl > source.max_zl) {
      notes.append(h("span", { class: "zone-hint zone-hint-warn" }, t("zones.above_source", { provider: source.name || source.code, level: detailName(source.max_zl) })));
    }
    const mark = zs.marks.get(z.id);
    if (mark) notes.append(h("span", { class: "zone-hint zone-hint-fail" }, t("zones.invalid", { reason: mark.reason })));

    const li = h("li", { class: `zone-item${mark ? " is-invalid" : ""}`, "aria-current": String(z.id === zs.selected), dataset: { zone: z.id } },
      h("span", { class: `zl-swatch zl-${z.zl}`, "aria-hidden": "true" }),
      h("div", { class: "zone-main" },
        h("div", { class: "zone-row" }, name, up, down, del),
        h("div", { class: "zone-row" }, detail),
        h("div", { class: "zone-row" }, imagery),
        h("div", { class: "zone-row" }, colours),
        sliders ? h("div", { class: "zone-row zone-sliders" }, sliders) : null,
        notes));
    li.addEventListener("focusin", () => select(z.id, "list"));
    li.addEventListener("click", (ev) => {
      if (!(ev.target instanceof Element) || !ev.target.closest("button, input, select")) select(z.id, "list");
    });
    return li;
  }

  // ---------------------------------------------------------------- wiring

  function bindPanel() {
    $("zone-zl-select")?.addEventListener("change", (ev) => {
      zs.nextZl = Number(ev.target.value);
      renderDraft();
      renderLegend();
    });
    $("zone-provider-select")?.addEventListener("change", (ev) => {
      zs.nextProvider = ev.target.value;
      zs.nextZl = clampZl(zs.nextZl, zs.nextProvider || null);
      renderToolOptions();
      renderDraft();
    });
    $("draw-rect")?.addEventListener("click", () => (zs.draft?.kind === "rect" ? cancelDraft() : startDraw("rect")));
    $("draw-shape")?.addEventListener("click", () => (zs.draft?.kind === "shape" ? cancelDraft() : startDraw("shape")));
    $("draw-center")?.addEventListener("click", () => {
      if (!map) return;
      const c = map.getCenter();
      draftPoint(c.lng, c.lat);
    });
    $("draw-undo")?.addEventListener("click", undoPoint);
    $("draw-finish")?.addEventListener("click", finishShape);
    $("draw-cancel")?.addEventListener("click", cancelDraft);
    $("zones-clear")?.addEventListener("click", removeAllZones);
    $("zones-others-toggle")?.addEventListener("click", () => {
      zs.showAll = !zs.showAll;
      renderList();
    });
    $("map-hint-close")?.addEventListener("click", dismissHint);
    $("zones-retry")?.addEventListener("click", () => {
      if (!zs.loaded) {
        load();
        return;
      }
      setPending(true);
      saveNow();
    });
    document.addEventListener("keydown", onKeyDown);
    // A page being closed or left is hidden first (a tab switch too): an edit waiting for its
    // delay is saved at once. pagehide covers browsers that skip visibilitychange on unload.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushSave();
    });
    window.addEventListener("pagehide", flushSave);
    window.addEventListener("online", () => {
      retryBordersNow();
      renderBorders();
    });
    try {
      matchMedia("(prefers-color-scheme: dark)").addEventListener("change", themeChanged);
    } catch (_e) {
      // old browsers: the theme button still redraws
    }
  }

  function themeChanged() {
    if (map && ctx.mock && base && typeof base.redraw === "function") base.redraw();
  }

  function rerender() {
    const el = $("plan-map");
    if (el && el.dataset.unavailable) setUnavailable();
    if (map) {
      el.setAttribute("aria-label", t("map.label"));
      addZoomControl();
      if (ctx.mock) setBaseLayer(true);
      if (borders.ready) {
        // Leaflet counts attributions as the layers come and go: the new text goes in off the map.
        map.removeLayer(layers.borders);
        const attribution = escapeHtml(t("map.borders_attribution"));
        layers.borders.eachLayer((line) => {
          line.options.attribution = attribution;
        });
        renderBorders();
      }
    }
    renderAll();
  }

  bindPanel();
  renderAll();

  return {
    load,
    show,
    rerender,
    themeChanged,
    /** Latitude of the map centre, or null before the map exists. */
    mapLatitude: () => (map ? map.getCenter().lat : null),
    /** The map centre `{lat, lon}`, or null before the map exists. */
    mapCenter: () => (map ? { lat: map.getCenter().lat, lon: map.getCenter().lng } : null),
    /** The colours the squares given share: `{mixed, photo}` (the Plan's colour control). */
    tilesPhoto,
    /** Set the colours of the squares given; `null` gives them back to Settings. */
    setTilesPhoto,
    /** Set the colours of several squares at once, each one its own; `null` gives them back. */
    setEachTilePhoto,
    /** Whether the saved document is loaded: nothing may be set before it is. */
    zonesLoaded: () => zs.loaded,
    /** Give back to Settings the colours of what is not installed; answers how many. */
    resetColours,
    /** How many squares and zones carry their own colours, and how many can be given back. */
    ownColours,
    /** The colours a square carries of its own, or null (the Library compares them). */
    tilePhoto: (name) => zs.tiles[name]?.photo || null,
    /** What the squares of a build carry of their own, for the request (``map-zones.md`` 5). */
    tilesSettings(names) {
      const out = {};
      for (const name of names) {
        const photo = zs.tiles[name]?.photo;
        if (photo?.look) out[name] = { photo };
      }
      return out;
    },
    /**
     * The flight plan of step 1 changed: draw it again, or take it away. ``fit`` brings the map to
     * it, which is what a route drawn by hand or read from SimBrief asks for; the one restored
     * when the page opens leaves the map where the user left it.
     */
    routeChanged(fit = false) {
      drawRoute();
      // the grid paints a route's two ends differently, so it has to be drawn again: clearing a
      // route left them painted until the map next moved (found in review, 2026-09-23)
      renderGrid();
      const points = (ctx.route?.() || {}).points || [];
      if (!fit || !map || points.length < 2) return;
      // setView rather than fitBounds: the latter moved the centre and kept the zoom on this map
      // (measured 2026-09-19), while the zoom it computes is right.
      const bounds = L.latLngBounds(routePieces(points).at);
      const zoom = Math.min(9, map.getBoundsZoom(bounds, false, L.point(60, 60)));
      map.setView(bounds.getCenter(), zoom);
    },
    /** The selection changed (chips, text, airport or a click on the map). */
    tilesChanged() {
      if (ctx.tiles().length && !zs.hintDone) dismissHint();
      renderGrid();
      renderList();
      renderSizes();
      renderHint();
    },
    /** The provider list, the Plan's imagery source or its detail level changed. */
    planChanged() {
      setBaseLayer();
      renderToolOptions();
      renderList();
      renderSizes();
    },
    libraryChanged() {
      renderGrid();
    },
    /** The running build moved on: its tiles are drawn again when what it does with them changed
     * (a progress report alone changes nothing on the map). */
    jobChanged() {
      const key = JSON.stringify([...buildingNow().entries()]);
      if (key === buildingKey) return;
      buildingKey = key;
      renderGrid();
      renderLegend();
    },
    /**
     * `zones` for POST /api/plan and /api/jobs: always the list shown (map-zones.md 5). Zones that
     * could not be loaded are asked for again first; null when that fails too, and then nothing
     * may be sent: an empty list would build without the saved zones, unseen.
     */
    /** The zones a plan or a job sends: those whose bounding box reaches one of `tiles`, in list
     * order. A zone elsewhere changes nothing in that build, so a problem on it (a red zone) must
     * not refuse the build; the engine applies the same rule to the saved zones. */
    async zonesForRequest(tiles) {
      if (!zs.loaded) await load();
      if (!zs.loaded) return null;
      const wanted = new Set(tiles || []);
      return zs.zones
        .filter((z) => tilesInBounds(z.polygon).some((name) => wanted.has(name)))
        .map(zoneForApi);
    },
    /** An error of /api/plan or /api/jobs: mark the zone it names. True for a ZONE_* error. */
    noteError(err) {
      return noteError(err, "build");
    },
    /** /api/plan or /api/jobs accepted the zones: their earlier refusals are gone. */
    zonesAccepted() {
      if (!dropMarks("build")) return;
      renderList();
      renderZones();
      renderStatus();
    },
  };
}
