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
  const box = h("div", { class: "photo-preview" },
    h("div", { class: "photo-shot" }, before,
      h("span", { class: "photo-label" }, t("settings.q.colours_preview_before"))),
    h("div", { class: "photo-shot" }, after,
      h("span", { class: "photo-label" }, t("settings.q.colours_preview_after"))),
    h("div", { class: "photo-words" }, ...words));
  // Same origin as the page (the engine, or a data: URL in the mock): no crossOrigin, which
  // would only make the browser refuse a copy it already cached without it.
  const image = new Image();
  image.addEventListener("load", () => {
    for (const [canvas, applied] of [[before, null], [after, look]]) {
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(image, 0, 0, size, size);
      if (applied && !photoUnchanged(applied)) {
        ctx.putImageData(adjustImageData(ctx.getImageData(0, 0, size, size), applied), 0, 0);
      }
    }
    note.textContent = t(noteKey);
  });
  image.addEventListener("error", () => {
    note.textContent = t("settings.q.colours_preview_failed");
    box.classList.add("is-quiet");
  });
  image.src = sample.url.startsWith("mock-photo:") ? mockPhoto(size, sample.url) : sample.url;
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
