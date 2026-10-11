// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The Live window's words, in English and French, and the few helpers of OrthoStudio XP's own
// i18n.js it needs (the same rules: English unless the switch chose French, and a key missing in
// French falls back to English, then to itself).

export const STRINGS = {
  en: {
    "app.language": "Language",
    "nav.live": "Live",
    "nav.settings": "Settings",

    "state.idle": "Ready",
    "state.idle_lead": "Start Live, then X-Plane.",
    "state.blocked": "Not ready",
    "state.starting": "Starting…",
    "state.starting_region": "Setting up the region, once: a few seconds.",
    "state.starting_install": "Putting the pack and the plugin in X-Plane.",
    "state.starting_zoom": "Setting the textures to the new zoom, once: a few seconds, nothing to download.",
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
    "hold.text": "{photos} textures to download · {mbs} MB/s · {seconds} s",
    "hold.photos_in": "Every photo is in · {seconds} s",
    "hold.building": "{building} textures to build",

    "action.start": "Start Live",
    "action.stop": "Stop",
    "action.xplane": "Start X-Plane",
    "action.osxp": "Build tiles",
    "action.leave": "Leave it installed",
    "action.uninstall": "Take the pack out of X-Plane",
    "quit.button": "Quit",
    "quit.help": "Stop OrthoStudio XP Live",
    "quit.title": "Quit OrthoStudio XP Live?",
    "quit.text": "Live's window closes until you open it again.",
    "quit.streaming": "Live is streaming: quitting stops it.",
    "quit.xplane": "X-Plane is running: Live stays until X-Plane quits, then takes its pack out of X-Plane.",
    "quit.stay": "Stay",
    "quit.confirm": "Quit OrthoStudio XP Live",
    "building.title": "A build is running",
    "building.text": "OrthoStudio XP is building tiles. A build uses a lot of processor time and may slow X-Plane and Live down.",
    "building.wait": "Not now",
    "building.go": "Go on anyway",
    "action.fresh": "From scratch",
    "action.fresh_title":
      "Forgets every texture built and every photo downloaded, as the very first time, and builds them again with the photo source above. Without it, textures already built keep their photos.",
    "action.fresh_help": "Forgets every texture built and all photos downloaded, as the very first time.",

    "departure.title": "Prepare my departure",
    "departure.placeholder": "ICAO, e.g. LFMN",
    "departure.prepare": "Prepare",
    "departure.help": "Built before X-Plane loads the flight: no wait on its loading screen.",
    "departure.from_plan": "Last SimBrief plan: {plan}",
    "departure.updated": "Flight plan updated: {plan}",
    "departure.last": "Your last SimBrief plan: {plan}",
    "departure.check": "Look on SimBrief for a newer plan",
    "departure.no_newer": "No newer plan on SimBrief.",
    "departure.no_plan": "SimBrief gave no plan: try again in a moment.",
    "departure.load": "Load it",
    "departure.unknown": "X-Plane knows no airport {code}.",
    "departure.empty": "Type an airport's ICAO code.",
    "departure.busy": "Live is starting or stopping: prepare it again in a moment.",

    "flight.title": "Flight",
    "flight.data_none": "No position from X-Plane yet",
    "flight.data_fresh": "X-Plane sends its position",
    "flight.data_stale": "X-Plane is silent",
    "flight.position": "Position",
    "flight.altitude": "Altitude",
    "flight.speed": "Ground speed",
    "flight.plan": "Flight plan",
    "flight.plan_following": "{name} (SimBrief), followed",
    "flight.sharp": "Sharp up to",
    "flight.sharp_value": "{km} km ({width} px image, {fov}° field of view)",
    "flight.camera": "Camera",
    "flight.camera_value": "{km} km from the aircraft",
    "flight.start": "Start",
    "flight.xplane_running": "X-Plane is running",
    "flight.xplane_stopped": "X-Plane is not running",

    "build.title": "Textures",
    "build.idle": "Shown while Live runs.",
    "build.state_nothing": "Nothing to build",
    "build.state_done": "All built",
    "build.state_building": "Building",
    "build.state_late": "Late: {n} urgent",
    "build.state_failed": "Downloads failing",
    "build.state_refused": "Downloads refused",
    "build.queued": "to build, none urgent",
    "build.queued_urgent": "to build, {n} of them urgent",
    "build.building": "being built",
    "build.pieces_awaited": "{n} pieces awaited",
    "build.built": "built since the start",
    "build.downloaded": "downloaded",
    "build.pieces": "{n} pieces",
    "build.downloaded_pieces": "{n} pieces, {cache} from the cache",
    "build.failed": "{n} failed",
    "build.sizes": "Built, by size:",
    "build.rate": "Rate",
    "build.disk": "Disk",
    "build.disk_value": "{used} GB of {budget} GB",
    "build.at_8192": "{n} textures at 8192 px",

    "checks.title": "Checks",
    "checks.unsupported": "Live runs on macOS, Windows and Linux, not on this system.",
    "checks.folder": "Live's folder: {path}",
    "checks.folder_problem": "Live's folder: {problem}",
    "checks.xplane": "X-Plane 12: {path}",
    "checks.xplane_missing": "X-Plane 12 not found: {problem}",
    "checks.xplane_running": "X-Plane is running",
    "checks.xplane_running_late":
      "X-Plane is running: quit it, start Live, then X-Plane (it looks at its scenery only when it starts).",
    "checks.xplane_stopped": "X-Plane is not running",
    "checks.mesh": "Regions installed: {path}",
    "checks.own_tiles": "Your own tiles: {tiles}. X-Plane shows them, and Live downloads nothing there.",
    "checks.own_squares": "{n} tiles",
    "checks.mesh_missing": "No mesh yet: install the region you fly over.",
    "checks.region": "Region set up",
    "checks.region_todo": "The region is set up at the first start (a few seconds)",
    "flight.notice": "No mesh here: X-Plane shows its own scenery. Install {region}.",
    "flight.notice_installed": "No mesh here yet: {region} is installed, and used from Live's next start.",
    "map.title": "Map",
    "map.follow": "Follow the aircraft",
    "map.details": "Details",
    "map.route": "Show my SimBrief route",
    "map.route_error": "No route: {error}",
    "map.route_no_name": "No route: put your SimBrief name in Settings (Flight plan).",
    "map.tip_photos": "Photos of ZL{zoom}: {ground} a pixel",
    "map.tip_cm": "{n} cm",
    "map.tip_m": "{n} m",
    "map.tip_tile": "a tile of the mesh {km} km wide (ZL{zoom})",
    "map.tip_tile_airport": "a tile of the mesh {km} km wide, an airport's (ZL{zoom})",
    "map.tip_file": "{px} px, {mb} MB",
    "map.tip_seen": "Last within reach: {ago}",
    "map.tip_now": "just now",
    "map.tip_min": "{n} min ago",
    "map.tip_hours": "{n} h ago",
    "map.tip_days": "{n} days ago",
    "map.tip_patchy": "To build again: pieces did not come",
    "map.tip_square": "{n} textures built · the sharpest: photos of ZL{zoom}",
    "map.info_hint": "Point at a texture on the map to see what it is.",
    "map.legend_zl19": "ZL19 · 20 cm",
    "map.legend_zl18": "ZL18 · 40 cm",
    "map.legend_zl17": "ZL17 · 85 cm",
    "map.legend_zl16": "ZL16 · 1.7 m",
    "map.legend_zl15": "ZL15 · 3.4 m",
    "map.legend_less": "Blurrier",
    "map.legend_title": "Sharpness on the ground:",
    "map.legend_region": "Region installed",
    "map.legend_own_osxp": "Your tiles",
    "map.legend_own_ortho4xp": "Your Ortho4XP tiles",
    "map.legend_own_other": "Other scenery",
    "map.own_osxp": "Your tile {pack}: X-Plane shows it here, and Live downloads nothing.",
    "map.own_ortho4xp": "Your Ortho4XP tile {pack}: X-Plane shows it here, and Live downloads nothing.",
    "map.own_other": "{pack}: X-Plane shows this scenery here, and Live downloads nothing.",
    "map.legend_route": "Route",
    "regions.title": "The mesh, by region",
    "regions.go": "Go to the regions",
    "regions.help":
      "The sceneries Live lays its photos on. Install the regions you fly over, delete them whenever you like.",
    "regions.source":
      "Downloaded from github.com/kubilus1/autoortho-scenery. Once unpacked, a region takes about 1.7 times its size on the disk.",
    "regions.loading": "Asking GitHub for the list…",
    "regions.error": "GitHub did not give the list: {error}",
    "regions.size": "{gb} GB",
    "regions.installed": "Installed",
    "regions.newer": "A newer release is out ({release})",
    "regions.install": "Install",
    "regions.update": "Update",
    "regions.remove": "Delete",
    "regions.cancel": "Stop",
    "regions.progress": "{done} of {total} GB downloaded, {unpacked} of {zips} files unpacked",
    "regions.failed": "Stopped: {error}. Install again to go on from there.",
    "regions.cancelled": "Stopped. Install again to go on from there.",
    "regions.done": "Installed: used from the next start.",
    "regions.confirm_remove": "Sure? Delete {name}",
    "region.eur": "Europe",
    "region.na": "North America",
    "region.sa": "South America",
    "region.afr": "Africa",
    "region.asi": "Asia",
    "region.aus_pac": "Australia and the Pacific",
    "overlays.title": "Roads, forests and buildings",
    "overlays.help":
      "Where Live shows its photos, X-Plane no longer draws its own roads, trees and buildings. Each region brings them back with it (its \"overlays\").",
    "overlays.auto": "Yes, where simHeaven X-World does not put its own (recommended)",
    "overlays.auto_note":
      "Below your other sceneries: Global Forests hides their forests where it has its own. Not on your own tiles, which have theirs.",
    "overlays.off": "Never",
    "overlays.off_note": "The photo alone, or what your other sceneries add.",
    "checks.streamers": "No other program streaming ortho is running (XPME, AutoOrtho)",
    "checks.streamer_running": "{name} is running: quit it first, only one program may stream ortho to X-Plane.",
    "checks.streamers_running": "{names} are running: quit them first, only one program may stream ortho to X-Plane.",
    "checks.and": "and",
    "checks.plugin": "X-Plane plugin ready: X-Plane waits for the ortho at each start",
    "checks.plugin_build": "The X-Plane plugin is built at the first start (Xcode's command-line tools)",
    "checks.plugin_missing": "No X-Plane plugin: X-Plane will not wait for the ortho at a start",
    "checks.left_installed":
      "Live's pack is still in X-Plane: take it out, or X-Plane shows neutral textures wherever nothing was built.",
    "checks.machine":
      "This computer: {cores} cores, {ram} GB of memory. In flight, Live takes only a small share of it: X-Plane comes first.",

    "settings.title": "Settings",
    "settings.lead": "They take effect at the next start.",
    "settings.save": "Save",
    "settings.undo": "Undo my changes",
    "settings.saved": "Saved.",
    "settings.left_unsaved": "Settings not saved yet: they apply only after Save.",
    "settings.search": "Search the settings",
    "settings.search_ph": "Search: zoom, disk, SimBrief…",
    "settings.search_found": "{n} of {total} settings.",
    "settings.search_none": "Nothing matches. Try another word of the question.",
    "find.label": "Find on this page",
    "find.placeholder": "Find on this page",
    "find.previous": "Previous (Shift+Enter)",
    "find.next": "Next (Enter)",
    "find.close": "Close (Esc)",
    "app.text_size": "Size of the page's text, applied at once. The zoom, on the right, makes everything larger.",
    "app.zoom_out": "Zoom out (Ctrl+minus)",
    "app.zoom_out_mac": "Zoom out (⌘−)",
    "app.zoom_level": "Back to 100% (Ctrl+0)",
    "app.zoom_level_mac": "Back to 100% (⌘0)",
    "app.zoom_in": "Zoom in (Ctrl+plus)",
    "app.zoom_in_mac": "Zoom in (⌘+)",

    "source.title": "Photo source",
    "source.running": "It changes once Live is stopped.",
    "source.group_world": "The whole world",
    "source.group_mine": "My sources",
    "source.group_country": "One country",
    "source.fallback": "It covers one country only ({country}): elsewhere, Live takes Bing Maps.",
    "source.low_zoom": "It stops at zoom {zl}: up close, the ortho is blurrier.",
    "source.refused": "{provider} refuses your connection (access denied): stop Live and choose another source meanwhile.",

    "zoom.title": "Zoom",
    "zoom.help":
      "X-Plane picks by itself, by distance, which version of each texture to show: the zoom sets the sharpest one. A higher zoom is sharper near the ground only, around you; a lower one is blurrier within 38 km, the ground under the aircraft at cruise too.",
    "zoom.max": "Zoom",
    "zoom.max_help": "Away from airports. The package's zoom is 16.",
    "zoom.airports": "Airports",
    "zoom.airports_help": "Around airports, where the package's zoom is 18.",
    "zoom.lower": "ZL{zl}: less sharp up close, {times} times less to download",
    "zoom.same": "ZL{zl}: the package's",
    "zoom.where": "It is chosen on Live's screen, right of the photo source: away from airports (Zoom) and around them (Airports).",
    "zoom.running": "It changes once Live is stopped.",
    "zoom.higher": "ZL{zl}: sharper up close, more to download around you (8192 px textures)",
    "zoom.change":
      "A new zoom applies at the next start of Live, X-Plane closed, at once: each texture takes its new size only when X-Plane comes near it, with nothing to download.",
    "zoom.memory":
      "This computer has {ram} GB of memory, and ZL17 and ZL19 need {need} GB: X-Plane may stutter or run out of memory. ZL16 and ZL18, the package's, suit it better.",
    "home.title": "When the app starts",
    "home.help":
      "What the app shows first: Live, or OrthoStudio XP, to build tiles. Build | Live, at the top of the window, goes from one to the other.",
    "home.ask": "Ask me",
    "home.live": "Live",
    "home.build": "OrthoStudio XP, to build tiles",
    "welcome.title": "What would you like to do?",
    "welcome.lead": "Two ways to have ortho in X-Plane. You can go from one to the other at any time.",
    "welcome.live_title": "Fly now",
    "welcome.live_1": "The ortho is built as you fly, ahead of the aircraft.",
    "welcome.live_2":
      "Choose your departure in X-Plane and take off; or prepare it ahead in Live, and X-Plane starts at once.",
    "welcome.live_3": "Needs the internet in flight, and a ready-made mesh for now (Europe, North America…).",
    "welcome.live_choose": "Choose Live",
    "welcome.build_title": "Build my tiles",
    "welcome.build_1": "Prepare the regions you fly over in advance, at the best quality.",
    "welcome.build_2": "Nothing to download in flight: your tiles stay on your disk.",
    "welcome.build_3": "Takes computing time and disk space.",
    "welcome.build_choose": "Choose Build",
    "welcome.both":
      "Both at once? Build your tiles where you want them (your airport, your region), and Live takes care of the rest: X-Plane shows your tiles first.",
    "welcome.remember": "Open it directly next time (Live's settings change it)",
    "welcome.leaving": "OrthoStudio XP opens…",

    "plan.title": "Flight plan",
    "plan.help":
      "Live prepares the route ahead. With your last SimBrief plan, it follows the route, turns and approach included, while you fly it; without, it looks 90 seconds straight ahead.",
    "plan.name": "SimBrief name or pilot ID",
    "plan.privacy":
      "Only this name goes to simbrief.com: when a flight starts, and while the Live window is open (when it opens, when you come back to it, and every two minutes), to offer your departure and tell you when your plan changes. Empty: nothing is asked.",
    "plan.take": "Take OrthoStudio XP's: {name}",

    "start.title": "When a flight starts",
    "start.plain":
      "X-Plane waits on its loading screen for the textures around the aircraft; then Live builds them ahead of you.",
    "start.sharp":
      "X-Plane waits on its loading screen for the textures around the aircraft, 8192 px within 15 km. In flight, Live makes the 8192 px within 15 km of you and of your path ahead, before X-Plane reads it at 12 km. Farther, a texture shows its 4096 px there, never anything blank.",
    "start.tint": "Test: show the 8192 px textures in pink",
    "start.tint_help":
      "Only their 8192 px version is tinted: where the ground turns pink in X-Plane, X-Plane shows the 8192 px. After the test, untick it and delete the textures (Disk space, the photos kept): they are built again normally.",
    "state.tint_on":
      "Test on: the 8192 px textures are pink where X-Plane shows them; on the map, green (ZL17) away from airports, violet (ZL19) around them.",
    "state.tint_next":
      "Test on from the next start: the 8192 px textures will be pink where X-Plane shows them; on the map, green (ZL17) away from airports, violet (ZL19) around them.",

    "disk.title": "Disk space",
    "disk.help":
      "The space Live may take on the disk: half for the ortho made for X-Plane, half for the downloaded photos, kept to make it again without downloading them twice. Beyond it, what is farthest and oldest goes, and Live makes it again if you fly back there.",
    "disk.space": "Space for Live",
    "disk.now": "Now: {gb} GB ({textures} GB of ortho, {imagery} GB of photos).",
    "disk.delete": "Delete…",
    "disk.delete_streaming": "Stop Live to delete.",

    "delete.title": "Delete what Live keeps?",
    "delete.textures_name": "The built textures ({gb} GB)",
    "delete.textures_note": "Back to the neutral texture: Live builds them again as you fly.",
    "delete.imagery_name": "The downloaded photos ({gb} GB)",
    "delete.imagery_note": "Downloaded again when needed.",
    "delete.xplane_running": "X-Plane is running and reads the textures: quit it to delete them.",
    "delete.keep": "Keep them",
    "delete.confirm": "Delete",

    "folder.title": "Live's folder",
    "folder.help":
      "Where Live keeps its textures and its downloaded photos: on the computer's own disk, or on a disk of the same kind (APFS on a Mac, NTFS on Windows, ext4 on Linux). Not on a disk in Mac OS Extended, exFAT or FAT32.",
    "folder.now": "Now: {path}",
    "reveal.mac": "Show in Finder",
    "reveal.win": "Show in File Explorer",
    "reveal.lin": "Open the folder",
    "folder.other": "Another folder",
    "folder.choose": "Choose…",
    "folder.prompt": "Choose the folder where OrthoStudio XP Live keeps its textures and its photos",
    "folder.moved":
      "In another folder, Live sets the region up again, in a few seconds; what the old one holds stays there.",

    "country.Spain": "Spain",
    "country.Netherlands": "Netherlands",
    "country.Luxembourg": "Luxembourg",
    "country.Japan": "Japan",
    "country.United States": "United States",

    "log.title": "Log",
    "log.file": "Kept as a file too: {path}",
    "unit.gb": "GB",
    "unit.mb": "MB",
    "error.api": "Live does not answer: {error}",
  },
  fr: {
    "app.language": "Langue",
    "nav.live": "Live",
    "nav.settings": "Réglages",

    "state.idle": "Prêt",
    "state.idle_lead": "Démarrez Live, puis X-Plane.",
    "state.blocked": "Pas prêt",
    "state.starting": "Démarrage…",
    "state.starting_region": "Préparation de la région, une seule fois : quelques secondes.",
    "state.starting_install": "Installation du pack et du plugin dans X-Plane.",
    "state.starting_zoom": "Mise des textures au nouveau zoom, une seule fois : quelques secondes, rien à télécharger.",
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
    "hold.text": "{photos} textures à télécharger · {mbs} Mo/s · {seconds} s",
    "hold.photos_in": "Toutes les photos sont arrivées · {seconds} s",
    "hold.building": "{building} textures à construire",

    "action.start": "Démarrer Live",
    "action.stop": "Arrêter",
    "action.xplane": "Lancer X-Plane",
    "action.osxp": "Construire des tuiles",
    "action.leave": "Le laisser installé",
    "action.uninstall": "Retirer le pack de X-Plane",
    "quit.button": "Quitter",
    "quit.help": "Arrêter OrthoStudio XP Live",
    "quit.title": "Quitter OrthoStudio XP Live ?",
    "quit.text": "La fenêtre de Live se ferme jusqu'à ce que vous la rouvriez.",
    "quit.streaming": "Live est en marche : quitter l'arrête.",
    "quit.xplane": "X-Plane tourne : Live reste jusqu'à ce que X-Plane quitte, puis sort son pack de X-Plane.",
    "quit.stay": "Rester",
    "quit.confirm": "Quitter OrthoStudio XP Live",
    "building.title": "Un build est en cours",
    "building.text": "OrthoStudio XP construit des tuiles. Un build utilise beaucoup le processeur et peut ralentir X-Plane et Live.",
    "building.wait": "Pas maintenant",
    "building.go": "Continuer quand même",
    "action.fresh": "Repartir de zéro",
    "action.fresh_title":
      "Oublie toutes les textures construites et les photos téléchargées, comme la toute première fois, et les reconstruit avec la source photo choisie plus haut. Sans cela, les textures déjà construites gardent leurs photos.",
    "action.fresh_help":
      "Oublie toutes les textures construites et les photos téléchargées, comme la toute première fois.",

    "departure.title": "Préparer mon départ",
    "departure.placeholder": "OACI, ex. LFMN",
    "departure.prepare": "Préparer",
    "departure.help": "Construit avant que X-Plane charge le vol : pas d'attente sur son écran de chargement.",
    "departure.from_plan": "Dernier plan SimBrief : {plan}",
    "departure.updated": "Plan de vol mis à jour : {plan}",
    "departure.last": "Votre dernier plan SimBrief : {plan}",
    "departure.check": "Chercher un plan plus récent sur SimBrief",
    "departure.no_newer": "Pas de plan plus récent sur SimBrief.",
    "departure.no_plan": "SimBrief n'a donné aucun plan : réessayez dans un instant.",
    "departure.load": "Le charger",
    "departure.unknown": "X-Plane ne connaît pas d'aéroport {code}.",
    "departure.empty": "Entrez le code OACI d'un aéroport.",
    "departure.busy": "Live démarre ou s'arrête : réessayez dans un instant.",

    "flight.title": "Vol",
    "flight.data_none": "Pas encore de position d'X-Plane",
    "flight.data_fresh": "X-Plane envoie sa position",
    "flight.data_stale": "X-Plane est silencieux",
    "flight.position": "Position",
    "flight.altitude": "Altitude",
    "flight.speed": "Vitesse sol",
    "flight.plan": "Plan de vol",
    "flight.plan_following": "{name} (SimBrief), suivi",
    "flight.sharp": "Net jusqu'à",
    "flight.sharp_value": "{km} km (image de {width} px, champ de {fov}°)",
    "flight.camera": "Caméra",
    "flight.camera_value": "à {km} km de l'avion",
    "flight.start": "Départ",
    "flight.xplane_running": "X-Plane est lancé",
    "flight.xplane_stopped": "X-Plane n'est pas lancé",

    "build.title": "Textures",
    "build.idle": "Affiché pendant que Live tourne.",
    "build.state_nothing": "Rien à construire",
    "build.state_done": "Tout est construit",
    "build.state_building": "En construction",
    "build.state_late": "En retard : {n} urgentes",
    "build.state_failed": "Téléchargements en échec",
    "build.state_refused": "Téléchargements refusés",
    "build.queued": "à construire, aucune urgente",
    "build.queued_urgent": "à construire, {n} urgentes",
    "build.building": "en construction",
    "build.pieces_awaited": "{n} morceaux attendus",
    "build.built": "construites depuis le début",
    "build.downloaded": "téléchargés",
    "build.pieces": "{n} morceaux",
    "build.downloaded_pieces": "{n} morceaux, {cache} du cache",
    "build.failed": "{n} en échec",
    "build.sizes": "Construites, par taille :",
    "build.rate": "Débit",
    "build.disk": "Disque",
    "build.disk_value": "{used} Go sur {budget} Go",
    "build.at_8192": "{n} textures en 8192 px",

    "checks.title": "Vérifications",
    "checks.unsupported": "Live tourne sur macOS, Windows et Linux, pas sur ce système.",
    "checks.folder": "Dossier de Live : {path}",
    "checks.folder_problem": "Dossier de Live : {problem}",
    "checks.xplane": "X-Plane 12 : {path}",
    "checks.xplane_missing": "X-Plane 12 introuvable : {problem}",
    "checks.xplane_running": "X-Plane tourne",
    "checks.xplane_running_late":
      "X-Plane tourne : quittez-le, démarrez Live, puis X-Plane (il ne regarde ses scènes qu'à son démarrage).",
    "checks.xplane_stopped": "X-Plane n'est pas lancé",
    "checks.mesh": "Régions installées : {path}",
    "checks.own_tiles": "Vos propres tuiles : {tiles}. X-Plane les affiche, et Live n'y télécharge rien.",
    "checks.own_squares": "{n} tuiles",
    "checks.mesh_missing": "Pas encore de mesh : installez la région que vous survolez.",
    "checks.region": "Région préparée",
    "checks.region_todo": "La région sera préparée au premier démarrage (quelques secondes)",
    "flight.notice": "Pas de mesh ici : X-Plane affiche sa propre scène. Installez {region}.",
    "flight.notice_installed": "Pas encore de mesh ici : {region} est installée, et servira dès le prochain démarrage de Live.",
    "map.title": "Carte",
    "map.follow": "Suivre l'avion",
    "map.details": "Détails",
    "map.route": "Afficher ma route SimBrief",
    "map.route_error": "Pas de route : {error}",
    "map.route_no_name": "Pas de route : indiquez votre nom SimBrief dans les Réglages (Plan de vol).",
    "map.tip_photos": "Photos ZL{zoom} : {ground} par pixel",
    "map.tip_cm": "{n} cm",
    "map.tip_m": "{n} m",
    "map.tip_tile": "carreau du mesh de {km} km (ZL{zoom})",
    "map.tip_tile_airport": "carreau d'aéroport du mesh de {km} km (ZL{zoom})",
    "map.tip_file": "{px} px, {mb} Mo",
    "map.tip_seen": "Vue à portée : {ago}",
    "map.tip_now": "à l'instant",
    "map.tip_min": "il y a {n} min",
    "map.tip_hours": "il y a {n} h",
    "map.tip_days": "il y a {n} jours",
    "map.tip_patchy": "À refaire : des morceaux ne sont pas arrivés",
    "map.tip_square": "{n} textures construites · la plus nette : photos du ZL{zoom}",
    "map.info_hint": "Pointez une texture sur la carte pour voir ce qu'elle est.",
    "map.legend_zl19": "ZL19 · 20 cm",
    "map.legend_zl18": "ZL18 · 40 cm",
    "map.legend_zl17": "ZL17 · 85 cm",
    "map.legend_zl16": "ZL16 · 1,7 m",
    "map.legend_zl15": "ZL15 · 3,4 m",
    "map.legend_less": "Plus flou",
    "map.legend_title": "Netteté au sol :",
    "map.legend_region": "Région installée",
    "map.legend_own_osxp": "Vos tuiles",
    "map.legend_own_ortho4xp": "Vos tuiles Ortho4XP",
    "map.legend_own_other": "Autre scène",
    "map.own_osxp": "Votre tuile {pack} : X-Plane l'affiche ici, et Live n'y télécharge rien.",
    "map.own_ortho4xp": "Votre tuile Ortho4XP {pack} : X-Plane l'affiche ici, et Live n'y télécharge rien.",
    "map.own_other": "{pack} : X-Plane affiche cette scène ici, et Live n'y télécharge rien.",
    "map.legend_route": "Route",
    "regions.title": "Le mesh, par région",
    "regions.go": "Aller aux régions",
    "regions.help":
      "Les scènes sur lesquelles Live pose ses photos. Installez les régions que vous survolez, supprimez-les quand vous voulez.",
    "regions.source":
      "Téléchargées depuis github.com/kubilus1/autoortho-scenery. Une fois décompressée, une région prend environ 1,7 fois sa taille sur le disque.",
    "regions.loading": "Liste demandée à GitHub…",
    "regions.error": "GitHub n'a pas donné la liste : {error}",
    "regions.size": "{gb} Go",
    "regions.installed": "Installée",
    "regions.newer": "Nouvelle version ({release})",
    "regions.install": "Installer",
    "regions.update": "Mettre à jour",
    "regions.remove": "Supprimer",
    "regions.cancel": "Arrêter",
    "regions.progress": "{done} Go sur {total} téléchargés, {unpacked} fichiers sur {zips} décompressés",
    "regions.failed": "Arrêté : {error}. Relancez l'installation pour reprendre là où elle s'est arrêtée.",
    "regions.cancelled": "Arrêté. Relancez l'installation pour reprendre là où elle s'est arrêtée.",
    "regions.done": "Installée : utilisée dès le prochain démarrage.",
    "regions.confirm_remove": "Sûr ? Supprimer {name}",
    "region.eur": "Europe",
    "region.na": "Amérique du Nord",
    "region.sa": "Amérique du Sud",
    "region.afr": "Afrique",
    "region.asi": "Asie",
    "region.aus_pac": "Australie et Pacifique",
    "overlays.title": "Routes, forêts et bâtiments",
    "overlays.help":
      "Là où Live affiche ses photos, X-Plane ne dessine plus ses propres routes, arbres et bâtiments. Chaque région les ramène avec elle (ses « overlays »).",
    "overlays.auto": "Oui, là où simHeaven X-World ne met pas les siens (recommandé)",
    "overlays.auto_note":
      "Rangés sous vos autres scènes : Global Forests efface leurs forêts là où il a les siennes. Pas sur vos propres tuiles, qui ont les leurs.",
    "overlays.off": "Jamais",
    "overlays.off_note": "La photo seule, ou ce qu'ajoutent vos autres scènes.",
    "checks.streamers": "Aucun autre logiciel qui streame des orthos n'est lancé (XPME, AutoOrtho)",
    "checks.streamer_running": "{name} est lancé : quittez-le d'abord, un seul logiciel peut streamer des orthos pour X-Plane.",
    "checks.streamers_running": "{names} sont lancés : quittez-les d'abord, un seul logiciel peut streamer des orthos pour X-Plane.",
    "checks.and": "et",
    "checks.plugin": "Plugin X-Plane prêt : X-Plane attend l'ortho à chaque départ",
    "checks.plugin_build":
      "Le plugin X-Plane sera compilé au premier démarrage (outils en ligne de commande de Xcode)",
    "checks.plugin_missing": "Pas de plugin X-Plane : X-Plane n'attendra pas l'ortho au départ",
    "checks.left_installed":
      "Le pack de Live est encore dans X-Plane : retirez-le, sinon X-Plane affiche des textures neutres partout où rien n'a été construit.",
    "checks.machine":
      "Cet ordinateur : {cores} cœurs, {ram} Go de mémoire. En vol, Live n'en prend qu'une petite part : X-Plane passe en premier.",

    "settings.title": "Réglages",
    "settings.lead": "Ils s'appliquent au prochain démarrage.",
    "settings.save": "Enregistrer",
    "settings.undo": "Annuler mes changements",
    "settings.saved": "Enregistré.",
    "settings.left_unsaved": "Réglages pas encore enregistrés : ils ne s'appliquent qu'après « Enregistrer ».",
    "settings.search": "Chercher un réglage",
    "settings.search_ph": "Chercher : zoom, disque, SimBrief…",
    "settings.search_found": "{n} sur {total} réglages.",
    "settings.search_none": "Rien ne correspond. Essayez un autre mot de la question.",
    "find.label": "Chercher dans cette page",
    "find.placeholder": "Chercher dans cette page",
    "find.previous": "Précédent (Maj+Entrée)",
    "find.next": "Suivant (Entrée)",
    "find.close": "Fermer (Échap)",
    "app.text_size": "Taille du texte de la page, appliquée tout de suite. Le zoom, à droite, agrandit tout.",
    "app.zoom_out": "Réduire (Ctrl+moins)",
    "app.zoom_out_mac": "Réduire (⌘−)",
    "app.zoom_level": "Revenir à 100 % (Ctrl+0)",
    "app.zoom_level_mac": "Revenir à 100 % (⌘0)",
    "app.zoom_in": "Agrandir (Ctrl+plus)",
    "app.zoom_in_mac": "Agrandir (⌘+)",

    "source.title": "Source des photos",
    "source.running": "Elle se change quand Live est arrêté.",
    "source.group_world": "Le monde entier",
    "source.group_mine": "Mes sources",
    "source.group_country": "Un seul pays",
    "source.fallback": "Elle ne couvre qu'un pays ({country}) : ailleurs, Live prend Bing Maps.",
    "source.low_zoom": "Elle s'arrête au zoom {zl} : de près, l'ortho est plus floue.",
    "source.refused": "{provider} refuse votre connexion (accès refusé) : arrêtez Live et choisissez une autre source en attendant.",

    "zoom.title": "Zoom",
    "zoom.help":
      "X-Plane choisit seul, selon la distance, quelle version de chaque texture montrer : le zoom règle la plus nette. Un zoom plus haut n'est plus net que près du sol, autour de vous ; un zoom plus bas est plus flou à moins de 38 km, le sol sous l'avion en croisière aussi.",
    "zoom.max": "Zoom",
    "zoom.max_help": "Loin des aéroports. Le zoom du package est 16.",
    "zoom.airports": "Aéroports",
    "zoom.airports_help": "Autour des aéroports, où le zoom du package est 18.",
    "zoom.lower": "ZL{zl} : moins net de près, {times} fois moins à télécharger",
    "zoom.same": "ZL{zl} : celui du package",
    "zoom.where": "Il se choisit sur l'écran de Live, à droite de la source des photos : loin des aéroports (Zoom) et autour d'eux (Aéroports).",
    "zoom.running": "Il se change quand Live est arrêté.",
    "zoom.higher":
      "ZL{zl} : plus net de près, plus à télécharger autour de vous (textures 8192 px)",
    "zoom.change":
      "Un nouveau zoom s'applique au prochain démarrage de Live, X-Plane fermé, tout de suite : chaque texture prend sa nouvelle taille seulement quand X-Plane s'en approche, sans rien télécharger.",
    "zoom.memory":
      "Cet ordinateur a {ram} Go de mémoire, et ZL17 et ZL19 en demandent {need} : X-Plane risque de saccader ou d'en manquer. ZL16 et ZL18, ceux du package, lui conviennent mieux.",
    "home.title": "Au lancement de l'application",
    "home.help":
      "Ce que l'application montre d'abord : Live, ou OrthoStudio XP pour construire des tuiles. Construire | Live, en haut de la fenêtre, passe de l'un à l'autre.",
    "home.ask": "Me demander",
    "home.live": "Live",
    "home.build": "OrthoStudio XP, pour construire des tuiles",
    "welcome.title": "Que voulez-vous faire ?",
    "welcome.lead":
      "Deux façons d'avoir de l'ortho dans X-Plane. Vous pourrez passer de l'une à l'autre à tout moment.",
    "welcome.live_title": "Voler tout de suite",
    "welcome.live_1": "L'ortho se construit pendant que vous volez, devant l'avion.",
    "welcome.live_2":
      "Choisissez votre départ dans X-Plane et décollez ; ou préparez-le à l'avance dans Live, et X-Plane démarre aussitôt.",
    "welcome.live_3":
      "Il faut internet pendant le vol, et un mesh déjà prêt pour l'instant (Europe, Amérique du Nord…).",
    "welcome.live_choose": "Choisir Live",
    "welcome.build_title": "Construire mes tuiles",
    "welcome.build_1": "Préparez à l'avance les régions que vous survolez, à la qualité maximale.",
    "welcome.build_2": "Rien à télécharger en vol : vos tuiles restent sur votre disque.",
    "welcome.build_3": "Demande du temps de calcul et de la place sur le disque.",
    "welcome.build_choose": "Choisir Construire",
    "welcome.both":
      "Les deux à la fois ? Construisez vos tuiles là où vous les voulez (votre aéroport, votre région), et Live s'occupe du reste : X-Plane affiche vos tuiles en priorité.",
    "welcome.remember": "L'ouvrir directement la prochaine fois (les réglages de Live le changent)",
    "welcome.leaving": "OrthoStudio XP s'ouvre…",

    "plan.title": "Plan de vol",
    "plan.help":
      "Live prépare la route devant vous. Avec votre dernier plan SimBrief, il suit la route, virages et approche compris, tant que vous la volez ; sans, il regarde 90 secondes tout droit devant.",
    "plan.name": "Nom SimBrief ou ID pilote",
    "plan.privacy":
      "Seul ce nom est envoyé à simbrief.com : au départ de chaque vol, et tant que la fenêtre de Live est ouverte (à son ouverture, quand vous y revenez, et toutes les deux minutes), pour proposer votre départ et vous dire quand votre plan change. Vide : rien n'est demandé.",
    "plan.take": "Reprendre celui d'OrthoStudio XP : {name}",

    "start.title": "Au démarrage d'un vol",
    "start.plain":
      "X-Plane attend sur son écran de chargement les textures autour de l'avion ; ensuite, Live les construit devant vous.",
    "start.sharp":
      "X-Plane attend sur son écran de chargement les textures autour de l'avion, en 8192 px à moins de 15 km. En vol, Live fait le 8192 px à moins de 15 km de vous et de votre route, avant qu'X-Plane le lise à 12 km. Plus loin, une texture y montre son 4096 px, jamais rien de vide.",
    "start.tint": "Test : montrer les textures 8192 px en rose",
    "start.tint_help":
      "Seule leur version 8192 px est teintée : là où le sol rosit dans X-Plane, X-Plane affiche le 8192 px. Après le test, décochez et supprimez les textures (Place sur le disque, en gardant les photos) : elles sont refaites normalement.",
    "state.tint_on":
      "Test en cours : les textures 8192 px sont roses là où X-Plane les affiche ; sur la carte, vertes (ZL17) hors des aéroports, violettes (ZL19) autour.",
    "state.tint_next":
      "Test au prochain démarrage : les textures 8192 px seront roses là où X-Plane les affiche ; sur la carte, vertes (ZL17) hors des aéroports, violettes (ZL19) autour.",

    "disk.title": "Place sur le disque",
    "disk.help":
      "La place que Live peut prendre sur le disque : moitié pour l'ortho préparée pour X-Plane, moitié pour les photos téléchargées, gardées pour la refaire sans les retélécharger. Au-delà, ce qui est le plus loin et le plus ancien s'en va, et Live le refait si vous y revenez.",
    "disk.space": "Place pour Live",
    "disk.now": "En ce moment : {gb} Go ({textures} Go d'ortho, {imagery} Go de photos).",
    "disk.delete": "Supprimer…",
    "disk.delete_streaming": "Arrêtez Live pour supprimer.",

    "delete.title": "Supprimer ce que Live garde ?",
    "delete.textures_name": "Les textures construites ({gb} Go)",
    "delete.textures_note": "Elles redeviennent neutres : Live les reconstruit pendant que vous volez.",
    "delete.imagery_name": "Les photos téléchargées ({gb} Go)",
    "delete.imagery_note": "Téléchargées à nouveau quand il le faut.",
    "delete.xplane_running": "X-Plane tourne et lit les textures : quittez-le pour les supprimer.",
    "delete.keep": "Les garder",
    "delete.confirm": "Supprimer",

    "folder.title": "Dossier de Live",
    "folder.help":
      "Où Live garde ses textures et les photos téléchargées : sur le disque de l'ordinateur, ou sur un disque du même format (APFS sur Mac, NTFS sur Windows, ext4 sur Linux). Pas sur un disque en Mac OS étendu, exFAT ou FAT32.",
    "folder.now": "En ce moment : {path}",
    "reveal.mac": "Afficher dans le Finder",
    "reveal.win": "Afficher dans l'Explorateur de fichiers",
    "reveal.lin": "Ouvrir le dossier",
    "folder.other": "Un autre dossier",
    "folder.choose": "Choisir…",
    "folder.prompt": "Choisissez le dossier où OrthoStudio XP Live garde ses textures et ses photos",
    "folder.moved":
      "Dans un autre dossier, Live prépare la région à nouveau, en quelques secondes ; ce que l'ancien contient y reste.",

    "country.Spain": "Espagne",
    "country.Netherlands": "Pays-Bas",
    "country.Luxembourg": "Luxembourg",
    "country.Japan": "Japon",
    "country.United States": "États-Unis",

    "log.title": "Journal",
    "log.file": "Gardé aussi dans un fichier : {path}",
    "unit.gb": "Go",
    "unit.mb": "Mo",
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

/** Apply the data-i18n attributes of a subtree: a text, a title, a field's placeholder, a name
 * read aloud. */
export function applyStatic(root) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
  for (const el of root.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  }
  for (const el of root.querySelectorAll("[data-i18n-aria-label]")) {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  }
}
