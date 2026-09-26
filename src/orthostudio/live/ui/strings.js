// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's words, in English and French, and the few helpers of OrthoStudio XP's own
// i18n.js it needs (the same rules: English unless the switch chose French, and a key missing in
// French falls back to English, then to itself).

export const STRINGS = {
  en: {
    "app.language": "Language",

    "state.idle": "Ready",
    "state.idle_lead":
      "Start Live before X-Plane, then fly: at each start, X-Plane waits on its loading screen until the ortho around the airport is built.",
    "state.blocked": "Not ready",
    "state.starting": "Starting…",
    "state.starting_region": "Setting up the region, once: about two minutes.",
    "state.starting_install": "Putting the pack and the plugin in X-Plane.",
    "state.waiting": "Waiting for X-Plane",
    "state.waiting_lead": "Start X-Plane and choose a flight.",
    "state.held": "X-Plane waits for the ortho around {name}",
    "state.held_lead": "X-Plane stays on its loading screen until everything it will show there is built.",
    "state.building_start": "Building the ortho around {name}",
    "state.building_start_lead": "X-Plane is about to load this flight.",
    "state.start_ready": "The ortho around {name} is ready",
    "state.start_ready_lead": "X-Plane shows it as soon as it gets there.",
    "state.following": "Following the aircraft",
    "state.following_lead": "The ortho ahead of the aircraft and around the camera is built as it flies.",
    "state.loading": "X-Plane is loading",
    "state.loading_lead": "It stopped sending its position: a flight loads, or it is starting.",
    "state.xplane_gone": "X-Plane has quit",
    "state.xplane_gone_lead": "Start it again, or stop Live.",
    "state.stopping": "Stopping…",
    "state.leaving": "Waiting for X-Plane to quit",
    "state.leaving_lead":
      "X-Plane reads its textures from Live's pack: the pack comes out of X-Plane once X-Plane has quit.",
    "state.error": "Live stopped: {error}",
    "hold.text": "{left} textures to build · {seconds} s · {mbs} MB/s",

    "action.start": "Start",
    "action.stop": "Stop",
    "action.leave": "Leave it installed",
    "action.uninstall": "Take the pack out of X-Plane",
    "action.fresh": "From scratch",
    "action.fresh_help": "Forgets every texture built and all imagery downloaded, as the very first time.",

    "flight.title": "Flight",
    "flight.xplane": "X-Plane",
    "flight.data_none": "no position yet",
    "flight.data_fresh": "sends its position",
    "flight.data_stale": "silent",
    "flight.position": "Position",
    "flight.altitude": "Altitude",
    "flight.speed": "Ground speed",
    "flight.camera": "Camera",
    "flight.camera_value": "{km} km from the aircraft",
    "flight.start": "Start",
    "flight.none": "Nothing streams.",
    "flight.xplane_running": "running",
    "flight.xplane_stopped": "not running",

    "build.title": "Textures",
    "build.queued": "To build",
    "build.urgent": "{n} urgent",
    "build.building": "Being built",
    "build.building_value": "{n} textures, {pieces} pieces awaited",
    "build.built": "Built since the start",
    "build.downloaded": "Downloaded",
    "build.downloaded_value": "{n} pieces ({mb} MB), {cache} from the cache",
    "build.failed": "{n} failed",
    "build.rate": "Rate",
    "build.disk": "Disk",
    "build.disk_value": "{used} of {budget} GB",
    "build.none": "none",
    "build.idle": "Shown while Live runs.",

    "checks.title": "Checks",
    "checks.macos_no": "Live runs on macOS only for now: its pack relies on APFS clones.",
    "checks.xplane": "X-Plane 12: {path}",
    "checks.xplane_missing": "X-Plane 12 not found: {problem}",
    "checks.xplane_running": "X-Plane is running",
    "checks.xplane_running_late":
      "X-Plane is running: quit it, start Live, then X-Plane (it looks at its scenery only when it starts).",
    "checks.xplane_stopped": "X-Plane is not running",
    "checks.mesh": "AutoOrtho's Europe mesh: {path}",
    "checks.mesh_missing": "AutoOrtho's Europe mesh is missing ({path}): Live streams onto it for now.",
    "checks.region": "Region set up",
    "checks.region_todo": "The region is set up at the first start (about two minutes)",
    "checks.ao": "AutoOrtho is not running",
    "checks.ao_mounted": "AutoOrtho is running: quit it first, it serves the same tiles.",
    "checks.plugin": "X-Plane plugin ready: X-Plane waits for the ortho at each start",
    "checks.plugin_build": "The X-Plane plugin is built at the first start (Xcode's command-line tools)",
    "checks.plugin_missing": "No X-Plane plugin: X-Plane will not wait for the ortho at a start",
    "checks.left_installed":
      "Live's pack is still in X-Plane: take it out, or X-Plane shows neutral textures wherever nothing was built.",
    "checks.folder": "Live's folder: {path}",
    "checks.folder_problem": "Live's folder: {problem}",

    "settings.title": "Settings",
    "settings.lead": "They take effect at the next start.",
    "settings.disk": "Disk for the textures",
    "settings.disk_help": "Beyond it, the textures farthest from the aircraft are made coarser.",
    "settings.cache": "Disk for the downloaded imagery",
    "settings.cache_help": "Kept to build again without downloading; beyond it, the oldest goes.",
    "settings.reach": "Sharp this far for outside views",
    "settings.reach_help": "How far an outside view (Shift+4) may take the camera from the aircraft.",
    "settings.quality": "Texture quality",
    "settings.quality_auto": "As X-Plane is set",
    "settings.quality_maximum": "Maximum (4096 px)",
    "settings.quality_high": "High (2048 px)",
    "settings.folder": "Live's folder",
    "settings.folder_help":
      "Its pack and the downloaded imagery, on an APFS disk. Another folder sets the region up again there (about two minutes); the old one stays where it is.",
    "settings.provider": "Imagery",
    "settings.save": "Save",
    "settings.saved": "Saved.",

    "log.title": "Log",
    "unit.gb": "GB",
    "unit.km": "km",
    "error.api": "Live does not answer: {error}",
  },
  fr: {
    "app.language": "Langue",

    "state.idle": "Prêt",
    "state.idle_lead":
      "Démarrez Live avant X-Plane, puis volez : à chaque départ, X-Plane attend sur son écran de chargement que l'ortho autour de l'aéroport soit construite.",
    "state.blocked": "Pas prêt",
    "state.starting": "Démarrage…",
    "state.starting_region": "Préparation de la région, une seule fois : deux minutes environ.",
    "state.starting_install": "Installation du pack et du plugin dans X-Plane.",
    "state.waiting": "En attente de X-Plane",
    "state.waiting_lead": "Lancez X-Plane et choisissez un vol.",
    "state.held": "X-Plane attend l'ortho autour de {name}",
    "state.held_lead":
      "X-Plane reste sur son écran de chargement jusqu'à ce que tout ce qu'il affichera là soit construit.",
    "state.building_start": "Construction de l'ortho autour de {name}",
    "state.building_start_lead": "X-Plane va charger ce vol.",
    "state.start_ready": "L'ortho autour de {name} est prête",
    "state.start_ready_lead": "X-Plane l'affiche dès qu'il y arrive.",
    "state.following": "Suit l'avion",
    "state.following_lead": "L'ortho devant l'avion et autour de la caméra se construit pendant le vol.",
    "state.loading": "X-Plane charge",
    "state.loading_lead": "Il n'envoie plus sa position : un vol se charge, ou il démarre.",
    "state.xplane_gone": "X-Plane a quitté",
    "state.xplane_gone_lead": "Relancez-le, ou arrêtez Live.",
    "state.stopping": "Arrêt…",
    "state.leaving": "En attente de la fermeture de X-Plane",
    "state.leaving_lead":
      "X-Plane lit ses textures dans le pack de Live : le pack sort de X-Plane quand X-Plane quitte.",
    "state.error": "Live s'est arrêté : {error}",
    "hold.text": "{left} textures à construire · {seconds} s · {mbs} Mo/s",

    "action.start": "Démarrer",
    "action.stop": "Arrêter",
    "action.leave": "Le laisser installé",
    "action.uninstall": "Retirer le pack de X-Plane",
    "action.fresh": "Repartir de zéro",
    "action.fresh_help":
      "Oublie toutes les textures construites et les images téléchargées, comme la toute première fois.",

    "flight.title": "Vol",
    "flight.xplane": "X-Plane",
    "flight.data_none": "pas encore de position",
    "flight.data_fresh": "envoie sa position",
    "flight.data_stale": "silencieux",
    "flight.position": "Position",
    "flight.altitude": "Altitude",
    "flight.speed": "Vitesse sol",
    "flight.camera": "Caméra",
    "flight.camera_value": "à {km} km de l'avion",
    "flight.start": "Départ",
    "flight.none": "Rien ne tourne.",
    "flight.xplane_running": "lancé",
    "flight.xplane_stopped": "pas lancé",

    "build.title": "Textures",
    "build.queued": "À construire",
    "build.urgent": "{n} urgentes",
    "build.building": "En construction",
    "build.building_value": "{n} textures, {pieces} morceaux attendus",
    "build.built": "Construites depuis le démarrage",
    "build.downloaded": "Téléchargé",
    "build.downloaded_value": "{n} morceaux ({mb} Mo), {cache} du cache",
    "build.failed": "{n} en échec",
    "build.rate": "Débit",
    "build.disk": "Disque",
    "build.disk_value": "{used} sur {budget} Go",
    "build.none": "aucune",
    "build.idle": "Affiché pendant que Live tourne.",

    "checks.title": "Vérifications",
    "checks.macos_no": "Live ne tourne que sur macOS pour l'instant : son pack repose sur les clones APFS.",
    "checks.xplane": "X-Plane 12 : {path}",
    "checks.xplane_missing": "X-Plane 12 introuvable : {problem}",
    "checks.xplane_running": "X-Plane tourne",
    "checks.xplane_running_late":
      "X-Plane tourne : quittez-le, démarrez Live, puis X-Plane (il ne regarde ses scènes qu'à son démarrage).",
    "checks.xplane_stopped": "X-Plane n'est pas lancé",
    "checks.mesh": "Maillage Europe d'AutoOrtho : {path}",
    "checks.mesh_missing": "Le maillage Europe d'AutoOrtho manque ({path}) : Live s'appuie dessus pour l'instant.",
    "checks.region": "Région préparée",
    "checks.region_todo": "La région sera préparée au premier démarrage (deux minutes environ)",
    "checks.ao": "AutoOrtho n'est pas lancé",
    "checks.ao_mounted": "AutoOrtho est lancé : quittez-le d'abord, il sert les mêmes tuiles.",
    "checks.plugin": "Plugin X-Plane prêt : X-Plane attend l'ortho à chaque départ",
    "checks.plugin_build":
      "Le plugin X-Plane sera compilé au premier démarrage (outils en ligne de commande de Xcode)",
    "checks.plugin_missing": "Pas de plugin X-Plane : X-Plane n'attendra pas l'ortho au départ",
    "checks.left_installed":
      "Le pack de Live est encore dans X-Plane : retirez-le, sinon X-Plane affiche des textures neutres partout où rien n'a été construit.",
    "checks.folder": "Dossier de Live : {path}",
    "checks.folder_problem": "Dossier de Live : {problem}",

    "settings.title": "Réglages",
    "settings.lead": "Ils s'appliquent au prochain démarrage.",
    "settings.disk": "Disque pour les textures",
    "settings.disk_help": "Au-delà, les textures les plus loin de l'avion sont rendues plus grossières.",
    "settings.cache": "Disque pour les images téléchargées",
    "settings.cache_help": "Gardées pour reconstruire sans retélécharger ; au-delà, les plus anciennes partent.",
    "settings.reach": "Net jusqu'à cette distance en vue extérieure",
    "settings.reach_help": "Jusqu'où une vue extérieure (Maj+4) peut emmener la caméra loin de l'avion.",
    "settings.quality": "Qualité des textures",
    "settings.quality_auto": "Comme X-Plane est réglé",
    "settings.quality_maximum": "Maximale (4096 px)",
    "settings.quality_high": "Haute (2048 px)",
    "settings.folder": "Dossier de Live",
    "settings.folder_help":
      "Son pack et les images téléchargées, sur un disque APFS. Un autre dossier y prépare la région à nouveau (deux minutes environ) ; l'ancien reste où il est.",
    "settings.provider": "Imagerie",
    "settings.save": "Enregistrer",
    "settings.saved": "Enregistré.",

    "log.title": "Journal",
    "unit.gb": "Go",
    "unit.km": "km",
    "error.api": "Live ne répond pas : {error}",
  },
};

const LANGUAGE_KEY = "osxp.language";
let current = "en";

/** English whatever the browser's language, as OrthoStudio XP, unless the switch chose French. */
export function detectLanguage() {
  try {
    const stored = localStorage.getItem(LANGUAGE_KEY);
    if (stored === "fr" || stored === "en") return stored;
  } catch (_e) {
    // storage may be unavailable; fall through
  }
  return "en";
}

export function setLanguage(lang, remember = false) {
  current = lang === "fr" ? "fr" : "en";
  if (remember) {
    try {
      localStorage.setItem(LANGUAGE_KEY, current);
    } catch (_e) {
      // ignore
    }
  }
  if (typeof document !== "undefined") document.documentElement.lang = current;
}

export function language() {
  return current;
}

/** Translate a literal key; a missing key shows itself (visible, so tests catch it). */
export function t(key, params) {
  const text = STRINGS[current][key] ?? STRINGS.en[key] ?? key;
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

/** Apply the data-i18n attributes of a subtree. */
export function applyStatic(root) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
}
