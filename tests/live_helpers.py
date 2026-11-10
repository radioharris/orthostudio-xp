# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the tests of OrthoStudio XP Live share: an AutoOrtho-like source pack, an X-Plane folder,
a downloader that answers at once, and a look at where a file's blocks sit on the disk."""

from __future__ import annotations

import io
import struct
import time
from collections.abc import Iterable
from pathlib import Path


def atom(atom_id: bytes, payload: bytes) -> bytes:
    return atom_id[::-1] + struct.pack("<I", len(payload) + 8) + payload


def fake_dsf(terrains: Iterable[str]) -> bytes:
    tert = atom(b"TERT", b"".join(t.encode() + b"\0" for t in terrains))
    defn = atom(b"DEFN", tert + atom(b"OBJT", b"") + atom(b"POLY", b""))
    head = atom(b"HEAD", atom(b"PROP", b"sim/west\0" + b"6\0"))
    return b"XPLNEDSF" + struct.pack("<i", 1) + head + defn + atom(b"GEOD", b"\0" * 16) + b"\0" * 16


def make_source(root: Path, tile=(45, 6), textures=("23296_33152_BI16",)) -> Path:
    """A source pack with one DSF naming one plain and one water-overlay .ter per texture."""
    lat, lon = tile
    src = root / "z_ao_test"
    dsf = src / "Earth nav data" / "+40+000" / f"{lat:+03d}{lon:+04d}.dsf"
    dsf.parent.mkdir(parents=True)
    (src / "terrain").mkdir()
    (src / "textures").mkdir()
    terrains = ["terrain_Water"]
    for name in textures:
        terrains += [f"terrain/{name}.ter", f"terrain/{name}_water_overlay.ter"]
        (src / "terrain" / f"{name}.ter").write_text(
            f"A\n800\nTERRAIN\n\nLOAD_CENTER 45.5 6.5 7000 4096\nBASE_TEX_NOWRAP "
            f"../textures/{name}.dds\nNO_ALPHA\n"
        )
        (src / "terrain" / f"{name}_water_overlay.ter").write_text(
            f"A\n800\nTERRAIN\n\nBASE_TEX_NOWRAP ../textures/{name}.dds\nBORDER_TEX "
            f"../textures/water_transition.png\nWET\n"
        )
    (src / "textures" / "water_transition.png").write_bytes(b"\x89PNG fake")
    dsf.write_bytes(fake_dsf(terrains))
    return src


def live_names(
    pack: Path, busy=lambda name: False, forget=lambda name: None, log=None, night=False
):
    """The ``Names`` of a pack set up (``orthostudio.live.names``), as the streamer makes it,
    nothing made yet."""
    import threading

    from orthostudio.live.geo import parse_texture_name
    from orthostudio.live.names import Names, RegionTextures
    from orthostudio.live.region import region_zooms, sources_of_pack
    from orthostudio.live.streamer import TextureIndex

    sources = sources_of_pack(pack)
    region = RegionTextures(sources)
    index = TextureIndex(region.entries())
    zooms = region_zooms(pack)

    def top_of(name: str) -> int:
        return zooms.top(parse_texture_name(name).zoom)

    return Names(
        pack,
        index.by_tile,
        sources,
        top_of,
        threading.Lock(),
        busy=busy,
        forget=forget,
        log=log or (lambda _: None),
        others=region.others,
        night=night,
    )


def square_of(name: str) -> tuple[int, int]:
    """The square of a texture's centre."""
    import math

    from orthostudio.live.geo import parse_texture_name

    lat, lon = parse_texture_name(f"{name.removesuffix('.dds')}.dds").center
    return math.floor(lat), math.floor(lon)


def make_squares(pack: Path, busy=lambda name: False):
    """The files of every square of the pack's regions made, as Live makes those round the
    aircraft (``orthostudio.live.names``): the ``Names`` that made them."""
    from orthostudio.live.evict import tiles_of
    from orthostudio.live.geo import parse_texture_name

    names = live_names(pack, busy)
    squares = {
        square
        for entries in names.by_tile.values()
        for entry in entries
        for square in tiles_of(parse_texture_name(entry[0]))
    }
    for square in sorted(squares):
        names.make(square)
        names.done.add(square)
    return names


def make_xplane(root: Path) -> Path:
    xp = root / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    (scenery / "z_ao_eur").mkdir(parents=True)
    (scenery / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\n"
        "SCENERY_PACK Custom Scenery/Aerosoft - LFMN Nice Cote d Azur X/\n"
        "SCENERY_PACK *GLOBAL_AIRPORTS*\n"
        "SCENERY_PACK Custom Scenery/simHeaven_X-WORLD-Pro_Library/\n"
        "SCENERY_PACK Custom Scenery/z_ao_eur/\n"
        "SCENERY_PACK Custom Scenery/z_autoortho/\n"
    )
    return xp


def bc1_texel(blocks: bytes, width: int, x: int, y: int):
    """The RGB of texel (x, y) of a BC1 level ``width`` texels wide."""
    bw = max(1, width // 4)
    c0, c1, idx = struct.unpack_from("<HHI", blocks, ((y // 4) * bw + x // 4) * 8)

    def rgb(c):
        return ((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31

    p0, p1 = rgb(c0), rgb(c1)
    if c0 > c1:
        palette = [
            p0,
            p1,
            tuple((2 * a + b) // 3 for a, b in zip(p0, p1, strict=False)),
            tuple((a + 2 * b) // 3 for a, b in zip(p0, p1, strict=False)),
        ]
    else:
        palette = [p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1, strict=False)), (0, 0, 0)]
    return palette[(idx >> (2 * ((y % 4) * 4 + x % 4))) & 3]


try:
    import ispc_texcomp  # noqa: F401
    import numpy  # noqa: F401
    from PIL import Image
except ImportError:  # pragma: no cover - outside the project's environment
    Image = None

NAMES = ("23296_33152_BI16", "23296_33168_BI16")


def jpeg_body():
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (180, 60, 60)).save(buf, "JPEG")
    return buf.getvalue()


class FakeDownloader:
    """Answers every piece from a thread of its own; pieces in ``slow`` after ``delay`` seconds,
    or once ``released`` is set (a threading.Event): when the test says, whatever the machine's
    speed."""

    def __init__(self, on_piece, slow=(), delay=0.0, released=None):
        import threading

        self.on_piece, self.slow, self.delay = on_piece, set(slow), delay
        self.released = released
        self.body = jpeg_body()
        self.downloaded = self.bytes = self.failed = 0
        self.asked = []
        self._threads = []
        self._lock = threading.Lock()

    def submit(self, key, priority):
        import threading

        with self._lock:
            self.asked.append(key)

        def answer():
            if key in self.slow:
                if self.released is not None:
                    self.released.wait(60)
                else:
                    time.sleep(self.delay)
            self.downloaded += 1
            self.on_piece(key, 1, self.body)

        t = threading.Thread(target=answer, daemon=True)
        t.start()
        self._threads.append(t)

    def close(self):
        pass


def disk_clones(folder: Path) -> bool:
    """Whether the disk of ``folder`` clones files (APFS, btrfs, xfs): a texture's unbuilt levels
    are the neutral texture's there, and a hole elsewhere (NTFS, ext4), which reads as zeros
    (orthostudio.live.filesystem)."""
    from orthostudio.live.clone import clones_work

    return clones_work(folder)


def unbuilt(neutral_level: bytes, folder: Path) -> bytes:
    """What a level of a texture that is not built reads as on the disk of ``folder``: the neutral
    texture's own, or zeros where the disk clones nothing."""
    return neutral_level if disk_clones(folder) else bytes(len(neutral_level))


F_LOG2PHYS_EXT = 65


def file_extents(path: Path) -> list[tuple[int, int, int]]:
    """[(file offset, device offset, length)] of every contiguous run of the file, in bytes
    (APFS)."""
    import fcntl
    import os

    fd = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        out = []
        offset = 0
        while offset < size:
            # struct log2phys { uint l2p_flags; off_t l2p_contigbytes; off_t l2p_devoffset; },
            # packed 4
            res = fcntl.fcntl(fd, F_LOG2PHYS_EXT, struct.pack("=Iqq", 0, size - offset, offset))
            _, contig, dev = struct.unpack("=Iqq", res)
            if contig <= 0:
                break
            out.append((offset, dev, contig))
            offset += contig
        return out
    finally:
        os.close(fd)
