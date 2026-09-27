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
    "state.deleting": "Deleting…",
    "state.error": "Live stopped: {error}",
    "hold.text": "{left} textures to build · {seconds} s · {mbs} MB/s",

    "action.start": "Start",
    "action.stop": "Stop",
    "action.xplane": "Start X-Plane",
    "action.reload": "Reload the scenery in X-Plane",
    "offer.building":
      "8192 px around the aircraft: {n} textures to build, then the scenery can be reloaded to show them.",
    "offer.ready":
      "The 8192 px textures around the aircraft are built: reload the scenery to see them, here or with the button in X-Plane. X-Plane shows “reload objects & textures” for a few seconds.",
    "offer.asked": "Scenery reloaded: X-Plane shows the 8192 px textures around the aircraft.",
    "action.xplane_wait":
      "Start X-Plane once Live is ready: X-Plane sees Live's pack only if it is there when X-Plane starts.",
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
    "flight.xplane_running": "running",
    "flight.xplane_stopped": "not running",

    "build.title": "Textures",
    "build.idle": "Shown while Live runs.",
    "build.queued": "To build",
    "build.urgent": "{n} urgent",
    "build.building": "Being built",
    "build.building_value": "{n} textures, {pieces} pieces awaited",
    "build.built": "Built since the start",
    "build.downloaded": "Downloaded",
    "build.downloaded_value": "{n} pieces ({mb} MB), {cache} from the cache",
    "build.failed": "{n} failed",
    "build.grown": "At 8192 px",
    "build.rate": "Rate",
    "build.disk": "Disk",
    "build.disk_value": "{used} of {budget} GB",
    "build.none": "none",

    "checks.title": "Checks",
    "checks.macos_no": "Live runs on macOS only for now: its pack relies on APFS clones.",
    "checks.folder": "Live's folder: {path}",
    "checks.folder_problem": "Live's folder: {problem}",
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

    "settings.title": "Settings",
    "settings.lead": "They take effect at the next start.",
    "settings.save": "Save",
    "settings.undo": "Undo my changes",
    "settings.saved": "Saved.",

    "source.title": "Imagery source",
    "source.help":
      "The aerial photos Live downloads. Textures already built keep theirs: delete them (Disk space) to build them again from another source.",
    "source.group_world": "The whole world",
    "source.group_mine": "My sources",
    "source.group_country": "One country",
    "source.fallback": "It covers one country only ({country}): elsewhere, Live takes Bing Maps.",
    "source.low_zoom": "It stops at zoom {zl}: up close, the ortho is blurrier.",

    "zoom.title": "Zoom",
    "zoom.help":
      "X-Plane picks by itself, by distance, which version of each texture to show: the zoom sets the sharpest one. A higher zoom is sharper near the ground only (within 6 km at ZL17); a lower one is blurrier within 38 km, the ground under the aircraft at cruise too.",
    "zoom.max": "Maximum zoom",
    "zoom.max_help": "Away from airports. The package's zoom is 16.",
    "zoom.airports": "Zoom near airports",
    "zoom.airports_help": "Around airports, where the package's zoom is 18.",
    "zoom.lower": "ZL{zl}: less sharp up close, {times} times less to download",
    "zoom.same": "ZL{zl}: the package's",
    "zoom.higher": "ZL{zl}: sharper up close, 4 times more to download (8192 px textures)",
    "zoom.change":
      "A new zoom applies at the next start of Live, X-Plane closed: Live puts its textures at their new size, about a minute, with nothing to download.",

    "start.title": "When a flight starts",
    "start.help":
      "With zoom 17, or 19 near airports, the sharpest textures are 8192 px instead of 4096. This choice says what X-Plane waits for in 8192 px on its loading screen.",
    "start.why":
      "Why it matters: X-Plane keeps the size of a texture it has read, and reads it again only to take a larger one. A texture it reads at 4096 px while the flight loads, near the aircraft, stays 4096 px for the whole flight, like a picture on a web page you do not reload. A texture it reads small, farther away, is read again as you come closer: Live will have put the 8192 px in by then.",
    "start.full": "Full resolution",
    "start.full_note":
      "The whole start in 8192 px, even far from the aircraft, wherever an external view can go (25 km). The longest wait: about 2.5 times Normal's.",
    "start.around": "Normal, and 8192 px around the aircraft",
    "start.around_note":
      "Everything X-Plane would read at 4096 px while the flight loads is 8192 px instead: about 10 km around the aircraft, 3 km for the airport's textures. Nothing around you stays 4096 px. About 40 to 50 % longer than Normal.",
    "start.normal": "Normal resolution",
    "start.normal_note":
      "X-Plane waits for 4096 px only: the shortest wait. Around the start, over about 10 km, the textures stay 4096 px for this flight.",
    "start.reload": "Normal, then reload in 8192 px",
    "start.reload_note":
      "The same wait as Normal. Then Live builds the start in 8192 px, and once it is ready, a button in X-Plane (and in this window) reloads the scenery: X-Plane shows “reload objects & textures” for a few seconds, then the start in 8192 px.",
    "start.flight":
      "Beyond the start, all four do the same: Live makes the textures ahead of the aircraft 8192 px before X-Plane reads them up close, with nothing to reload.",
    "start.none": "Your zooms make no 8192 px texture: the four start the same.",
    "settings.recommended": "Recommended",

    "disk.title": "Disk space",
    "disk.help": "Live keeps two things on the disk, each within the space set here.",
    "disk.textures": "Built textures",
    "disk.textures_help":
      "The ortho made for X-Plane around where you fly: what X-Plane shows. Beyond this space, the textures farthest from the aircraft become coarser, and Live builds them again if you fly back there.",
    "disk.imagery": "Downloaded imagery",
    "disk.imagery_help":
      "The photos as the source sent them, kept so that a texture can be built again without downloading them twice. Beyond this space, the oldest are deleted.",
    "disk.now": "Now: {gb} GB.",
    "disk.delete": "Delete…",
    "disk.delete_streaming": "Stop Live to delete.",

    "delete.title": "Delete what Live keeps?",
    "delete.textures_name": "The built textures ({gb} GB)",
    "delete.textures_note": "Back to the neutral texture: Live builds them again as you fly.",
    "delete.imagery_name": "The downloaded imagery ({gb} GB)",
    "delete.imagery_note": "Downloaded again when needed.",
    "delete.xplane_running": "X-Plane is running and reads the textures: quit it to delete them.",
    "delete.keep": "Keep them",
    "delete.confirm": "Delete",

    "folder.title": "Live's folder",
    "folder.help": "Where Live keeps its textures and its downloaded imagery, on an APFS disk.",
    "folder.now": "Now: {path}",
    "folder.show": "Show in the Finder",
    "folder.other": "Another folder",
    "folder.choose": "Choose…",
    "folder.prompt": "Choose the folder where OrthoStudio XP Live keeps its textures and its imagery",
    "folder.moved":
      "In another folder, Live sets the region up again, about two minutes; what the old one holds stays there.",

    "country.Spain": "Spain",
    "country.Netherlands": "Netherlands",
    "country.Luxembourg": "Luxembourg",
    "country.Japan": "Japan",
    "country.United States": "United States",

    "log.title": "Log",
    "unit.gb": "GB",
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
    "state.deleting": "Suppression…",
    "state.error": "Live s'est arrêté : {error}",
    "hold.text": "{left} textures à construire · {seconds} s · {mbs} Mo/s",

    "action.start": "Démarrer",
    "action.stop": "Arrêter",
    "action.xplane": "Lancer X-Plane",
    "action.reload": "Recharger la scène dans X-Plane",
    "offer.building":
      "8192 px autour de l'avion : {n} textures à construire, puis la scène pourra être rechargée pour les montrer.",
    "offer.ready":
      "Les textures 8192 px autour de l'avion sont construites : rechargez la scène pour les voir, ici ou avec le bouton dans X-Plane. X-Plane affiche « reload objects & textures » quelques secondes.",
    "offer.asked": "Scène rechargée : X-Plane montre les textures 8192 px autour de l'avion.",
    "action.xplane_wait":
      "Lancez X-Plane quand Live est prêt : X-Plane ne voit le pack de Live que s'il est là quand X-Plane démarre.",
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
    "flight.xplane_running": "lancé",
    "flight.xplane_stopped": "pas lancé",

    "build.title": "Textures",
    "build.idle": "Affiché pendant que Live tourne.",
    "build.queued": "À construire",
    "build.urgent": "{n} urgentes",
    "build.building": "En construction",
    "build.building_value": "{n} textures, {pieces} morceaux attendus",
    "build.built": "Construites depuis le démarrage",
    "build.downloaded": "Téléchargé",
    "build.downloaded_value": "{n} morceaux ({mb} Mo), {cache} du cache",
    "build.failed": "{n} en échec",
    "build.grown": "En 8192 px",
    "build.rate": "Débit",
    "build.disk": "Disque",
    "build.disk_value": "{used} sur {budget} Go",
    "build.none": "aucune",

    "checks.title": "Vérifications",
    "checks.macos_no": "Live ne tourne que sur macOS pour l'instant : son pack repose sur les clones APFS.",
    "checks.folder": "Dossier de Live : {path}",
    "checks.folder_problem": "Dossier de Live : {problem}",
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

    "settings.title": "Réglages",
    "settings.lead": "Ils s'appliquent au prochain démarrage.",
    "settings.save": "Enregistrer",
    "settings.undo": "Annuler mes changements",
    "settings.saved": "Enregistré.",

    "source.title": "Source des images",
    "source.help":
      "Les photos aériennes que Live télécharge. Les textures déjà construites gardent les leurs : supprimez-les (Place sur le disque) pour les reconstruire avec une autre source.",
    "source.group_world": "Le monde entier",
    "source.group_mine": "Mes sources",
    "source.group_country": "Un seul pays",
    "source.fallback": "Elle ne couvre qu'un pays ({country}) : ailleurs, Live prend Bing Maps.",
    "source.low_zoom": "Elle s'arrête au zoom {zl} : de près, l'ortho est plus floue.",

    "zoom.title": "Zoom",
    "zoom.help":
      "X-Plane choisit seul, selon la distance, quelle version de chaque texture montrer : le zoom règle la plus nette. Un zoom plus haut n'est plus net que près du sol (à moins de 6 km en ZL17) ; un zoom plus bas est plus flou à moins de 38 km, le sol sous l'avion en croisière aussi.",
    "zoom.max": "Zoom maximum",
    "zoom.max_help": "Loin des aéroports. Le zoom du package est 16.",
    "zoom.airports": "Zoom près des aéroports",
    "zoom.airports_help": "Autour des aéroports, où le zoom du package est 18.",
    "zoom.lower": "ZL{zl} : moins net de près, {times} fois moins à télécharger",
    "zoom.same": "ZL{zl} : celui du package",
    "zoom.higher": "ZL{zl} : plus net de près, 4 fois plus à télécharger (textures 8192 px)",
    "zoom.change":
      "Un nouveau zoom s'applique au prochain démarrage de Live, X-Plane fermé : Live met ses textures à leur nouvelle taille, une minute environ, sans rien télécharger.",

    "start.title": "Au démarrage d'un vol",
    "start.help":
      "Avec le zoom 17, ou 19 près des aéroports, les textures les plus nettes font 8192 px au lieu de 4096. Ce choix dit ce qu'X-Plane attend en 8192 px sur son écran de chargement.",
    "start.why":
      "Pourquoi c'est important : X-Plane garde en mémoire la taille de texture qu'il a lue, et ne la relit que pour en prendre une plus grande. Une texture qu'il lit en 4096 px pendant le chargement du vol, près de l'avion, reste en 4096 px tout le vol, comme une photo sur une page web qu'on ne recharge pas. Une texture qu'il lit en petit, plus loin, sera relue quand vous approcherez : Live y aura mis la 8192 px entre-temps.",
    "start.full": "Pleine résolution",
    "start.full_note":
      "Tout le départ en 8192 px, même loin de l'avion, là où une vue extérieure peut aller (25 km). L'attente la plus longue : environ 2,5 fois celle de Normale.",
    "start.around": "Normale, et 8192 px autour de l'avion",
    "start.around_note":
      "Tout ce qu'X-Plane lirait en 4096 px pendant le chargement est en 8192 px : environ 10 km autour de l'avion, 3 km pour les textures de l'aéroport. Rien autour de vous ne reste en 4096 px. Environ 40 à 50 % d'attente de plus que Normale.",
    "start.normal": "Résolution normale",
    "start.normal_note":
      "X-Plane n'attend que du 4096 px : l'attente la plus courte. Autour du départ, sur environ 10 km, les textures restent en 4096 px pendant ce vol.",
    "start.reload": "Normale, puis recharger en 8192 px",
    "start.reload_note":
      "Même attente que Normale. Live prépare ensuite le départ en 8192 px ; quand c'est prêt, un bouton dans X-Plane (et dans cette fenêtre) recharge la scène : X-Plane affiche « reload objects & textures » quelques secondes, puis le départ en 8192 px.",
    "start.flight":
      "Au-delà du départ, les quatre font pareil : Live passe en 8192 px les textures devant l'avion avant qu'X-Plane les lise de près, sans rien recharger.",
    "start.none": "Vos zooms ne font aucune texture 8192 px : les quatre démarrent pareil.",
    "settings.recommended": "Recommandé",

    "disk.title": "Place sur le disque",
    "disk.help": "Live garde deux choses sur le disque, chacune dans la place réglée ici.",
    "disk.textures": "Textures construites",
    "disk.textures_help":
      "L'ortho préparée pour X-Plane autour de là où vous volez : ce que X-Plane affiche. Au-delà de cette place, les textures les plus loin de l'avion redeviennent plus grossières, et Live les reconstruit si vous y revenez.",
    "disk.imagery": "Images téléchargées",
    "disk.imagery_help":
      "Les photos telles que la source les envoie, gardées pour reconstruire une texture sans les télécharger deux fois. Au-delà de cette place, les plus anciennes sont effacées.",
    "disk.now": "En ce moment : {gb} Go.",
    "disk.delete": "Supprimer…",
    "disk.delete_streaming": "Arrêtez Live pour supprimer.",

    "delete.title": "Supprimer ce que Live garde ?",
    "delete.textures_name": "Les textures construites ({gb} Go)",
    "delete.textures_note": "Elles redeviennent neutres : Live les reconstruit pendant que vous volez.",
    "delete.imagery_name": "Les images téléchargées ({gb} Go)",
    "delete.imagery_note": "Téléchargées à nouveau quand il le faut.",
    "delete.xplane_running": "X-Plane tourne et lit les textures : quittez-le pour les supprimer.",
    "delete.keep": "Les garder",
    "delete.confirm": "Supprimer",

    "folder.title": "Dossier de Live",
    "folder.help": "Où Live garde ses textures et les images téléchargées, sur un disque APFS.",
    "folder.now": "En ce moment : {path}",
    "folder.show": "Afficher dans le Finder",
    "folder.other": "Un autre dossier",
    "folder.choose": "Choisir…",
    "folder.prompt": "Choisissez le dossier où OrthoStudio XP Live garde ses textures et ses images",
    "folder.moved":
      "Dans un autre dossier, Live prépare la région à nouveau, deux minutes environ ; ce que l'ancien contient y reste.",

    "country.Spain": "Espagne",
    "country.Netherlands": "Pays-Bas",
    "country.Luxembourg": "Luxembourg",
    "country.Japan": "Japon",
    "country.United States": "États-Unis",

    "log.title": "Journal",
    "unit.gb": "Go",
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

/** A country's name as the registry gives it (English), in the page's language. */
export function countryName(extent) {
  return STRINGS[current][`country.${extent}`] ?? extent;
}

/** Apply the data-i18n attributes of a subtree. */
export function applyStatic(root) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
}
