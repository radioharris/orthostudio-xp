// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's map, over the shared base map (basemap.js): the aircraft and the camera as
// X-Plane sends them, the route of the SimBrief plan, the regions installed, and the textures
// built, coloured by how sharp they are (orthostudio/live/mapdata.py). The status the page polls
// every second moves the aircraft; the textures are asked for again when the view moves, and
// every few seconds while Live streams.

import { createBaseMap } from "./basemap.js";

export const LEVEL_COLOURS = { "-1": "#2e9e4f", 0: "#8cc63f", 1: "#f2b134", coarser: "#9aa0a6" };
const REGION_COLOUR = "#5a8dd6";
const ROUTE_COLOUR = "#e0572f"; // OrthoStudio XP's own route colour
const BUILT_EVERY_MS = 10_000;

export function levelColour(level) {
  return LEVEL_COLOURS[String(level)] || LEVEL_COLOURS.coarser;
}

/** The aircraft, pointing along its track. */
function planeIcon(track) {
  const svg = `<svg viewBox="0 0 24 24" width="26" height="26" style="transform: rotate(${Math.round(track)}deg)">`
    + '<path d="M12 2l1.4 7 7.6 4.2v1.9l-7.6-2.3-.5 5.6 2.5 1.9V22l-3.4-.9-3.4.9v-1.7l2.5-1.9-.5-5.6-7.6 2.3v-1.9L10.6 9z" '
    + 'fill="#ffffff" stroke="#1d1c1a" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  return L.divIcon({ className: "live-plane", html: svg, iconSize: [26, 26], iconAnchor: [13, 13] });
}

export function createLiveMap(el, { api }) {
  const base = createBaseMap(el);
  const { map } = base;
  const canvas = L.canvas({ padding: 0.2 });
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

  async function drawBuilt() {
    clearTimeout(builtTimer);
    const b = map.getBounds();
    const q = `west=${b.getWest()}&south=${b.getSouth()}&east=${b.getEast()}&north=${b.getNorth()}`;
    let got;
    try {
      got = await api(`map/built?${q}`);
    } catch (_e) {
      got = null;
    }
    if (got) {
      built.clearLayers();
      const style = (level) => ({
        renderer: canvas, weight: 0, fillColor: levelColour(level), fillOpacity: 0.45, interactive: false,
      });
      for (const [s, w, n, e, level] of got.textures || []) {
        L.rectangle([[s, w], [n, e]], style(level)).addTo(built);
      }
      for (const [lat, lon, , best] of got.squares || []) {
        L.rectangle([[lat, lon], [lat + 1, lon + 1]], style(best)).addTo(built);
      }
    }
    if (streaming) builtTimer = setTimeout(drawBuilt, BUILT_EVERY_MS);
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
          renderer: canvas, weight: 0, fillColor: REGION_COLOUR, fillOpacity: 0.12, interactive: false,
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

  map.on("moveend", () => drawBuilt());
  map.on("dragstart", () => {
    follow = false; // the pilot looks elsewhere: the map stays there until asked back
    el.dispatchEvent(new CustomEvent("live-map-follow", { detail: false }));
  });

  drawRegions();
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
