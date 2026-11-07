// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
/**
 * The cloud mode of the Plan's map (``docs/specs/clouds.md`` 1.3).
 *
 * A view of what the engine says of a tile (``GET /api/clouds/{tile}``): every cloud with its tick
 * and its source; After, the tile as X-Plane shows it, the textures Apply makes again with the
 * previews of the clouds it lays in them; Before, the tile with no cloud replaced. Each tick or
 * source is sent as it is made and the tile read again: the page keeps no choice of its own.
 * Apply lays its changes in the tile's textures, a task of the engine; nothing else changes what
 * X-Plane shows. The page decides nothing: what a row, a button, a click on an outline does or
 * says is the engine's (``clouds/state.py``), shown as it comes.
 *
 * The map is drawn in three layers: the ground (the tile's picture and the previews), kept as it
 * is while the files are the same, so the tile never flashes when a cloud is clicked; the shapes
 * (the outlines, a task's label) and the marks (a found cloud's number, a pin on a cloud too small
 * to see), drawn again when what they show changes, the marks at each zoom too.
 */

import { codeText, fmtNum, t } from "./i18n.js";

/** How often a tile is read again while something of it runs, in ms: a cloud's images in half a
 * second are shown half a second after. */
const POLL_MS = 500;
/** The closest Show brings the map. */
const SHOW_ZOOM = 15;
/** A cloud smaller than this on the screen, in px, gets a pin: it could not be seen. */
const PIN_BELOW_PX = 12;
/** The pane the ground is laid in: under the outlines, whatever is added first. */
const GROUND_PANE = "clouds-ground";

/** ``4 ha`` under a square kilometre, ``1.3 km²`` above. */
export function areaWords(ha) {
  return ha < 100 ? t("clouds.ha", { n: fmtNum(ha, ha < 10 ? 1 : 0) }) : t("clouds.km2", { n: fmtNum(ha / 100, 1) });
}

/** A cloud's name: ``Cloud 7``, ``Outline 2``. */
export function cloudName(c) {
  return t(c.kind === "drawn" ? "clouds.outline_name" : "clouds.cloud_name", { n: c.number });
}

/** ``[lat0, lon0, ...]`` as Leaflet's ``[[lat, lon], ...]``. */
export function latlngs(ring) {
  const out = [];
  for (let i = 0; i + 1 < ring.length; i += 2) out.push([ring[i], ring[i + 1]]);
  return out;
}

/** The words of a line the engine sent (``{key, params}``), a source's code given its name. */
export function lineWords(line, names = {}) {
  if (!line) return "";
  const params = { ...(line.params || {}) };
  for (const k of ["source", "held"]) if (params[k]) params[k] = names[params[k]] || params[k];
  return t(line.key, params);
}

/** How far a task is, when it says (``Task.view``): `` 11 %``. */
export function progressWords(task) {
  return task?.progress != null ? ` ${Math.round(task.progress * 100)} %` : "";
}

/** Where a task is: its phase, and how far when it says (``Task.view``). */
export function taskWords(task) {
  return `${t(`clouds.phase.${task.phase}`)}${progressWords(task)}`;
}

/** A line of several pieces the engine sent, each a key and its parameters. */
export function piecesWords(pieces) {
  return (pieces || []).map((w) => lineWords(w)).join(" · ");
}

/**
 * The point of a ring (``[lat0, lon0, ...]``) farthest from its edges, where a cloud's number
 * reads inside it whatever its shape (a crescent's centre is outside it).
 */
export function labelPoint(ring, steps = 24) {
  const pts0 = latlngs(ring);
  const lats = pts0.map((p) => p[0]);
  const k = Math.cos((((Math.min(...lats) + Math.max(...lats)) / 2) * Math.PI) / 180);
  const pts = pts0.map(([lat, lon]) => [lon * k, lat]);
  const xs = pts.map((p) => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...lats), y1 = Math.max(...lats);
  const inside = (x, y) => {
    let on = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i];
      const [xj, yj] = pts[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) on = !on;
    }
    return on;
  };
  const edge = (x, y) => {
    let best = Infinity;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i];
      const [xj, yj] = pts[j];
      const dx = xj - xi, dy = yj - yi;
      const u = dx || dy ? Math.max(0, Math.min(1, ((x - xi) * dx + (y - yi) * dy) / (dx * dx + dy * dy))) : 0;
      best = Math.min(best, Math.hypot(x - xi - u * dx, y - yi - u * dy));
    }
    return best;
  };
  let at = null;
  let far = -1;
  for (let i = 0; i < steps; i++) {
    for (let j = 0; j < steps; j++) {
      const x = x0 + ((i + 0.5) / steps) * (x1 - x0);
      const y = y0 + ((j + 0.5) / steps) * (y1 - y0);
      if (!inside(x, y)) continue;
      const d = edge(x, y);
      if (d > far) {
        far = d;
        at = [y, x / k];
      }
    }
  }
  return at || [(y0 + y1) / 2, (x0 + x1) / 2 / k];
}

export function createCloudMode(ctx) {
  const { api, h, icon, clear, morph, toast, errorMessage } = ctx;
  const L = globalThis.L;
  const $ = (id) => document.getElementById(id);
  const cs = {
    tile: null, answer: null, view: "after", current: null, poll: null,
    ground: null, overlays: null, groundKey: null, shapes: null, shapesKey: null, marks: null,
    /** The row to bring into view after the next drawing, and whether to its middle. */
    reveal: null, centre: false,
    /** The tile's readings asked, and the one shown: an answer older than it is left (two clicks
     * close together, the first one's answer coming back last, showed the tile as it was). */
    asked: 0, shown: 0,
    /** Why the tile could not be read, when no answer came yet: said in the panel. */
    error: null,
    /** The clicks whose answer the engine has not given yet (:func:`waiting`). */
    sending: 0,
  };
  const labels = new Map(); // a cloud's number's place, by its outline

  const names = () => Object.fromEntries((ctx.providers() || []).map((p) => [p.code, p.name || p.code]));
  /** A control's key: drawn again in place, an element stays the one it was, its handler with it.
   * Matched by place alone, a button took another's of the same look: a search's Stop came in as
   * Tick all's, and ticked every cloud (a user, 2026-11-06). Every control of the panel and the
   * banner has one; a cloud's row has its cloud's. */
  const key = (k) => ({ focusKey: `clouds-${k}` });
  const file = (name) => `/api/clouds/${encodeURIComponent(cs.tile)}/files/${name}`;

  async function open(tile) {
    const again = cs.tile === tile; // back from another screen: the mode was left open
    if (cs.tile && !again) close();
    // its last answer kept (another tile's goes with ``close``): cleared, the tile's picture and
    // outlines went until its answer came back, a blink at each return (a user, 2026-11-07)
    Object.assign(cs, { tile, view: "after", current: null, reveal: null, centre: false, error: null });
    ctx.showPlan();
    document.querySelector(".plan-flow")?.classList.add("is-clouds");
    $("clouds-panel").hidden = false;
    $("clouds-banner").hidden = false;
    ctx.planMap.takeInput(true); // the map's clicks choose no square under the clouds
    // the map framed on the tile when it is opened, not when it is opened again: framed at each
    // return, a view zoomed in on a cloud went back to the whole tile and the base map was loaded
    // again, a blink (a user, 2026-11-07)
    if (!again) ctx.planMap.fitTile(tile);
    ctx.planMap.leaflet()?.off("zoomend", drawMarks).on("zoomend", drawMarks); // opened again: one
    render();
    await refresh();
  }

  function close() {
    if (!cs.tile) return;
    stopPoll();
    ctx.planMap.cancelDraw();
    ctx.planMap.leaflet()?.off("zoomend", drawMarks);
    for (const layer of [cs.ground, cs.shapes, cs.marks]) layer?.remove();
    Object.assign(cs, { tile: null, answer: null, ground: null, overlays: null, groundKey: null, shapes: null, shapesKey: null, marks: null, error: null });
    $("clouds-panel").hidden = true;
    $("clouds-banner").hidden = true;
    document.querySelector(".plan-flow")?.classList.remove("is-clouds");
    ctx.planMap.leaflet()?.getContainer().classList.remove("is-outlining");
    ctx.planMap.takeInput(false);
  }

  function stopPoll() {
    if (cs.poll) clearTimeout(cs.poll);
    cs.poll = null;
  }

  async function refresh() {
    const tile = cs.tile;
    if (!tile) return;
    stopPoll();
    const asked = ++cs.asked;
    try {
      const answer = await api("GET", `/api/clouds/${encodeURIComponent(tile)}`);
      if (cs.tile !== tile || asked < cs.shown) return; // a later reading is shown already
      cs.shown = asked;
      cs.answer = answer;
      cs.error = null;
    } catch (err) {
      if (cs.tile !== tile) return;
      cs.error = errorMessage(err);
      if (cs.answer) toast(cs.error, "fail");
    }
    render();
    if (cs.answer?.busy) cs.poll = setTimeout(refresh, POLL_MS);
  }

  /** An action sent to the engine, the tile read again after it. The engine refuses what runs
   * the cloud detector before it is downloaded (``CLOUD_DETECTOR_ASK``): the user is asked, and on
   * yes what the engine says goes with ``download`` (``then``: the clouds' images, once a tick is
   * kept), else the same action again. */
  async function send(method, path, body) {
    try {
      return await waiting(async () => {
        const res = await api(method, `/api/clouds/${encodeURIComponent(cs.tile)}${path}`, body);
        await refresh();
        return res;
      });
    } catch (err) {
      const e = err?.detail?.error;
      if (e?.code === "CLOUD_DETECTOR_ASK" && !body?.download) {
        await refresh(); // what was kept meanwhile (a tick) shown under the question
        if (await ask(t("clouds.tool_title"), [t("clouds.tool_text", { mb: e.context?.mb })], t("clouds.tool_go"))) {
          if (e.context?.then) return send("POST", "/check", { ...e.context.then, download: true });
          return send(method, path, { ...(body || {}), download: true });
        }
      } else {
        toast(errorMessage(err), "fail");
      }
      await refresh();
      return null;
    }
  }

  /** A click's work, the engine's answer awaited: a spinner beside the title meanwhile, the one
   * way every click says it was taken (Tick all on 189 clouds showed nothing for a second, a
   * source already got nothing at all, a user, 2026-11-07). */
  async function waiting(work) {
    cs.sending += 1;
    render();
    try {
      return await work();
    } finally {
      cs.sending -= 1;
      render();
    }
  }

  // ------------------------------------------------------------------ the map

  function render() {
    renderBanner();
    renderPanel();
    drawMap();
  }

  function drawMap() {
    const map = ctx.planMap.leaflet();
    if (!map || !L) return;
    // a thin cross to aim at the points of an outline, not Leaflet's hand
    map.getContainer().classList.toggle("is-outlining", ctx.planMap.drafting());
    drawGround(map);
    // the outlines and the marks drawn again only when what they show changed: drawn at every
    // reading, twice a second while a task ran, the tooltip under the mouse went away and two
    // hundred clouds weighed on the page (a review, 2026-11-06)
    const shapes = JSON.stringify([cs.tile, cs.current, ctx.planMap.drafting(), (cs.answer?.clouds || []).map((c) =>
      [c.id, c.rings, c.tick, c.tip, c.label ? labelWords(c) : null, c.numbered])]);
    if (shapes === cs.shapesKey) return;
    cs.shapesKey = shapes;
    drawShapes(map);
    drawMarks();
  }

  /** The ground: the picture of the view, After's previews over it (a cloud whose images are
   * being got keeps its own meanwhile, the engine's). Laid again only when its files change, the
   * new one shown once loaded: the tile never flashes. */
  function drawGround(map) {
    const a = cs.answer;
    const pictures = [];
    const base = a ? (cs.view === "after" ? a.after : a.picture) : null;
    if (base) pictures.push(base);
    if (a && cs.view === "after") for (const c of a.clouds) if (c.preview) pictures.push(c.preview);
    const key = pictures.map((p) => p.name).join(" ");
    if (key === cs.groundKey) return;
    cs.groundKey = key;
    if (!map.getPane(GROUND_PANE)) map.createPane(GROUND_PANE).style.zIndex = 390; // under the shapes
    if (!cs.ground) {
      cs.ground = L.layerGroup().addTo(map);
      cs.overlays = new Map(); // each picture by its file: one kept is never laid again
    }
    const wanted = new Set(pictures.map((p) => p.name));
    const gone = [...cs.overlays.keys()].filter((name) => !wanted.has(name));
    const fresh = pictures.filter((p) => !cs.overlays.has(p.name));
    let left = fresh.length;
    const letGo = () => {
      for (const name of gone) {
        if (wanted.has(name) || !cs.overlays.has(name)) continue;
        cs.ground.removeLayer(cs.overlays.get(name));
        cs.overlays.delete(name);
      }
    };
    for (const p of fresh) {
      const [s, w, n, e] = p.bounds;
      const overlay = L.imageOverlay(file(p.name), [[s, w], [n, e]], { interactive: false, pane: GROUND_PANE });
      overlay.once("load", () => { if (--left <= 0) letGo(); }).once("error", () => { if (--left <= 0) letGo(); });
      cs.ground.addLayer(overlay);
      cs.overlays.set(p.name, overlay);
    }
    if (!fresh.length) letGo();
    // the view's picture under its previews, in the list's order, whichever came first
    for (const p of pictures) cs.overlays.get(p.name)?.bringToFront();
  }

  /** The tile's surroundings darkened, every cloud's outline on a dark casing (read over snow as
   * over dark ground), its tooltip saying what a click does, and a task's label at its centre
   * (``label``, the engine's: a spinner, or its line). */
  function drawShapes(map) {
    cs.shapes?.remove();
    const layer = L.layerGroup();
    const m = /^([+-]\d{2})([+-]\d{3})$/.exec(cs.tile || "");
    if (m) {
      // seen from far, the base map is another, greener mosaic than the photos the tile is
      // built from: darkened around the tile, the tile does not look changed (a user, 2026-11-03)
      const lat = Number(m[1]);
      const lon = Number(m[2]);
      const world = [[85, -360], [85, 360], [-85, 360], [-85, -360]];
      const tile = [[lat, lon], [lat + 1, lon], [lat + 1, lon + 1], [lat, lon + 1]];
      L.polygon([world, tile], { className: "cloud-outside", interactive: false, stroke: false, fillOpacity: 0.5 }).addTo(layer);
    }
    const a = cs.answer;
    // while a cloud is outlined, the outlines take no click: every point goes to the drawing
    const drawing = ctx.planMap.drafting();
    for (const c of a?.clouds || []) {
      const rings = c.rings.map(latlngs);
      const current = c.id === cs.current ? " is-current" : "";
      L.polygon(rings, { className: `cloud-casing${current}`, interactive: false, fill: false, weight: 4 }).addTo(layer);
      const shape = L.polygon(rings, {
        className: `cloud-outline${c.tick ? " is-on" : ""}${current}`,
        weight: 2,
        fill: true, // nearly clear, so that a click inside the outline picks the cloud
        fillOpacity: 0.01,
        dashArray: c.tick ? null : "4 4",
        interactive: !drawing,
      });
      if (!drawing) {
        shape.on("click", (ev) => {
          L.DomEvent.stop(ev);
          clickCloud(c);
        });
        shape.bindTooltip(tipOf(c), { sticky: true, className: "cloud-tip" });
      }
      shape.addTo(layer);
      if (c.label) {
        const label = h("div", { class: "cloud-pending" }, h("span", { class: "spinner", "aria-hidden": "true" }), labelWords(c));
        L.marker(L.latLngBounds(rings.flat()).getCenter(), {
          icon: L.divIcon({ className: "cloud-pending-anchor", html: label, iconSize: [0, 0] }),
          interactive: false,
          keyboard: false,
        }).addTo(layer);
      }
    }
    layer.addTo(map);
    cs.shapes = layer;
  }

  /** A cloud's label on the map while its task runs: its name and where the task is. */
  function labelWords(c) {
    return t("clouds.label", { name: cloudName(c), what: lineWords(c.line, names()) });
  }

  /** A cloud's name on the map, with what a click on it does (``tip``, the engine's). */
  function tipOf(c) {
    const name = cloudName(c);
    return c.tip ? t(c.tip, { name }) : name;
  }

  /** In each found cloud large enough, its number, discreet, to find it in the list; on each
   * cloud too small to see at the map's zoom, a pin that clicks as its outline does (two found on
   * +52+004 could not be found on the map, a user, 2026-11-03). Drawn again at each zoom. */
  function drawMarks() {
    const map = ctx.planMap.leaflet();
    cs.marks?.remove();
    cs.marks = null;
    if (!map || !L || !cs.tile || !cs.answer) return;
    const layer = L.layerGroup();
    const drawing = ctx.planMap.drafting();
    for (const c of cs.answer.clouds) {
      const ring = c.rings[0];
      if (!ring || ring.length < 6) continue;
      const box = L.latLngBounds(latlngs(ring));
      const nw = map.latLngToLayerPoint(box.getNorthWest());
      const se = map.latLngToLayerPoint(box.getSouthEast());
      if (Math.max(se.x - nw.x, se.y - nw.y) >= PIN_BELOW_PX) {
        if (!c.numbered) continue;
        const key = ring.join(",");
        if (!labels.has(key)) labels.set(key, labelPoint(ring));
        L.marker(labels.get(key), {
          icon: L.divIcon({ className: "cloud-number-anchor", html: h("span", { class: "cloud-number" }, String(c.number)), iconSize: [0, 0] }),
          interactive: false,
          keyboard: false,
        }).addTo(layer);
        continue;
      }
      const pin = L.circleMarker(box.getCenter(), { radius: 7, weight: 2, className: `cloud-pin${c.tick ? " is-on" : ""}`, interactive: !drawing });
      if (!drawing) {
        pin.on("click", (ev) => {
          L.DomEvent.stop(ev);
          clickCloud(c);
        });
        pin.bindTooltip(tipOf(c), { className: "cloud-tip" });
      }
      pin.addTo(layer);
    }
    layer.addTo(map);
    cs.marks = layer;
  }

  /** A click on a cloud's outline or pin: what the engine says it does (``toggle``); its row framed
   * and brought to the middle of the list (from the map, where it stood in the list was not seen,
   * a user, 2026-11-03). */
  function clickCloud(c) {
    cs.current = c.id;
    cs.reveal = c.id;
    cs.centre = true;
    if (c.toggle) toggle(c);
    else render();
  }

  /** Outline a cloud: drawn the way a zone is, on the Plan's map, its polygon sent as it ends. */
  function outline() {
    ctx.planMap.drawShape(async (polygon) => {
      const res = await send("POST", "/outlines", { polygon });
      // the outline just drawn is the current cloud, its row framed and brought into view while
      // its sources are tried (a user, 2026-11-06: it was not found in the list)
      if (res?.id) {
        cs.current = res.id;
        cs.reveal = res.id;
        render();
      }
    }, render);
    render();
  }

  // ------------------------------------------------------------------ actions

  /** The cloud ``id`` as the engine said last (handlers outlive the answer they were drawn from). */
  function cloudNow(id) {
    return cs.answer?.clouds.find((x) => x.id === id) || null;
  }

  /** Stop the task ``id`` the engine sent. */
  async function stopTask(id) {
    await waiting(async () => {
      await api("POST", `/api/clouds/tasks/${id}/stop`).catch((err) => toast(errorMessage(err), "fail"));
      await refresh();
    });
  }

  /** Tick or untick the cloud, as its state says now (``toggle``): a row's checkbox is kept as it
   * is from one answer to the next, its handler with it, and acts on the cloud as it is. */
  function toggle(c) {
    const action = (cloudNow(c.id) || c).toggle;
    return action ? send("POST", "/picks", { id: c.id, action }) : refresh();
  }

  function choose(c, source) {
    cs.reveal = c.id; // its row whole in view: what the new source says under it
    return send("POST", "/picks", { id: c.id, action: "choose", source });
  }

  function ask(title, lines, go) {
    const dialog = $("clouds-dialog");
    if (!dialog || dialog.open) return Promise.resolve(false);
    $("clouds-dialog-title").textContent = title;
    clear($("clouds-dialog-text")).append(...lines.map((line) => h("p", null, line)));
    $("clouds-dialog-go").textContent = go;
    dialog.returnValue = "";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "go"), { once: true });
      dialog.showModal();
    });
  }

  async function apply(action) {
    // the question's words are the engine's, from what Apply would build
    const q = cs.answer?.texts?.[`${action}_question`];
    if (!q || !(await ask(lineWords(q.title), q.lines.map((w) => lineWords(w)), lineWords(q.go)))) return;
    await send("POST", "/apply", { action });
  }

  // ------------------------------------------------------------------ the panel

  function renderBanner() {
    const el = $("clouds-banner");
    if (!el) return;
    const a = cs.answer;
    const drawing = ctx.planMap.drafting();
    // the three sentences stacked in one cell, the current one seen: the banner keeps one size
    const said = { after: a?.views?.after ? t(a.views.after) : "", before: a?.views?.before ? t(a.views.before) : "", drawing: t("clouds.outlining") };
    const now = drawing ? "drawing" : cs.view;
    // the pressed view says so as the map's banner buttons do (a primary one read white on pale blue)
    const view = (which, words) => h("button", { type: "button", class: "btn", dataset: key(`view-${which}`), "aria-pressed": String(cs.view === which), onclick: () => { cs.view = which; render(); } }, words);
    morph(el, h("div", null,
      h("p", { class: "map-banner-text clouds-view-text", dataset: key("view-text") }, ...Object.entries(said).map(([k, words]) => h("span", { class: k === now ? "is-current" : null }, words))),
      h("div", { class: "map-banner-actions", dataset: key("banner-actions") },
        view("after", t("clouds.view_after")),
        view("before", t("clouds.view_before")),
        h("button", { type: "button", class: "btn", dataset: key("outline"), disabled: drawing || !a?.allowed.includes("outline"), onclick: outline }, t("clouds.outline")),
        h("button", { type: "button", class: "btn", dataset: key("back"), onclick: close }, t("clouds.back"))),
    ));
  }

  function renderPanel() {
    const el = $("clouds-panel");
    if (!el) return;
    const a = cs.answer;
    // beside the title, the source the tile's photo was built from: the one its clouds replace
    const built = a?.provider ? h("span", { class: "clouds-built" }, t("clouds.built_from", { source: names()[a.provider] || a.provider })) : null;
    const sending = cs.sending ? h("span", { class: "spinner clouds-sending", "aria-hidden": "true" }) : null;
    const kids = [h("h2", { id: "clouds-title" }, t("clouds.title", { tile: cs.tile || "" }), built, sending)];
    if (!a) {
      kids.push(cs.error
        ? h("p", { class: "clouds-warning", role: "status" }, cs.error)
        : h("div", { class: "clouds-progress", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }), h("span", null, t("clouds.reading"))));
      morph(el, h("div", null, ...kids));
      return;
    }
    if (a.line) kids.push(h("p", { class: "clouds-lead", dataset: key("lead") }, lineWords(a.line)));
    for (const task of a.tasks) { // the tile's own: a cloud's is in its row
      kids.push(h("div", { class: "clouds-progress", dataset: key(`task-${task.id}`) },
        h("span", { class: "spinner", "aria-hidden": "true" }),
        h("span", { role: "status" }, `${t(`clouds.task.${task.kind}`)} ${taskWords(task)}`),
        task.actions.includes("stop") ? h("button", { type: "button", class: "btn", dataset: key(`stop-${task.id}`), onclick: () => stopTask(task.id) }, t("clouds.stop")) : null));
    }
    if (a.failed) {  // the tile's last task that failed, until its next one: the engine's words for it
      const e = a.failed.error || {};
      const [message, remedy] = codeText(e.code, e.context) || [e.message || "", e.remedy || ""];
      kids.push(h("p", { class: "clouds-warning", role: "status", dataset: key("failed") }, `${t(`clouds.task.${a.failed.kind}`)} ${message} ${remedy || ""}`.trim()));
    }
    // what the tile allows is the engine's to say (``state.View.allowed``)
    const can = new Set(a.allowed);
    if (a.clouds.length) {
      // the count on a line of its own, the buttons under it: a count that grew pushed Untick all
      // under Tick all (a user, 2026-11-07)
      kids.push(h("p", { class: "plan-step-size clouds-count-line", dataset: key("count") }, piecesWords(a.texts.count)));
      kids.push(h("div", { class: "clouds-count", dataset: key("count-buttons") },
        h("button", { type: "button", class: "btn", dataset: key("tick-all"), disabled: !can.has("tick"), onclick: () => send("POST", "/picks", { action: "tick" }) }, t("clouds.all")),
        h("button", { type: "button", class: "btn", dataset: key("untick-all"), disabled: !can.has("untick"), onclick: () => send("POST", "/picks", { action: "untick" }) }, t("clouds.none")),
        // while the clouds' tasks run, every one stopped at once (after Tick all, one by one)
        can.has("stop") ? h("button", { type: "button", class: "btn clouds-stop-all", dataset: key("stop-all"), onclick: () => send("POST", "/stop") },
          h("span", { class: "spinner", "aria-hidden": "true" }), lineWords(a.texts.stop_all)) : null));
    }
    const list = h("ul", { class: "zone-list clouds-list", dataset: key("list") });
    for (const c of a.clouds) list.append(row(c));
    kids.push(list);
    // nothing found yet: the search first, and what it costs; Apply shown whenever it may be
    // (a texture to make again whose clouds no file says, the list empty)
    const first = !a.clouds.length && !can.has("apply");
    // three rows: what Apply does with the list, the searches, then Restore apart (a user,
    // 2026-11-07)
    if (!first) {
      kids.push(h("div", { class: "actions actions-build", dataset: key("actions") },
        h("button", { type: "button", class: "btn btn-primary", dataset: key("apply"), disabled: !can.has("apply"), onclick: () => apply("apply") }, lineWords(a.texts.apply)),
        h("button", { type: "button", class: "btn", dataset: key("discard"), disabled: !can.has("discard"), onclick: () => send("POST", "/discard") }, t("clouds.discard"))));
    }
    kids.push(h("div", { class: "actions actions-build", dataset: key("searches") }, ...searchButtons(a, can, first)));
    if (first) kids.push(h("p", { class: "help", dataset: key("search-help") }, t("clouds.search_help")));
    if (a.texts.restore || can.has("forget")) {
      kids.push(h("div", { class: "actions actions-build", dataset: key("restore-row") },
        a.texts.restore ? h("button", { type: "button", class: "btn", dataset: key("restore"), disabled: !can.has("restore"), onclick: () => apply("restore") }, lineWords(a.texts.restore)) : null,
        h("button", { type: "button", class: "btn btn-danger", dataset: key("forget"), disabled: !can.has("forget"), onclick: () => apply("forget") }, lineWords(a.texts.forget))));
    }
    const scroll = el.querySelector(".clouds-list")?.scrollTop || 0;
    morph(el, h("div", null, ...kids));
    revealRow(el.querySelector(".clouds-list"), scroll);
  }

  /** The two searches: the quick one, and the fine one, off and saying why on a tile with nothing
   * finer to read (``fine``, the engine's: built at the search's level or coarser). */
  function searchButtons(a, can, primary) {
    const off = !can.has("find");
    const quick = h("button", { type: "button", class: primary ? "btn btn-primary" : "btn", dataset: key("quick"), disabled: off, onclick: () => send("POST", "/find", { fine: false }) }, t("clouds.search_quick"));
    const fine = h("button", { type: "button", class: "btn", dataset: key("fine"), disabled: off || !a.fine, onclick: () => send("POST", "/find", { fine: true }) }, t("clouds.search_fine"));
    // a disabled button shows no tooltip in every browser: the reason on what holds it
    return [quick, a.fine ? fine : h("span", { class: "clouds-fine-off", dataset: key("fine-off"), title: t("clouds.fine_none") }, fine)];
  }

  /** The list kept where it was, and the row asked for brought into view: to the middle of the
   * list as far as it goes for a cloud clicked on the map, else just enough. */
  function revealRow(list, scroll) {
    if (!list) return;
    list.scrollTop = scroll;
    const row = cs.reveal ? list.querySelector(`[data-cloud="${cs.reveal}"]`) : null;
    const centre = cs.centre;
    cs.reveal = null;
    cs.centre = false;
    if (!row) return;
    const r = row.getBoundingClientRect();
    const top = list.getBoundingClientRect().top + list.clientTop;
    const bottom = top + list.clientHeight;
    if (centre) list.scrollTop += r.top + r.height / 2 - (top + bottom) / 2;
    else if (r.bottom > bottom) list.scrollTop += r.bottom - bottom;
    else if (r.top < top) list.scrollTop -= top - r.top;
  }

  /** One cloud, laid out as the Plan's zones: its tick and name, its source, what it waits for. */
  function row(c) {
    const n = names();
    const can = new Set(c.actions);
    const label = cloudName(c);
    const box = h("input", { type: "checkbox", disabled: !c.toggle, onchange: () => toggle(c) });
    box.checked = c.tick;
    const menu = h("select", { "aria-label": t("clouds.source_of", { name: label }), disabled: !can.has("choose"), onchange: (ev) => choose(c, ev.target.value) },
      c.source ? null : h("option", { value: "", disabled: true }, t("clouds.choose")),
      ...c.menu.map((m) => h("option", { value: m.source, disabled: !m.proposable },
        [n[m.source] || m.source, m.answer ? t(`clouds.answer.${m.answer}`) : "", m.fit ? t("clouds.fit", { n: m.fit }) : ""].filter(Boolean).join(" · "))));
    menu.value = c.source || "";
    const task = c.task;
    const show = () => {
      cs.current = c.id;
      cs.reveal = c.id;
      // clear of the banner, which lies over the top of the map
      const top = ($("clouds-banner")?.offsetHeight || 0) + 24;
      const now = cloudNow(c.id) || c; // the row's element outlives the answer it was drawn from
      ctx.planMap.leaflet()?.fitBounds(now.rings.map(latlngs).flat(), { maxZoom: SHOW_ZOOM, paddingTopLeft: [16, top], paddingBottomRight: [16, 16] });
      render();
    };
    const buttons = [
      can.has("check") ? h("button", { type: "button", class: "btn", dataset: key(`check-${c.id}`), "aria-label": `${t("clouds.get_images")}: ${label}`, onclick: () => send("POST", "/check", { id: c.id, again: true }) }, t("clouds.get_images")) : null,
    ].filter(Boolean);
    // its task, when it is the one cloud at work (``label``, the engine's): a spinner beside its
    // name, around a square that stops it, in place of its line and a Stop of its own (a line of
    // words and a row of its own, a user, 2026-11-07); several at work, Stop all says it
    const busy = !c.label ? null : can.has("stop")
      ? h("button", { type: "button", class: "cloud-stop", dataset: key(`stop-${c.id}`), title: t("clouds.stop"), "aria-label": `${t("clouds.stop")}: ${label}`, onclick: () => { const now = cloudNow(c.id)?.task; if (now) stopTask(now.id); } },
        h("span", { class: "spinner", "aria-hidden": "true" }), icon("tpl-stop-icon"))
      : h("span", { class: "spinner", "aria-hidden": "true" });
    const trash = can.has("remove")
      ? h("button", { type: "button", class: "btn btn-icon btn-danger btn-trash", "aria-label": `${t("clouds.remove")}: ${label}`, title: t("clouds.remove"), onclick: () => send("DELETE", `/outlines/${c.id}`) },
        icon("tpl-bin-icon"))
      : null;
    const line = c.line && !task
      ? h("span", { class: `zone-hint cloud-line${c.line.tone === "warn" ? " zone-hint-warn" : ""}` }, lineWords(c.line, n))
      : null;
    // keyed by its cloud: redrawn in place, a row stays its cloud's, its handlers with it
    // a click on the row shows its cloud; its checkbox and its name tick it, its menu and its
    // buttons do what they say (a user, 2026-11-06); whatever is done on a row makes its cloud
    // the current one, the one rule for its tick, its source, its images and its Stop; a click
    // on the row, its tick or its source changed shows it too, to look at what is ticked and see
    // its new images (a user, 2026-11-07: a source changed, a tick left the map elsewhere)
    const pick = (ev) => {
      cs.current = c.id;
      if (ev.type === "change" || !ev.target.closest("label, input, select, button")) show();
    };
    return h("li", { class: "zone-item cloud-item", "aria-current": String(c.id === cs.current), dataset: { cloud: c.id, state: c.state, focusKey: `cloud-${c.id}` }, onclick: pick, onchange: pick },
      h("span", { class: `zl-swatch cloud-swatch${c.tick ? " is-on" : ""}`, "aria-hidden": "true" }),
      h("div", { class: "zone-main" },
        h("div", { class: "zone-row" },
          h("label", { class: "cloud-name" }, box, " ", label),
          busy,
          h("span", { class: "zone-hint num" }, areaWords(c.ha)),
          h("button", { type: "button", class: "btn btn-icon cloud-show", dataset: key(`show-${c.id}`), title: t("clouds.show"), "aria-label": `${t("clouds.show")}: ${label}`, onclick: show }, icon("tpl-eye-icon")),
          trash),
        h("div", { class: "zone-row" }, menu),
        line,
        buttons.length ? h("div", { class: "zone-row" }, ...buttons) : null));
  }

  return {
    open,
    close,
    tile: () => cs.tile,
    rerender: () => cs.tile && ((cs.shapesKey = null), render()), // a language changed: every word again
    jobsChanged: () => cs.tile && refresh(),
  };
}
