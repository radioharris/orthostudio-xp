// The Settings screen in plain words (docs/specs/ui.md 2.4; what each setting really does, and
// the wording: docs/specs/settings-plain-language.md).
//
// A pilot answers questions about what they want to see in X-Plane, and OrthoStudio XP keeps the
// detailed settings behind the answers. The recommended answers are marked, three presets set
// several answers at once, and every other setting waits under "For experts" with a plain label,
// a short note and its name in Ortho4XP. Descriptions and pure functions first (tested under
// node), the DOM last; app.js owns the saved settings, the draft, and the Save button.

import { photoValues } from "./colour.js";
import { FLIGHT_PLAN } from "./release.js";
import { fmtInt, fmtNum, homely, t } from "./i18n.js";
import { colourPreview } from "./preview.js";
import { detailLabel, detailName } from "./map.js";
import { sourceGroups, sourceLabel } from "./sources.js";

/** Ground sizes of the detail levels are given at this latitude (settings are not per tile). */
export const GROUND_LAT = 45;

// ---------------------------------------------------------------- values

export function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

export function setPath(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (const k of keys.slice(0, -1)) {
    if (cur[k] == null || typeof cur[k] !== "object") cur[k] = {};
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
}

export function sameValue(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The leaves of the inlined schema of GET /api/settings/schema, by path. */
export function schemaLeaves(schema) {
  const out = {};
  const walk = (props, prefix) => {
    for (const [name, prop] of Object.entries(props || {})) {
      if (prop && prop.properties) walk(prop.properties, `${prefix}${name}.`);
      else out[`${prefix}${name}`] = prop || {};
    }
  };
  walk(schema?.properties, "");
  return out;
}

/** A whole settings document at the schema's defaults. */
export function schemaDefaults(schema) {
  const doc = {};
  for (const [path, prop] of Object.entries(schemaLeaves(schema))) {
    setPath(doc, path, prop.default === undefined ? null : structuredClone(prop.default));
  }
  return doc;
}

/** What belongs to this computer and to its pilot, which *Default values* keeps: none of it is a
 * look of the tiles, and the defaults sending the downloads back to the computer's own disk would
 * surprise (2026-09-15); the SimBrief name is the pilot's own (2026-09-19). */
export const COMPUTER_FOLDERS = ["essential.xplane_dir", "essential.data_dir", "essential.simbrief_user"];

/** The schema's defaults, with the folders of ``draft`` kept. */
export function defaultsKeepingFolders(schema, draft) {
  const doc = schemaDefaults(schema);
  for (const path of COMPUTER_FOLDERS) setPath(doc, path, getPath(draft, path) ?? null);
  return doc;
}

// ---------------------------------------------------------------- presets

/** Sizes: docs/specs/settings-plain-language.md section 5 (a coastal ZL16 tile with 7 main
 * airports; estimates). A preset leaves the imagery source, the relief, the overlays and every
 * expert setting as they are. */
export const PRESETS = [
  {
    id: "recommended",
    name: () => t("settings.preset.recommended"),
    text: () => t("settings.preset.recommended_text"),
    values: {
      "essential.zoom_level": 16,
      "essential.airports.mode": "icao",
      "essential.airports.zoom_level": 18,
      "essential.airports.extent_km": 1,
      "essential.coast_transition.profile": "sand",
      "essential.coast_transition.width_m": 100,
      "essential.water_rendering": "XP11 + bathy",
      "advanced.ratio_water_pct": 25,
    },
  },
  {
    id: "best",
    name: () => t("settings.preset.best"),
    text: () => t("settings.preset.best_text"),
    values: {
      "essential.zoom_level": 17,
      "essential.airports.mode": "on",
      "essential.airports.zoom_level": 18,
      "essential.airports.extent_km": 1,
      "essential.coast_transition.profile": "sand",
      "essential.coast_transition.width_m": 100,
      "essential.water_rendering": "XP11 + bathy",
      "advanced.ratio_water_pct": 25,
    },
  },
  {
    id: "light",
    name: () => t("settings.preset.light"),
    text: () => t("settings.preset.light_text"),
    values: {
      "essential.zoom_level": 15,
      "essential.airports.mode": "off",
      "essential.coast_transition.profile": "sand",
      "essential.coast_transition.width_m": 100,
      "essential.water_rendering": "XP11 + bathy",
      "advanced.ratio_water_pct": 25,
    },
  },
];

export function applyPreset(draft, id) {
  const preset = PRESETS.find((p) => p.id === id);
  if (preset) {
    for (const [path, value] of Object.entries(preset.values)) setPath(draft, path, structuredClone(value));
  }
  return draft;
}

/** The preset every value of which the settings hold, or null. */
export function matchingPreset(settings) {
  const found = PRESETS.find((p) => Object.entries(p.values).every(([path, value]) => sameValue(getPath(settings, path), value)));
  return found ? found.id : null;
}

// ---------------------------------------------------------------- questions

export const QUESTION_PATHS = {
  provider: ["essential.provider"],
  detail: ["essential.zoom_level"],
  airports: ["essential.airports.mode"],
  coast: ["essential.coast_transition.profile"],
  width: ["essential.coast_transition.width_m"],
  water: ["essential.water_rendering"],
  lakes: ["advanced.ratio_water_pct"],
  colours: ["essential.photo_look", "expert.photo_brightness", "expert.photo_contrast", "expert.photo_saturation"],
  relief: ["essential.relief.source", "essential.relief.file", "essential.relief.fill_nodata"],
  own: ["essential.relief.folder"],
  overlays: ["essential.overlays"],
  xplane: ["essential.xplane_dir"],
  data: ["essential.data_dir"],
  simbrief: ["essential.simbrief_user"],
};

const QUESTION_TEXT = {
  provider: [() => t("settings.q.provider"), () => t("settings.q.provider_help")],
  detail: [() => t("settings.q.detail"), () => t("settings.q.detail_help")],
  airports: [() => t("settings.q.airports"), () => t("settings.q.airports_help")],
  coast: [() => t("settings.q.coast"), () => t("settings.q.coast_help")],
  width: [() => t("settings.q.width"), () => t("settings.q.width_help")],
  water: [() => t("settings.q.water"), () => t("settings.q.water_help")],
  lakes: [() => t("settings.q.lakes"), () => t("settings.q.lakes_help")],
  colours: [() => t("settings.q.colours"), () => t("settings.q.colours_help")],
  relief: [() => t("settings.q.relief"), () => t("settings.q.relief_help")],
  own: [() => t("settings.q.own"), () => t("settings.q.own_help")],
  overlays: [() => t("settings.q.overlays"), () => t("settings.q.overlays_help")],
  xplane: [() => t("settings.q.xplane"), () => t("settings.q.xplane_help")],
  data: [() => t("settings.q.data"), () => t("settings.q.data_help")],
  simbrief: [() => t("settings.q.simbrief"), () => t("settings.q.simbrief_help")],
};

/**
 * What the data question says of the folder the tiles and the downloads go to now, from the
 * status's ``data_dir`` (``{path, chosen, present}``): ``{text, warn}``, or null while unknown.
 */
export function dataFolderLine(dataDir) {
  if (!dataDir || !dataDir.path) return null;
  if (dataDir.present === false) return { text: t("settings.q.data_missing", { path: homely(dataDir.path) }), warn: true };
  const path = homely(dataDir.path);
  return { text: dataDir.chosen ? t("settings.q.data_now", { path }) : t("settings.q.data_now_home", { path }), warn: false };
}

/** The disk formats that can hold the data, for the platform the engine runs on. */
export function dataFormats(platform) {
  if (platform === "win") return t("settings.q.data_formats_win");
  if (platform === "lin") return t("settings.q.data_formats_lin");
  return t("settings.q.data_formats_mac");
}

/** "Very sharp · ZL18". */
function levelText(zl) {
  return t("settings.q.level", { name: detailName(zl), zl });
}

/** The value set by hand that no choice offers, as one more choice (never hidden, never lost). */
function withCurrent(choices, current, label) {
  if (current == null || choices.some((c) => sameValue(c.value, current))) return choices;
  return [...choices, { value: current, label }];
}

/** Whether a question is shown: the sea's reach is a single width, set under For experts for a
 * fade in three steps. */
export function questionShown(id, settings) {
  if (id === "width") return getPath(settings, "essential.coast_transition.profile") !== "3steps";
  return id in QUESTION_PATHS;
}

/** The choices of a question, in order: ``{value, label, note?, recommended?, disabled?}``. */
export function questionChoices(id, settings, { providers = [], ownScenery = [] } = {}) {
  const value = (path) => getPath(settings, path);
  switch (id) {
    case "provider": {
      // the whole world first (Bing Maps, then Esri), the user's own, then the countries'
      const groups = sourceGroups(providers, [], value("essential.provider"));
      const choices = [...groups.world, ...groups.mine, ...groups.other].map((p) => ({
        value: p.code,
        label: sourceLabel(p),
        note: p.alive === false ? t("plan.provider_dead") : undefined,
        recommended: p.code === "BI",
        disabled: p.alive === false,
      }));
      return withCurrent(choices, value("essential.provider"), String(value("essential.provider")));
    }
    case "detail": {
      const provider = providers.find((p) => p.code === value("essential.provider"));
      const max = provider && Number.isInteger(provider.max_zl) ? provider.max_zl : 19;
      const notes = {
        15: () => t("settings.q.detail_less"),
        17: () => t("settings.q.detail_more"),
        18: () => t("settings.q.detail_much_more"),
      };
      const choices = [15, 16, 17, 18].map((zl) => ({
        value: zl,
        label: detailLabel(zl, GROUND_LAT),
        note: zl > max ? t("settings.q.detail_above_source") : notes[zl]?.(),
        recommended: zl === 16,
        disabled: zl > max,
      }));
      const current = value("essential.zoom_level");
      return withCurrent(choices, current, Number.isInteger(current) ? detailLabel(current, GROUND_LAT) : String(current));
    }
    case "airports": {
      const level = levelText(value("essential.airports.zoom_level"));
      const choices = [
        { value: "icao", label: t("settings.q.airports_icao", { level }), recommended: true },
        { value: "on", label: t("settings.q.airports_on", { level }) },
        { value: "off", label: t("settings.q.airports_off") },
      ];
      return withCurrent(choices, value("essential.airports.mode"), t("settings.q.airports_existing"));
    }
    case "coast": {
      const choices = [
        { value: "sand", label: t("settings.q.coast_sand"), recommended: true },
        { value: "rocks", label: t("settings.q.coast_rocks") },
      ];
      return withCurrent(choices, value("essential.coast_transition.profile"), t("settings.q.coast_3steps"));
    }
    case "width": {
      const choices = [
        { value: 50, label: t("settings.q.width_m", { n: 50 }), note: t("settings.q.width_50") },
        { value: 100, label: t("settings.q.width_m", { n: 100 }), recommended: true },
        { value: 200, label: t("settings.q.width_m", { n: 200 }), note: t("settings.q.width_200") },
      ];
      const current = value("essential.coast_transition.width_m");
      return Array.isArray(current) ? choices : withCurrent(choices, current, t("settings.q.width_m", { n: fmtNum(Number(current), 1) }));
    }
    case "water":
      return [
        { value: "XP11 + bathy", label: t("settings.q.water_xp11"), note: t("settings.q.water_xp11_note"), recommended: true },
        { value: "XP12", label: t("settings.q.water_xp12") },
      ];
    case "lakes": {
      const choices = [
        { value: 10, label: t("settings.q.lakes_10") },
        { value: 25, label: t("settings.q.lakes_25"), recommended: true },
        { value: 50, label: t("settings.q.lakes_50") },
      ];
      const current = value("advanced.ratio_water_pct");
      return withCurrent(choices, current, t("settings.q.lakes_other", { n: fmtNum(Number(current), 1) }));
    }
    case "colours":
      return [
        { value: "as_delivered", label: t("settings.q.colours_as_delivered"), recommended: true },
        { value: "softer", label: t("settings.q.colours_softer"), note: t("settings.q.colours_softer_note") },
        { value: "much_softer", label: t("settings.q.colours_much_softer"), note: t("settings.q.colours_much_softer_note") },
        { value: "custom", label: t("settings.q.colours_custom"), note: t("settings.q.colours_custom_note") },
      ];
    case "relief":
      return [
        { value: "auto", label: t("settings.q.relief_auto"), note: t("settings.q.relief_auto_note"), recommended: true },
        { value: "copernicus", label: t("settings.q.relief_cop30"), note: t("settings.q.relief_cop30_note") },
        { value: "usgs", label: t("settings.q.relief_usgs"), note: t("settings.q.relief_usgs_note") },
        { value: "usgs1", label: t("settings.q.relief_usgs1"), note: t("settings.q.relief_usgs1_note") },
        { value: "canada", label: t("settings.q.relief_canada"), note: t("settings.q.relief_canada_note") },
        { value: "south_america", label: t("settings.q.relief_anadem"), note: t("settings.q.relief_anadem_note") },
        { value: "file", label: t("settings.q.relief_file"), note: t("settings.q.relief_file_note") },
      ];
    case "holes":
      return [
        { value: "nearest", label: t("settings.q.holes_nearest"), recommended: true },
        { value: "zero", label: t("settings.q.holes_zero"), note: t("settings.q.holes_zero_note") },
      ];
    case "overlays": {
      // A pack that brings its own roads, forests and buildings is in X-Plane: ours would come
      // on top of its own, everything drawn twice. The answer to recommend is the other one.
      const brought = ownScenery.length ? ownScenery.join(", ") : "";
      return [
        {
          value: "xplane",
          label: t("settings.q.overlays_xplane"),
          note: brought ? t("settings.q.overlays_twice", { pack: brought }) : undefined,
          recommended: !brought,
        },
        {
          value: "none",
          label: t("settings.q.overlays_none"),
          note: brought ? t("settings.q.overlays_found", { pack: brought }) : t("settings.q.overlays_none_note"),
          recommended: Boolean(brought),
        },
      ];
    }
    default:
      return [];
  }
}

/** Set a question's answer on the draft, with what goes with it. */
export function answer(draft, id, value) {
  switch (id) {
    case "provider":
      setPath(draft, "essential.provider", value);
      break;
    case "detail":
      setPath(draft, "essential.zoom_level", Number(value));
      break;
    case "airports":
      setPath(draft, "essential.airports.mode", value);
      break;
    case "coast":
      setPath(draft, "essential.coast_transition.profile", value);
      // sand and rocks take one width: leaving three steps comes back to the recommended one
      if (value !== "3steps" && Array.isArray(getPath(draft, "essential.coast_transition.width_m"))) {
        setPath(draft, "essential.coast_transition.width_m", 100);
      }
      break;
    case "width":
      setPath(draft, "essential.coast_transition.width_m", Number(value));
      break;
    case "water":
      setPath(draft, "essential.water_rendering", value);
      break;
    case "lakes":
      setPath(draft, "advanced.ratio_water_pct", Number(value));
      break;
    case "colours":
      setPath(draft, "essential.photo_look", value);
      break;
    case "relief":
      setPath(draft, "essential.relief.source", value);
      break;
    case "holes":
      setPath(draft, "essential.relief.fill_nodata", value);
      break;
    case "overlays":
      setPath(draft, "essential.overlays", value);
      break;
    default:
      break;
  }
  return draft;
}

/** What the Plan says of the settings its build uses, in a few words. */
export function settingsSummary(settings) {
  const e = settings?.essential;
  if (!e) return [];
  const parts = [];
  const level = levelText(e.airports?.zoom_level);
  const airports = {
    icao: () => t("plan.s.airports_icao", { level }),
    on: () => t("plan.s.airports_on", { level }),
    existing: () => t("plan.s.airports_existing"),
  };
  parts.push(airports[e.airports?.mode]?.() ?? t("plan.s.airports_off"));
  const ct = e.coast_transition || {};
  if (ct.profile === "3steps") parts.push(t("plan.s.coast_3steps"));
  else if (ct.profile === "rocks") parts.push(t("plan.s.coast_rocks", { n: fmtNum(Number(ct.width_m), 1) }));
  else parts.push(t("plan.s.coast_sand", { n: fmtNum(Number(ct.width_m), 1) }));
  parts.push(e.water_rendering === "XP12" ? t("plan.s.water_xp12") : t("plan.s.water_xp11"));
  const relief = e.relief?.source;
  if (relief === "file") parts.push(t("plan.s.relief_file"));
  else if (relief === "copernicus") parts.push(t("plan.s.relief_cop30"));
  else if (relief === "usgs") parts.push(t("plan.s.relief_usgs"));
  else if (relief === "usgs1") parts.push(t("plan.s.relief_usgs1"));
  else if (relief === "canada") parts.push(t("plan.s.relief_canada"));
  else if (relief === "south_america") parts.push(t("plan.s.relief_anadem"));
  else parts.push(t("plan.s.relief_auto"));
  if (relief !== "file" && String(e.relief?.folder || "").trim()) parts.push(t("plan.s.relief_own"));
  parts.push(e.overlays === "none" ? t("plan.s.overlays_none") : t("plan.s.overlays_xplane"));
  return parts;
}

// ---------------------------------------------------------------- for experts

/** The one expert field that holds a folder: it gets a placeholder and the folder dialog. */
const PATCHES_DIR = "expert.patches_dir";

/** The folder a build reads when the patches field is left empty: ``<home>/patches``. */
function patchesDefault(view) {
  const home = view.home;
  if (!home) return "";
  return `${home}${home.includes("\\") ? "\\" : "/"}patches`;
}

/** The expert control over the fade in three steps (``coast_transition`` profile and widths). */
const THREE_STEPS = "coast.three_steps";

export const EXPERT_GROUPS = [
  { id: "airports", title: () => t("settings.x.group_airports"), fields: ["essential.airports.zoom_level", "essential.airports.extent_km"] },
  { id: "coast", title: () => t("settings.x.group_coast"), fields: [THREE_STEPS, "expert.mask_zl", "expert.distance_masks_too", "expert.sea_texture_blur", "advanced.overlay_lod_km"] },
  { id: "water", title: () => t("settings.x.group_water"), fields: ["advanced.max_area", "advanced.min_area", "advanced.use_masks_for_inland", "advanced.water_smoothing", "expert.water_simplification", "advanced.sea_smoothing_mode"] },
  {
    id: "terrain",
    title: () => t("settings.x.group_terrain"),
    fields: ["advanced.curvature_tol", "advanced.limit_tris", "expert.min_angle", "expert.apt_curv_tol", "expert.apt_curv_ext", "expert.coast_curv_tol", "expert.coast_curv_ext", "advanced.apt_smoothing_pix", "expert.patches_dir"],
  },
  { id: "roads", title: () => t("settings.x.group_roads"), fields: ["advanced.road_level", "expert.road_banking_limit", "expert.lane_width", "expert.max_levelled_segs"] },
  { id: "look", title: () => t("settings.x.group_look"), fields: ["advanced.terrain_casts_shadows", "expert.normal_map_strength", "expert.use_decal_on_terrain", "expert.decal_on_sea", "expert.photo_brightness", "expert.photo_contrast", "expert.photo_saturation"] },
  { id: "objects", title: () => t("settings.x.group_objects"), fields: ["expert.ovl_exclude_pol", "expert.ovl_exclude_net"] },
  // Nothing to do with the tiles: what the app itself does, which is why it has a group of its own.
  { id: "app", title: () => t("settings.x.group_app"), fields: ["expert.check_updates"] },
];

/** Settings OrthoStudio XP no longer offers (the study's "Removed / automatic"): shown only when a saved
 * value differs from the default, so that nothing set by hand is hidden. */
export const RETIRED = ["advanced.ratio_bathy", "advanced.imprint_masks_to_dds", "expert.mesh_zl", "expert.masks_custom_extent", "expert.masks_use_dem_too"];

const FIELD_TEXT = {
  "expert.check_updates": [() => t("settings.x.check_updates"), () => t("settings.x.check_updates_hint")],
  [THREE_STEPS]: [() => t("settings.x.three_steps"), () => t("settings.x.three_steps_hint")],
  "essential.airports.zoom_level": [() => t("settings.x.airports_zl"), () => t("settings.x.airports_zl_hint")],
  "essential.airports.extent_km": [() => t("settings.x.airports_extent"), () => t("settings.x.airports_extent_hint")],
  "expert.mask_zl": [() => t("settings.x.mask_zl"), () => t("settings.x.mask_zl_hint")],
  "expert.distance_masks_too": [() => t("settings.x.shallow"), () => t("settings.x.shallow_hint")],
  "expert.sea_texture_blur": [() => t("settings.x.sea_blur"), () => t("settings.x.sea_blur_hint")],
  "advanced.overlay_lod_km": [() => t("settings.x.overlay_lod"), () => t("settings.x.overlay_lod_hint")],
  "advanced.max_area": [() => t("settings.x.max_area"), () => t("settings.x.max_area_hint")],
  "advanced.min_area": [() => t("settings.x.min_area"), () => t("settings.x.min_area_hint")],
  "advanced.use_masks_for_inland": [() => t("settings.x.inland_masks"), () => t("settings.x.inland_masks_hint")],
  "advanced.water_smoothing": [() => t("settings.x.water_smoothing"), () => t("settings.x.water_smoothing_hint")],
  "expert.water_simplification": [() => t("settings.x.water_simplification"), () => t("settings.x.water_simplification_hint")],
  "advanced.sea_smoothing_mode": [() => t("settings.x.sea_level"), () => t("settings.x.sea_level_hint")],
  "advanced.curvature_tol": [() => t("settings.x.curvature"), () => t("settings.x.curvature_hint")],
  "advanced.limit_tris": [() => t("settings.x.limit_tris"), () => t("settings.x.limit_tris_hint")],
  "expert.min_angle": [() => t("settings.x.min_angle"), () => t("settings.x.min_angle_hint")],
  "expert.apt_curv_tol": [() => t("settings.x.apt_curv"), () => t("settings.x.apt_curv_hint")],
  "expert.apt_curv_ext": [() => t("settings.x.apt_curv_ext"), () => t("settings.x.apt_curv_ext_hint")],
  "expert.coast_curv_tol": [() => t("settings.x.coast_curv"), () => t("settings.x.coast_curv_hint")],
  "expert.coast_curv_ext": [() => t("settings.x.coast_curv_ext"), () => t("settings.x.coast_curv_ext_hint")],
  "advanced.apt_smoothing_pix": [() => t("settings.x.apt_smoothing"), () => t("settings.x.apt_smoothing_hint")],
  "expert.masks_use_dem_too": [() => t("settings.x.masks_dem"), () => t("settings.x.masks_dem_hint")],
  "advanced.road_level": [() => t("settings.x.road_level"), () => t("settings.x.road_level_hint")],
  "expert.road_banking_limit": [() => t("settings.x.banking"), () => t("settings.x.banking_hint")],
  "expert.lane_width": [() => t("settings.x.lane_width"), () => t("settings.x.lane_width_hint")],
  "expert.max_levelled_segs": [() => t("settings.x.max_segs"), () => t("settings.x.max_segs_hint")],
  "advanced.terrain_casts_shadows": [() => t("settings.x.shadows"), () => t("settings.x.shadows_hint")],
  "expert.normal_map_strength": [() => t("settings.x.normals"), () => t("settings.x.normals_hint")],
  "expert.use_decal_on_terrain": [() => t("settings.x.decal"), () => t("settings.x.decal_hint")],
  "expert.decal_on_sea": [() => t("settings.x.decal_sea"), () => t("settings.x.decal_sea_hint")],
  "expert.photo_brightness": [() => t("settings.x.photo_brightness"), () => t("settings.x.photo_brightness_hint")],
  "expert.photo_contrast": [() => t("settings.x.photo_contrast"), () => t("settings.x.photo_contrast_hint")],
  "expert.photo_saturation": [() => t("settings.x.photo_saturation"), () => t("settings.x.photo_saturation_hint")],
  "expert.patches_dir": [() => t("settings.x.patches"), () => t("settings.x.patches_hint")],
  "expert.ovl_exclude_pol": [() => t("settings.x.exclude_pol"), () => t("settings.x.exclude_pol_hint")],
  "expert.ovl_exclude_net": [() => t("settings.x.exclude_net"), () => t("settings.x.exclude_net_hint")],
  "advanced.ratio_bathy": [() => t("settings.x.ratio_bathy"), () => t("settings.x.ratio_bathy_hint")],
  "advanced.imprint_masks_to_dds": [() => t("settings.x.imprint"), () => t("settings.x.imprint_hint")],
  "expert.mesh_zl": [() => t("settings.x.mesh_zl"), () => t("settings.x.mesh_zl_hint")],
  "expert.masks_custom_extent": [() => t("settings.x.custom_extent"), () => t("settings.x.custom_extent_hint")],
};

const OPTION_TEXT = {
  "advanced.road_level": {
    0: () => t("settings.x.road_0"),
    1: () => t("settings.x.road_1"),
    2: () => t("settings.x.road_2"),
    3: () => t("settings.x.road_3"),
    4: () => t("settings.x.road_4"),
    5: () => t("settings.x.road_5"),
  },
  "advanced.sea_smoothing_mode": {
    zero: () => t("settings.x.sea_zero"),
    mean: () => t("settings.x.sea_mean"),
    none: () => t("settings.x.sea_none"),
  },
  "expert.mask_zl": {
    14: () => t("settings.x.mask_14"),
    15: () => t("settings.x.mask_15"),
    16: () => t("settings.x.mask_16"),
  },
};

/** Units shown in plain words where the schema's are a code (``M``) or an English word. */
/** The values a number may take and the one it has unless changed, said first and in bold: a
 * user wanted to see where the recommended value lies before moving it, above all among the
 * expert ones (2026-09-25). Only the bounds the schema holds, since they are what the engine
 * checks: a number it does not bound says its default alone. */
export function rangeText(prop, unit = "") {
  const known = (n) => typeof n === "number" && Number.isFinite(n);
  const num = (n) => (unit === "ZL" ? `ZL${n}` : fmtNum(n, 3));
  const u = !unit || unit === "ZL" ? "" : unit === "°" ? unit : ` ${unit}`;
  let range = "";
  if (known(prop.minimum) && known(prop.maximum)) range = t("settings.x.range_between", { min: num(prop.minimum), max: num(prop.maximum) + u });
  else if (known(prop.minimum)) range = t("settings.x.range_at_least", { min: num(prop.minimum) + u });
  else if (known(prop.exclusiveMinimum)) range = t("settings.x.range_above", { min: num(prop.exclusiveMinimum) + u });
  const byDefault = known(prop.default) ? t("settings.x.range_default", { value: num(prop.default) + u }) : "";
  const said = [range, byDefault].filter(Boolean).join(", ");
  return said ? `${said.charAt(0).toUpperCase()}${said.slice(1)}.` : "";
}

const UNIT_TEXT = {
  "advanced.limit_tris": () => t("settings.x.unit_million"),
  "advanced.water_smoothing": () => t("settings.x.unit_passes"),
  "expert.max_levelled_segs": () => t("settings.x.unit_points"),
  "expert.sea_texture_blur": () => "px",
};

export function fieldLabel(path) {
  return FIELD_TEXT[path] ? FIELD_TEXT[path][0]() : path;
}

export function fieldHint(path) {
  return FIELD_TEXT[path] ? FIELD_TEXT[path][1]() : "";
}

/** Every path of the settings the screen offers somewhere: a question, an expert field, or the
 * retired list (a test holds it equal to the schema's leaves). */
export function coveredPaths() {
  const paths = new Set(Object.values(QUESTION_PATHS).flat());
  for (const group of EXPERT_GROUPS) for (const f of group.fields) if (f !== THREE_STEPS) paths.add(f);
  for (const p of RETIRED) paths.add(p);
  return [...paths].sort();
}

/** The retired settings whose value is not the default. */
export function retiredShown(settings, schema) {
  const leaves = schemaLeaves(schema);
  return RETIRED.filter((path) => leaves[path] && !sameValue(getPath(settings, path), leaves[path].default));
}

export function threeStepsText(settings) {
  const ct = getPath(settings, "essential.coast_transition") || {};
  return ct.profile === "3steps" && Array.isArray(ct.width_m) ? ct.width_m.join(", ") : "";
}

/** The three widths of an expert ("100, 200, 100") onto the draft. Empty text: back to the
 * questions' fade. False, and nothing changed, when the text is not three widths in metres. */
export function applyThreeSteps(draft, text) {
  const parts = String(text ?? "").split(/[\s,;]+/).filter(Boolean);
  const ct = getPath(draft, "essential.coast_transition");
  if (!ct) return false;
  if (!parts.length) {
    if (ct.profile === "3steps") {
      ct.profile = "sand";
      ct.width_m = 100;
    }
    return true;
  }
  const widths = parts.map(Number);
  if (widths.length !== 3 || widths.some((w) => !Number.isFinite(w) || w < 0)) return false;
  ct.profile = "3steps";
  ct.width_m = widths;
  return true;
}

/** A list setting typed by an expert ("0, .for"): numbers stay numbers. */
export function parseList(text) {
  return String(text ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => (/^-?\d+$/.test(x) ? Number(x) : x));
}

// ---------------------------------------------------------------- the DOM

/**
 * Draw the screen's three parts from ``view``: ``{dom: {h, clear}, draft, schema, providers,
 * xplane: {path, detected} | null, dataDir: {path, chosen, present} | null, home, platform,
 * reveal, chooseFolder, changed()}``. Every answer goes into ``draft`` at once and
 * calls ``changed()``, which draws again; the focus, the caret and the scroll position survive
 * (data-focus-key, keptScroll).
 */
/** Case and accents out of the way, so "rivieres" finds "rivières" and "EAU" finds "eau". */
function fold(text) {
  return String(text ?? "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

/** The words of a search. Several words all have to be found, in any order.
 *
 * A plural loses its s, so "rivers" finds "Simplify lake and river outlines" and "rivieres"
 * finds "rivière". Short words keep theirs: "gps" is not "gp".
 */
export function searchWords(query) {
  return fold(query)
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => (w.length > 3 && w.endsWith("s") ? w.slice(0, -1) : w));
}

export function matchesSearch(text, words) {
  const hay = fold(text);
  return words.every((w) => hay.includes(w));
}

export function renderSettingsView(parts, view) {
  const saved = captureFocus(parts.root);
  const scroll = keptScroll();
  // Drawn apart, then only what differs is put in (`view.dom.morph`): drawn anew at each change,
  // the whole screen flashed in the Mac's window, and a number field was replaced under its own
  // arrows (a user, 2026-09-21). What has not changed stays as it is, whatever the change.
  const morph = view.dom.morph;
  const twin = (box) => (morph ? box.cloneNode(false) : box);
  const drawn = { presets: twin(parts.presets), questions: twin(parts.questions), experts: twin(parts.experts) };
  renderPresets(drawn.presets, view);
  renderQuestions(drawn.questions, view);
  renderExperts(drawn.experts, view);
  searchFilter(drawn, searchWords(view.search || "")); // compared as they will be shown
  if (morph) for (const part of ["presets", "questions", "experts"]) morph(parts[part], drawn[part]);
  countExperts(parts.experts);
  applySearch(parts, view);
  restoreFocus(parts.root, saved);
  scroll?.restore();
}

/**
 * Keep only the questions and the expert settings the search names, and say how many are left.
 *
 * The questions are laid out in two CSS columns, so the tenth of them sits halfway down the
 * right-hand one and reading the page from the top never reaches it; the window, unlike a
 * browser, has no Find to jump there (a user looked for "How much of the photo on lakes and
 * rivers?" and did not find it, 2026-09-20). What is hidden leaves the column flow, so the
 * matches gather at the top. Everything the box shows is searched, its Ortho4XP name included:
 * typing `min_area` finds "Smallest pond drawn as water".
 */
let expertsWereOpen;

/** Hide what the search leaves out, and count what it keeps. Set only where it changes, so that
 * a part drawn the same stays untouched. */
function searchFilter(parts, words) {
  const searching = words.length > 0;
  let shown = 0;
  let total = 0;
  for (const box of [...parts.questions.querySelectorAll(".question"), ...parts.experts.querySelectorAll(".gen-field")]) {
    total += 1;
    const hit = !searching || matchesSearch(box.textContent, words);
    if (box.hidden !== !hit) box.hidden = !hit;
    if (hit) shown += 1;
  }
  // A group of expert settings with nothing left in it takes its title away too.
  for (const group of parts.experts.querySelectorAll(".expert-group")) {
    const empty = searching && ![...group.querySelectorAll(".gen-field")].some((f) => !f.hidden);
    if (group.hidden !== empty) group.hidden = empty;
  }
  return { shown, total };
}

export function applySearch(parts, view) {
  const words = searchWords(view.search || "");
  const searching = words.length > 0;
  const { shown, total } = searchFilter(parts, words);
  // The presets answer several questions at once: they are not a setting anyone searches for.
  if (parts.presets && parts.presets.hidden !== searching) parts.presets.hidden = searching;
  // A match under the band is no use behind it: a search opens it, and closing the search puts
  // the band back the way the user had it.
  const experts = parts.experts.closest("details");
  if (experts) {
    if (searching && expertsWereOpen === undefined) expertsWereOpen = experts.open;
    if (searching && !experts.open) experts.open = true;
    else if (expertsWereOpen !== undefined) {
      experts.open = expertsWereOpen;
      expertsWereOpen = undefined;
    }
  }
  if (!parts.note) return;
  // written only where it changes, like the rest of the screen (app.js morphChildren)
  const note = !searching ? "" : shown ? t("settings.search_found", { n: shown, total }) : t("settings.search_none");
  if (parts.note.hidden !== !searching) parts.note.hidden = !searching;
  if (parts.note.classList.contains("is-warn") !== (searching && shown === 0)) parts.note.classList.toggle("is-warn");
  if (parts.note.textContent !== note) parts.note.textContent = note;
}

/**
 * The window's scroll position, to put back once the screen is drawn again. Removing the answer
 * just clicked, which has the focus, makes the browser lay out the page while it is half drawn:
 * the page, shorter for an instant, scrolled up, and a user who changed an answer at the bottom
 * of Settings found the top of the screen each time (2026-09-14).
 */
export function keptScroll() {
  if (typeof window === "undefined") return null;
  const x = window.scrollX;
  const y = window.scrollY;
  return {
    restore: () => {
      if (window.scrollX !== x || window.scrollY !== y) window.scrollTo(x, y);
    },
  };
}

function captureFocus(root) {
  const el = typeof document !== "undefined" ? document.activeElement : null;
  if (!root || !el || !root.contains(el) || !el.dataset?.focusKey) return null;
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

function restoreFocus(root, saved) {
  if (!root || !saved) return;
  const el = root.querySelector(`[data-focus-key="${CSS.escape(saved.key)}"]`);
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

function renderPresets(box, view) {
  const { h, clear } = view.dom;
  clear(box);
  const current = matchingPreset(view.draft);
  const buttons = PRESETS.map((p) => h("button", {
    type: "button",
    class: "preset",
    "aria-pressed": String(current === p.id),
    dataset: { focusKey: `preset:${p.id}` },
    onclick: () => {
      applyPreset(view.draft, p.id);
      view.changed(t("settings.preset.applied", { name: p.name() }));
    },
  }, h("span", { class: "preset-name" }, p.name()), h("span", { class: "preset-text" }, p.text())));
  const match = PRESETS.find((p) => p.id === current);
  box.append(
    h("p", { class: "presets-title" }, t("settings.presets")),
    h("div", { class: "preset-list" }, buttons),
    h("p", { class: "help presets-status" }, match ? t("settings.preset.matches", { name: match.name() }) : t("settings.preset.custom")),
  );
}

function questionBox(view, id, ...body) {
  const { h } = view.dom;
  const [title, help] = QUESTION_TEXT[id];
  return h("fieldset", { class: "question", id: `q-${id}` },
    h("legend", { class: "question-title" }, title()),
    h("p", { class: "question-help" }, help()),
    ...body);
}

function radios(view, id, name, current) {
  const { h } = view.dom;
  const choices = questionChoices(id, view.draft, {
    providers: view.providers,
    ownScenery: view.xplane?.packs_of_their_own || [],
  });
  return h("div", { class: "choices" }, choices.map((c) => {
    const input = h("input", {
      type: "radio",
      name,
      value: String(c.value),
      disabled: c.disabled,
      dataset: { focusKey: `${name}:${c.value}` },
    });
    input.checked = sameValue(c.value, current);
    input.addEventListener("change", () => {
      if (!input.checked) return;
      answer(view.draft, id, c.value);
      view.changed();
    });
    return h("label", { class: `choice${c.disabled ? " is-disabled" : ""}` },
      input,
      h("span", { class: "choice-text" },
        h("span", { class: "choice-label" }, c.label, c.recommended ? h("span", { class: "choice-tag" }, t("settings.recommended")) : null),
        c.note ? h("span", { class: "choice-note" }, c.note) : null));
  }));
}

function renderQuestions(box, view) {
  const { h, clear } = view.dom;
  const d = view.draft;
  clear(box);

  // First what OrthoStudio XP needs to know of the user's X-Plane: where it is, and whether another pack
  // (simHeaven X-World) brings the roads, forests and buildings. Then what the tiles look like.
  const xp = view.xplane;
  const found = Boolean(xp && xp.detected && xp.path);
  // The status can come after the screen (app.js boot): until it does, nothing is "not detected".
  let detected = t("settings.q.xplane_looking");
  if (found) detected = t("settings.q.xplane_detected", { path: homely(xp.path) });
  else if (xp) detected = t("settings.q.xplane_not_detected");
  // "empty = the detected folder" only when there is one.
  const folder = h("input", { type: "text", id: "q-xplane-dir", class: "question-path", spellcheck: "false", autocomplete: "off", placeholder: found ? t("settings.q.xplane_placeholder") : "", dataset: { focusKey: "q:xplane" } });
  folder.value = getPath(d, "essential.xplane_dir") || "";
  folder.addEventListener("input", () => setPath(d, "essential.xplane_dir", folder.value.trim() || null));
  folder.addEventListener("change", () => view.changed());
  // A button instead of typing the path (user request, 2026-09-15): the Finder's, the File
  // Explorer's or the Linux file manager's own dialog, opened by the engine on this computer.
  const choose = view.chooseFolder
    ? h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: "q:xplane-choose" }, onclick: async () => {
        const path = await view.chooseFolder(t("settings.q.xplane_prompt"), folder.value.trim() || xp?.path || null);
        if (!path) return;
        folder.value = path;
        setPath(d, "essential.xplane_dir", path);
        view.changed();
      } }, t("settings.q.xplane_choose"))
    : null;
  const showXp = view.reveal && xp && xp.detected && xp.path
    ? h("button", { type: "button", class: "btn btn-small reveal-btn", dataset: { focusKey: "q:xplane-reveal" }, onclick: () => view.reveal.open(xp.path) }, view.reveal.label)
    : null;
  // More than one X-Plane 12 on the machine: a user installed a tile into the one he had
  // forgotten, and found nothing in the Custom Scenery of the one he flies (2026-09-17).
  const others = found && xp.others && xp.others.length
    ? h("p", { class: "question-help" }, t("settings.q.xplane_others", { paths: xp.others.join(", ") }))
    : null;
  box.append(questionBox(view, "xplane",
    h("p", { class: "question-detected" }, detected, showXp ? [" ", showXp] : null),
    others,
    h("div", { class: "sub-question" }, h("label", { class: "sub-question-title", for: "q-xplane-dir" }, t("settings.q.xplane_other")),
      h("div", { class: "path-row" }, folder, choose))));
  box.append(dataQuestion(view));
  // it asks for a SimBrief name and its help points at a Plan button this release does
  // not have (found in review, 2026-09-23)
  if (FLIGHT_PLAN) box.append(simbriefQuestion(view));
  box.append(questionBox(view, "overlays", radios(view, "overlays", "q-overlays", getPath(d, "essential.overlays"))));

  const providerChoices = questionChoices("provider", d, { providers: view.providers });
  const select = h("select", { class: "question-select", "aria-label": t("settings.q.provider"), dataset: { focusKey: "q:provider" } },
    providerChoices.map((c) => h("option", { value: c.value, disabled: c.disabled }, c.recommended ? `${c.label} · ${t("settings.recommended")}` : c.label)));
  select.value = getPath(d, "essential.provider");
  select.addEventListener("change", () => {
    answer(d, "provider", select.value);
    view.changed();
  });
  box.append(questionBox(view, "provider", select));

  box.append(questionBox(view, "detail", radios(view, "detail", "q-detail", getPath(d, "essential.zoom_level"))));
  box.append(questionBox(view, "airports", radios(view, "airports", "q-airports", getPath(d, "essential.airports.mode"))));
  box.append(questionBox(view, "coast", radios(view, "coast", "q-coast", getPath(d, "essential.coast_transition.profile"))));
  if (questionShown("width", d)) {
    box.append(questionBox(view, "width", radios(view, "width", "q-width", getPath(d, "essential.coast_transition.width_m"))));
  }
  box.append(questionBox(view, "water", radios(view, "water", "q-water", getPath(d, "essential.water_rendering"))));
  box.append(questionBox(view, "lakes", radios(view, "lakes", "q-lakes", getPath(d, "advanced.ratio_water_pct"))));

  const relief = [radios(view, "relief", "q-relief", getPath(d, "essential.relief.source"))];
  if (getPath(d, "essential.relief.source") === "file") {
    const path = h("input", { type: "text", id: "q-relief-file", class: "question-path", spellcheck: "false", autocomplete: "off", placeholder: "/…/elevation.tif", dataset: { focusKey: "q:relief-file" } });
    path.value = getPath(d, "essential.relief.file") || "";
    path.addEventListener("input", () => setPath(d, "essential.relief.file", path.value));
    path.addEventListener("change", () => view.changed());
    const missing = !String(getPath(d, "essential.relief.file") || "").trim();
    relief.push(
      h("div", { class: "sub-question" },
        h("label", { class: "sub-question-title", for: "q-relief-file" }, t("settings.q.relief_path")),
        path,
        h("p", { class: `question-help${missing ? " is-warn" : ""}` }, missing ? t("settings.q.relief_path_missing") : t("settings.q.relief_path_help"))),
      h("fieldset", { class: "sub-question" },
        h("legend", { class: "sub-question-title" }, t("settings.q.holes")),
        h("p", { class: "question-help" }, t("settings.q.holes_help")),
        radios(view, "holes", "q-holes", getPath(d, "essential.relief.fill_nodata"))),
    );
  }
  box.append(questionBox(view, "relief", ...relief));
  box.append(ownFolderQuestion(view));

  // The colours of the photo: the choice in plain words, and the three numbers right here when
  // the pilot asks for their own, so nobody has to go hunting under For experts (2026-09-18).
  const colours = [radios(view, "colours", "q-colours", getPath(d, "essential.photo_look"))];
  if (getPath(d, "essential.photo_look") === "custom") {
    colours.push(h("div", { class: "sub-question colour-values" },
      PHOTO_VALUES.map(([path, label]) => colourNumber(view, path, label()))));
  }
  const sample = view.photoSample ? view.photoSample(getPath(d, "essential.provider")) : null;
  const preview = colourPreview(view.dom.h, sample, photoValues(getPath(d, "essential.photo_look"), {
    brightness: getPath(d, "expert.photo_brightness"),
    contrast: getPath(d, "expert.photo_contrast"),
    saturation: getPath(d, "expert.photo_saturation"),
  }));
  if (preview) colours.push(h("div", { class: "sub-question" }, preview));
  box.append(questionBox(view, "colours", ...colours));
}

/** The three colour numbers of the 'my own values' answer, in the order they are applied. */
const PHOTO_VALUES = [
  ["expert.photo_brightness", () => t("settings.x.photo_brightness")],
  ["expert.photo_contrast", () => t("settings.x.photo_contrast")],
  ["expert.photo_saturation", () => t("settings.x.photo_saturation")],
];

/** One colour number as a slider with its value beside it (-0.5 .. 0.5, saturation from -1). */
function colourNumber(view, path, label) {
  const { h } = view.dom;
  const d = view.draft;
  const id = `q-${path.replace(/\W+/g, "-")}`;
  const min = path.endsWith("saturation") ? -1 : -0.5;
  const shown = h("output", { class: "colour-value", for: id });
  const slider = h("input", { type: "range", id, min: String(min), max: "0.5", step: "0.05",
    dataset: { focusKey: `q:${path}` } });
  const show = () => { shown.textContent = fmtNum(Number(slider.value), 2); };
  slider.value = String(getPath(d, path) ?? 0);
  show();
  slider.addEventListener("input", () => {
    setPath(d, path, Number(slider.value));
    show();
  });
  slider.addEventListener("change", () => view.changed());
  return h("div", { class: "colour-row" },
    h("label", { class: "sub-question-title", for: id }, label), slider, shown);
}

/**
 * Where the tiles and the downloaded imagery go: on an external disk, for instance (user request,
 * 2026-09-15). The engine checks the folder chosen when Settings are saved: found, writable, on a
 * disk that links files; nothing already downloaded is moved.
 */
function dataQuestion(view) {
  const { h } = view.dom;
  const d = view.draft;
  const now = dataFolderLine(view.dataDir);
  const field = h("input", { type: "text", id: "q-data-dir", class: "question-path", spellcheck: "false", autocomplete: "off", placeholder: t("settings.q.data_placeholder", { path: homely(view.home) || "~/.orthostudio" }), dataset: { focusKey: "q:data" } });
  field.value = getPath(d, "essential.data_dir") || "";
  field.addEventListener("input", () => setPath(d, "essential.data_dir", field.value.trim() || null));
  field.addEventListener("change", () => view.changed());
  const choose = view.chooseFolder
    ? h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: "q:data-choose" }, onclick: async () => {
        const path = await view.chooseFolder(t("settings.q.data_prompt"), field.value.trim() || view.dataDir?.path || null);
        if (!path) return;
        field.value = path;
        setPath(d, "essential.data_dir", path);
        view.changed();
      } }, t("settings.q.data_choose"))
    : null;
  const show = view.reveal && now && !now.warn
    ? h("button", { type: "button", class: "btn btn-small reveal-btn", dataset: { focusKey: "q:data-reveal" }, onclick: () => view.reveal.open(view.dataDir.path) }, view.reveal.label)
    : null;
  return questionBox(view, "data",
    now ? h("p", { class: `question-detected${now.warn ? " is-warn" : ""}` }, now.text, show ? [" ", show] : null) : null,
    h("div", { class: "sub-question" }, h("label", { class: "sub-question-title", for: "q-data-dir" }, t("settings.q.data_other")),
      h("div", { class: "path-row" }, field, choose)),
    h("p", { class: "question-help" }, t("settings.q.data_kept")),
    h("p", { class: "question-help" }, t("settings.q.data_disk", { formats: dataFormats(view.platform) })),
    // A user typed his Custom Scenery there and was refused once he had saved (2026-09-17).
    h("p", { class: "question-help" }, t("settings.q.data_outside")));
}

/**
 * A folder of the user's own elevation files, laid over the relief chosen above (2026-09-19).
 *
 * A user of the X-Plane.Org page has the lidar models of Europe by the hundred, one file per
 * square, and asked to name the folder once instead of a file per tile. Where the folder has
 * nothing, the relief above answers, so a partial set is safe.
 */
function ownFolderQuestion(view) {
  const { h } = view.dom;
  const d = view.draft;
  const field = h("input", { type: "text", id: "q-relief-folder", class: "question-path", spellcheck: "false", autocomplete: "off", placeholder: t("settings.q.own_placeholder"), dataset: { focusKey: "q:relief-folder" } });
  field.value = getPath(d, "essential.relief.folder") || "";
  field.addEventListener("input", () => setPath(d, "essential.relief.folder", field.value.trim()));
  field.addEventListener("change", () => view.changed());
  const choose = view.chooseFolder
    ? h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: "q:relief-folder-choose" }, onclick: async () => {
        const path = await view.chooseFolder(t("settings.q.own_prompt"), field.value.trim() || null);
        if (!path) return;
        field.value = path;
        setPath(d, "essential.relief.folder", path);
        view.changed();
      } }, t("settings.q.own_choose"))
    : null;
  return questionBox(view, "own",
    h("div", { class: "sub-question" },
      h("label", { class: "sub-question-title", for: "q-relief-folder" }, t("settings.q.own_folder")),
      h("div", { class: "path-row" }, field, choose)),
    h("p", { class: "question-help" }, t("settings.q.own_names")),
    h("p", { class: "question-help" }, t("settings.q.own_rest")));
}

/**
 * The SimBrief name, for the flight plan the Plan draws (2026-09-19). A name, never a password:
 * their interface serves the last plan of a user to whoever asks for it. The help says where the
 * name goes, since it is the only thing of the user that leaves this computer.
 */
function simbriefQuestion(view) {
  const { h } = view.dom;
  const d = view.draft;
  const field = h("input", { type: "text", id: "q-simbrief", class: "question-path", spellcheck: "false", autocomplete: "off", maxlength: "64", placeholder: t("settings.q.simbrief_placeholder"), dataset: { focusKey: "q:simbrief" } });
  field.value = getPath(d, "essential.simbrief_user") || "";
  field.addEventListener("input", () => setPath(d, "essential.simbrief_user", field.value.trim() || null));
  field.addEventListener("change", () => view.changed());
  return questionBox(view, "simbrief",
    h("div", { class: "sub-question" },
      h("label", { class: "sub-question-title", for: "q-simbrief" }, t("settings.q.simbrief_name")),
      field),
    h("p", { class: "question-help" }, t("settings.q.simbrief_sent")));
}

function renderExperts(box, view) {
  const { h, clear } = view.dom;
  clear(box);
  const leaves = schemaLeaves(view.schema);
  const group = (title, fields, lead) => h("section", { class: "expert-group" },
    h("h3", { class: "expert-group-title" }, title),
    lead ? h("p", { class: "help" }, lead) : null,
    h("div", { class: "form-grid" }, fields.map((path) => expertField(view, path, leaves[path] || {}))));
  for (const g of EXPERT_GROUPS) box.append(group(g.title(), g.fields));
  const retired = retiredShown(view.draft, view.schema);
  if (retired.length) box.append(group(t("settings.x.group_retired"), retired, t("settings.x.retired_help")));
}

/** How many settings are behind the band: a user did not know there was anything there
 * (2026-09-18). Counted from what is shown, so it can never drift. */
function countExperts(box) {
  const count = box.querySelectorAll(".gen-field").length;
  const label = box.parentElement?.querySelector(".experts-count");
  const text = count ? t("settings.experts_count", { count }) : "";
  if (label && label.textContent !== text) label.textContent = text;
}

function expertField(view, path, prop) {
  const { h } = view.dom;
  const d = view.draft;
  const id = `x-${path.replace(/\W+/g, "-")}`;
  const key = { focusKey: `x:${path}` };
  let control;
  if (path === THREE_STEPS) {
    control = h("input", { type: "text", id, spellcheck: "false", autocomplete: "off", placeholder: "100, 200, 100", dataset: key });
    control.value = threeStepsText(d);
    control.addEventListener("change", () => {
      if (applyThreeSteps(d, control.value)) view.changed();
      else view.changed(t("settings.x.three_steps_invalid"), "fail");
    });
  } else if (Array.isArray(prop.enum)) {
    const names = OPTION_TEXT[path] || {};
    control = h("select", { id, dataset: key }, prop.enum.filter((v) => v !== null).map((v) => h("option", { value: String(v) }, names[v] ? names[v]() : String(v))));
    control.value = String(getPath(d, path));
    control.addEventListener("change", () => {
      const raw = control.value;
      setPath(d, path, typeof prop.enum[0] === "number" ? Number(raw) : raw);
      view.changed();
    });
  } else if (prop.type === "boolean") {
    control = h("input", { type: "checkbox", id, dataset: key });
    control.checked = Boolean(getPath(d, path));
    control.addEventListener("change", () => {
      setPath(d, path, control.checked);
      view.changed();
    });
  } else if (prop.type === "integer" || prop.type === "number") {
    const min = prop.minimum ?? prop.exclusiveMinimum;
    const max = prop.maximum ?? prop.exclusiveMaximum;
    control = h("input", { type: "number", id, min, max, step: prop.type === "integer" ? 1 : "any", inputmode: prop.type === "integer" ? "numeric" : "decimal", dataset: key });
    control.value = getPath(d, path) == null ? "" : String(getPath(d, path));
    control.addEventListener("change", () => {
      const n = Number(control.value);
      if (control.value === "" || !Number.isFinite(n)) {
        control.value = String(getPath(d, path));
        return;
      }
      setPath(d, path, n);
      view.changed();
    });
  } else if (prop.type === "array") {
    control = h("input", { type: "text", id, spellcheck: "false", autocomplete: "off", dataset: key });
    control.value = (getPath(d, path) || []).join(", ");
    control.addEventListener("change", () => {
      setPath(d, path, parseList(control.value));
      view.changed();
    });
  } else if (path === PATCHES_DIR) {
    control = h("input", { type: "text", id, spellcheck: "false", autocomplete: "off", placeholder: t("settings.x.patches_placeholder", { path: patchesDefault(view) }), dataset: key });
    control.value = getPath(d, path) ?? "";
    control.addEventListener("change", () => {
      setPath(d, path, control.value.trim());
      view.changed();
    });
  } else {
    control = h("input", { type: "text", id, spellcheck: "false", autocomplete: "off", dataset: key });
    control.value = getPath(d, path) ?? "";
    control.addEventListener("change", () => {
      setPath(d, path, control.value);
      view.changed();
    });
  }
  // The folder field gets the platform's own dialog, like the X-Plane and data folders: a user
  // did not know what to type in it (2026-09-17).
  const choose = path === PATCHES_DIR && view.chooseFolder
    ? h("button", { type: "button", class: "btn btn-small", dataset: { focusKey: `x:${path}-choose` }, onclick: async () => {
        const picked = await view.chooseFolder(t("settings.x.patches_prompt"), control.value.trim() || null);
        if (!picked) return;
        control.value = picked;
        setPath(d, path, picked);
        view.changed();
      } }, t("settings.x.patches_choose"))
    : null;
  const unit = UNIT_TEXT[path] ? UNIT_TEXT[path]() : prop.unit || "";
  // A check box's title names it without being tied to it: tied, a press on the title showed the
  // box pressed, a flash (a user, 2026-09-21). The box still reads the title as its name.
  const tied = control.type !== "checkbox";
  if (!tied) control.setAttribute("aria-labelledby", `${id}-title`);
  const labelRow = h("div", { class: "label-row" }, h("label", tied ? { for: id } : { id: `${id}-title` }, fieldLabel(path)));
  if (prop.ortho4xp) labelRow.append(h("span", { class: "badge-ortho4xp", title: t("settings.ortho4xp", { name: prop.ortho4xp }) }, prop.ortho4xp));
  const field = h("div", { class: choose ? "field gen-field field-span2" : "field gen-field" }, labelRow);
  if (control.type === "checkbox") field.append(h("label", { class: "switch", for: id }, control, h("span", { class: "help" }, t("settings.x.on"))));
  else if (unit && control.tagName === "INPUT") field.append(h("div", { class: "with-unit" }, control, h("span", { class: "unit" }, unit)));
  else if (choose) field.append(h("div", { class: "path-row" }, control, choose));
  else field.append(control);
  // what the folder holds, filled in place when the engine answers (app.js loadSettingsPatches)
  const found = path === PATCHES_DIR ? h("span", { class: "hint-found" }, patchesFoundText(view.patches)) : null;
  const range = control.type === "number" ? rangeText(prop, unit) : "";
  field.append(h("div", { class: "hint" }, range ? h("strong", { class: "hint-range" }, range) : null, range ? " " : null, fieldHint(path), found));
  return field;
}

/**
 * What the folder of patches shown holds, saved or not (GET /api/patches): the tiles it has
 * patches for, so that a pilot sees his are found before anything is built. A user took
 * "Patches: none" in a report for his patch not being found, when it was for another square
 * (2026-09-21). The first tiles are named, the rest counted.
 */
export function patchesFoundText(found, named = 6) {
  if (!found || !found.dir) return "";
  if (found.exists === false) return t("settings.x.patches_missing");
  const tiles = Object.keys(found.tiles || {});
  if (!tiles.length) return t("settings.x.patches_none");
  const first = tiles.slice(0, named).join(", ");
  const rest = tiles.length - named;
  const list = rest > 0 ? t("settings.x.patches_more", { tiles: first, n: fmtInt(rest) }) : first;
  return t("settings.x.patches_found", { n: fmtInt(tiles.length), tiles: list });
}
