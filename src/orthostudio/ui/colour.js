// The colours of the photo, on the page: the same arithmetic as the engine's
// ``orthostudio/textures/colour.py``, so the preview shows what the build will encode.
//
// A test feeds both the same pixels and holds them equal to within one step
// (``tests/test_ui_colour.py``): the engine rounds half to even and JavaScript rounds half up,
// which is the only difference a pilot could never see.
//
// Values are deviations: 0 changes nothing, -0.3 takes 30 % away. Order: brightness, then
// contrast, then saturation, as Ortho4XP's filters apply them.

/** ITU-R BT.601 grey, the one the engine uses. */
export const LUMA = [0.299, 0.587, 0.114];

/** The looks the Settings question offers, and the numbers behind them (`config/overrides.py`). */
export const PHOTO_LOOKS = {
  as_delivered: { brightness: 0, contrast: 0, saturation: 0 },
  softer: { brightness: -0.03, contrast: 0, saturation: -0.15 },
  much_softer: { brightness: -0.06, contrast: -0.03, saturation: -0.3 },
};

/** Whether these three leave every pixel as it is. */
export function photoUnchanged({ brightness = 0, contrast = 0, saturation = 0 } = {}) {
  return brightness === 0 && contrast === 0 && saturation === 0;
}

/** The three values of an answer: a named look, or the draft's own numbers on "custom". */
export function photoValues(look, own = {}) {
  if (look === "custom") {
    return {
      brightness: Number(own.brightness) || 0,
      contrast: Number(own.contrast) || 0,
      saturation: Number(own.saturation) || 0,
    };
  }
  return PHOTO_LOOKS[look] || PHOTO_LOOKS.as_delivered;
}

/** One pixel, 0-255 in and out (pure: this is what the fidelity test compares). */
export function adjustPixel(r, g, b, { brightness = 0, contrast = 0, saturation = 0 } = {}) {
  let out = [r, g, b];
  if (brightness) out = out.map((v) => v * (1 + brightness));
  if (contrast) out = out.map((v) => (v - 127.5) * (1 + contrast) + 127.5);
  if (saturation) {
    const grey = out[0] * LUMA[0] + out[1] * LUMA[1] + out[2] * LUMA[2];
    out = out.map((v) => grey + (v - grey) * (1 + saturation));
  }
  return out.map((v) => Math.min(255, Math.max(0, Math.round(v))));
}

/** Apply the look to an ImageData in place (RGBA; alpha untouched) and give it back. */
export function adjustImageData(image, look) {
  if (photoUnchanged(look)) return image;
  const d = image.data;
  for (let i = 0; i < d.length; i += 4) {
    const [r, g, b] = adjustPixel(d[i], d[i + 1], d[i + 2], look);
    d[i] = r;
    d[i + 1] = g;
    d[i + 2] = b;
  }
  return image;
}
