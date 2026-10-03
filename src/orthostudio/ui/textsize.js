// OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
// Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
// The size of the page's text, at the foot of the page beside the zoom. A module of its own, as
// zoom.js is: OrthoStudio XP Live's page (live.js) loads both, and its foot is OrthoStudio XP's.

import { t } from "./i18n.js";

/** The sizes of the page's text styles (styles.css `:root`, which a test keeps equal), in px. */
export const TEXT_TOKENS = { "--fs-title-1": 18, "--fs-title-2": 15, "--fs-title-3": 13.5, "--fs": 13.5, "--fs-2": 12.5, "--fs-3": 11.5 };
const TEXT_SIZE_KEY = "osxp.text_size";

/** The sizes the page's text can take, in percent of its own. */
export const TEXT_SIZES = [100, 115, 130];

/**
 * The page's text at `percent` of its own size: its styles grow, the controls' own sizes and the
 * map do not, which is the zoom's work. A user on an ultrawide screen found the text too small
 * (TinkerNZ, 2026-10-03). Kept in the page's storage, as the zoom is, for the next start.
 */
export function applyTextSize(percent, root = document.documentElement) {
  const scale = Number(percent) / 100;
  const own = !Number.isFinite(scale) || scale <= 0 || scale === 1;
  for (const [token, px] of Object.entries(TEXT_TOKENS)) {
    if (own) root.style.removeProperty(token);
    else root.style.setProperty(token, `${Number((px * scale).toFixed(2))}px`);
  }
  try {
    if (own) localStorage.removeItem(TEXT_SIZE_KEY);
    else localStorage.setItem(TEXT_SIZE_KEY, String(percent));
  } catch (_e) {
    // ignore: the settings say it again at the next start
  }
}

/** The text size the page was left at, one of TEXT_SIZES. */
export function savedTextSize() {
  try {
    const saved = Number(localStorage.getItem(TEXT_SIZE_KEY));
    return TEXT_SIZES.includes(saved) ? saved : 100;
  } catch (_e) {
    return 100;
  }
}

/**
 * The text size's own control at the foot of the page, `Aa` and its sizes, beside the zoom:
 * applied at once and kept, with nothing to save. It was a question of Settings first, saved with
 * the rest, and a user wanted it at hand and at once (2026-10-03). In a browser too: the page draws
 * its text itself. Returns what writes its sizes again in the page's language.
 */
export function bindTextSize(select) {
  if (!select) return null;
  const label = () => {
    const now = savedTextSize();
    select.replaceChildren(...TEXT_SIZES.map((size) => {
      const option = document.createElement("option");
      option.value = String(size);
      option.textContent = t("app.zoom_percent", { percent: size });
      option.selected = size === now;
      return option;
    }));
    select.value = String(now);
  };
  label();
  select.addEventListener("change", () => applyTextSize(Number(select.value)));
  return label;
}
