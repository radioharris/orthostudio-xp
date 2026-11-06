# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Finding a tile's clouds, judging the other sources under each, and getting a cloud's images
(``clouds.md`` 4).

The search reads the tile as X-Plane draws it (``clouds.picture``) at about 15 m a pixel, and,
where its textures are finer, once more a level finer: the finer reading finds the small cumulus,
the coarser keeps the large pale veils (B1). Each cloud found is an outline with its shadow; each
other source is judged under it at the search's level: no picture there, the same photo as the
tile's, cloudy or clear, and how well its colours fit the photo around it.

Getting a cloud's images (the *check*) is what Apply lays from: the source's pieces at the level
each texture the cloud reaches reads it at, frozen into parts, with the digest of each texture as
built they were got on; Apply's own layer made from them (``clouds/layer.py``), reduced to the
map's level and laid where X-Plane shows that texture, over water at the share X-Plane gives the
photo. So the preview is what the texture gets.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import Polygon
from shapely.validation import make_valid

from orthostudio.clouds.layer import (
    RULE,
    decode_part,
    encode_part,
    layer,
    pieces,
    read_piece,
    ring_pixels,
)
from orthostudio.clouds.model import SHADOW, THICK, THIN, classify
from orthostudio.clouds.opencv import cv2
from orthostudio.clouds.picture import Picture, box_bounds, over_water, pixel_metres
from orthostudio.clouds.sources import SourcePieces
from orthostudio.clouds.tile import (
    TileClouds,
    entry_patches,
    find_part,
    laid_fields,
    part_path,
    patches_of,
    preview_path,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.graph.digest import digest_bytes
from orthostudio.imagery.grid import parse_texture_name, tile_to_wgs84
from orthostudio.imagery.providers import Provider
from orthostudio.model import TileRef

__all__ = ["Area", "Check", "Judgement", "check", "find", "lonlat", "preview", "screen"]

MIN_CLOUD_HA = 2.0
SHADOW_REACH_M = 600.0
"""How far from its cloud a shadow is still its own."""
SHADOW_WHOLE = 2.0
"""A shadow the reach touches is taken whole up to this many times the cloud (about its size)."""
MARGIN_PX = 2
BAND_M = 400.0
"""The band around a cloud a source's colours are compared with the photo's on."""
CLEAR_AT_MOST = 0.10
SAME_PHOTO = 0.9
"""A source whose lightness follows the tile's this closely under a cloud is the same photo."""
COVERAGE = 0.9
"""Below this share of a cloud covered, a source has no picture there."""
MAX_CLOUDS = 200
Phase = Callable[[str, int, int], None]


def _quiet(_p: str, _d: int, _t: int) -> None:
    return None


def lonlat(p: Picture, x: float, y: float) -> tuple[float, float]:
    """The ``(lon, lat)`` of the picture's pixel ``(x, y)``."""
    lat, lon = tile_to_wgs84((p.x0 + x) / 256, (p.y0 + y) / 256, p.zl)
    return lon, lat


def to_pixels(p: Picture, ring: Sequence[float]) -> np.ndarray:
    """A ring of the zones' format (``[lat0, lon0, ...]``) in the picture's pixels."""
    return ring_pixels(ring, p.zl, p.x0, p.y0)


# -- the search -----------------------------------------------------------------------------------


@dataclass(slots=True)
class Area:
    """A cloud in the picture's pixels over the box ``(x0, y0, x1, y1)``: the cloud alone
    (``core``), with its shadow and margin (``mask``)."""

    box: tuple[int, int, int, int]
    core: np.ndarray
    mask: np.ndarray

    def rings(self, p: Picture) -> list[list[float]]:
        """Its outline in the zones' ring format, closed."""
        x0, y0 = self.box[:2]
        ring: list[float] = []
        pts = _outline(self.mask)
        for x, y in [*pts, pts[0]]:
            lon, lat = lonlat(p, x0 + x + 0.5, y0 + y + 0.5)
            ring += [round(lat, 9), round(lon, 9)]
        return [ring]


def _lat(p: Picture) -> float:
    return lonlat(p, p.rgb.shape[1] / 2, p.rgb.shape[0] / 2)[1]


def _disc(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def _outline(mask: np.ndarray) -> np.ndarray:
    """The outer edge of ``mask``, simplified to a pixel and simple (made one piece where it
    touches itself): what the preview and Apply both take (B3)."""
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(cs, key=cv2.contourArea)
    pts = cv2.approxPolyDP(contour, 0.75, True).reshape(-1, 2)
    if len(pts) < 3 or Polygon(pts).is_valid:
        return pts
    whole = make_valid(Polygon(contour.reshape(-1, 2))).buffer(0.5, join_style="mitre")
    parts = [g for g in getattr(whole, "geoms", [whole]) if isinstance(g, Polygon)]
    ring = Polygon(max(parts, key=lambda g: g.area).exterior).simplify(0.75, preserve_topology=True)
    return np.array(ring.exterior.coords[:-1], np.float64)


def _one_piece(mask: np.ndarray) -> np.ndarray:
    for radius in (0, 2, 4, 8, 12):
        joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _disc(radius)) if radius else mask
        count, labels = cv2.connectedComponents(joined.astype(np.uint8))
        if count <= 2:
            return joined
    return (labels == int(np.argmax(np.bincount(labels.ravel())[1:])) + 1).astype(np.uint8)


def find_areas(classes: np.ndarray, metres: float) -> list[Area]:
    """The clouds of ``classes``, largest first, each with the shadow beside it; areas that
    overlap through any chain made one (one hole, one source, one colour match)."""
    cloud = _cloudy(classes).astype(np.uint8)
    grouped = cv2.morphologyEx(cv2.dilate(cloud, _disc(2)), cv2.MORPH_CLOSE, _disc(3))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    reach = max(1, round(SHADOW_REACH_M / metres))
    shadow_px = (classes == SHADOW).astype(np.uint8)
    _, shades, shade_stats, _ = cv2.connectedComponentsWithStats(shadow_px, connectivity=8)
    h, w = classes.shape

    def grown(x0: int, y0: int, x1: int, y1: int, pad: int) -> tuple[int, int, int, int]:
        return max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)

    found: list[tuple[int, Area]] = []
    for k in range(1, count):
        x, y, bw, bh, _a = stats[k]
        box = grown(x, y, x + bw, y + bh, reach + MARGIN_PX + 2)
        bx0, by0, bx1, by1 = box
        n = int(((labels[by0:by1, bx0:bx1] == k) & (cloud[by0:by1, bx0:bx1] > 0)).sum())
        if n < MIN_CLOUD_HA * 10_000 / metres**2:
            continue
        mine = (labels[by0:by1, bx0:bx1] == k).astype(np.uint8)
        near = (
            (cv2.dilate(mine, _disc(reach)) > 0) & (mine == 0) & (shadow_px[by0:by1, bx0:bx1] > 0)
        )
        whole = [
            int(s)
            for s in np.unique(shades[by0:by1, bx0:bx1][near])
            if shade_stats[s, cv2.CC_STAT_AREA] <= SHADOW_WHOLE * n
        ]
        for s in whole:  # the box takes each whole shadow in
            sx, sy, sw, sh, _ = shade_stats[s]
            g = grown(sx, sy, sx + sw, sy + sh, MARGIN_PX + 2)
            box = (min(box[0], g[0]), min(box[1], g[1]), max(box[2], g[2]), max(box[3], g[3]))
        bx0, by0, bx1, by1 = box
        core = (labels[by0:by1, bx0:bx1] == k) & (cloud[by0:by1, bx0:bx1] > 0)
        mine = (labels[by0:by1, bx0:bx1] == k).astype(np.uint8)
        near = (
            (cv2.dilate(mine, _disc(reach)) > 0) & (mine == 0) & (shadow_px[by0:by1, bx0:bx1] > 0)
        )
        shade = (near | np.isin(shades[by0:by1, bx0:bx1], whole)).astype(np.uint8)
        has_shadow = int(shade.sum()) >= 4
        piece = _one_piece(cv2.dilate(mine | (shade if has_shadow else 0), _disc(MARGIN_PX)))
        found.append((n, Area(box, core, piece > 0)))
    merged = merge_overlapping(found)
    merged.sort(key=lambda a: -a[0])
    return [a for _n, a in merged[:MAX_CLOUDS]]


def merge_overlapping(found: list[tuple[int, Area]]) -> list[tuple[int, Area]]:
    """Areas whose masks overlap, through any chain of them, made one."""
    parent = list(range(len(found)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (_n, a) in enumerate(found):
        for j in range(i + 1, len(found)):
            b = found[j][1]
            x0, y0 = max(a.box[0], b.box[0]), max(a.box[1], b.box[1])
            x1, y1 = min(a.box[2], b.box[2]), min(a.box[3], b.box[3])
            if x1 <= x0 or y1 <= y0:
                continue
            mine = a.mask[y0 - a.box[1] : y1 - a.box[1], x0 - a.box[0] : x1 - a.box[0]]
            theirs = b.mask[y0 - b.box[1] : y1 - b.box[1], x0 - b.box[0] : x1 - b.box[0]]
            if (mine & theirs).any():
                parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(found)):
        groups.setdefault(root(i), []).append(i)
    out: list[tuple[int, Area]] = []
    for members in groups.values():
        areas = [found[i][1] for i in members]
        box = (
            min(a.box[0] for a in areas),
            min(a.box[1] for a in areas),
            max(a.box[2] for a in areas),
            max(a.box[3] for a in areas),
        )
        core = np.zeros((box[3] - box[1], box[2] - box[0]), bool)
        mask = np.zeros_like(core)
        for a in areas:
            at = (
                slice(a.box[1] - box[1], a.box[3] - box[1]),
                slice(a.box[0] - box[0], a.box[2] - box[0]),
            )
            core[at] |= a.core
            mask[at] |= a.mask
        out.append((sum(found[i][0] for i in members), Area(box, core, mask)))
    return out


_RANK = np.array([0, 3, 2, 1], np.uint8)
"""A class's rank as a cloud: clear 0, shadow 1, thin 2, thick 3 (indexed by class)."""
_OF_RANK = np.array([0, SHADOW, THIN, THICK], np.uint8)


def merge_fine(quick: np.ndarray, qp: Picture, fine: np.ndarray, fp: Picture) -> np.ndarray:
    """``quick``'s classes with what the finer reading found where it did not: each pixel the
    most a cloud of itself and the 2 x 2 finer pixels it covers (B1)."""
    h, w = quick.shape
    top, left = fp.y0 % 2, fp.x0 % 2
    fh, fw = fine.shape
    grid = np.zeros((top + fh + (top + fh) % 2, left + fw + (left + fw) % 2), np.uint8)
    grid[top : top + fh, left : left + fw] = _RANK[fine]
    pooled = grid.reshape(grid.shape[0] // 2, 2, grid.shape[1] // 2, 2).max(axis=(1, 3))
    oy, ox = (fp.y0 - top) // 2 - qp.y0, (fp.x0 - left) // 2 - qp.x0
    seen = np.zeros((h, w), np.uint8)
    sy0, sx0, dy0, dx0 = max(0, -oy), max(0, -ox), max(0, oy), max(0, ox)
    ch, cw = min(pooled.shape[0] - sy0, h - dy0), min(pooled.shape[1] - sx0, w - dx0)
    if ch > 0 and cw > 0:
        seen[dy0 : dy0 + ch, dx0 : dx0 + cw] = pooled[sy0 : sy0 + ch, sx0 : sx0 + cw]
    return _OF_RANK[np.maximum(_RANK[quick], seen)]


def find(
    p: Picture,
    fine: Picture | None = None,
    *,
    cancel: threading.Event | None = None,
    phase: Phase = _quiet,
) -> tuple[list[Area], np.ndarray]:
    """The clouds of the picture ``p`` (and its finer reading), and the classes they were read
    from. Only what X-Plane shows is read: its own water alone holds no cloud to replace."""
    phase("looking", 0, 1)
    classes = classify(
        p.rgb, p.share > 0, cancel=cancel, progress=lambda d, t: phase("looking", d, t)
    )
    if fine is not None:
        phase("looking_fine", 0, 1)
        fc = classify(
            fine.rgb,
            fine.share > 0,
            cancel=cancel,
            regnety_alone=True,
            progress=lambda d, t: phase("looking_fine", d, t),
        )
        classes = merge_fine(classes, p, fc, fine)
    return find_areas(classes, pixel_metres(_lat(p), p.zl)), classes


# -- judging a source under a cloud --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Judgement:
    """What a source has under a cloud: ``clear``, ``cloudy``, ``same_photo``, ``no_picture``,
    ``did_not_answer``, ``refused``, ``not_a_picture``; how well its colours fit the photo
    around (``fit``, 0 to 100) and the gain and shift that match them (``match``)."""

    answer: str
    fit: int = 0
    match: tuple[tuple[float, ...], tuple[float, ...]] = ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0))

    @property
    def usable(self) -> bool:
        return self.answer in ("clear", "cloudy")


def _lum(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _correlation(a: np.ndarray, b: np.ndarray) -> float | None:
    """Pearson's r, or ``None`` where one is flat (a band of one sea colour)."""
    if len(a) < 2 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def judge(
    here: np.ndarray,
    src: np.ndarray,
    have: np.ndarray,
    inside: np.ndarray,
    core: np.ndarray,
    band: np.ndarray,
    cloudy: np.ndarray,
) -> Judgement:
    """A source (``src``, where it ``have`` a picture, ``cloudy`` where the detector sees cloud
    in it) against the photo ``here``, all over one window of the picture: under the cloud's
    ``core``, and on the ``band`` around its outline ``inside`` (others' clouds left out)."""
    if not core.any() or have[core].mean() < COVERAGE:
        return Judgement("no_picture")
    alike = _correlation(_lum(here)[core], _lum(src)[core])
    if alike is not None and alike >= SAME_PHOTO:
        return Judgement("same_photo")
    answer = "clear" if cloudy[core].mean() <= CLEAR_AT_MOST else "cloudy"
    ok = band & have
    if ok.sum() < 30:
        return Judgement(answer)
    r = _correlation(_lum(here)[ok], _lum(src)[ok])
    hb, sb = here[ok].astype(np.float32), src[ok].astype(np.float32)
    gain = np.clip(hb.std(0) / np.maximum(sb.std(0), 1.0), 0.5, 2.0)
    shift = hb.mean(0) - sb.mean(0) * gain
    return Judgement(
        answer,
        max(0, round(100 * r)) if r is not None else 0,
        (tuple(round(float(v), 4) for v in gain), tuple(round(float(v), 4) for v in shift)),
    )


def _inside(
    p: Picture, rings: Sequence[Sequence[float]], box: tuple[int, int, int, int]
) -> np.ndarray:
    """The cloud over ``box`` where X-Plane shows the photo: what a source is judged on, its
    coverage and its colours; the sea X-Plane draws alone is no part of a cloud."""
    x0, y0, x1, y1 = box
    out = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillPoly(out, [np.round(to_pixels(p, r) - (x0, y0)).astype(np.int32) for r in rings], 1)
    return (out > 0) & (p.share[y0:y1, x0:x1] > 0)


def _box(p: Picture, rings: Sequence[Sequence[float]], pad: int) -> tuple[int, int, int, int]:
    pts = np.concatenate([to_pixels(p, r) for r in rings])
    h, w = p.rgb.shape[:2]
    return (
        max(0, math.floor(pts[:, 0].min()) - pad),
        max(0, math.floor(pts[:, 1].min()) - pad),
        min(w, math.ceil(pts[:, 0].max()) + pad),
        min(h, math.ceil(pts[:, 1].max()) + pad),
    )


def _band(inside: np.ndarray, band_px: int, dirty: np.ndarray) -> np.ndarray:
    u8 = inside.astype(np.uint8)
    return (cv2.dilate(u8, _disc(band_px)) > 0) & (cv2.dilate(u8, _disc(1)) == 0) & ~dirty


# -- what the search and a check judge a source by, said once ----------------------------------


def _band_px(p: Picture) -> int:
    """The width of the band around a cloud a source's colours are matched on, in the picture's
    pixels (:data:`BAND_M`)."""
    return max(2, round(BAND_M / pixel_metres(_lat(p), p.zl)))


def _cloud_box(p: Picture, rings: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    """The box a source is read on around a cloud: its outline, the band and a margin; the
    search's, a check's and the preview's."""
    return _box(p, rings, _band_px(p) + 4)


def _reading_level(p: Picture, provider: Provider) -> int:
    """The level a source is judged at: one finer than the picture's where it has it."""
    return min(p.zl + 1, provider.max_zl)


def _cloudy(classes: np.ndarray) -> np.ndarray:
    """Where ``classes`` (the detector's) say cloud, thick or thin."""
    return (classes == THICK) | (classes == THIN)


def _not_ground(classes: np.ndarray, share: np.ndarray) -> np.ndarray:
    """What the band leaves out: clouds, their shadows, and X-Plane's water alone."""
    return _cloudy(classes) | (classes == SHADOW) | (share == 0)


def _core(inside: np.ndarray, classes: np.ndarray) -> np.ndarray:
    """Under a cloud, where the detector saw it; the whole outline when it saw nothing there."""
    seen = inside & (classes != 0)
    return seen if seen.any() else inside


def _lost(sources: SourcePieces, code: str, keys: Iterable[tuple[int, int, int]]) -> str | None:
    """What a source answered for its pieces ``keys`` when one is neither a picture nor none:
    ``refused`` when one was, else the first's answer; ``None`` when all came."""
    lost = [
        k for k in (sources.kind(code, key) for key in keys) if k not in ("picture", "no_picture")
    ]
    return None if not lost else "refused" if "refused" in lost else lost[0]


def _source_at(
    sources: SourcePieces, code: str, zl: int, p: Picture, box: tuple[int, int, int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """The source over ``box`` of the picture, read at ``zl`` and reduced to the picture's
    level; where it has a picture."""
    k = 2 ** (zl - p.zl)
    x0, y0, x1, y1 = box
    gx0, gy0, gx1, gy1 = (p.x0 + x0) * k, (p.y0 + y0) * k, (p.x0 + x1) * k, (p.y0 + y1) * k
    out = np.zeros((gy1 - gy0, gx1 - gx0, 3), np.uint8)
    have = np.zeros(out.shape[:2], np.uint8)
    n = 1 << zl
    for ty in range(gy0 // 256, (gy1 - 1) // 256 + 1):
        for tx in range(gx0 // 256, (gx1 - 1) // 256 + 1):
            piece = read_piece(sources.body(code, (tx % n, ty, zl)))
            if piece.kind != "picture":
                continue
            ox, oy = tx * 256 - gx0, ty * 256 - gy0
            sx0, sy0, dx0, dy0 = max(0, -ox), max(0, -oy), max(0, ox), max(0, oy)
            cw, ch = min(256 - sx0, out.shape[1] - dx0), min(256 - sy0, out.shape[0] - dy0)
            if cw > 0 and ch > 0:
                out[dy0 : dy0 + ch, dx0 : dx0 + cw] = piece.rgb[sy0 : sy0 + ch, sx0 : sx0 + cw]  # type: ignore[index]
                have[dy0 : dy0 + ch, dx0 : dx0 + cw] = piece.shows[sy0 : sy0 + ch, sx0 : sx0 + cw]  # type: ignore[index]
    size = (x1 - x0, y1 - y0)
    small = cv2.resize(out, size, interpolation=cv2.INTER_AREA)
    small_have = cv2.resize(have * 255, size, interpolation=cv2.INTER_AREA) >= 128
    return small, small_have


def screen(
    p: Picture,
    clouds: Sequence[Sequence[Sequence[float]]],
    classes: np.ndarray,
    sources: SourcePieces,
    *,
    cancel: threading.Event | None = None,
    phase: Phase = _quiet,
) -> list[dict[str, Judgement]]:
    """Every source of ``sources`` judged under each of ``clouds`` (their rings) at the picture's
    level: a source read one level finer when it has one, reduced, as the search's finer reading.
    A source with no picture at that level is read at its own finest."""
    band_px = _band_px(p)
    boxes = [_cloud_box(p, rings) for rings in clouds]
    dirty = _not_ground(classes, p.share)
    levels = {c: _reading_level(p, pr) for c, pr in sources.providers.items()}
    todo = {c: _keys_of(p, boxes, z) for c, z in levels.items()}
    phase("fetching", 0, sum(len(v) for v in todo.values()))
    sources.fetch(todo, cancel=cancel, progress=lambda d, t: phase("fetching", d, t))
    out: list[dict[str, Judgement]] = [{} for _ in clouds]
    # said cloud by cloud: a source judged under two hundred clouds took minutes, the line left
    # on the download's 100 % meanwhile (a user, 2026-11-06); a Stop heard at each one
    total, done = len(sources.providers) * len(clouds), 0
    phase("sources", 0, total)
    for code in sorted(sources.providers):
        for i, (rings, box) in enumerate(zip(clouds, boxes, strict=True)):
            if cancel is not None and cancel.is_set():
                raise OsxpError("SYS_CANCELLED")
            done += 1
            phase("sources", done, total)
            lost = _lost(sources, code, _keys_of(p, [box], levels[code]))
            if lost is not None:
                out[i][code] = Judgement(lost)
                continue
            src, have = _source_at(sources, code, levels[code], p, box)
            x0, y0, x1, y1 = box
            seen = (
                classify(src, have, cancel=cancel, regnety_alone=True)
                if have.any()
                else np.zeros(have.shape, np.uint8)
            )
            inside = _inside(p, rings, box)
            out[i][code] = judge(
                p.photo[y0:y1, x0:x1],
                src,
                have,
                inside,
                _core(inside, classes[y0:y1, x0:x1]),
                _band(inside, band_px, dirty[y0:y1, x0:x1]),
                _cloudy(seen),
            )
    return out


def _keys_of(
    p: Picture, boxes: Sequence[tuple[int, int, int, int]], zl: int
) -> set[tuple[int, int, int]]:
    k, n = 2 ** (zl - p.zl), 1 << zl
    keys: set[tuple[int, int, int]] = set()
    for x0, y0, x1, y1 in boxes:
        gx0, gy0, gx1, gy1 = (p.x0 + x0) * k, (p.y0 + y0) * k, (p.x0 + x1) * k, (p.y0 + y1) * k
        keys |= {
            (tx % n, ty, zl)
            for ty in range(gy0 // 256, (gy1 - 1) // 256 + 1)
            for tx in range(gx0 // 256, (gx1 - 1) // 256 + 1)
        }
    return keys


# -- getting a cloud's images ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    """What getting a cloud's images from one source gave: its judgement at the tile's sharpness,
    and when usable the cloud as Apply lays it (a ``clouds.tile`` entry)."""

    judgement: Judgement
    entry: dict[str, Any] | None = None


def _reduce(arr: np.ndarray, k: int) -> np.ndarray:
    """``arr`` (height and width multiples of ``k``) averaged ``k`` x ``k``."""
    if k == 1:
        return arr
    h, w = arr.shape[:2]
    return arr.reshape(h // k, k, w // k, k, *arr.shape[2:]).mean(axis=(1, 3))


def check(
    p: Picture,
    tile: TileRef,
    cid: str,
    rings: Sequence[Sequence[float]],
    code: str,
    shown: TileClouds,
    sources: SourcePieces,
    *,
    classes: np.ndarray | None = None,
    again: bool = False,
    cancel: threading.Event | None = None,
    phase: Phase = _quiet,
) -> Check:
    """Get the images of the cloud ``cid`` (its ``rings``) from the source ``code`` of
    ``sources`` at the tile's sharpness, freeze them into parts, and judge them as the search
    does."""
    provider = sources.providers[code]
    textures = shown.textures
    c = {
        "id": cid,
        "source": code,
        "match": [[1.0] * 3, [0.0] * 3],
        "rings": [list(r) for r in rings],
    }
    reached = patches_of(c, tile, textures, lambda t: min(t.zl, provider.max_zl))
    if not reached:
        return Check(Judgement("no_picture"))
    plain = {n: shown.plain(n) for n in reached}  # the folder no other holds meanwhile (tasks)
    by_name = {n: parse_texture_name(n) for n in reached}
    box = _cloud_box(p, rings)
    # the source around the cloud too, at the search's reading of it: its colours are matched
    # to the photo's on the band around the outline, which the layer does not reach
    band_zl = _reading_level(p, provider)
    around = _keys_of(p, [box], band_zl)
    todo = {code: {k for n, pa in reached.items() for k in pieces(pa, by_name[n])}}
    phase("fetching", 0, len(todo[code]) + len(around))
    sources.fetch(
        {code: todo[code] | around},
        again=again,
        cancel=cancel,
        progress=lambda d, t: phase("fetching", d, t),
    )
    lost = _lost(sources, code, todo[code] | around)
    if lost is not None:  # a part frozen with passing holes would lay them for good
        return Check(Judgement(lost))
    phase("making", 0, len(reached))
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    lay_rgb = np.zeros((h, w, 3), np.float32)
    lay_a = np.zeros((h, w), np.float32)
    entry_parts: dict[str, list[Any]] = {}
    for n, (name, pa) in enumerate(sorted(reached.items()), start=1):
        t = by_name[name]
        got = {k: sources.body(code, k) for k in pieces(pa, t)}
        data = encode_part(got)
        digest = digest_bytes(data)
        path = part_path(digest)
        if find_part(digest) is None:  # not there whole (a power cut tore it): written again
            atomic_write_bytes(path, data, fsync=True)
        entry_parts[name] = [pa.source_zl, digest]
        la = layer(pa, t, got)
        if la is not None:
            _lay_reduced(la, t, p, box, name, lay_rgb, lay_a)
        phase("making", n, len(reached))
    inside = _inside(p, rings, box)
    src = np.clip(lay_rgb, 0, 255).astype(np.uint8)
    # where the source has a picture: its layer there, the soft edge of it too, which is no hole.
    # Taken over half the layer, every cloud under some 30 ha lost its edge and was said to have
    # no picture in every source (+56-131, Tick all, a user, 2026-11-06); how much of the cloud
    # it covers is the judging's to say, as for the search (``judge``)
    have = lay_a > 0
    cls = classes[y0:y1, x0:x1] if classes is not None else np.zeros((h, w), np.uint8)
    dirty = _not_ground(cls, p.share[y0:y1, x0:x1])
    seen = classify(src, have, cancel=cancel, regnety_alone=True)
    core = _core(inside, cls)
    here = p.photo[y0:y1, x0:x1]
    band = _band(inside, _band_px(p), dirty)
    # what it has under the cloud, at the tile's sharpness; its colours, on the band around
    sharp = judge(here, src, have, inside, core, np.zeros_like(band), _cloudy(seen))
    if not sharp.usable:
        return Check(sharp)
    # its colours, matched on the band at the reading the screening judged: there it answers too
    wide, wide_have = _source_at(sources, code, band_zl, p, box)
    fitted = judge(here, wide, wide_have, inside, core, band, np.zeros_like(band))
    if not fitted.usable:
        return Check(fitted)
    j = Judgement(sharp.answer, fitted.fit, fitted.match)
    entry = {
        "id": cid,
        "source": code,
        "match": [list(j.match[0]), list(j.match[1])],
        "rings": [list(r) for r in rings],
        "parts": entry_parts,
        "plain": plain,
    }
    # its preview made now, in the task, as After will show it: made by the page's reading, a
    # dozen of them took a reading 6.5 s (a user's 189 clouds, 2026-11-07)
    preview(p, tile, entry, shown.built, sorted(entry_parts))
    return Check(j, entry)


def preview(
    p: Picture, tile: TileRef, entry: Mapping[str, Any], built: str, textures: Sequence[str]
) -> tuple[str, list[float]] | None:
    """After's picture of the cloud ``entry`` (a ``clouds.tile`` entry) in the ``textures`` of it
    Apply makes again (``state.View.previews``), on ``p``, a picture of the tile the build placed
    (``built``): the name of its PNG and its ``[south, west, north, east]``; ``None`` when one of
    its frozen parts there is found nowhere. Named by what it is made of, the build, the level, the
    way clouds are laid, what lays the entry and the textures, so it is made once, when it is first
    shown, and again only when one of them changes."""
    names = list(textures)
    laid = json.dumps(laid_fields(entry), sort_keys=True)
    out = preview_path(tile, f"{built}:{p.zl}:{RULE}:{laid}:{','.join(names)}")
    box = _cloud_box(p, entry["rings"])
    if not out.exists() and not _make_preview(p, tile, entry, box, out, names):
        return None
    return out.name, box_bounds(p, box)


def _make_preview(
    p: Picture,
    tile: TileRef,
    entry: Mapping[str, Any],
    box: tuple[int, int, int, int],
    out: Path,
    names: Sequence[str],
) -> bool:
    """The preview written at ``out``: the cloud's layers in the textures ``names``, made from its
    frozen parts there by the laying's own function, in its colours, reduced as the textures'
    mips are, laid where X-Plane shows each texture, over water at the share X-Plane gives the
    photo, cut to the tile."""
    textures = [parse_texture_name(n) for n in names]
    x0, y0, x1, y1 = box
    rgb = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
    alpha = np.zeros((y1 - y0, x1 - x0), np.float32)
    for name, pa in entry_patches(entry, tile, textures).items():
        path = find_part(str(entry["parts"][name][1]))
        if path is None:
            return False
        t = parse_texture_name(name)
        la = layer(pa, t, decode_part(path.read_bytes()))
        if la is not None:
            _lay_reduced(la, t, p, box, name, rgb, alpha)
    shown = over_water(np.clip(rgb, 0, 255), p.share[y0:y1, x0:x1])
    rgba = np.dstack([shown, np.rint(np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
    good, png = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    if not good:
        raise OsxpError(
            "SYS_INTERNAL_ERROR",
            context={"type": "PNG", "detail": "a cloud's preview could not be encoded"},
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(out, png.tobytes(), fsync=True)
    return True


def _lay_reduced(
    la: Any,
    t: Any,
    p: Picture,
    box: tuple[int, int, int, int],
    name: str,
    rgb: np.ndarray,
    alpha: np.ndarray,
) -> None:
    """The layer ``la`` of texture ``t`` in its colours (``Layer.rgb``, Apply's) reduced to
    the picture's level and laid in ``rgb`` and ``alpha`` (over ``box`` of the picture) where
    X-Plane shows that texture."""
    k = 2 ** (t.zl - p.zl)
    wx0, wy0, wx1, wy1 = la.window
    # the window aligned on the picture's pixels, what it does not cover left transparent
    ax0, ay0 = (wx0 // k) * k, (wy0 // k) * k
    ax1, ay1 = -(-wx1 // k) * k, -(-wy1 // k) * k
    src = np.zeros((ay1 - ay0, ax1 - ax0, 3), np.float32)
    a = np.zeros((ay1 - ay0, ax1 - ax0), np.float32)
    a[wy0 - ay0 : wy1 - ay0, wx0 - ax0 : wx1 - ax0] = la.alpha.astype(np.float32) / 255
    for r0 in range(0, wy1 - wy0, 512):  # its colours a few rows at a time (``Layer.rgb``)
        r1 = min(wy1 - wy0, r0 + 512)
        src[wy0 - ay0 + r0 : wy0 - ay0 + r1, wx0 - ax0 : wx1 - ax0] = la.rgb(r0, r1)
    # alpha-weighted, in linear light as the textures' mips are made (``gamma22``)
    ra = _reduce(a, k)
    lin = _reduce(np.power(src / 255.0, 2.2) * a[..., None], k)
    colour = np.power(lin / np.maximum(ra[..., None], 1e-6), 1 / 2.2) * 255.0
    # the picture's pixels of this window, and where X-Plane shows this texture there
    px0 = (t.til_x * 256 + ax0) // k - p.x0 - box[0]
    py0 = (t.til_y * 256 + ay0) // k - p.y0 - box[1]
    h, w = alpha.shape
    sx0, sy0, dx0, dy0 = max(0, -px0), max(0, -py0), max(0, px0), max(0, py0)
    ch, cw = min(ra.shape[0] - sy0, h - dy0), min(ra.shape[1] - sx0, w - dx0)
    if ch <= 0 or cw <= 0:
        return
    index = p.names.index(name) if name in p.names else -2
    shows = p.shows[box[1] + dy0 : box[1] + dy0 + ch, box[0] + dx0 : box[0] + dx0 + cw] == index
    here_a = np.where(shows, ra[sy0 : sy0 + ch, sx0 : sx0 + cw], 0)
    sub = (slice(dy0, dy0 + ch), slice(dx0, dx0 + cw))
    # colours where its alpha shows, alpha-weighted mean colour per picture pixel
    take = here_a > alpha[sub]
    alpha[sub] = np.maximum(alpha[sub], here_a)
    np.copyto(rgb[sub], colour[sy0 : sy0 + ch, sx0 : sx0 + cw], where=take[..., None])
