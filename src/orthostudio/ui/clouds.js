// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
/**
 * The cloud mode of the Plan's map (``docs/specs/clouds.md`` 1.3).
 *
 * A view of what the engine says of a tile (``GET /api/clouds/{tile}``): the tile drawn as X-Plane
 * draws it, every cloud with its tick and its source, and After laying each ticked cloud's preview
 * over it in its order, Before the tile with no cloud replaced. Each tick or source is sent as it is
 * made and the tile read again: the page keeps no choice of its own. Apply builds the tile with its
 * changes; nothing else changes what X-Plane shows.
 */

import { codeText, fmtNum, t } from "./i18n.js";

/** How often a tile is read again while a task of it runs, in ms. */
const POLL_MS = 1500;
/** The closest Show brings the map. */
const SHOW_ZOOM = 15;

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
  if (params.progress != null) params.percent = Math.round(params.progress * 100);
  return t(line.key, params);
}

/** What the count line says of the changes Apply would make, only those it has. */
export function pendingWords(p) {
  const parts = ["replace", "another", "back", "waiting"].filter((k) => p?.[k] > 0).map((k) => t(`clouds.pending.${k}`, { n: p[k] }));
  return parts.length ? parts.join(" · ") : t("clouds.pending_none");
}

export function createCloudMode(ctx) {
  const { api, h, clear, toast, errorMessage } = ctx;
  const L = globalThis.L;
  const $ = (id) => document.getElementById(id);
  const cs = { tile: null, answer: null, view: "after", current: null, draft: null, poll: null, layer: null, busy: false };

  const names = () => Object.fromEntries((ctx.providers() || []).map((p) => [p.code, p.name || p.code]));

  async function open(tile) {
    if (cs.tile && cs.tile !== tile) close();
    Object.assign(cs, { tile, view: "after", current: null, draft: null, answer: null });
    ctx.showPlan();
    document.querySelector(".plan-flow")?.classList.add("is-clouds");
    $("clouds-panel").hidden = false;
    $("clouds-banner").hidden = false;
    ctx.planMap.takeInput({ click: onMapClick, dblclick: finishOutline, key: onKey });
    ctx.planMap.fitTile(tile);
    render();
    await refresh();
  }

  function close() {
    if (!cs.tile) return;
    stopPoll();
    cs.layer?.remove();
    cs.layer = null;
    cs.tile = null;
    cs.answer = null;
    $("clouds-panel").hidden = true;
    $("clouds-banner").hidden = true;
    document.querySelector(".plan-flow")?.classList.remove("is-clouds");
    ctx.planMap.takeInput(null);
  }

  function stopPoll() {
    if (cs.poll) clearTimeout(cs.poll);
    cs.poll = null;
  }

  async function refresh() {
    const tile = cs.tile;
    if (!tile) return;
    stopPoll();
    try {
      const answer = await api("GET", `/api/clouds/${encodeURIComponent(tile)}`);
      if (cs.tile !== tile) return;
      cs.answer = answer;
    } catch (err) {
      toast(errorMessage(err), "fail");
    }
    render();
    const a = cs.answer;
    if (a?.busy) cs.poll = setTimeout(refresh, POLL_MS);
  }

  /** An action sent to the engine, the tile read again after it. The engine refuses what runs
   * the cloud detector before it is downloaded (``CLOUD_DETECTOR_ASK``): the user is asked, and on
   * yes the same action goes again with ``download``. */
  async function send(method, path, body) {
    try {
      const res = await api(method, `/api/clouds/${encodeURIComponent(cs.tile)}${path}`, body);
      await refresh();
      return res;
    } catch (err) {
      const e = err?.detail?.error;
      if (e?.code === "CLOUD_DETECTOR_ASK" && !body?.download) {
        if (await ask(t("clouds.tool_title"), [t("clouds.tool_text", { mb: e.context?.mb })], t("clouds.tool_go"))) {
          return send(method, path, { ...(body || {}), download: true });
        }
      } else {
        toast(errorMessage(err), "fail");
      }
      await refresh();
      return null;
    }
  }

  // ------------------------------------------------------------------ the map

  function drawMap() {
    const map = ctx.planMap.leaflet();
    if (!map || !L) return;
    cs.layer?.remove();
    cs.layer = L.layerGroup().addTo(map);
    const a = cs.answer;
    if (!a) return;
    const file = (name) => `/api/clouds/${encodeURIComponent(cs.tile)}/files/${name}`;
    if (a.picture) {
      const [s, w, n, e] = a.picture.bounds;
      L.imageOverlay(file(a.picture.name), [[s, w], [n, e]], { interactive: false }).addTo(cs.layer);
    }
    if (cs.view === "after") {
      for (const c of a.clouds) {
        if (!c.preview) continue;
        const [s, w, n, e] = c.preview.bounds;
        L.imageOverlay(file(c.preview.name), [[s, w], [n, e]], { interactive: false }).addTo(cs.layer);
      }
    }
    for (const c of a.clouds) {
      const cls = ["cloud-outline", c.tick ? "is-on" : "", c.id === cs.current ? "is-current" : ""].join(" ");
      const poly = L.polygon(c.rings.map(latlngs), { className: cls, fill: true, fillOpacity: 0, weight: 2 });
      poly.on("click", (ev) => {
        L.DomEvent.stop(ev);
        if (!cs.draft) clickCloud(c);
      });
      poly.addTo(cs.layer);
    }
    if (cs.draft?.points.length) L.polyline(cs.draft.points, { className: "cloud-draft" }).addTo(cs.layer);
  }

  function clickCloud(c) {
    cs.current = c.id;
    if (c.actions.includes("tick") || c.actions.includes("untick")) tick(c, !c.tick);
    else render();
  }

  function onMapClick(latlng) {
    if (!cs.draft) return;
    cs.draft.points.push([latlng.lat, latlng.lng]);
    drawMap();
  }

  function onKey(ev) {
    if (!cs.draft) return;
    if (ev.key === "Escape") {
      cs.draft = null;
      render();
    } else if (ev.key === "Enter") finishOutline(ev);
  }

  async function finishOutline(ev) {
    if (!cs.draft) return;
    ev?.originalEvent?.preventDefault?.();
    const points = cs.draft.points;
    cs.draft = null;
    if (points.length < 3) {
      render();
      return;
    }
    await send("POST", "/outlines", { polygon: points.map(([lat, lon]) => [lon, lat]) });
  }

  // ------------------------------------------------------------------ actions

  function tick(c, on) {
    return send("POST", "/picks", { id: c.id, tick: on });
  }

  function choose(c, source) {
    return send("POST", "/picks", { id: c.id, source });
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

  async function apply(restore = false) {
    const p = cs.answer?.pending || {};
    const title = restore ? t("clouds.restore_title", { tile: cs.tile }) : t(p.changes === 1 ? "clouds.apply_title_one" : "clouds.apply_title", { tile: cs.tile, n: p.changes });
    const lines = restore
      ? [t("clouds.restore_text", { n: p.held })]
      : [pendingWords({ ...p, waiting: 0 }), ...(p.waiting ? [t("clouds.apply_waiting", { n: p.waiting })] : []), t("clouds.apply_text")];
    if (!(await ask(title, lines, restore ? t("clouds.restore_go") : t("clouds.apply_go")))) return;
    const res = await send("POST", "/apply", restore ? { restore: true } : {});
    if (res?.job) {
      toast(t("clouds.applying", { tile: cs.tile }));
      ctx.followJob?.(res.job);
    }
  }

  // ------------------------------------------------------------------ the panel

  function render() {
    renderBanner();
    renderPanel();
    drawMap();
  }

  function renderBanner() {
    const el = $("clouds-banner");
    if (!el) return;
    const a = cs.answer;
    const key = a?.views?.[cs.view];
    const text = cs.draft ? t("clouds.outlining") : key ? t(key) : "";
    clear(el).append(
      h("button", { type: "button", class: cs.view === "after" ? "btn btn-primary" : "btn", onclick: () => { cs.view = "after"; render(); } }, t("clouds.view_after")),
      h("button", { type: "button", class: cs.view === "before" ? "btn btn-primary" : "btn", onclick: () => { cs.view = "before"; render(); } }, t("clouds.view_before")),
      h("button", { type: "button", class: "btn", disabled: !a?.allowed.includes("outline"), onclick: () => { cs.draft = { points: [] }; render(); } }, t("clouds.outline")),
      h("span", { class: "clouds-view-text" }, text),
      h("button", { type: "button", class: "btn clouds-leave", onclick: close }, t("clouds.back")),
    );
  }

  function renderPanel() {
    const el = $("clouds-panel");
    if (!el) return;
    const a = cs.answer;
    const kids = [h("h2", { id: "clouds-title" }, t("clouds.title", { tile: cs.tile || "" }))];
    if (!a) {
      kids.push(h("p", null, t("clouds.reading")));
      clear(el).append(...kids);
      return;
    }
    if (a.line) kids.push(h("p", { class: "clouds-lead" }, lineWords(a.line)));
    for (const task of a.tasks) {
      if (task.cloud) continue; // shown in its row
      const pct = task.progress != null ? ` ${Math.round(task.progress * 100)} %` : "";
      kids.push(h("p", { class: "clouds-progress" }, t(`clouds.task.${task.kind}`) + " " + t(`clouds.phase.${task.phase}`) + pct, " ",
        h("button", { type: "button", class: "btn", onclick: () => api("POST", `/api/clouds/tasks/${task.id}/stop`).then(refresh) }, t("clouds.stop"))));
    }
    // what the tile allows is the engine's to say (``mode.possible``, ``mode.useful``)
    if (a.failed) {  // the tile's last task that failed, until its next one: the engine's words for it
      const e = a.failed.error || {};
      const [message, remedy] = codeText(e.code, e.context) || [e.message || "", e.remedy || ""];
      kids.push(h("p", { class: "clouds-warning", role: "status" }, `${t(`clouds.task.${a.failed.kind}`)} ${message} ${remedy || ""}`.trim()));
    }
    const can = new Set(a.allowed);
    const searching = a.tasks.some((x) => x.kind === "search");
    if (can.has("find") && !a.clouds.length && !searching) kids.push(h("p", null, a.search ? t("clouds.none_found") : t("clouds.not_looked")));
    const list = h("ul", { class: "zone-list" });
    for (const c of a.clouds) list.append(row(c));
    kids.push(list);
    const p = a.pending || {};
    kids.push(h("p", { class: "clouds-count" }, pendingWords(p)));
    kids.push(h("div", { class: "plan-actions" },
      h("button", { type: "button", class: "btn", disabled: !can.has("find") || searching, onclick: () => send("POST", "/find") }, t("clouds.find")),
      h("button", { type: "button", class: "btn btn-primary", disabled: !can.has("apply"), onclick: () => apply(false) }, t(p.changes === 1 ? "clouds.apply_one" : "clouds.apply", { n: p.changes || 0 })),
      h("button", { type: "button", class: "btn", disabled: !can.has("discard"), onclick: () => send("POST", "/discard") }, t("clouds.discard")),
      can.has("restore") ? h("button", { type: "button", class: "btn", onclick: () => apply(true) }, t("clouds.restore")) : null,
    ));
    clear(el).append(...kids);
  }

  /** One cloud, laid out as the Plan's zones: its tick and name, its source, what it waits for. */
  function row(c) {
    const n = names();
    const can = new Set(c.actions);
    const label = cloudName(c);
    const box = h("input", { type: "checkbox", disabled: !can.has("tick") && !can.has("untick"), onchange: (ev) => tick(c, ev.target.checked) });
    box.checked = c.tick;
    const menu = h("select", { "aria-label": t("clouds.source_of", { name: label }), disabled: !can.has("choose"), onchange: (ev) => choose(c, ev.target.value) },
      c.source ? null : h("option", { value: "", disabled: true }, t("clouds.choose")),
      ...c.menu.map((m) => h("option", { value: m.source, disabled: !m.proposable },
        [n[m.source] || m.source, m.answer ? t(`clouds.answer.${m.answer}`) : "", m.fit ? t("clouds.fit", { n: m.fit }) : ""].filter(Boolean).join(" · "))));
    menu.value = c.source || "";
    const task = cs.answer.tasks.find((x) => x.cloud === c.id);
    const show = () => {
      cs.current = c.id;
      // clear of the banner, which lies over the top of the map
      const top = ($("clouds-banner")?.offsetHeight || 0) + 24;
      ctx.planMap.leaflet()?.fitBounds(c.rings.map(latlngs).flat(), { maxZoom: SHOW_ZOOM, paddingTopLeft: [16, top], paddingBottomRight: [16, 16] });
      render();
    };
    const buttons = [
      can.has("get_images") ? h("button", { type: "button", class: "btn", onclick: () => send("POST", "/check", { id: c.id }) }, t("clouds.get_images")) : null,
      can.has("stop") && task ? h("button", { type: "button", class: "btn", onclick: () => api("POST", `/api/clouds/tasks/${task.id}/stop`).then(refresh) }, t("clouds.stop")) : null,
    ].filter(Boolean);
    const trash = can.has("remove")
      ? h("button", { type: "button", class: "btn btn-icon btn-danger btn-trash", "aria-label": t("clouds.remove"), title: t("clouds.remove"), onclick: () => send("DELETE", `/outlines/${c.id}`) },
        document.getElementById("tpl-trash-icon").content.firstElementChild.cloneNode(true))
      : null;
    return h("li", { class: "zone-item cloud-item", "aria-current": String(c.id === cs.current), dataset: { cloud: c.id, state: c.state } },
      h("span", { class: `zl-swatch cloud-swatch${c.tick ? " is-on" : ""}`, "aria-hidden": "true" }),
      h("div", { class: "zone-main" },
        h("div", { class: "zone-row" },
          h("label", { class: "cloud-name" }, box, " ", label),
          h("span", { class: "zone-hint num" }, areaWords(c.ha)),
          h("button", { type: "button", class: "btn", onclick: show }, t("clouds.show")),
          trash),
        h("div", { class: "zone-row" }, menu),
        c.line ? h("span", { class: `zone-hint${c.state === "waiting" ? " zone-hint-warn" : ""}`, role: task ? "status" : null }, lineWords(c.line, n)) : null,
        buttons.length ? h("div", { class: "zone-row" }, ...buttons) : null));
  }

  return {
    open,
    close,
    tile: () => cs.tile,
    rerender: () => cs.tile && render(),
    jobsChanged: () => cs.tile && refresh(),
  };
}
