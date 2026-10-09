// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The base map, meant for every map of OrthoStudio XP's pages: Leaflet (vendored, loaded as a
// classic script before), the engine's aerial base map (GET api/map/{provider}/{z}/{x}/{y}, with
// its cache), the country borders (static/vendor/borders/borders.json, Natural Earth) and the
// airports of the view (GET api/airports/in), drawn as the Plan's map draws them. The Live
// window's map uses it now. OrthoStudio XP's Plan map, which draws the same three itself, takes it
// once Live joins OrthoStudio XP. Until then nothing of OrthoStudio XP changes: this reads its
// files as they are.
//
// Nothing here knows about Live: what a page draws over it is its own.

import { decodeBorders } from "../static/geo.js";

export const BORDERS_URL = "static/vendor/borders/borders.json";
export const BORDERS_MAX_ZOOM = 10; // beyond, they would visibly miss the borders on the imagery
export const AIRPORTS_MIN_ZOOM = 8; // below, too many rings to read
export const AIRPORTS_LABEL_ZOOM = 9;
export const AIRPORTS_LIMIT = 200;
const AIRPORT_COLOUR = "#ffd166";

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** A provider's base layer: its highest native zoom, then stretched; its credit. */
export function providerTileUrl(p) {
  const version = p.cache && p.cache !== p.code ? `?v=${encodeURIComponent(p.cache)}` : "";
  return `api/map/${encodeURIComponent(p.code)}/{z}/{x}/{y}${version}`;
}

/**
 * The base map in `el`. Returns {map, setProvider(p), airportsShown(on), bordersShown(on)}:
 * `p` as GET /api/providers gives it ({code, name, attribution, max_zl, cache}).
 */
export function createBaseMap(el, { center = [46, 5], zoom = 5 } = {}) {
  const map = L.map(el, { worldCopyJump: false, minZoom: 2, maxZoom: 19, zoomSnap: 0.5 });
  map.setView(center, zoom);
  map.attributionControl.setPrefix(false);
  const bordersPane = map.createPane("baseBorders");
  bordersPane.style.zIndex = "340";
  bordersPane.style.pointerEvents = "none";
  const airportsPane = map.createPane("baseAirports");
  airportsPane.style.zIndex = "365";
  airportsPane.style.pointerEvents = "none";

  let base = null;
  function setProvider(p) {
    if (!p) return;
    if (base) map.removeLayer(base);
    base = L.tileLayer(providerTileUrl(p), {
      // its credit, and its licence where it names one: Live's window says them nowhere else
      attribution: escapeHtml([p.attribution || p.name || p.code, p.licence].filter(Boolean).join(" · ")),
      maxZoom: 19,
      maxNativeZoom: Math.min(Number(p.max_zl) || 19, 19),
      noWrap: true,
      bounds: [[-85.0511, -180], [85.0511, 180]],
    }).addTo(map);
  }

  // country borders: read once, shown while zoomed out
  const borders = L.layerGroup();
  let bordersWanted = true;
  let bordersRead = false;
  async function readBorders() {
    if (bordersRead) return;
    bordersRead = true;
    try {
      const res = await fetch(BORDERS_URL);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      for (const [name, lines] of Object.entries(decodeBorders(await res.json()))) {
        if (!lines.length) continue;
        borders.addLayer(L.polyline(lines, {
          pane: "baseBorders", className: `base-border is-${name}`, interactive: false,
        }));
      }
    } catch (_e) {
      bordersRead = false; // tried again at the next move
    }
    drawBorders();
  }
  function drawBorders() {
    const show = bordersWanted && map.getZoom() <= BORDERS_MAX_ZOOM;
    if (show && !bordersRead) readBorders();
    if (show && !map.hasLayer(borders)) borders.addTo(map);
    if (!show && map.hasLayer(borders)) map.removeLayer(borders);
  }

  // the airports of the view, from the engine's index
  const airports = L.layerGroup();
  let airportsWanted = true;
  let asked = "";
  async function drawAirports() {
    const zoom = map.getZoom();
    if (!airportsWanted || zoom < AIRPORTS_MIN_ZOOM) {
      airports.clearLayers();
      asked = "";
      if (map.hasLayer(airports)) map.removeLayer(airports);
      return;
    }
    const b = map.getBounds();
    const key = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth(), zoom >= AIRPORTS_LABEL_ZOOM]
      .map((v) => (typeof v === "number" ? v.toFixed(3) : v)).join(",");
    if (key === asked) return;
    asked = key;
    let rows = [];
    try {
      const q = `west=${b.getWest()}&south=${b.getSouth()}&east=${b.getEast()}&north=${b.getNorth()}`;
      const res = await fetch(`api/airports/in?${q}&limit=${AIRPORTS_LIMIT}`);
      if (res.ok) rows = await res.json();
    } catch (_e) {
      rows = [];
    }
    if (key !== asked) return; // the view moved on meanwhile
    airports.clearLayers();
    const labelled = zoom >= AIRPORTS_LABEL_ZOOM;
    for (const a of rows) {
      const at = [Number(a.lat), Number(a.lon)];
      if (!Number.isFinite(at[0]) || !Number.isFinite(at[1])) continue;
      const code = String(a.icao || "");
      L.circleMarker(at, {
        pane: "baseAirports", radius: 4, weight: 2, color: AIRPORT_COLOUR, fillColor: AIRPORT_COLOUR,
        fillOpacity: 0.35, interactive: false,
      }).addTo(airports);
      if (labelled) {
        L.marker(at, {
          pane: "baseAirports",
          interactive: false,
          icon: L.divIcon({ className: "base-airport-label", html: escapeHtml(code), iconSize: [0, 0], iconAnchor: [-11, 5] }),
        }).addTo(airports);
      }
    }
    if (!map.hasLayer(airports)) airports.addTo(map);
  }

  map.on("zoomend moveend", () => {
    drawBorders();
    drawAirports();
  });
  drawBorders();
  drawAirports();

  return {
    map,
    setProvider,
    bordersShown(on) {
      bordersWanted = on;
      drawBorders();
    },
    airportsShown(on) {
      airportsWanted = on;
      asked = "";
      drawAirports();
    },
  };
}
