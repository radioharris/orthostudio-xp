// Geometry of tiles, textures and zones for the page (docs/specs/map-zones.md sections 3 and 7).
// Plain functions, no DOM and no Leaflet, so that node imports this module in the tests.
// Points are [lon, lat] pairs, the order of the zones document (GeoJSON).

export const ZONES_FORMAT = "osxp-zones-1";
export const ZONE_ZL_MIN = 12;
export const ZONE_ZL_MAX = 20;
export const MAX_ZONES = 500;
export const MAX_VERTICES = 2000;
export const MAX_NAME = 80;
/** Latitude limit of a zone vertex (web mercator stops at about 85.05°). */
export const MAX_LAT = 85;

const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const TILE_NAME_RE = /^([+-]\d{2})([+-]\d{3})$/;
/** Area (deg²) above which a clipped part of a zone counts for a tile, as in the engine (section 4). */
const MIN_PART_AREA = 1e-10;
/** Beyond this many 1° cells in a zone's bounding box, every cell of the box counts (no clipping). */
const MAX_EXACT_CELLS = 4096;

// ------------------------------------------------------------------ tiles

/** Tile containing a point: `floor(lat)`, `floor(lon)` formatted `%+03d%+04d` (+46+006). */
export function tileName(lat, lon) {
  const la = Math.floor(lat);
  const lo = Math.floor(lon);
  const sl = la < 0 ? "-" : "+";
  const so = lo < 0 ? "-" : "+";
  return `${sl}${String(Math.abs(la)).padStart(2, "0")}${so}${String(Math.abs(lo)).padStart(3, "0")}`;
}

/** South-west corner of a tile name, or null. */
export function parseTile(name) {
  const m = TILE_NAME_RE.exec(String(name));
  return m ? { lat: Number(m[1]), lon: Number(m[2]) } : null;
}

/** Ground resolution of a web-mercator 256 px tile at a latitude, in m/px. */
export function metersPerPixel(zl, lat) {
  return (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zl;
}

export function round9(x) {
  return Math.round(x * 1e9) / 1e9;
}

export function clampLat(lat) {
  return Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
}

export function wrapLon(lon) {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

/**
 * The 1° squares a flight plan crosses, in the order they come, without repeats.
 *
 * The line between two points is sampled every ``step`` degrees, well under the one degree a
 * square measures, so none is skipped; a leg that crosses the antimeridian is followed the short
 * way round, as an aircraft flies it. It is the map's own arithmetic, so what the buttons count is
 * what the map draws.
 */
export function tilesAlong(points, step = 0.1) {
  const seen = new Set();
  const out = [];
  const add = (lat, lon) => {
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat >= 90) return;
    const name = tileName(lat, wrapLon(lon));
    if (seen.has(name)) return;
    seen.add(name);
    out.push(name);
  };
  points.forEach((a, i) => {
    add(a.lat, a.lon);
    const b = points[i + 1];
    if (!b) return;
    const dlat = b.lat - a.lat;
    let dlon = b.lon - a.lon;
    if (dlon > 180) dlon -= 360;
    if (dlon < -180) dlon += 360;
    const legs = Math.max(1, Math.ceil(Math.max(Math.abs(dlat), Math.abs(dlon)) / step));
    for (let n = 1; n < legs; n += 1) {
      add(a.lat + (dlat * n) / legs, a.lon + (dlon * n) / legs);
    }
  });
  return out;
}

/** The length of a route in kilometres, on the sphere (what the route line says). */
export function routeLength(points) {
  const R = 6371;
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const la = (a.lat * Math.PI) / 180;
    const lb = (b.lat * Math.PI) / 180;
    let dlon = b.lon - a.lon;
    if (dlon > 180) dlon -= 360;
    if (dlon < -180) dlon += 360;
    const h =
      Math.sin((lb - la) / 2) ** 2 +
      Math.cos(la) * Math.cos(lb) * Math.sin(((dlon * Math.PI) / 180) / 2) ** 2;
    total += 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  return total;
}

// ------------------------------------------------------------------ textures

/**
 * Continuous texture coordinates [u, v] of a point at zoom level zl. A texture is 16 × 16
 * web-mercator tiles of zl, that is one tile of zl - 4: the integer parts are the texture
 * indices, the texture's top-left tile is (16 u, 16 v) (orthostudio.imagery.grid.texture_at).
 */
export function textureCoords(lon, lat, zl) {
  const mult = 2 ** (zl - 5);
  const u = (lon / 180 + 1) * mult;
  const v = (1 - Math.log(Math.tan(((90 + lat) * Math.PI) / 360)) / Math.PI) * mult;
  return [u, v];
}

/** [lon, lat] of the texture-grid corner (u, v) at zoom level zl (grid.tile_to_wgs84 of 16 u, 16 v). */
export function textureCorner(u, v, zl) {
  const mult = 2 ** (zl - 5);
  const lon = (u / mult - 1) * 180;
  const lat = (360 / Math.PI) * Math.atan(Math.exp(Math.PI * (1 - v / mult))) - 90;
  return [lon, lat];
}

/**
 * The texture of zoom level zl containing a point, as a zone polygon: south-west, south-east,
 * north-east, north-west (Ortho4XP's Ctrl+click, `O4_GUI_Utils.newPol`), 9 decimals.
 */
export function textureSquare(lon, lat, zl) {
  const [u, v] = textureCoords(lon, lat, zl);
  const iu = Math.trunc(u);
  const iv = Math.trunc(v);
  const [west, north] = textureCorner(iu, iv, zl);
  const [east, south] = textureCorner(iu + 1, iv + 1, zl);
  const s = round9(clampLat(south));
  const n = round9(clampLat(north));
  return [
    [round9(west), s],
    [round9(east), s],
    [round9(east), n],
    [round9(west), n],
  ];
}

/**
 * The texture-grid corner nearest to a point at zoom level zl (Ortho4XP's Ctrl+Shift+click,
 * `O4_GUI_Utils.newPointGrid`: the next corner once the point is 8 tiles of 16 into the texture).
 */
export function snapToTextureCorner(lon, lat, zl) {
  const [u, v] = textureCoords(lon, lat, zl);
  const [x, y] = textureCorner(Math.floor(u + 0.5), Math.floor(v + 0.5), zl);
  return [round9(x), round9(clampLat(y))];
}

// ------------------------------------------------------------------ polygons

/** Finite [lon, lat] points, consecutive duplicates and the closing vertex dropped. */
export function cleanPolygon(points) {
  const out = [];
  for (const p of Array.isArray(points) ? points : []) {
    if (!Array.isArray(p) || p.length < 2) continue;
    const q = [Number(p[0]), Number(p[1])];
    if (!Number.isFinite(q[0]) || !Number.isFinite(q[1])) continue;
    const last = out[out.length - 1];
    if (last && last[0] === q[0] && last[1] === q[1]) continue;
    out.push(q);
  }
  while (out.length > 1 && out[0][0] === out[out.length - 1][0] && out[0][1] === out[out.length - 1][1]) {
    out.pop();
  }
  return out;
}

export function samePolygon(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  return a.every((p, i) => p[0] === b[i][0] && p[1] === b[i][1]);
}

/** Shoelace area in deg² (positive counter-clockwise); exact for the output of clipToBox too. */
export function signedArea(poly) {
  let a = 0;
  for (let i = 0, n = poly.length; i < n; i += 1) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % n];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

export function polygonBounds(poly) {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const [x, y] of poly) {
    west = Math.min(west, x);
    east = Math.max(east, x);
    south = Math.min(south, y);
    north = Math.max(north, y);
  }
  return { west, south, east, north };
}

/** Even-odd ray casting; a point on an edge may fall either way. */
export function pointInPolygon(point, poly) {
  const [x, y] = point;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function orient(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function withinBox(a, b, c) {
  return Math.min(a[0], b[0]) <= c[0] && c[0] <= Math.max(a[0], b[0]) && Math.min(a[1], b[1]) <= c[1] && c[1] <= Math.max(a[1], b[1]);
}

/** Segments p1p2 and p3p4 cross or touch. */
function segmentsMeet(p1, p2, p3, p4) {
  const d1 = orient(p3, p4, p1);
  const d2 = orient(p3, p4, p2);
  const d3 = orient(p1, p2, p3);
  const d4 = orient(p1, p2, p4);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
  return (
    (d1 === 0 && withinBox(p3, p4, p1)) ||
    (d2 === 0 && withinBox(p3, p4, p2)) ||
    (d3 === 0 && withinBox(p1, p2, p3)) ||
    (d4 === 0 && withinBox(p1, p2, p4))
  );
}

/**
 * A simple ring, what the engine's validity check (shapely is_valid) accepts for an exterior
 * ring: 3 distinct vertices or more, a non-zero area, no edge meeting a non-adjacent edge and no
 * spike (the ring turning back on itself). Quadratic, run when a polygon is closed.
 */
export function polygonIsSimple(points) {
  const poly = cleanPolygon(points);
  const n = poly.length;
  if (n < 3 || Math.abs(signedArea(poly)) < 1e-14) return false;
  for (let i = 0; i < n; i += 1) {
    const a = poly[(i + n - 1) % n];
    const b = poly[i];
    const c = poly[(i + 1) % n];
    const back = (c[0] - b[0]) * (a[0] - b[0]) + (c[1] - b[1]) * (a[1] - b[1]);
    if (orient(a, b, c) === 0 && back > 0) return false;
  }
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 2; j < n; j += 1) {
      if (i === 0 && j === n - 1) continue; // adjacent through the closing edge
      if (segmentsMeet(poly[i], poly[i + 1], poly[j], poly[(j + 1) % n])) return false;
    }
  }
  return true;
}

function clipEdge(points, inside, cross) {
  const out = [];
  for (let i = 0; i < points.length; i += 1) {
    const cur = points[i];
    const prev = points[(i + points.length - 1) % points.length];
    const curIn = inside(cur);
    if (curIn !== inside(prev)) out.push(cross(prev, cur));
    if (curIn) out.push(cur);
  }
  return out;
}

/** Sutherland-Hodgman clip of a ring by a lon/lat box (degenerate edges may remain; the area is right). */
export function clipToBox(poly, west, south, east, north) {
  const atLon = (x) => (a, b) => [x, a[1] + ((x - a[0]) / (b[0] - a[0])) * (b[1] - a[1])];
  const atLat = (y) => (a, b) => [a[0] + ((y - a[1]) / (b[1] - a[1])) * (b[0] - a[0]), y];
  let out = poly;
  out = clipEdge(out, (p) => p[0] >= west, atLon(west));
  if (out.length) out = clipEdge(out, (p) => p[0] <= east, atLon(east));
  if (out.length) out = clipEdge(out, (p) => p[1] >= south, atLat(south));
  if (out.length) out = clipEdge(out, (p) => p[1] <= north, atLat(north));
  return out;
}

/** Names of the tiles a zone covers with a positive area, south to north then west to east. */
export function tilesTouched(points) {
  const poly = cleanPolygon(points);
  if (poly.length < 3) return [];
  const b = polygonBounds(poly);
  const lat0 = Math.max(-90, Math.floor(b.south));
  const lat1 = Math.min(89, Math.ceil(b.north) - 1);
  const lon0 = Math.max(-180, Math.floor(b.west));
  const lon1 = Math.min(179, Math.ceil(b.east) - 1);
  const exact = (lat1 - lat0 + 1) * (lon1 - lon0 + 1) <= MAX_EXACT_CELLS;
  const out = [];
  for (let lat = lat0; lat <= lat1; lat += 1) {
    for (let lon = lon0; lon <= lon1; lon += 1) {
      if (!exact || Math.abs(signedArea(clipToBox(poly, lon, lat, lon + 1, lat + 1))) > MIN_PART_AREA) {
        out.push(tileName(lat, lon));
      }
    }
  }
  return out;
}

/** Names of the tiles the bounding box of `points` overlaps with a positive area, even when the
 * polygon itself is invalid: every tile a zone can affect, the engine's rule for a zone with a
 * problem (map-zones.md 10). Points that are not two finite numbers are ignored. */
export function tilesInBounds(points) {
  const pts = (Array.isArray(points) ? points : []).filter(
    (p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]),
  );
  if (!pts.length) return [];
  const b = polygonBounds(pts);
  const lat0 = Math.max(-90, Math.floor(b.south));
  const lat1 = Math.min(89, Math.ceil(b.north) - 1);
  const lon0 = Math.max(-180, Math.floor(b.west));
  const lon1 = Math.min(179, Math.ceil(b.east) - 1);
  const out = [];
  for (let lat = lat0; lat <= lat1; lat += 1) {
    for (let lon = lon0; lon <= lon1; lon += 1) out.push(tileName(lat, lon));
  }
  return out;
}

/** Megabytes of one texture on disk: a 4096² BC1 DDS with its mipmaps (orthostudio.estimate.BC1_BYTES). */
export const TEXTURE_MB = 11.184952;
/** Above this many clip operations (squares × vertices), a zone's count is its bounding box's. */
const EXACT_TEXTURE_WORK = 2e6;

/** Textures of a whole tile at zl, as the engine's estimate counts them (grid.textures_covering). */
export function tileTextureCount(tile, zl) {
  const c = parseTile(tile);
  if (!c) return 0;
  const [u0, v0] = textureCoords(c.lon, clampLat(c.lat + 1), zl);
  const [u1, v1] = textureCoords(c.lon + 1, clampLat(c.lat), zl);
  return (Math.trunc(u1) - Math.trunc(u0) + 1) * (Math.trunc(v1) - Math.trunc(v0) + 1);
}

/**
 * Keys "u:v" of the textures of zoom level zl whose square overlaps the zone clipped to the tile
 * by more than 1e-10 deg² (orthostudio.zones.zone_list_textures). Beyond a work budget, every texture of
 * the clipped bounding box: an upper bound, like the engine's estimate.
 */
export function zoneTextureKeys(points, zl, tile) {
  const c = parseTile(tile);
  const poly = cleanPolygon(points);
  const keys = [];
  if (!c || poly.length < 3) return keys;
  const part = clipToBox(poly, c.lon, c.lat, c.lon + 1, c.lat + 1);
  if (part.length < 3 || Math.abs(signedArea(part)) <= MIN_PART_AREA) return keys;
  const b = polygonBounds(part);
  const [u0, v0] = textureCoords(b.west, clampLat(b.north), zl);
  const [u1, v1] = textureCoords(b.east, clampLat(b.south), zl);
  const iu0 = Math.trunc(u0);
  const iu1 = Math.trunc(u1);
  const iv0 = Math.trunc(v0);
  const iv1 = Math.trunc(v1);
  const exact = (iu1 - iu0 + 1) * (iv1 - iv0 + 1) * part.length <= EXACT_TEXTURE_WORK;
  for (let iv = iv0; iv <= iv1; iv += 1) {
    const north = textureCorner(0, iv, zl)[1];
    const south = textureCorner(0, iv + 1, zl)[1];
    const row = exact ? clipToBox(part, b.west - 1, south, b.east + 1, north) : null;
    if (row && (row.length < 3 || Math.abs(signedArea(row)) <= MIN_PART_AREA)) continue;
    for (let iu = iu0; iu <= iu1; iu += 1) {
      if (row) {
        const west = textureCorner(iu, 0, zl)[0];
        const east = textureCorner(iu + 1, 0, zl)[0];
        if (Math.abs(signedArea(clipToBox(row, west, south, east, north))) <= MIN_PART_AREA) continue;
      }
      keys.push(`${iu}:${iv}`);
    }
  }
  return keys;
}

// ------------------------------------------------------------------ zones

/** A fresh id `[A-Za-z0-9_-]{1,64}` not used by any of the zones. */
export function newZoneId(zones, now = Date.now(), random = Math.random) {
  const taken = new Set((zones || []).map((z) => z.id));
  const stem = `z${now.toString(36)}${Math.floor(random() * 36 ** 3).toString(36).padStart(3, "0")}`;
  let id = stem;
  for (let i = 1; taken.has(id); i += 1) id = `${stem}-${i}`;
  return id;
}

/**
 * Where a new zone goes in the priority list (index 0 wins, map-zones.md M4): before the first
 * zone of the same or a lower zoom level, so a finer zone drawn inside a coarser one wins, as
 * Ortho4XP's `save_zone_list` sorted them; the user reorders afterwards.
 */
export function insertIndexForZl(zones, zl) {
  const i = zones.findIndex((z) => z.zl <= zl);
  return i < 0 ? zones.length : i;
}

/**
 * The zones step 2 lists, in the list's order, which decides overlaps: every zone when `all` is
 * set or no tile is chosen, else those touching one of the `chosen` tiles (names) and those
 * `keep` holds. A helicopter pilot with a zone per landing site went through all of them to reach
 * the few of one square (X-Plane.Org, 2026-09-22); the others stay on the map and in the file.
 */
export function listedZones(zones, chosen, { all = false, keep = () => false, tilesOf = (z) => tilesTouched(z.polygon) } = {}) {
  const names = new Set(chosen);
  if (all || !names.size) return zones.slice();
  return zones.filter((z) => keep(z) || tilesOf(z).some((n) => names.has(n)));
}

/**
 * The zones once the arrow of zone `id` moved it one row up (`delta` -1) or down (+1) in the
 * `listed` rows: right before, or right after, the row it passes, over the zones the list leaves
 * out. For two zones side by side in `zones`, the swap it always was; `null` at an end.
 */
export function movedInList(zones, listed, id, delta) {
  const rows = listed.map((z) => z.id);
  const at = rows.indexOf(id);
  const past = at < 0 ? undefined : rows[at + delta];
  const zone = zones.find((z) => z.id === id);
  if (past === undefined || !zone) return null;
  const out = zones.filter((z) => z.id !== id);
  const j = out.findIndex((z) => z.id === past);
  out.splice(delta < 0 ? j : j + 1, 0, zone);
  return out;
}

/** Why one zone breaks the `osxp-zones-1` format (section 3), or null. `ids` holds the ids before it. */
export function zoneProblem(zone, ids, providers) {
  if (!zone || typeof zone !== "object") return "a zone must be an object";
  if (typeof zone.id !== "string" || !ID_RE.test(zone.id)) return "id: 1 to 64 characters among A-Z a-z 0-9 _ -";
  if (ids && ids.has(zone.id)) return `id ${zone.id} is used twice`;
  if (zone.name != null && (typeof zone.name !== "string" || zone.name.length > MAX_NAME)) {
    return `name: at most ${MAX_NAME} characters`;
  }
  if (!Number.isInteger(zone.zl) || zone.zl < ZONE_ZL_MIN || zone.zl > ZONE_ZL_MAX) {
    return `zl: an integer from ${ZONE_ZL_MIN} to ${ZONE_ZL_MAX}`;
  }
  if (zone.provider != null) {
    if (typeof zone.provider !== "string") return "provider: null or a provider code";
    if (Array.isArray(providers)) {
      const p = providers.find((x) => x.code === zone.provider);
      if (!p) return `provider ${zone.provider} is not in the registry`;
      if (zone.zl > p.max_zl) return `zl ${zone.zl} is above the maximum of ${p.code} (${p.max_zl})`;
    }
  }
  if (!Array.isArray(zone.polygon)) return "polygon: a list of [lon, lat] pairs";
  for (const p of zone.polygon) {
    if (!Array.isArray(p) || p.length !== 2 || !p.every(Number.isFinite)) return "polygon: a list of [lon, lat] pairs";
    if (p[0] < -180 || p[0] > 180 || p[1] < -MAX_LAT || p[1] > MAX_LAT) {
      return `polygon: longitudes in [-180, 180], latitudes in [-${MAX_LAT}, ${MAX_LAT}]`;
    }
  }
  const ring = cleanPolygon(zone.polygon);
  if (ring.length < 3 || zone.polygon.length > MAX_VERTICES + 1) return `polygon: 3 to ${MAX_VERTICES} vertices`;
  if (!polygonIsSimple(ring)) return "polygon: not a simple polygon (self-intersection or no area)";
  return null;
}

/** The first problem of a zones document as {zone, reason}, or null when it is valid. */
export function validateZonesDocument(doc, providers) {
  if (!doc || typeof doc !== "object" || doc.format !== ZONES_FORMAT) {
    return { zone: "", reason: `format must be ${ZONES_FORMAT}` };
  }
  if (!Array.isArray(doc.zones)) return { zone: "", reason: "zones must be a list" };
  if (doc.zones.length > MAX_ZONES) return { zone: "", reason: `at most ${MAX_ZONES} zones` };
  const ids = new Set();
  for (const [i, zone] of doc.zones.entries()) {
    const reason = zoneProblem(zone, ids, providers);
    if (reason) return { zone: zone && typeof zone.id === "string" ? zone.id : String(i), reason };
    ids.add(zone.id);
  }
  return null;
}

// ------------------------------------------------------------------ country borders

export const BORDERS_FORMAT = "osxp-borders-1";

/**
 * The borders of `vendor/borders/borders.json` (tools/borders/build_borders.py) by class, as
 * Leaflet polylines: `{international: [[[lat, lon], ...], ...], disputed: [...]}`. Each line of
 * the file is `[class, lon0, lat0, dlon1, dlat1, ...]` in 1/scale degrees; the sums stay integers
 * until the division, so no error builds up along a border. Another format throws: a wrong file
 * shows as "borders unavailable", not as lines across the map.
 */
export function decodeBorders(doc) {
  const scale = doc?.scale;
  if (doc?.format !== BORDERS_FORMAT || !(scale > 0) || !Array.isArray(doc.classes) || !Array.isArray(doc.lines)) {
    throw new Error(`borders: the format must be ${BORDERS_FORMAT}`);
  }
  const out = Object.fromEntries(doc.classes.map((name) => [name, []]));
  for (const line of doc.lines) {
    const name = Array.isArray(line) ? doc.classes[line[0]] : undefined;
    if (name === undefined || line.length < 5 || line.length % 2 === 0) throw new Error("borders: a line is malformed");
    let x = 0;
    let y = 0;
    const points = [];
    for (let i = 1; i < line.length; i += 2) {
      x += line[i];
      y += line[i + 1];
      points.push([y / scale, x / scale]);
    }
    out[name].push(points);
  }
  return out;
}

/** One zone as the page keeps it and sends it: the five fields, ring open, 9 decimals. */
/** The looks a tile or a zone may name of its own; ``null`` inherits the level above
 * (Settings, then the tile, then the zone: ``zones.py``). */
export const PHOTO_LOOK_VALUES = ["as_delivered", "softer", "much_softer", "custom"];

/** What two colour choices must share to be the same answer.
 *
 * ``""`` when nothing is set (the level above applies); the name alone for a named look, since
 * its numbers are not used; the three numbers for ``custom``. Comparing the objects themselves
 * said "the chosen squares differ" for squares that did not (a user, 2026-09-18): the keys of
 * two equal choices can be in any order, and a named look drags numbers nobody reads. */
export function photoKey(photo) {
  const look = photo && photo.look ? photo.look : "";
  if (!look) return "";
  if (look !== "custom") return look;
  const n = (v) => Number(v || 0).toFixed(3);
  return `custom:${n(photo.brightness)}:${n(photo.contrast)}:${n(photo.saturation)}`;
}

/** The regions that carry colours of their own, **in painting order**: what comes last covers
 * what came before, so the list ends with what a build would apply.
 *
 * ``zones`` is the document's list and ``tiles`` its squares (``{name: {photo}}``). The squares
 * come first; the zones follow, from the last of the document to the first, since a zone wins
 * inside its polygon and, where two overlap, the one higher in the list wins -- the rule a build
 * follows, a texture taking the colours of the first zone holding its centre
 * (``build.photo_zone_colours``). Painting the squares last hid the colours of every zone drawn
 * inside one (a user, 2026-09-18). Each region is ``{ring: [[lon, lat], ...], photo, square?}``.
 */
export function colouredRegions(zones, tiles) {
  const out = [];
  for (const [name, choice] of Object.entries(tiles || {})) {
    const corner = parseTile(name);
    if (!corner || !choice?.photo?.look) continue;
    const { lat, lon } = corner;
    out.push({
      ring: [[lon, lat], [lon + 1, lat], [lon + 1, lat + 1], [lon, lat + 1]],
      photo: choice.photo,
      square: true,
    });
  }
  for (let i = (zones || []).length - 1; i >= 0; i -= 1) {
    const zone = zones[i];
    if (zone?.photo?.look && (zone.polygon || []).length >= 3) {
      out.push({ ring: zone.polygon, photo: zone.photo });
    }
  }
  return out;
}

/** One colour choice, as the API stores it: a look and the three numbers of "custom". */
export function normalizePhoto(photo) {
  const p = photo && typeof photo === "object" ? photo : {};
  const number = (value, min) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.min(0.5, Math.max(min, n)) : 0;
  };
  return {
    look: PHOTO_LOOK_VALUES.includes(p.look) ? p.look : null,
    brightness: number(p.brightness, -0.5),
    contrast: number(p.contrast, -0.5),
    saturation: number(p.saturation, -1),
  };
}

export function normalizeZone(zone) {
  return {
    id: String(zone.id),
    name: typeof zone.name === "string" ? zone.name.slice(0, MAX_NAME) : "",
    zl: Number(zone.zl),
    provider: typeof zone.provider === "string" && zone.provider ? zone.provider : null,
    photo: normalizePhoto(zone.photo),
    polygon: cleanPolygon(zone.polygon).map(([x, y]) => [round9(x), round9(y)]),
  };
}
