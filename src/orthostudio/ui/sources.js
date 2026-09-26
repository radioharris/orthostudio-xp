// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// Imagery sources in the page's lists (docs/specs/ui.md, Step 1): what each covers, grouped as a
// user reads them, since the sources of several countries used to be mixed in one list (user
// report, 2026-09-14). Plain functions of the rows of GET /api/providers: node imports this module
// in the tests.

import { t, tOpt } from "./i18n.js";

const TILE_NAME_RE = /^([+-]\d{2})([+-]\d{3})$/;

/** Whether a source's rectangle meets the 1° tile `name` (`+46+006`), as the engine's
 * `Provider.covers`; always true for a source without one (the whole world). */
export function sourceCovers(p, name) {
  const b = p?.extent_bounds;
  const m = TILE_NAME_RE.exec(String(name));
  if (!Array.isArray(b) || b.length !== 4 || !m) return true;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  const [lon0, lat0, lon1, lat1] = b;
  return lon < lon1 && lon + 1 > lon0 && lat < lat1 && lat + 1 > lat0;
}

/** A country's name in the page's language (the engine gives it in English). */
export function countryName(extent) {
  return extent ? tOpt(`country.${extent}`) ?? String(extent) : "";
}

/** A source as the lists show it: its country first when it has one ("Netherlands · PDOK 2020"). */
export function sourceLabel(p) {
  if (!p) return "";
  const name = p.name || p.code;
  return p.extent && !p.custom ? `${countryName(p.extent)} · ${name}` : name;
}

/**
 * The sources in the order of the lists: `world` (the whole world: Bing Maps, then Esri, in the
 * engine's order), `tiles` (a country's that covers every tile chosen), `mine` (the sources the
 * user added), `other` (the other countries', by country). A source that is the same imagery as
 * another (`same_as`: NL is PDOK) is left out, unless it is `current`.
 */
export function sourceGroups(providers, tiles = [], current = null) {
  const groups = { world: [], tiles: [], mine: [], other: [] };
  for (const p of Array.isArray(providers) ? providers : []) {
    if (!p || (p.same_as && p.code !== current)) continue;
    if (p.custom) groups.mine.push(p);
    else if (!p.extent) groups.world.push(p);
    else if (tiles.length && tiles.every((name) => sourceCovers(p, name))) groups.tiles.push(p);
    else groups.other.push(p);
  }
  // by country; within one, the engine's order (PDOK's latest imagery before its years)
  const byCountry = (a, b) => countryName(a.extent).localeCompare(countryName(b.extent));
  groups.tiles.sort(byCountry);
  groups.other.sort(byCountry);
  return groups;
}

/** The title of a group of the lists; `other` is every country's when no tile is chosen. */
export function sourceGroupTitle(key, anyTiles) {
  if (key === "world") return t("sources.group_world");
  if (key === "tiles") return t("sources.group_tiles");
  if (key === "mine") return t("sources.group_mine");
  return anyTiles ? t("sources.group_other") : t("sources.group_countries");
}

/** What is wrong with the address of a source a user types, before the engine sees it: `scheme`
 * (not http or https) or `place` (no `{x}` `{y}` `{zoom}`, nor `{quadkey}`); null when it will do. */
export function sourceAddressProblem(url) {
  const u = String(url || "").trim();
  if (!/^https?:\/\//.test(u)) return "scheme";
  const has = (...names) => names.some((name) => u.includes(name));
  if (!u.includes("{quadkey}") && !(has("{x}") && has("{y}", "{-y}", "{|y|}") && has("{zoom}", "{zoom:02d}"))) return "place";
  return null;
}

/** The tiles among `tiles` that source `p` does not cover (its rectangle does not meet them). */
export function tilesNotCovered(p, tiles) {
  return p ? tiles.filter((name) => !sourceCovers(p, name)) : [];
}
