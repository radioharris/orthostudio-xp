# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``src/orthostudio/dem/data/viewfinder.json``: the archives viewfinderpanoramas serves, read from
its two coverage maps, the only list ``orthostudio.dem.sources.view_urls`` reads.

    uv run python tools/dem/viewfinder_archives.py

The site's 1" archives are listed on ``Coverage map viewfinderpanoramas_org1.htm``
(``dem1/M31.zip``: a block of 4 x 6 squares, Paris's among them), its 3" ones on
``..._org3.htm`` (``dem3/L31.zip``, the northern blocks as ``dem3/P35v2.zip`` since the site moved
them, and islands under names of their own: ``SHL`` for Shetland, ``FAR``, ``ISL``, ``BEAR``...).
Ortho4XP's list, which OrthoStudio XP ported, knew 1" for 34 squares of the Alps and 22 northern
blocks: on 2026-10-17, 626 of the 1,981 squares of AutoOrtho's Europe had 1" on the site and got
3". For an archive whose name says no block, the squares it holds are read from its zip's
directory (an HTTP range of its end, not the file).
"""

from __future__ import annotations

import json
import re
import struct
import urllib.request
from pathlib import Path

SITE = "https://viewfinderpanoramas.org"
MAPS = {"dem1": "Coverage%20map%20viewfinderpanoramas_org1.htm",
        "dem3": "Coverage%20map%20viewfinderpanoramas_org3.htm"}  # fmt: skip
OUT = Path(__file__).resolve().parents[2] / "src/orthostudio/dem/data/viewfinder.json"
BLOCK = re.compile(r"^(S?[A-Z])(\d\d)(v2)?$")
CELL = re.compile(r"(?:^|/)([NS])(\d\d)([EW])(\d\d\d)\.hgt$", re.IGNORECASE)


def get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "OrthoStudio XP", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def names_in(page: str, folder: str) -> list[str]:
    html = get(f"{SITE}/{page}").decode("latin-1")
    return sorted(set(re.findall(rf"/{folder}/([A-Za-z0-9_-]+)\.zip", html)))


def cells_of_zip(url: str) -> list[str]:
    """The ``.hgt`` members of a zip on the site, from its central directory alone."""
    head = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "OrthoStudio XP"})
    with urllib.request.urlopen(head, timeout=60) as r:
        size = int(r.headers["Content-Length"])
    tail = get(url, {"Range": f"bytes={max(0, size - 65558)}-{size - 1}"})
    end = tail.rfind(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<II", tail, end + 12)
    cd = get(url, {"Range": f"bytes={cd_offset}-{cd_offset + cd_size - 1}"})
    cells, pos = [], 0
    while pos + 46 <= len(cd) and cd[pos : pos + 4] == b"PK\x01\x02":
        n, e, c = struct.unpack_from("<HHH", cd, pos + 28)
        m = CELL.search(cd[pos + 46 : pos + 46 + n].decode("latin-1"))
        if m:
            cells.append(f"{m[1].upper()}{m[2]}{m[3].upper()}{m[4]}")
        pos += 46 + n + e + c
    return sorted(set(cells))


def main() -> None:
    dem1 = names_in(MAPS["dem1"], "dem1")
    dem3 = names_in(MAPS["dem3"], "dem3")
    special = {}
    for name in dem1 + dem3:
        if BLOCK.match(name):
            continue
        folder = "dem1" if name in dem1 else "dem3"
        for cell in cells_of_zip(f"{SITE}/{folder}/{name}.zip"):
            special.setdefault(cell, f"{folder}/{name}")
        print(name, "read")
    doc = {
        "source": f"{SITE}/{MAPS['dem1']} and {MAPS['dem3']}",
        "dem1": [n for n in dem1 if BLOCK.match(n)],
        "dem3": [n for n in dem3 if BLOCK.match(n)],
        "special": dict(sorted(special.items())),
    }
    OUT.write_text(json.dumps(doc, indent=0) + "\n")
    print(f"{OUT}: {len(doc['dem1'])} 1\" blocks, {len(doc['dem3'])} 3\" ones, "
          f"{len(special)} cells in archives of their own")  # fmt: skip


if __name__ == "__main__":
    main()
