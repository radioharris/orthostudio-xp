// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's map, over the shared base map (basemap.js): the aircraft and the camera as
// X-Plane sends them, the route of the SimBrief plan, the regions installed, and the textures
// built, coloured by how sharp they are (orthostudio/live/mapdata.py). The status the page polls
// every second moves the aircraft; the textures are asked for again when the view moves, and
// every few seconds while Live streams. The texture under the pointer is told in a line below
// the map, which covers nothing (a tooltip over the map was too present, a user said).
//
// Each layer is drawn opaque in a pane of its own, the pane made see-through as a whole: an
// airport's textures over the ZL16 ones do not add their colours up and look darker than they are.

import { createBaseMap, escapeHtml } from "./basemap.js";
import { language, t } from "./strings.js";

// four hues told apart at a glance: blue the sharpest, then green, amber, grey
export const LEVEL_COLOURS = { "-1": "#2f6fd6", 0: "#35a852", 1: "#f2b134", coarser: "#9aa0a6" };
const REGION_COLOUR = "#5a8dd6";
const REGIONS_MAX_ZOOM = 6; // closer, the view is inside a region: its shade would only tint it
const ROUTE_COLOUR = "#e0572f"; // OrthoStudio XP's own route colour
const BUILT_EVERY_MS = 3000; // while Live streams: the engine answers "same" while nothing changed

export function levelColour(level) {
  return LEVEL_COLOURS[String(level)] || LEVEL_COLOURS.coarser;
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

/** What the line below the map says of a texture built (orthostudio/live/mapdata.py
 * textures_at). */
export function textureInfo(x) {
  const parts = [
    `<b>${escapeHtml(x.name)}</b>`,
    escapeHtml(t(x.zoom >= 18 ? "map.tip_airport" : "map.tip_zoom", { zoom: x.zoom })),
    escapeHtml(t("map.tip_px", { px: fmt(x.px), photos: x.photos_zoom })),
    escapeHtml(t("map.tip_size", {
      mb: fmt(x.bytes / 1e6, x.bytes < 1e7 ? 1 : 0), w: fmt(x.km[0], 1), h: fmt(x.km[1], 1),
    })),
  ];
  if (x.seen_s != null) parts.push(escapeHtml(t("map.tip_seen", { ago: ago(x.seen_s) })));
  if (x.patchy) parts.push(`<span class="info-warn">${escapeHtml(t("map.tip_patchy"))}</span>`);
  return parts.join(" · ");
}

/** The aircraft, pointing along its track. */
function planeIcon(track) {
  const svg = `<svg viewBox="0 0 24 24" width="26" height="26" style="transform: rotate(${Math.round(track)}deg)">`
    + '<path d="M12 2l1.4 7 7.6 4.2v1.9l-7.6-2.3-.5 5.6 2.5 1.9V22l-3.4-.9-3.4.9v-1.7l2.5-1.9-.5-5.6-7.6 2.3v-1.9L10.6 9z" '
    + 'fill="#ffffff" stroke="#1d1c1a" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  return L.divIcon({ className: "live-plane", html: svg, iconSize: [26, 26], iconAnchor: [13, 13] });
}

export function createLiveMap(el, { api, info = null }) {
  const base = createBaseMap(el);
  const { map } = base;
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
  const regions = L.layerGroup().addTo(map);
  const built = L.layerGroup().addTo(map);
  const route = L.layerGroup().addTo(map);
  let plane = null;
  let camera = null;
  let routeKey = "";
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
      hovered = null; // its facts may have changed
      built.clearLayers();
      const style = (level, renderer) => ({
        renderer, weight: 0, fillColor: levelColour(level), fillOpacity: 1, interactive: false,
      });
      // opaque in their panes, which are see-through as a whole: overlaps never darken
      for (const [lat, lon, , best] of got.squares || []) {
        L.rectangle([[lat, lon], [lat + 1, lon + 1]], style(best, squaresCanvas)).addTo(built);
      }
      for (const [s, w, n, e, level] of (got.textures || []).slice().reverse()) {
        // the sharpest drawn last, on top
        L.rectangle([[s, w], [n, e]], style(level, texturesCanvas)).addTo(built);
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

  // the regions' shade only while zoomed out: closer, the view is inside one
  function showRegions() {
    const wanted = map.getZoom() <= REGIONS_MAX_ZOOM;
    if (wanted && !map.hasLayer(regions)) regions.addTo(map);
    if (!wanted && map.hasLayer(regions)) map.removeLayer(regions);
  }
  map.on("zoomend", showRegions);

  // the line below the map: the texture under the pointer, found in what is drawn, its facts
  // asked of the engine once the pointer rests on another one
  let hovered = null; // the texture or square under the pointer, as a key
  let pointer = null;
  let asking = 0;
  function under(latlng) {
    const { lat, lng } = latlng;
    let best = null;
    for (const r of shownTextures) {
      if (lat >= r[0] && lat < r[2] && lng >= r[1] && lng < r[3]) {
        const area = (r[2] - r[0]) * (r[3] - r[1]);
        if (!best || area < best.area) best = { key: `t:${r[0]},${r[1]},${r[4]}`, area };
      }
    }
    if (best) return best;
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
  map.on("mousemove", (ev) => {
    pointer = ev.latlng;
    const found = under(ev.latlng);
    if (!found) return nothing();
    if (found.key === hovered) return;
    hovered = found.key;
    clearTimeout(asking);
    if (found.square) {
      const [, , count, best] = found.square;
      const px = best < 0 ? 8192 : 4096 >> best;
      say(escapeHtml(t("map.tip_square", { n: fmt(count), px: fmt(px) })));
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
  });
  map.on("mouseout", nothing);
  say("");

  // the map takes the height of the blocks beside it: Leaflet is told when its box changes
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => map.invalidateSize()).observe(el);
  map.on("moveend", () => drawBuilt());
  map.on("dragstart", () => {
    follow = false; // the pilot looks elsewhere: the map stays there until asked back
    el.dispatchEvent(new CustomEvent("live-map-follow", { detail: false }));
  });

  drawRegions();
  showRegions();
  drawBuilt();

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
      const stream = status.state === "streaming" ? status.stream : null;
      const wasStreaming = streaming;
      streaming = Boolean(stream);
      if (streaming && !wasStreaming) drawBuilt();
      const a = stream?.data === "fresh" ? stream.aircraft : null;
      if (a) {
        const at = [a.lat, a.lon];
        if (!plane) plane = L.marker(at, { icon: planeIcon(a.trk || 0), interactive: false, zIndexOffset: 1000 }).addTo(map);
        else {
          plane.setLatLng(at);
          plane.setIcon(planeIcon(a.trk || 0));
        }
        if (follow) map.panTo(at, { animate: false });
      } else if (plane) {
        map.removeLayer(plane);
        plane = null;
      }
      const cam = stream?.camera_km > 0.5 ? stream.camera : null;
      if (cam) {
        if (!camera) camera = L.circleMarker([cam.lat, cam.lon], { radius: 5, weight: 2, color: "#ffffff", fillColor: "#1f5fa8", fillOpacity: 1, interactive: false }).addTo(map);
        else camera.setLatLng([cam.lat, cam.lon]);
      } else if (camera) {
        map.removeLayer(camera);
        camera = null;
      }
      const plan = stream?.streamer?.flight_plan;
      if (plan?.points) drawRoute(plan.points);
    },
    /** The route asked for with the map's button, before a flight. */
    showRoute(points) {
      drawRoute(points);
      if (points?.length) map.fitBounds(L.latLngBounds(points), { padding: [24, 24] });
    },
    regionsChanged: drawRegions,
    languageChanged() {
      hovered = null;
      say("");
    },
    setFollow(on) {
      follow = on;
      if (on && plane) map.panTo(plane.getLatLng(), { animate: false });
    },
    airportsShown: base.airportsShown,
    bordersShown: base.bordersShown,
    invalidate() {
      map.invalidateSize();
    },
  };
}
