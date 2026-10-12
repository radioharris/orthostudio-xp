// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The season of the photos, on the page: the arithmetic of ``orthostudio/live/look.py``, so that
// the preview of Settings shows the colours Live will give the textures. A test feeds both the
// same pixels and holds them equal to within one step (tests/test_live_look.py), and holds the
// seasons' values below equal to the engine's.

export const SEASONS = ["spring", "summer", "autumn", "winter"];

/** Live's own values of each season (look.DEFAULT_LOOKS): "Live's values" sets them back. */
export const DEFAULT_LOOKS = {
  spring: { brightness: 0, contrast: 0, saturation: 0, temperature: 0, vegetation: 0.15 },
  summer: { brightness: 0, contrast: 0, saturation: 0, temperature: 0, vegetation: 0 },
  autumn: { brightness: 0, contrast: 0, saturation: -0.1, temperature: 0, vegetation: 0.25 },
  winter: { brightness: 0, contrast: 0, saturation: -0.15, temperature: 0, vegetation: 0.6 },
};

/** The five sliders, in the order they apply, with their ends (settings.LookValues). */
export const LOOK_VALUES = [
  ["brightness", -0.5, 0.5],
  ["contrast", -0.5, 0.5],
  ["saturation", -1, 1],
  ["temperature", -1, 1],
  ["vegetation", -1, 1],
];

const LUMA = [0.299, 0.587, 0.114];
const WARMTH = 0.12;
const GREEN_STEPS = 12;
const TURN_DEG = 75;
const FRESHER = 0.6;

function hueTurn(deg) {
  const c = Math.cos((deg * Math.PI) / 180);
  const s = Math.sin((deg * Math.PI) / 180);
  return [
    0.299 + 0.701 * c + 0.168 * s, 0.587 - 0.587 * c + 0.330 * s, 0.114 - 0.114 * c - 0.497 * s,
    0.299 - 0.299 * c - 0.328 * s, 0.587 + 0.413 * c + 0.035 * s, 0.114 - 0.114 * c + 0.292 * s,
    0.299 - 0.300 * c + 1.250 * s, 0.587 - 0.588 * c - 1.050 * s, 0.114 + 0.886 * c - 0.203 * s,
  ];
}

/** Whether the five leave every pixel as it is. */
export function lookUnchanged(look) {
  return LOOK_VALUES.every(([key]) => !Number(look?.[key]));
}

/** A function of one pixel (0-255 in and out) in ``look``: pure, what the fidelity test compares. */
export function pixelInLook(look) {
  const b = Number(look.brightness) || 0;
  const c = Number(look.contrast) || 0;
  const s = Number(look.saturation) || 0;
  const t = Number(look.temperature) || 0;
  const g = Number(look.vegetation) || 0;
  const m = g > 0 ? hueTurn(TURN_DEG * g) : null;
  const clamp = (v) => Math.min(255, Math.max(0, Math.round(v)));
  return (r0, g0, b0) => {
    const green = g ? Math.min(1, Math.max(0, (g0 - Math.max(r0, b0)) / GREEN_STEPS)) : 0;
    let r = r0 * (1 + b);
    let gr = g0 * (1 + b);
    let bl = b0 * (1 + b);
    if (c) {
      r = (r - 127.5) * (1 + c) + 127.5;
      gr = (gr - 127.5) * (1 + c) + 127.5;
      bl = (bl - 127.5) * (1 + c) + 127.5;
    }
    if (s) {
      const grey = r * LUMA[0] + gr * LUMA[1] + bl * LUMA[2];
      r = grey + (r - grey) * (1 + s);
      gr = grey + (gr - grey) * (1 + s);
      bl = grey + (bl - grey) * (1 + s);
    }
    if (t) {
      r *= 1 + WARMTH * t;
      bl *= 1 - WARMTH * t;
    }
    if (m && green) {
      const tr = m[0] * r + m[1] * gr + m[2] * bl;
      const tg = m[3] * r + m[4] * gr + m[5] * bl;
      const tb = m[6] * r + m[7] * gr + m[8] * bl;
      r += green * (tr - r);
      gr += green * (tg - gr);
      bl += green * (tb - bl);
    } else if (g < 0 && green) {
      const grey = r * LUMA[0] + gr * LUMA[1] + bl * LUMA[2];
      const k = green * -g * FRESHER;
      r += k * (r - grey);
      gr += k * (gr - grey);
      bl += k * (bl - grey);
    }
    return [clamp(r), clamp(gr), clamp(bl)];
  };
}

/** ``image`` (an ImageData, RGBA) in ``look``'s colours, in place, alpha untouched. */
export function lookImageData(image, look) {
  if (lookUnchanged(look)) return image;
  const pixel = pixelInLook(look);
  const d = image.data;
  for (let i = 0; i < d.length; i += 4) {
    const [r, g, b] = pixel(d[i], d[i + 1], d[i + 2]);
    d[i] = r;
    d[i + 1] = g;
    d[i + 2] = b;
  }
  return image;
}
