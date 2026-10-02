// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The colour preview: one image of the ground, as the source delivers it and as a build will
// encode it. Settings shows it under its colour question, and Plan under step 3, so that the
// colours are judged where they are chosen and again where the build is launched (a user,
// 2026-09-18).
//
// The arithmetic is `colour.js`, which a test holds equal to the engine's `textures/colour.py`.
// Nothing here talks to the API: the caller passes the sample (`{url, tile, lat, lon}`) that
// `app.js` builds from the map's centre or a square of the Plan.

import { adjustImageData, photoUnchanged } from "./colour.js";
import { fmtNum, t } from "./i18n.js";

export const PREVIEW_SIZE = 148;

/** The images already asked for, by address: the image once it came, or that it failed (no photo
 * there: Bing's placeholder over water). Every change of a setting draws the preview anew; with
 * the image in hand it is painted at once, with no request and no empty frame for an instant, and
 * a place with no photo keeps its frames hidden: the two came back at each change of the source
 * or the colours, and the page jumped (a Windows VM, 2026-10-02). A failure is asked again after
 * a minute, a server that did not answer being no proof that there is no photo. */
const SEEN = new Map();
const SEEN_MAX = 24;
const FAILED_FOR_MS = 60_000;
/** The requests under way, by address: a redraw while one is out waits for it, not for another. */
const PENDING = new Map();
/** What each preview showed last (Settings and the Plan apart, by their words): an image, or no
 * photo. A new image, another source or another place, starts from it while it comes. */
const LAST = new Map();

function remember(url, entry) {
  SEEN.delete(url);
  SEEN.set(url, entry);
  while (SEEN.size > SEEN_MAX) SEEN.delete(SEEN.keys().next().value);
}

function known(url) {
  const entry = SEEN.get(url);
  if (entry?.failed && Date.now() - entry.at > FAILED_FOR_MS) {
    SEEN.delete(url);
    return null;
  }
  return entry || null;
}

/**
 * The two images and their words, as one element.
 *
 * ``sample`` is ``{url, tile, lat, lon}``; ``look`` the three values to apply (``colour.js``
 * ``photoValues``). ``noteKey`` and ``whereKey`` are the two lines under the images, so that
 * Settings speaks of the map's centre and Plan of the square that was chosen.
 */
export function colourPreview(h, sample, look, options = {}) {
  const {
    size = PREVIEW_SIZE,
    noteKey = "settings.q.colours_preview_note",
    whereKey = "settings.q.colours_preview_where",
    wide = false, // the two fill the width they are given, up to `size`, the words under them
  } = options;
  if (!sample || !sample.url) return null;
  const before = h("canvas", { width: size, height: size, class: "photo-canvas" });
  const after = h("canvas", { width: size, height: size, class: "photo-canvas" });
  const note = h("p", { class: "question-help" }, t("settings.q.colours_preview_wait"));
  const words = [note];
  if (whereKey) {
    words.push(h("p", { class: "question-help photo-where" },
      t(whereKey, { tile: sample.tile, lat: fmtNum(sample.lat, 3), lon: fmtNum(sample.lon, 3) })));
  }
  // what the canvases show, drawn once the image is there: compared by it (app.js sameNode)
  const shots = [
    h("div", { class: "photo-shot" }, before,
      h("span", { class: "photo-label" }, t("settings.q.colours_preview_before"))),
    h("div", { class: "photo-shot" }, after,
      h("span", { class: "photo-label" }, t("settings.q.colours_preview_after"))),
  ];
  const box = h("div", { class: wide ? "photo-preview is-wide" : "photo-preview", "data-version": JSON.stringify([sample.url, look, size]) },
    ...shots,
    h("div", { class: "photo-words" }, ...words));
  const slot = `${noteKey}|${whereKey}`;
  const paint = (image) => {
    for (const [canvas, applied] of [[before, null], [after, look]]) {
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(image, 0, 0, size, size);
      if (applied && !photoUnchanged(applied)) {
        ctx.putImageData(adjustImageData(ctx.getImageData(0, 0, size, size), applied), 0, 0);
      }
    }
  };
  // The images hide themselves, and the box keeps the attributes it was drawn with: a redraw
  // keeps a box only when it is the same as the one it draws anew (app.js morphNode). Marked on
  // the box, a preview with no photo was replaced at every change of a setting, its two images
  // back for an instant, the whole page growing then shrinking (the map over the Atlantic, in a
  // Windows VM, 2026-10-02).
  const quiet = (hidden) => {
    for (const shot of shots) shot.classList[hidden ? "add" : "remove"]("is-quiet");
  };
  const shown = (image) => {
    paint(image);
    quiet(false);
    note.textContent = t(noteKey);
    LAST.set(slot, { image });
  };
  const failed = () => {
    quiet(true);
    note.textContent = t("settings.q.colours_preview_failed");
    LAST.set(slot, { failed: true });
  };
  const seen = known(sample.url);
  if (seen?.image) shown(seen.image);
  else if (seen?.failed) failed();
  else {
    // not known yet: what the preview showed last stays until this image has come
    const last = LAST.get(slot);
    if (last?.image) paint(last.image);
    else if (last?.failed) quiet(true);
    let image = PENDING.get(sample.url);
    if (!image) {
      // Same origin as the page (the engine, or a data: URL in the mock): no crossOrigin, which
      // would only make the browser refuse a copy it already cached without it.
      image = new Image();
      const url = sample.url;
      PENDING.set(url, image);
      image.addEventListener("load", () => {
        PENDING.delete(url);
        remember(url, { image });
      });
      image.addEventListener("error", () => {
        PENDING.delete(url);
        remember(url, { failed: true, at: Date.now() });
      });
      image.src = url.startsWith("mock-photo:") ? mockPhoto(size, url) : url;
    }
    image.addEventListener("load", () => shown(image));
    image.addEventListener("error", failed);
  }
  return box;
}

/** The mock mode has no imagery: a ground-looking image drawn from the sample's own name, so the
 * preview can be seen and tested with no network. */
export function mockPhoto(size, seed) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  let n = 0;
  for (const ch of seed) n = (n * 31 + ch.charCodeAt(0)) % 100000;
  const rand = () => ((n = (n * 1103515245 + 12345) % 2147483648) / 2147483648);
  ctx.fillStyle = "#6f7a4e";
  ctx.fillRect(0, 0, size, size);
  for (let i = 0; i < 260; i++) {
    const x = rand() * size;
    const y = rand() * size;
    const r = 4 + rand() * 26;
    const green = 90 + Math.floor(rand() * 90);
    ctx.fillStyle = `rgb(${60 + Math.floor(rand() * 70)}, ${green}, ${40 + Math.floor(rand() * 50)})`;
    ctx.fillRect(x, y, r, r * (0.4 + rand()));
  }
  ctx.strokeStyle = "#b9b0a2";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(0, size * 0.62);
  ctx.bezierCurveTo(size * 0.4, size * 0.4, size * 0.6, size * 0.9, size, size * 0.55);
  ctx.stroke();
  return canvas.toDataURL("image/png");
}
