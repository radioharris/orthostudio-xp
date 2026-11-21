// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's map, over the shared base map (basemap.js): the aircraft and the camera as
// X-Plane sends them, the route of the SimBrief plan, the regions installed, the squares of other
// tools' meshes (a click puts one in front of Live or back), and the textures built, coloured by
// how sharp they are (orthostudio/live/mapdata.py). The status the page polls
// every second moves the aircraft; the textures are asked for again when the view moves, and
// every few seconds while Live streams. The texture under the pointer is told in a bar over the
// map's top, one line or two, which can be hidden (a tooltip was too present, a user said), and
// told again as it changes while the pointer rests on it. Its legend is laid on the map, as on
// Build's Plan map and by the same module (static/legend.js): folded away or open, with a box
// for each layer under "Show", each choice kept for the viewer (static/kept.js).
//
// Each layer is drawn opaque in a pane of its own, the pane made see-through as a whole: an
// airport's textures over the ZL16 ones do not add their colours up and look darker than they are.

import { AIRPORTS_MIN_ZOOM, BORDERS_MAX_ZOOM, createBaseMap, escapeHtml } from "./basemap.js";
import { tileName } from "../static/geo.js";
import { pageKeep, pageKept } from "../static/kept.js";
import { drawLegend, groupHead, legendList, legendRow, legendToggle } from "../static/legend.js";
import { language, t } from "./strings.js";

// by the zoom of its photos, how sharp a texture is on the ground whatever its size (an airport's
// ZL18 texture at 4096 px is sharper than a ZL16 one at 8192): hues told apart at a glance
export const PHOTO_COLOURS = {
  19: "#8e44d9", // about 20 cm a pixel
  18: "#2f6fd6", // 40 cm
  17: "#35a852", // 85 cm
  16: "#f2b134", // 1.7 m
  15: "#e8743b", // 3.4 m
  coarser: "#9aa0a6",
};
const REGION_COLOUR = "#5a8dd6";
const REGIONS_MAX_ZOOM = 6; // closer, the view is inside a region: its shade would only tint it
// a flight's start: about 60 km across round the aircraft, where it is in its region (the user,
// 2026-10-09: closer than 9; 2026-10-31: wider than 11)
const START_ZOOM = 10;
const ROUTE_COLOUR = "#e0572f"; // OrthoStudio XP's own route colour
const BUILT_EVERY_MS = 3000; // while Live streams: the engine answers "same" while nothing changed
const OWN_EVERY_MS = 30000; // the user's own tiles asked for again this often (the engine: 30 s)
const OWN_INSET = 2.5; // px: as OrthoStudio XP's Plan frames a tile, inside it, never on its edge
const OTHER_INSET = 1; // px: a square of another tool's mesh, inside it, the user's tile frame outside
// the legend folded away, and its "Show" (Build's Plan map keeps its own: osxp.mapLegend...)
const LEGEND_KEY = "osxp.live.mapLegend";
const LEGEND_SHOW_KEY = "osxp.live.mapLegendShow";
// + a layer's name (LAYERS): "0" while its box under "Show" is unticked, else shown
const SHOWN_KEY = "osxp.live.mapShown.";
const LAYERS = ["regions", "own-osxp", "own-ortho4xp", "own-other", "other-behind", "other-front", "route", "borders", "airports"];

export function photoColour(zoom) {
  return PHOTO_COLOURS[Math.min(19, zoom)] || PHOTO_COLOURS.coarser;
}

function fmt(value, digits = 0) {
  return new Intl.NumberFormat(language() === "fr" ? "fr-FR" : "en-GB", {
    maximumFractionDigits: digits,
  }).format(value);
}

function ago(seconds) {
  if (seconds < 60) return t("map.tip_now");
  if (seconds < 3600) return t("map.tip_min", { n: fmt(seconds / 60) });
  if (seconds < 86400) return t("map.tip_hours", { n: fmt(seconds / 3600) });
  return t("map.tip_days", { n: fmt(seconds / 86400) });
}

/** The ground a pixel shows: "85 cm", "1.7 m". */
function groundPerPixel(metres) {
  return metres < 1 ? t("map.tip_cm", { n: fmt(metres * 100) }) : t("map.tip_m", { n: fmt(metres, 1) });
}

/** What the bar over the map says of a texture built (orthostudio/live/mapdata.py textures_at):
 * how sharp it is on the ground first, the zoom of its photos; then the tile of the mesh it
 * fills, whose own number says only how big it is (16: 6.8 km, 18 around airports: 1.7 km); then
 * the pixels that takes. "A ZL16 at 8192 px" meant nothing to a pilot (a user, 2026-09-27). */
export function textureInfo(x) {
  const parts = [
    `<b>${escapeHtml(t("map.tip_photos", { zoom: x.photos_zoom, ground: groundPerPixel((x.km[0] * 1000) / x.px) }))}</b>`,
    escapeHtml(t(x.zoom >= 18 ? "map.tip_tile_airport" : "map.tip_tile", { zoom: x.zoom, km: fmt(x.km[0], 1) })),
    escapeHtml(t("map.tip_file", { px: fmt(x.px), mb: fmt(x.bytes / 1e6, x.bytes < 1e7 ? 1 : 0) })),
  ];
  if (x.seen_s != null) parts.push(escapeHtml(t("map.tip_seen", { ago: ago(x.seen_s) })));
  if (x.patchy) parts.push(`<span class="info-warn">${escapeHtml(t("map.tip_patchy"))}</span>`);
  return parts.join(" · ");
}

/** The aircraft, pointing along its heading, as X-Plane's. */
// the aircraft seen from above, nose up: an airliner with swept wings, its two engines and its tail
// (the user, 2026-10-31: the old one too small, and ugly)
const PLANE_PX = 48;
const PLANE_PATH = "M32 3C34.2 3 35.2 6 35.2 9L35.2 24L60 38.5L60 42L35.2 36.5L35 49L44 55.5L44 58.5"
  + "L34.2 56.5L33 61L31 61L29.8 56.5L20 58.5L20 55.5L29 49L28.8 36.5L4 42L4 38.5L28.8 24L28.8 9"
  + "C28.8 6 29.8 3 32 3Z";

function planeIcon(heading) {
  const svg = `<svg viewBox="0 0 64 64" width="${PLANE_PX}" height="${PLANE_PX}" style="transform: rotate(${Math.round(heading)}deg)">`
    + '<g fill="#ffffff" stroke="#1d1c1a" stroke-width="1.6" stroke-linejoin="round">'
    + '<ellipse cx="19" cy="31" rx="2.4" ry="4.4"/><ellipse cx="45" cy="31" rx="2.4" ry="4.4"/>'
    + `<path d="${PLANE_PATH}"/></g></svg>`;
  const half = PLANE_PX / 2;
  return L.divIcon({ className: "live-plane", html: svg, iconSize: [PLANE_PX, PLANE_PX], iconAnchor: [half, half] });
}

export function createLiveMap(el, { api, info = null, legend = null, onOther = null }) {
  const base = createBaseMap(el);
  const { map } = base;
  // each layer under the legend's "Show", shown unless its box was unticked
  const shown = Object.fromEntries(LAYERS.map((name) => [name, pageKept(SHOWN_KEY + name) !== "0"]));
  let legendOpen = pageKept(LEGEND_KEY) !== "0";
  let legendShowOpen = pageKept(LEGEND_SHOW_KEY) !== "0";
  base.bordersShown(shown.borders);
  base.airportsShown(shown.airports);
  function pane(name, zIndex, opacity) {
    const p = map.createPane(name);
    p.style.zIndex = String(zIndex);
    p.style.opacity = String(opacity);
    p.style.pointerEvents = "none";
    return L.canvas({ padding: 0.2, pane: name });
  }
  // under the borders (340): the regions, the grouped small textures, the textures one by one
  const regionsCanvas = pane("liveRegions", 300, 0.14);
  const squaresCanvas = pane("liveSquares", 310, 0.3);
  const texturesCanvas = pane("liveTextures", 320, 0.5);
  // over the borders (340), under the airports (365): the user's own tiles, framed
  const ownPane = map.createPane("liveOwn");
  ownPane.style.zIndex = "350";
  ownPane.style.pointerEvents = "none";
  // over the borders, under the user's own tiles: the squares of another tool's mesh
  const otherPane = map.createPane("liveOther");
  otherPane.style.zIndex = "345";
  otherPane.style.pointerEvents = "none";
  const regions = L.layerGroup().addTo(map);
  const built = L.layerGroup().addTo(map);
  const own = L.layerGroup().addTo(map);
  const other = L.layerGroup().addTo(map);
  let otherSquares = []; // [{lat, lon, front}]: GET map/other
  let otherKey = "";
  let ownTiles = []; // [{lat, lon, pack, kind}]: GET map/own
  let ownKey = "";
  let ownAt = 0;
  const route = L.layerGroup();
  if (shown.route) route.addTo(map);
  let plane = null;
  let camera = null;
  let cameraLine = null; // from the aircraft to the camera, with the distance X-Plane counts
  let routeKey = "";
  let askedRoute = null; // the route the "Show my SimBrief route" button drew
  let follow = true;
  let providerCode = "";
  let streaming = false;
  let builtTimer = 0;

  let builtVersion = -1; // what the map shows, as the engine counts its changes
  let shownTextures = []; // [s, w, n, e, level] drawn: what the pointer is over, without asking
  let shownSquares = []; // [lat, lon, count, sharpest] drawn when zoomed out

  /** The textures built in the view: all again when the view moved (`fresh`), else only if the
   * engine has built or made coarser any since. */
  async function drawBuilt(fresh = true) {
    clearTimeout(builtTimer);
    if (!el.offsetParent) { // its screen is hidden: nothing asked until it is shown (shown())
      if (streaming) builtTimer = setTimeout(() => drawBuilt(false), BUILT_EVERY_MS);
      return;
    }
    const b = map.getBounds();
    let q = `west=${b.getWest()}&south=${b.getSouth()}&east=${b.getEast()}&north=${b.getNorth()}`;
    if (!fresh) q += `&known=${builtVersion}`;
    let got;
    try {
      got = await api(`map/built?${q}`);
    } catch (_e) {
      got = null;
    }
    if (got && !got.same) {
      builtVersion = got.version ?? -1;
      shownTextures = got.textures || [];
      shownSquares = got.squares || [];
      if (hovered && pointer) { // what the pointer rests on may have changed: told again
        hovered = null;
        pointAt(pointer);
      }
      built.clearLayers();
      const style = (photos, renderer) => ({
        renderer, weight: 0, fillColor: photoColour(photos), fillOpacity: 1, interactive: false,
      });
      // opaque in their panes, which are see-through as a whole: overlaps never darken
      for (const [lat, lon, , best] of got.squares || []) {
        L.rectangle([[lat, lon], [lat + 1, lon + 1]], style(best, squaresCanvas)).addTo(built);
      }
      for (const [s, w, n, e, photos] of (got.textures || []).slice().reverse()) {
        // the sharpest drawn last, on top
        L.rectangle([[s, w], [n, e]], style(photos, texturesCanvas)).addTo(built);
      }
    }
    if (streaming) builtTimer = setTimeout(() => drawBuilt(false), BUILT_EVERY_MS);
  }

  async function drawRegions() {
    let got;
    try {
      got = await api("map/regions");
    } catch (_e) {
      return;
    }
    regions.clearLayers();
    for (const squares of Object.values(got)) {
      for (const [lat, lon] of squares) {
        L.rectangle([[lat, lon], [lat + 1, lon + 1]], {
          renderer: regionsCanvas, weight: 0, fillColor: REGION_COLOUR, fillOpacity: 1, interactive: false,
        }).addTo(regions);
      }
    }
  }

  function drawRoute(points) {
    const key = points ? `${points.length}:${points[0]}:${points[points.length - 1]}` : "";
    if (key === routeKey) return;
    routeKey = key;
    route.clearLayers();
    if (!points || points.length < 2) return;
    L.polyline(points, { color: ROUTE_COLOUR, weight: 3, opacity: 0.9, interactive: false }).addTo(route);
    for (const end of [points[0], points[points.length - 1]]) {
      L.circleMarker(end, { radius: 5, weight: 2, color: ROUTE_COLOUR, fillColor: "#fff", fillOpacity: 1, interactive: false }).addTo(route);
    }
  }

  /**
   * The user's own tiles, framed as OrthoStudio XP's Plan frames the tiles in X-Plane (a user
   * asked for the same, 2026-09-27): a line inside each square, green for a tile OrthoStudio XP
   * built, violet for one imported from Ortho4XP, grey for another scenery's ground, over a dark
   * one that keeps it seen on any photo. X-Plane shows the tile there, never Live's textures,
   * which the engine leaves out of the map (mapdata.built_in).
   */
  function insetBox(box, px) {
    const nw = map.latLngToLayerPoint([box[1][0], box[0][1]]);
    const se = map.latLngToLayerPoint([box[0][0], box[1][1]]);
    if (se.x - nw.x < 4 * px || se.y - nw.y < 4 * px) return null;
    const inNw = map.layerPointToLatLng(L.point(nw.x + px, nw.y + px));
    const inSe = map.layerPointToLatLng(L.point(se.x - px, se.y - px));
    return [[inSe.lat, inNw.lng], [inNw.lat, inSe.lng]];
  }
  /** The user's own tiles whose line under "Show" is ticked. */
  const ownShown = () => ownTiles.filter((tile) => shown[`own-${tile.kind}`]);
  function drawOwn() {
    own.clearLayers();
    for (const tile of ownShown()) {
      const box = [[tile.lat, tile.lon], [tile.lat + 1, tile.lon + 1]];
      const frame = insetBox(box, OWN_INSET) || box; // far out, too small: on its own edge
      own.addLayer(L.rectangle(frame, {
        pane: "liveOwn", className: "live-own-casing", interactive: false, fill: false, weight: 5,
      }));
      own.addLayer(L.rectangle(frame, {
        pane: "liveOwn", className: `live-own is-${tile.kind}`, interactive: false, fill: false, weight: 3,
      }));
    }
  }
  async function readOwn() {
    ownAt = Date.now();
    let got;
    try {
      got = await api("map/own");
    } catch (_e) {
      return;
    }
    const key = JSON.stringify(got);
    if (key === ownKey) return;
    ownKey = key;
    ownTiles = Array.isArray(got) ? got : [];
    drawOwn();
    renderLegend(); // a line for each kind of tile the map frames
    drawBuilt(); // Live's textures under a tile new or gone: left out, or back
  }
  map.on("zoomend", drawOwn); // the frame's inset is in pixels

  /**
   * The squares of other tools' meshes (orthostudio/live/control.py other_squares), each with the
   * folder that gives it: dashed where it comes behind Live, framed and shaded where the pilot put
   * it in front of Live, its mesh there with Live's photos. A click on one puts it in front or
   * back (onOther); those whose line under "Show" is unticked are neither drawn nor clicked.
   */
  const otherShown = () => otherSquares.filter((x) => shown[x.front ? "other-front" : "other-behind"]);
  function drawOther() {
    other.clearLayers();
    for (const square of otherShown()) {
      const box = [[square.lat, square.lon], [square.lat + 1, square.lon + 1]];
      other.addLayer(L.rectangle(insetBox(box, OTHER_INSET) || box, {
        pane: "liveOther", className: square.front ? "live-other is-front" : "live-other",
        interactive: false, weight: square.front ? 3 : 1.5,
      }));
    }
  }
  async function readOther() {
    let got;
    try {
      got = await api("map/other");
    } catch (_e) {
      return;
    }
    const key = JSON.stringify(got);
    if (key === otherKey) return;
    otherKey = key;
    otherSquares = Array.isArray(got) ? got : [];
    drawOther();
    if (hovered && pointer) { // the square under the pointer put in front or back: told again
      hovered = null;
      pointAt(pointer);
    }
    renderLegend(); // a line for the squares behind Live, one for those in front
  }
  map.on("zoomend", drawOther);
  function otherAt(latlng) {
    const { lat, lng } = latlng;
    return otherShown().find((x) => lat >= x.lat && lat < x.lat + 1 && lng >= x.lon && lng < x.lon + 1);
  }
  // a click elsewhere does what it did before: nothing
  map.on("click", (ev) => {
    const square = otherAt(ev.latlng);
    if (square && onOther) onOther(square);
  });

  // the regions' shade only while zoomed out: closer, the view is inside one
  function showRegions() {
    const wanted = shown.regions && map.getZoom() <= REGIONS_MAX_ZOOM;
    if (wanted && !map.hasLayer(regions)) regions.addTo(map);
    if (!wanted && map.hasLayer(regions)) map.removeLayer(regions);
  }
  map.on("zoomend", showRegions);

  /** A layer shown or hidden by its box under "Show", kept for this viewer. */
  function setShown(name, on) {
    shown[name] = on;
    pageKeep(SHOWN_KEY + name, on ? "1" : "0");
    if (name === "regions") showRegions();
    else if (name === "route") {
      if (on) route.addTo(map);
      else map.removeLayer(route);
    } else if (name === "borders") base.bordersShown(on);
    else if (name === "airports") base.airportsShown(on);
    else if (name.startsWith("own-")) {
      drawOwn();
      drawBuilt(); // Live's textures under a tile: left out of the map while it is framed
    } else drawOther();
    hovered = null; // what the pointer rests on may be hidden or shown now
    if (pointer) pointAt(pointer);
    renderLegend();
  }

  /**
   * The legend, laid on the map as on Build's Plan map (static/legend.js): how sharp the textures
   * are on the ground, then under "Show" a box for each layer Live draws, its mark beside it, the
   * kinds of the user's tiles and of other tools' squares only when the map has them. A box
   * ticked for a layer not drawn at this zoom (the regions and the borders up close, the airports
   * far out) is dimmed, why said on hover. Folded away, and its "Show", kept for this viewer.
   */
  function renderLegend() {
    if (!legend) return;
    drawLegend(legend, {
      open: legendOpen,
      setOpen: (open) => {
        legendOpen = open;
        pageKeep(LEGEND_KEY, open ? "1" : "0");
        renderLegend();
      },
      words: { show: t("map.legend_show"), hide: t("map.legend_hide") },
      head: () => {
        const title = document.createElement("p");
        title.className = "legend-view";
        title.textContent = t("map.legend_title");
        return title;
      },
      body: () => {
        const sharpness = legendList([
          legendRow("legend-zl19", t("map.legend_zl19")),
          legendRow("legend-zl18", t("map.legend_zl18")),
          legendRow("legend-zl17", t("map.legend_zl17")),
          legendRow("legend-zl16", t("map.legend_zl16")),
          legendRow("legend-zl15", t("map.legend_zl15")),
          legendRow("legend-less", t("map.legend_less")),
        ], true);
        const words = { hide: t("map.legend_show_group_hide"), show: t("map.legend_show_group_show") };
        const head = groupHead(t("map.legend_show_group"), legendShowOpen, "show-fold", words, (open) => {
          legendShowOpen = open;
          pageKeep(LEGEND_SHOW_KEY, open ? "1" : "0");
          renderLegend();
        });
        return legendShowOpen ? [sharpness, head, legendList(layerToggles())] : [sharpness, head];
      },
    });
  }

  /** The boxes under "Show", one a layer the map draws now. */
  function layerToggles() {
    const zoom = map.getZoom();
    const owned = new Set(ownTiles.map((x) => x.kind));
    const others = new Set(otherSquares.map((x) => (x.front ? "other-front" : "other-behind")));
    const toggle = (name, swatch, text, idle = null, hint = null) => legendToggle({
      swatch, text, checked: shown[name], keep: name, onChange: (on) => setShown(name, on),
      idle: Boolean(idle && shown[name]), hint: idle && shown[name] ? [idle, hint].filter(Boolean).join(" ") : hint,
    });
    const lines = [toggle("regions", "legend-region", t("map.legend_region"), zoom > REGIONS_MAX_ZOOM ? t("map.zoom_out") : null)];
    if (owned.has("osxp")) lines.push(toggle("own-osxp", "legend-installed", t("map.legend_own_osxp")));
    if (owned.has("ortho4xp")) lines.push(toggle("own-ortho4xp", "legend-ortho4xp", t("map.legend_own_ortho4xp")));
    if (owned.has("other")) lines.push(toggle("own-other", "legend-own-other", t("map.legend_own_other")));
    if (others.has("other-behind")) lines.push(toggle("other-behind", "legend-other-mesh", t("map.legend_other_behind")));
    if (others.has("other-front")) lines.push(toggle("other-front", "legend-other-mesh is-front", t("map.legend_other_front")));
    lines.push(toggle("route", "legend-route", t("map.legend_route")));
    lines.push(toggle("borders", "legend-border", t("map.borders"), zoom > BORDERS_MAX_ZOOM ? t("map.zoom_out") : null, t("map.borders_hint")));
    lines.push(toggle("airports", "legend-airport", t("map.airports"), zoom < AIRPORTS_MIN_ZOOM ? t("map.zoom_in") : null, t("map.airports_hint")));
    return lines;
  }
  map.on("zoomend", renderLegend); // a box dimmed, or no longer, at this zoom

  // the bar over the map: the texture under the pointer, found in what is drawn, its facts asked
  // of the engine once the pointer rests on another one, and again when it is built anew
  let hovered = null; // the texture or square under the pointer, as a key
  let pointer = null;
  let asking = 0;
  function under(latlng) {
    const { lat, lng } = latlng;
    const mine = ownShown().find((x) => lat >= x.lat && lat < x.lat + 1 && lng >= x.lon && lng < x.lon + 1);
    if (mine) return { key: `o:${mine.lat},${mine.lon}`, own: mine }; // X-Plane shows it there
    let best = null;
    for (const r of shownTextures) {
      if (lat >= r[0] && lat < r[2] && lng >= r[1] && lng < r[3]) {
        const area = (r[2] - r[0]) * (r[3] - r[1]);
        if (!best || area < best.area) best = { key: `t:${r[0]},${r[1]},${r[4]}`, area };
      }
    }
    if (best) return best;
    const mesh = otherAt(latlng);
    if (mesh) return { key: `m:${mesh.lat},${mesh.lon},${mesh.front},${mesh.folder}`, mesh };
    const square = shownSquares.find((q) => lat >= q[0] && lat < q[0] + 1 && lng >= q[1] && lng < q[1] + 1);
    return square ? { key: `s:${square[0]},${square[1]},${square[2]},${square[3]}`, square } : null;
  }
  function say(html) {
    if (!info) return;
    info.innerHTML = html || escapeHtml(t("map.info_hint"));
    info.classList.toggle("is-hint", !html);
  }
  function nothing() {
    hovered = null;
    clearTimeout(asking);
    say("");
  }
  map.on("mousemove", (ev) => pointAt(ev.latlng));
  function pointAt(latlng) {
    pointer = latlng;
    el.classList.toggle("is-over-other", Boolean(onOther && otherAt(latlng))); // a click there
    const found = under(latlng);
    if (!found) return nothing();
    if (found.key === hovered) return;
    hovered = found.key;
    clearTimeout(asking);
    if (found.own) {
      say(escapeHtml(t(`map.own_${found.own.kind}`, { pack: found.own.pack })));
      return;
    }
    if (found.mesh) { // the folder that gives the square, in front of Live or behind
      const { lat, lon, front, folder } = found.mesh;
      say(escapeHtml(t(front ? "map.other_front" : "map.other_behind", { tile: tileName(lat, lon), folder })));
      return;
    }
    if (found.square) {
      const [, , count, best] = found.square;
      say(escapeHtml(t("map.tip_square", { n: fmt(count), zoom: best })));
      return;
    }
    const key = found.key;
    asking = setTimeout(async () => {
      let got;
      try {
        got = await api(`map/texture?lat=${pointer.lat}&lon=${pointer.lng}`);
      } catch (_e) {
        return;
      }
      if (hovered !== key || !got.textures?.length) return;
      say(textureInfo(got.textures[0])); // the one drawn on top: the smallest, an airport's
    }, 120);
  }
  map.on("mouseout", () => {
    pointer = null;
    el.classList.remove("is-over-other");
    nothing();
  });
  say("");

  // the map takes the height of the blocks beside it: Leaflet is told when its box changes
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => map.invalidateSize()).observe(el);
  map.on("moveend", () => drawBuilt());
  map.on("dragstart", () => {
    if (!plane) return; // no aircraft: the next one still followed (the user, 2026-10-09)
    follow = false; // the pilot looks elsewhere: the map stays there until asked back
    el.dispatchEvent(new CustomEvent("live-map-follow", { detail: false }));
  });

  drawRegions();
  showRegions();
  drawBuilt();
  readOwn();
  readOther();
  renderLegend();

  return {
    map,
    /** The page's status, every second: the aircraft, the camera, the route, the source. */
    update(status) {
      const providers = status.providers || [];
      const code = status.settings?.provider || "BI";
      if (code !== providerCode) {
        const p = providers.find((x) => x.code === code);
        if (p) {
          providerCode = code;
          base.setProvider(p);
        }
      }
      if (Date.now() - ownAt > OWN_EVERY_MS) { // a tile built or removed, a folder plugged in
        readOwn();
        readOther();
      }
      const stream = status.state === "streaming" ? status.stream : null;
      const wasStreaming = streaming;
      streaming = Boolean(stream);
      if (streaming && !wasStreaming) drawBuilt();
      const a = stream?.data === "fresh" ? stream.aircraft : null;
      if (a) {
        const at = [a.lat, a.lon];
        if (!plane) {
          plane = L.marker(at, { icon: planeIcon(a.hdg ?? a.trk ?? 0), interactive: false, zIndexOffset: 1000 }).addTo(map);
          // a flight starts: the map closer on the aircraft, unless already closer (the user,
          // 2026-10-09)
          if (follow && map.getZoom() < START_ZOOM) map.setView(at, START_ZOOM, { animate: false });
        } else {
          plane.setLatLng(at);
          plane.setIcon(planeIcon(a.hdg ?? a.trk ?? 0));
        }
        if (follow) map.panTo(at, { animate: false });
      } else if (plane) {
        map.removeLayer(plane);
        plane = null;
        // the flight over, or X-Plane gone: the next one followed, whatever a drag did during this
        // one (the user, 2026-10-09: always on by default)
        if (!follow) {
          follow = true;
          el.dispatchEvent(new CustomEvent("live-map-follow", { detail: true }));
        }
      }
      const cam = stream?.camera_km > 0.5 ? stream.camera : null;
      if (cam) {
        if (!camera) camera = L.circleMarker([cam.lat, cam.lon], { radius: 5, weight: 2, color: "#ffffff", fillColor: "#1f5fa8", fillOpacity: 1, interactive: false }).addTo(map);
        else camera.setLatLng([cam.lat, cam.lon]);
      } else if (camera) {
        map.removeLayer(camera);
        camera = null;
      }
      // the ground under the camera from the aircraft, in three dimensions as X-Plane counts it to
      // choose the levels it takes there (the aircraft's height in it): the camera's own counts
      // for nothing (the user, 2026-10-20)
      if (cam && a && cam.from_aircraft_km != null) {
        const ends = [[a.lat, a.lon], [cam.lat, cam.lon]];
        const km = new Intl.NumberFormat(language(), { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(cam.from_aircraft_km);
        if (!cameraLine) {
          cameraLine = L.polyline(ends, { color: "#ffffff", weight: 2, dashArray: "6 6", opacity: 0.9, interactive: false }).addTo(map);
          cameraLine.bindTooltip("", { permanent: true, direction: "center", className: "camera-km" });
        } else cameraLine.setLatLngs(ends);
        cameraLine.setTooltipContent(`${km} km`);
      } else if (cameraLine) {
        map.removeLayer(cameraLine);
        cameraLine = null;
      }
      // the SimBrief route while the aircraft flies it; else the one the button asked for, if any
      const plan = stream?.streamer?.flight_plan;
      drawRoute(plan?.following && plan.points ? plan.points : askedRoute);
    },
    /** The route asked for with the map's button, before a flight. */
    showRoute(points) {
      askedRoute = points || null;
      drawRoute(points);
      if (points?.length) map.fitBounds(L.latLngBounds(points), { padding: [24, 24] });
    },
    regionsChanged: drawRegions,
    /** A square of other tools' meshes put in front or back, their folders changed: asked anew. */
    otherChanged: readOther,
    /**
     * A departure prepared: the map on it, as close as at a flight's start unless closer already;
     * an aircraft followed no longer, as after a drag (the user, 2026-10-31).
     */
    showDeparture(at) {
      if (!at) return;
      if (plane && follow) {
        follow = false;
        el.dispatchEvent(new CustomEvent("live-map-follow", { detail: false }));
      }
      map.setView(at, Math.max(map.getZoom(), START_ZOOM), { animate: false });
    },
    /** The bar over the map, shown or hidden (the header's box). */
    setDetails(on) {
      if (info) info.hidden = !on;
    },
    languageChanged() {
      hovered = null;
      say("");
      renderLegend();
    },
    setFollow(on) {
      follow = on;
      if (on && plane) map.panTo(plane.getLatLng(), { animate: false });
    },
    invalidate() {
      map.invalidateSize();
    },
    /** Live's textures deleted: asked anew at once (a hidden map asks when shown). */
    builtChanged() {
      drawBuilt();
    },
    /** Its screen shown again: Leaflet measures its box, and the textures are asked anew. */
    shown() {
      map.invalidateSize();
      drawBuilt();
      readOwn();
      readOther();
    },
  };
}
