# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Witness tiles: what a change touches, against the last release.

See ``docs/testing/witness-tiles.md``.

``build`` builds the tiles of ``tiles.txt`` (and with ``--random N`` N more, drawn among X-Plane's
Global Scenery) through the app's own path at a new user's settings, up to their DSF: the terrain,
without imagery. It keeps a small record of each tile in ``--out``: the digests of the vector
files and of the DSF, the counts of the graph, and the mesh itself. ``compare`` sets two such
folders side by side and says, tile by tile, whether anything changed, and where and how much.

Run ``build`` with the Python of the checkout to measure and ``OSXP_HOME`` set to a folder of its
own, never your real one: the settings and the cache of the builds go there, the cache emptied
after each batch. The library's address and key are copied from your settings, never printed.

    cd <checkout of the release> && OSXP_HOME=/tmp/w .venv/bin/python <this> build --out REF
    cd <checkout to check>       && OSXP_HOME=/tmp/w .venv/bin/python <this> build --out NEW
    .venv/bin/python <this> compare REF NEW [--report FILE]

``--random N --seed S`` must be the same on both sides; a tile already in ``--out`` is kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

TILES_FILE = Path(__file__).with_name("tiles.txt")
FORMAT = "osxp-witness-1"
GLOBAL_SCENERY = Path("Global Scenery") / "X-Plane 12 Global Scenery" / "Earth nav data"
M_PER_DEG = 111320.0
NEEDLE_M2 = 1e-4
"""A triangle under 1 cm2 is a needle (the measure of the 1.0.2 campaign)."""
CELL_DEG = 0.01
"""Changed triangles are gathered into cells of a hundredth of a degree (about 1 km)."""
LAND_MOVE_M = 0.01
"""A land vertex that moved more than this is reported: land heights come from the relief alone."""


# -- the list ------------------------------------------------------------------------------------


def witness_tiles(path: Path = TILES_FILE) -> dict[str, str]:
    """``tile -> why`` from ``tiles.txt``, in its order."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tile, _, why = line.partition(" ")
            out[tile] = why.strip()
    return out


def scenery_tiles(xplane: Path) -> list[str]:
    """Every tile of X-Plane's Global Scenery (its Demo Areas left out), sorted."""
    root = xplane / GLOBAL_SCENERY
    return sorted(
        f.stem for d in root.iterdir() if d.is_dir() for f in d.iterdir() if f.suffix == ".dsf"
    )


def random_tiles(pool: list[str], n: int, seed: int, exclude: set[str]) -> list[str]:
    """``n`` tiles of ``pool`` outside ``exclude``, the same for the same seed and pool."""
    candidates = [t for t in pool if t not in exclude]
    return sorted(random.Random(seed).sample(candidates, min(n, len(candidates))))


# -- measures ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mesh:
    """What the comparison reads of a mesh: absolute lon, lat, z; corners; 0 land, 1 inland
    water, 2 sea per triangle (``remap_tri_types``)."""

    vertices: np.ndarray
    tris: np.ndarray
    types: np.ndarray


def load_mesh(path: Path) -> Mesh:
    from orthostudio.dsf.recut import remap_tri_types
    from orthostudio.mesh.mesh_file import read_mesh_npz

    m = read_mesh_npz(path)
    types = remap_tri_types(np.asarray(m.tri_attr), m.version, False)
    return Mesh(np.asarray(m.vertices), np.asarray(m.tris), types)


def _local_m(mesh: Mesh, tile: str) -> np.ndarray:
    lat, lon = int(tile[:3]), int(tile[3:])
    v = mesh.vertices
    x = (v[:, 0] - lon) * M_PER_DEG * math.cos(math.radians(lat + 0.5))
    y = (v[:, 1] - lat) * M_PER_DEG
    return np.column_stack([x, y])


def triangle_areas(mesh: Mesh, tile: str) -> np.ndarray:
    p = _local_m(mesh, tile)[mesh.tris]
    return np.abs(
        0.5
        * (
            (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
            - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
        )
    )


def mesh_measures(mesh: Mesh, tile: str) -> dict[str, Any]:
    a = triangle_areas(mesh, tile)
    return {
        "vertices": len(mesh.vertices),
        "triangles": len(mesh.tris),
        "area_m2": {
            name: round(float(a[mesh.types == code].sum()), 1)
            for code, name in ((0, "land"), (1, "inland"), (2, "sea"))
        },
        "needles": int((a < NEEDLE_M2).sum()),
        "fan": int(np.bincount(mesh.tris.ravel()).max()) if len(mesh.tris) else 0,
    }


def _rows(mesh: Mesh) -> np.ndarray:
    """One row per triangle: its corners rounded to 1e-8 degree, sorted, and its type."""
    q = np.round(mesh.vertices[:, :2] * 1e8).astype(np.int64)
    c = q[mesh.tris]
    order = np.lexsort((c[:, :, 1], c[:, :, 0]), axis=1)
    c = np.take_along_axis(c, order[:, :, None], 1).reshape(len(mesh.tris), 6)
    rows = np.ascontiguousarray(np.column_stack([c, mesh.types.astype(np.int64)]))
    return rows.view([("", np.int64)] * 7).ravel()


def _positions(mesh: Mesh) -> np.ndarray:
    q = np.ascontiguousarray(np.round(mesh.vertices[:, :2] * 1e8).astype(np.int64))
    return q.view([("", np.int64)] * 2).ravel()


def locate(mesh: Mesh, points: np.ndarray, cell: float = 0.002) -> tuple[np.ndarray, np.ndarray]:
    """The triangle of ``mesh`` holding each point (-1 when none) and the point's height on it.

    The triangles are binned into cells of ``cell`` degrees by their bounding box; each point is
    tested against the triangles of its cell only."""
    corners = mesh.vertices[mesh.tris][:, :, :2]
    lo, hi = corners.min(1), corners.max(1)
    origin = lo.min(0)
    c0 = np.floor((lo - origin) / cell).astype(np.int64)
    c1 = np.floor((hi - origin) / cell).astype(np.int64)
    width = int(c1[:, 0].max()) + 1
    nx, ny = c1[:, 0] - c0[:, 0] + 1, c1[:, 1] - c0[:, 1] + 1
    span = nx * ny
    tri = np.repeat(np.arange(len(span)), span)
    k = np.arange(len(tri)) - np.repeat(np.cumsum(span) - span, span)
    cx = c0[tri, 0] + k % np.repeat(nx, span)
    cy = c0[tri, 1] + k // np.repeat(nx, span)
    key = cy * width + cx
    order = np.argsort(key, kind="stable")
    key, tri = key[order], tri[order]
    found = np.full(len(points), -1, dtype=np.int64)
    height = np.full(len(points), np.nan)
    pc = np.floor((points - origin) / cell).astype(np.int64)
    inside = (pc[:, 0] >= 0) & (pc[:, 0] < width) & (pc[:, 1] >= 0)
    pkey = np.where(inside, pc[:, 1] * width + pc[:, 0], -1)
    start, stop = np.searchsorted(key, pkey, "left"), np.searchsorted(key, pkey, "right")
    for s in range(0, len(points), 100_000):
        idx = np.arange(s, min(s + 100_000, len(points)))
        n = stop[idx] - start[idx]
        if not n.sum():
            continue
        p = np.repeat(idx, n)
        cand = tri[np.repeat(start[idx], n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)]
        a, b, c = (mesh.vertices[mesh.tris[cand, j]] for j in range(3))
        q = points[p]
        d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = d != 0
        d = np.where(ok, d, 1)
        w1 = (
            (b[:, 1] - c[:, 1]) * (q[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (q[:, 1] - c[:, 1])
        ) / d
        w2 = (
            (c[:, 1] - a[:, 1]) * (q[:, 0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (q[:, 1] - c[:, 1])
        ) / d
        w3 = 1 - w1 - w2
        hit = ok & (w1 >= -1e-9) & (w2 >= -1e-9) & (w3 >= -1e-9)
        z = w1 * a[:, 2] + w2 * b[:, 2] + w3 * c[:, 2]
        first = np.flatnonzero(hit)
        first = first[np.unique(p[first], return_index=True)[1]]
        found[p[first]] = cand[first]
        height[p[first]] = z[first]
    return found, height


def _samples(mesh: Mesh, tris: np.ndarray) -> np.ndarray:
    """Corners, edge middles and centre of ``tris``, with their height on ``mesh`` (lon, lat, z)."""
    a, b, c = (mesh.vertices[mesh.tris[tris, j]] for j in range(3))
    return np.concatenate([a, b, c, (a + b) / 2, (b + c) / 2, (c + a) / 2, (a + b + c) / 3])


def _stats(dz: np.ndarray, at: np.ndarray) -> dict[str, Any]:
    i = int(np.argmax(dz))
    return {
        "median": round(float(np.median(dz)), 3),
        "p99": round(float(np.percentile(dz, 99)), 3),
        "max": round(float(dz[i]), 2),
        "max_at": [round(float(at[i, 1]), 5), round(float(at[i, 0]), 5)],
        "over_1m": int((dz > 1).sum()),
    }


def surface_moves(ref: Mesh, new: Mesh, gone: np.ndarray, came: np.ndarray) -> dict[str, Any]:
    """How far the land surface of one mesh is from the other's where the triangles changed: at
    the corners, edge middles and centre of every land triangle found in one mesh only, each set
    on the other mesh's surface. Two surfaces of triangles differ most at the corners and where
    edges cross, which these points come close to.

    A triangle whose three corners are all in the other mesh is only *cut again*: four points
    on a circle (the corners of a rectangle) have two diagonals Triangle may choose between, and
    it picks by the order it reads the points in, so a change anywhere in the tile can turn a
    diagonal elsewhere. Both are the same relief cut two ways; they are counted apart from the
    triangles that have a new corner, which are a change of the terrain itself."""
    moves: dict[bool, list[np.ndarray]] = {True: [], False: []}
    where: dict[bool, list[np.ndarray]] = {True: [], False: []}
    for a, changed, b in ((new, came, ref), (ref, gone, new)):
        tris = np.flatnonzero(changed & (a.types == 0))
        if not len(tris):
            continue
        known = np.isin(_positions(a), _positions(b))
        recut = known[a.tris[tris]].all(1)
        pts = _samples(a, tris)
        flag = np.tile(recut, 7)
        tri, z = locate(b, pts[:, :2])
        land = (tri >= 0) & (b.types[np.maximum(tri, 0)] == 0)
        for kind in (True, False):
            keep = land & (flag == kind)
            moves[kind].append(np.abs(pts[keep, 2] - z[keep]))
            where[kind].append(pts[keep, :2])
    out: dict[str, Any] = {}
    for kind, name in ((False, "land_surface_move_m"), (True, "diagonal_turn_m")):
        dz = np.concatenate(moves[kind]) if moves[kind] else np.zeros(0)
        if len(dz):
            out[name] = _stats(dz, np.concatenate(where[kind]))
    return out


def compare_meshes(ref: Mesh, new: Mesh, tile: str) -> dict[str, Any]:
    """Where and how much ``new`` differs from ``ref``: the triangles found in one only (shape and
    type), gathered into cells; the heights of the vertices at the same place in both."""
    out: dict[str, Any] = {}
    rr, rn = _rows(ref), _rows(new)
    gone, came = ~np.isin(rr, rn), ~np.isin(rn, rr)
    ar, an = triangle_areas(ref, tile), triangle_areas(new, tile)
    out["triangles_kept"] = round(float(1 - came.mean()), 6) if len(rn) else 1.0
    # Water is cut again far around a change (big flat triangles), which no pilot sees: the places
    # are those of the land triangles that changed, the water's only counted.
    cells: dict[tuple[int, int], float] = defaultdict(float)
    water_m2 = 0.0
    for mesh, mask, area in ((ref, gone, ar), (new, came, an)):
        water_m2 += float(area[mask & (mesh.types != 0)].sum()) / 2
        land = mask & (mesh.types == 0)
        if land.any():
            centre = mesh.vertices[mesh.tris[land]][:, :, :2].mean(1)
            keys = np.floor(centre / CELL_DEG).astype(np.int64)
            for (kx, ky), a in zip(map(tuple, keys), area[land], strict=True):
                cells[(kx, ky)] += float(a) / 2
    places = sorted(cells.items(), key=lambda kv: -kv[1])
    out["places"] = len(places)
    out["changed_m2"] = round(sum(cells.values()), 1)
    out["water_recut_m2"] = round(water_m2, 1)
    out["largest_places"] = [
        {
            "lat": round((ky + 0.5) * CELL_DEG, 3),
            "lon": round((kx + 0.5) * CELL_DEG, 3),
            "m2": round(a),
        }
        for (kx, ky), a in places[:3]
    ]
    # heights: the vertices standing alone at their place in both meshes
    pr, pn = _positions(ref), _positions(new)
    ur, cr = np.unique(pr, return_counts=True)
    un, cn = np.unique(pn, return_counts=True)
    both = np.intersect1d(ur[cr == 1], un[cn == 1])
    sr, sn = np.argsort(pr), np.argsort(pn)
    ir = sr[np.searchsorted(pr, both, sorter=sr)]
    inn = sn[np.searchsorted(pn, both, sorter=sn)]
    dz = np.abs(ref.vertices[ir, 2] - new.vertices[inn, 2])
    wet_r = np.zeros(len(ref.vertices), bool)
    wet_r[ref.tris[ref.types != 0].ravel()] = True
    wet_n = np.zeros(len(new.vertices), bool)
    wet_n[new.tris[new.types != 0].ravel()] = True
    land = ~wet_r[ir] & ~wet_n[inn]
    out["land_vertices_moved"] = int((dz[land] > LAND_MOVE_M).sum())
    out["land_max_move_m"] = round(float(dz[land].max()), 3) if land.any() else 0.0
    water = dz[~land]
    out["water_vertices_moved"] = int((water > LAND_MOVE_M).sum())
    if len(water):
        out["water_move_m"] = {
            "median": round(float(np.median(water)), 3),
            "p99": round(float(np.percentile(water, 99)), 3),
            "max": round(float(water.max()), 2),
        }
    out.update(surface_moves(ref, new, gone, came))
    return out


# -- build ---------------------------------------------------------------------------------------


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty", "--tags"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _save_settings(xplane: Path, library_from: Path) -> None:
    """A new user's settings, X-Plane's folder, and the map library the app carries."""
    from orthostudio import config

    s = config.load_settings(None, [])
    expert = s.expert
    if library_from.is_file():
        mine = tomllib.loads(library_from.read_text(encoding="utf-8")).get("expert", {})
        if mine.get("osm_library") and mine.get("osm_library_token"):
            expert = expert.model_copy(
                update={
                    "osm_library": mine["osm_library"],
                    "osm_library_token": mine["osm_library_token"],
                }
            )
    essential = s.essential.model_copy(update={"xplane_dir": str(xplane)})
    config.save_settings(s.model_copy(update={"essential": essential, "expert": expert}))


def _record(tile: dict[str, Any], why: str, out: Path, label: str, seconds: float) -> bool:
    """Keep what the comparison needs of one built tile: ``witness.json`` and ``mesh.npz``."""
    from orthostudio.graph.store import Store
    from orthostudio.home import default_store_root
    from orthostudio.vectors.noding import check_planar
    from orthostudio.vectors.triangle_files import read_node_file, read_poly_file

    name = tile["tile"]
    nodes = {n["role"]: n for n in tile["nodes"]}
    rec: dict[str, Any] = {
        "format": FORMAT,
        "tile": name,
        "why": why,
        "label": label,
        "commit": _commit(),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "batch_seconds": round(seconds, 1),
        "nodes": {
            role: {"status": n["status"], "error": n.get("error")}
            for role, n in nodes.items()
            if role in ("osm", "dem", "coastline", "vectors", "mesh", "masks", "dsf")
        },
    }
    here = out / name
    here.mkdir(parents=True, exist_ok=True)
    store = Store(default_store_root())
    try:
        ok = all(
            nodes.get(r, {}).get("status") in ("built", "hit") for r in ("vectors", "mesh", "dsf")
        )
        if ok:
            vec = store.path(nodes["vectors"]["key"])
            node_file, poly_file = vec / f"Data{name}.node", vec / f"Data{name}.poly"
            nd, po = read_node_file(node_file), read_poly_file(poly_file)
            rec["vectors"] = {
                "node_sha": _sha(node_file),
                "poly_sha": _sha(poly_file),
                "nodes": len(nd.xy),
                "segments": len(po.segments),
                "seeds": len(po.seeds),
                "crossings": int(check_planar(nd.xy, po.segments)),
            }
            npz = store.path(nodes["mesh"]["key"]) / "mesh.npz"
            shutil.copyfile(npz, here / "mesh.npz")
            rec["mesh"] = {"npz_sha": _sha(npz), **mesh_measures(load_mesh(npz), name)}
            dsfs = sorted(store.path(nodes["dsf"]["key"]).rglob("*.dsf"))
            rec["dsf"] = {"sha": _sha(dsfs[0]), "bytes": dsfs[0].stat().st_size} if dsfs else None
    finally:
        store.close()
    rec["ok"] = "mesh" in rec
    (here / "witness.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return rec["ok"]


def _batch(args: argparse.Namespace) -> None:
    """One batch, in a process of its own (the store is emptied between batches)."""
    from orthostudio import config
    from orthostudio.api.models import PlanRequest
    from orthostudio.api.specs import make_specs
    from orthostudio.pipeline import build as pb

    # the terrain only: no imagery, no pack, nothing installed
    pb.TileNodes.target = property(lambda self: self.dsf)  # type: ignore[assignment,method-assign]
    whys = json.loads(args.whys)
    settings = config.load_settings(None, [])
    specs = make_specs(
        PlanRequest(tiles=args.tiles), settings=settings, install=False, config_module=config
    )
    t0 = time.perf_counter()
    report = pb.build_tiles(specs).to_dict()
    seconds = time.perf_counter() - t0
    for tile in report["tiles"]:
        ok = _record(tile, whys.get(tile["tile"], ""), Path(args.out), args.label, seconds)
        print(f"   {tile['tile']}: {'recorded' if ok else 'FAILED'}", flush=True)


def build(args: argparse.Namespace) -> None:
    home_s = os.environ.get("OSXP_HOME", "")
    real = (Path.home() / ".orthostudio").resolve()
    if not home_s or Path(home_s).expanduser().resolve() == real:
        sys.exit(
            "Set OSXP_HOME to a folder of its own: the witness builds must not touch your data."
        )
    home = Path(home_s).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    if os.environ.get("OSXP_DATA_DIR"):
        sys.exit("Unset OSXP_DATA_DIR: the witness builds keep their cache under OSXP_HOME.")
    library_from = Path(args.library_from).expanduser()
    xplane = Path(args.xplane).expanduser() if args.xplane else None
    if xplane is None:
        mine = tomllib.loads(library_from.read_text()) if library_from.is_file() else {}
        xplane = Path(mine.get("essential", {}).get("xplane_dir") or Path.home() / "X-Plane 12")
    if not (xplane / GLOBAL_SCENERY).is_dir():
        sys.exit(f"No X-Plane 12 Global Scenery under {xplane}: give --xplane.")
    _save_settings(xplane, library_from)
    whys = witness_tiles()
    tiles = list(args.only) if args.only else list(whys)
    if args.random:
        drawn = random_tiles(scenery_tiles(xplane), args.random, args.seed, set(whys))
        whys.update({t: f"drawn at random (seed {args.seed})" for t in drawn})
        tiles += drawn
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tiles if not (out / t / "witness.json").is_file()]
    label = args.label or _commit()
    print(
        f"{len(tiles)} tiles, {len(tiles) - len(todo)} already in {out}, "
        f"{len(todo)} to build ({label})"
    )
    store_root = home / "store"
    for i in range(0, len(todo), args.batch):
        batch = todo[i : i + args.batch]
        t0 = time.perf_counter()
        print(f"batch {i // args.batch + 1}: {' '.join(batch)}", flush=True)
        subprocess.run(
            [
                sys.executable,
                __file__,
                "_batch",
                "--out",
                str(out),
                "--label",
                label,
                "--whys",
                json.dumps({t: whys.get(t, "") for t in batch}),
                *batch,
            ],
            check=False,
        )
        shutil.rmtree(store_root, ignore_errors=True)
        print(f"   {time.perf_counter() - t0:.0f} s, {i + len(batch)}/{len(todo)}", flush=True)


# -- compare -------------------------------------------------------------------------------------


def _read(folder: Path, tile: str) -> dict[str, Any] | None:
    path = folder / tile / "witness.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def compare_tile(ref_dir: Path, new_dir: Path, tile: str) -> dict[str, Any]:
    ref, new = _read(ref_dir, tile), _read(new_dir, tile)
    if ref is None or new is None:
        return {"tile": tile, "verdict": "missing", "side": "reference" if ref is None else "new"}
    out: dict[str, Any] = {"tile": tile, "why": new.get("why") or ref.get("why", "")}
    if not new["ok"] or not ref["ok"]:
        out["verdict"] = "failed" if not new["ok"] else "fixed"
        out["nodes"] = {"reference": ref["nodes"], "new": new["nodes"]}
        return out
    same_vectors = all(ref["vectors"][k] == new["vectors"][k] for k in ("node_sha", "poly_sha"))
    same_mesh = ref["mesh"]["npz_sha"] == new["mesh"]["npz_sha"]
    same_dsf = ref["dsf"] == new["dsf"]
    if same_vectors and same_mesh and same_dsf:
        out["verdict"] = "identical"
        return out
    out["verdict"] = "changed"
    out["same"] = {"vectors": same_vectors, "mesh": same_mesh, "dsf": same_dsf}
    for part, keys in (
        ("vectors", ("nodes", "segments", "seeds", "crossings")),
        ("mesh", ("triangles", "needles", "fan")),
    ):
        for k in keys:
            out[f"{part}.{k}"] = [ref[part][k], new[part][k]]
    out["area_change_m2"] = {
        k: round(new["mesh"]["area_m2"][k] - ref["mesh"]["area_m2"][k], 1)
        for k in ("land", "inland", "sea")
    }
    if not same_mesh:
        out.update(
            compare_meshes(
                load_mesh(ref_dir / tile / "mesh.npz"), load_mesh(new_dir / tile / "mesh.npz"), tile
            )
        )
    return out


def alerts(r: dict[str, Any]) -> list[str]:
    """What deserves a look before a release, in words."""
    if r["verdict"] in ("failed", "missing"):
        return [r["verdict"]]
    if r["verdict"] != "changed":
        return []
    out = []
    if r.get("land_vertices_moved"):
        out.append(
            f"land heights moved ({r['land_vertices_moved']} vertices, "
            f"up to {r['land_max_move_m']} m)"
        )
    s = r.get("land_surface_move_m")
    if s and s["max"] > 1:
        out.append(
            f"land surface moved up to {s['max']} m at {s['max_at'][0]},{s['max_at'][1]} "
            f"({s['over_1m']} points over 1 m)"
        )
    c0, c1 = r["vectors.crossings"]
    if c1 > c0:
        out.append(f"crossings {c0} -> {c1}")
    f0, f1 = r["mesh.fan"]
    if f1 > max(2 * f0, 1000):
        out.append(f"largest fan {f0} -> {f1}")
    n0, n1 = r["mesh.needles"]
    if n1 > 2 * n0 + 100:
        out.append(f"needles {n0} -> {n1}")
    for k, d in r["area_change_m2"].items():
        if abs(d) >= 10_000:
            out.append(f"{k} {d / 10_000:+.1f} ha")
    return out


def _line(r: dict[str, Any]) -> str:
    if r["verdict"] != "changed":
        return f"{r['tile']}  {r['verdict']}"
    parts = []
    if "places" in r:
        top = r["largest_places"][0] if r["largest_places"] else None
        where = f", largest {top['m2']} m2 at {top['lat']},{top['lon']}" if top else ""
        parts.append(f"land cut again in {r['places']} place(s), {r['changed_m2']:.0f} m2{where}")
        parts.append(f"water cut again {r['water_recut_m2'] / 1e6:.2f} km2")
    else:
        parts.append("mesh identical" if r["same"]["mesh"] is True else "")
    a = r["area_change_m2"]
    parts.append(f"land {a['land']:+.0f} / inland {a['inland']:+.0f} / sea {a['sea']:+.0f} m2")
    if "water_move_m" in r:
        w = r["water_move_m"]
        parts.append(f"water heights median {w['median']} p99 {w['p99']} max {w['max']} m")
    parts.append(f"land vertices max {r.get('land_max_move_m', 0)} m")
    if "land_surface_move_m" in r:
        s = r["land_surface_move_m"]
        parts.append(
            f"land surface (new points) median {s['median']} p99 {s['p99']} max {s['max']} m"
        )
    if "diagonal_turn_m" in r:
        s = r["diagonal_turn_m"]
        parts.append(
            f"diagonals turned, surface median {s['median']} max {s['max']} m "
            f"at {s['max_at'][0]},{s['max_at'][1]}"
        )
    parts.append(f"needles {r['mesh.needles'][0]}->{r['mesh.needles'][1]}")
    parts.append(f"fan {r['mesh.fan'][0]}->{r['mesh.fan'][1]}")
    parts.append(f"crossings {r['vectors.crossings'][0]}->{r['vectors.crossings'][1]}")
    return f"{r['tile']}  changed: " + "; ".join(p for p in parts if p)


def compare(args: argparse.Namespace) -> None:
    ref_dir, new_dir = Path(args.ref).expanduser(), Path(args.new).expanduser()
    tiles = sorted(
        {p.parent.name for p in ref_dir.glob("*/witness.json")}
        | {p.parent.name for p in new_dir.glob("*/witness.json")}
    )
    results = [compare_tile(ref_dir, new_dir, t) for t in tiles]
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        counts[r["verdict"]] += 1
    first = _read(ref_dir, tiles[0]) if tiles else None
    last = _read(new_dir, tiles[0]) if tiles else None
    lines = [
        f"Witness tiles: {first and first['label']} -> {last and last['label']}",
        f"{len(results)} tiles: " + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())),
        "",
    ]
    flagged = [(r, a) for r in results if (a := alerts(r))]
    lines.append(f"To look at: {len(flagged)}" if flagged else "To look at: nothing")
    lines += [f"  {r['tile']}: " + "; ".join(a) for r, a in flagged]
    lines.append("")
    lines += [_line(r) for r in results if r["verdict"] != "identical"]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
        Path(args.report).with_suffix(".json").write_text(json.dumps(results, indent=1) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the witness tiles with this checkout")
    b.add_argument("--out", required=True)
    b.add_argument("--label", default="")
    b.add_argument("--only", nargs="*", default=[])
    b.add_argument("--random", type=int, default=0)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--batch", type=int, default=8)
    b.add_argument("--xplane", default="")
    b.add_argument("--library-from", default="~/.orthostudio/config.toml")
    k = sub.add_parser("_batch")
    k.add_argument("--out", required=True)
    k.add_argument("--label", required=True)
    k.add_argument("--whys", required=True)
    k.add_argument("tiles", nargs="+")
    c = sub.add_parser("compare", help="compare two witness folders")
    c.add_argument("ref")
    c.add_argument("new")
    c.add_argument("--report", default="")
    args = ap.parse_args()
    {"build": build, "_batch": _batch, "compare": compare}[args.cmd](args)


if __name__ == "__main__":
    main()
