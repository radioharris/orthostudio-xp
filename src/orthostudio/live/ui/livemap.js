// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's map, over the shared base map (basemap.js): the aircraft and the camera as
// X-Plane sends them, the route of the SimBrief plan, the regions installed, and the textures
// built, coloured by how sharp they are (orthostudio/live/mapdata.py). The status the page polls
// every second moves the aircraft; the textures are asked for again when the view moves, and
// every few seconds while Live streams. The texture under the pointer is told in a bar over the
// map's top, one line or two, which can be hidden (a tooltip was too present, a user said), and
// told again as it changes while the pointer rests on it.
//
// Each layer is drawn opaque in a pane of its own, the pane made see-through as a whole: an
// airport's textures over the ZL16 ones do not add their colours up and look darker than they are.

import { createBaseMap, escapeHtml } from "./basemap.js";
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
// a flight's start: about 140 km round the aircraft, every texture one by one (mapdata.DETAIL_DEG)
const START_ZOOM = 9;
const ROUTE_COLOUR = "#e0572f"; // OrthoStudio XP's own route colour
const BUILT_EVERY_MS = 3000; // while Live streams: the engine answers "same" while nothing changed
const OWN_EVERY_MS = 30000; // the user's own tiles asked for again this often (the engine: 30 s)
const OWN_INSET = 2.5; // px: as OrthoStudio XP's Plan frames a tile, inside it, never on its edge

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
function planeIcon(heading) {
  const svg = `<svg viewBox="0 0 24 24" width="26" height="26" style="transform: rotate(${Math.round(heading)}deg)">`
    + '<path d="M12 2l1.4 7 7.6 4.2v1.9l-7.6-2.3-.5 5.6 2.5 1.9V22l-3.4-.9-3.4.9v-1.7l2.5-1.9-.5-5.6-7.6 2.3v-1.9L10.6 9z" '
    + 'fill="#ffffff" stroke="#1d1c1a" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  return L.divIcon({ className: "live-plane", html: svg, iconSize: [26, 26], iconAnchor: [13, 13] });
}

export function createLiveMap(el, { api, info = null, ownKinds = null }) {
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
  // over the borders (340), under the airports (365): the user's own tiles, framed
  const ownPane = map.createPane("liveOwn");
  ownPane.style.zIndex = "350";
  ownPane.style.pointerEvents = "none";
  const regions = L.layerGroup().addTo(map);
  const built = L.layerGroup().addTo(map);
  const own = L.layerGroup().addTo(map);
  let ownTiles = []; // [{lat, lon, pack, kind}]: GET map/own
  let ownKey = "";
  let ownAt = 0;
  const route = L.layerGroup().addTo(map);
  let plane = null;
  let camera = null;
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
  function drawOwn() {
    own.clearLayers();
    for (const tile of ownTiles) {
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
    ownKinds?.(new Set(ownTiles.map((x) => x.kind)));
    drawBuilt(); // Live's textures under a tile new or gone: left out, or back
  }
  map.on("zoomend", drawOwn); // the frame's inset is in pixels

  // the regions' shade only while zoomed out: closer, the view is inside one
  function showRegions() {
    const wanted = map.getZoom() <= REGIONS_MAX_ZOOM;
    if (wanted && !map.hasLayer(regions)) regions.addTo(map);
    if (!wanted && map.hasLayer(regions)) map.removeLayer(regions);
  }
  map.on("zoomend", showRegions);

  // the bar over the map: the texture under the pointer, found in what is drawn, its facts asked
  // of the engine once the pointer rests on another one, and again when it is built anew
  let hovered = null; // the texture or square under the pointer, as a key
  let pointer = null;
  let asking = 0;
  function under(latlng) {
    const { lat, lng } = latlng;
    const mine = ownTiles.find((x) => lat >= x.lat && lat < x.lat + 1 && lng >= x.lon && lng < x.lon + 1);
    if (mine) return { key: `o:${mine.lat},${mine.lon}`, own: mine }; // X-Plane shows it there
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
  map.on("mousemove", (ev) => pointAt(ev.latlng));
  function pointAt(latlng) {
    pointer = latlng;
    const found = under(latlng);
    if (!found) return nothing();
    if (found.key === hovered) return;
    hovered = found.key;
    clearTimeout(asking);
    if (found.own) {
      say(escapeHtml(t(`map.own_${found.own.kind}`, { pack: found.own.pack })));
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
      if (Date.now() - ownAt > OWN_EVERY_MS) readOwn(); // a tile built or removed meanwhile
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
    /** The bar over the map, shown or hidden (the header's box). */
    setDetails(on) {
      if (info) info.hidden = !on;
    },
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
    /** Its screen shown again: Leaflet measures its box, and the textures are asked anew. */
    shown() {
      map.invalidateSize();
      drawBuilt();
      readOwn();
    },
  };
}
