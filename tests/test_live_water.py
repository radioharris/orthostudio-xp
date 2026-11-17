# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The water of Live's own mesh (orthostudio.live.waterphoto): the setting for rivers and lakes,
the pack's record of what its terrain files name, the two borders, the water photo made from its
land and following it, the disk it takes, and the squares' files: what their water terrain files
name, made before them, and taken away with them."""

from __future__ import annotations

import io
import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from live_helpers import fake_dsf, live_names
from orthostudio.live import control as control_module
from orthostudio.live import dds, settings_file
from orthostudio.live.names import MASK_RE, TER_KINDS, TERRAIN_LISTS, WaterPool, mask_name
from orthostudio.live.region import (
    WaterRecord,
    set_water,
    setup_region,
    water_record,
)
from orthostudio.live.settings import LiveSettings, load_settings, save_settings
from orthostudio.live.texture import levels_from, write_sparse
from orthostudio.live.waterphoto import (
    EVEN_BORDER,
    NO_BORDER,
    file_size,
    first_level,
    follow,
    header,
    layout,
    make,
    water_name,
    water_top,
    write_placeholder,
)

TILE = (46, 2)
# ZL16 textures in a row eastwards, their centres on TILE: the sea alone, a river alone (its mask
# in rivers/textures), both, a river without a mask, the land alone
SEA, RIVER, BOTH, BARE, LAND = (f"23296_{33152 + 16 * k}_BI16" for k in range(5))
KINDS = {SEA: 1 | 4, RIVER: 1 | 2, BOTH: 1 | 2 | 4, BARE: 1 | 2, LAND: 1}


def quiet(_: str) -> None:
    pass


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def mask_png(side: int = 64) -> bytes:
    """A water mask as Live's mesh has them: the photo whole on the land's side (255), faded to
    nothing (0) across the water."""
    ramp = np.clip(np.linspace(300, -45, side), 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(np.tile(ramp, (side, 1))).save(out, format="PNG")
    return out.getvalue()


def live_region(root: Path, kinds: dict[str, int] = KINDS, rivers=(RIVER,)) -> Path:
    """A region of Live's own mesh: its DSF, its terrain files named in a list (kinds: 1 the land,
    2 a river's or a lake's overlay, 4 the sea's), the masks of the textures with sea in
    ``textures``, those of ``rivers`` (textures with rivers alone) in ``rivers/textures``."""
    lat, lon = TILE
    src = root / "z_osxp_test"
    dsf = src / "Earth nav data" / "+40+000" / f"{lat:+03d}{lon:+04d}.dsf"
    dsf.parent.mkdir(parents=True)
    (src / TERRAIN_LISTS).mkdir()
    (src / "textures").mkdir()
    (src / "rivers" / "textures").mkdir(parents=True)
    files = []
    for name, bits in kinds.items():
        files += [f"{name}{suffix}.ter" for suffix, bit in TER_KINDS if bits & bit]
        if bits & 4:
            (src / "textures" / mask_name(f"{name}.dds")).write_bytes(mask_png())
    for name in rivers:
        (src / "rivers" / "textures" / mask_name(f"{name}.dds")).write_bytes(mask_png())
    lst = src / TERRAIN_LISTS / f"z_osxp_test_{lat:+03d}{lon:+04d}.txt"
    lst.write_text("\n".join(files) + "\n", encoding="ascii")
    dsf.write_bytes(fake_dsf(["terrain_Water"] + [f"terrain/{f}" for f in files]))
    return src


def live_pack(root: Path, **kwargs) -> Path:
    pack = root / "pack"
    setup_region(live_region(root, **kwargs), pack, log=quiet)
    return pack


# --- the setting --------------------------------------------------------------------------------


def test_rivers_and_lakes_are_even_until_chosen(home: Path) -> None:
    """Off by default, carried to a start, and kept: a file of before has none, a wrong value costs
    itself only."""
    assert LiveSettings().rivers_fade is False
    assert control_module.options_for(LiveSettings()).rivers_fade is False
    assert control_module.options_for(LiveSettings(rivers_fade=True)).rivers_fade is True
    settings_file().write_text("[stream]\nnight = false\n")  # a file of before the setting
    assert load_settings().rivers_fade is False
    save_settings(LiveSettings(rivers_fade=True))
    assert load_settings().rivers_fade is True
    settings_file().write_text('[stream]\nrivers_fade = "blinding"\nprovider = "Arc"\n')
    got = load_settings()
    assert got.rivers_fade is False and got.provider == "Arc"


# --- the pack's record --------------------------------------------------------------------------


def test_the_record_makes_the_terrain_files_again_but_not_under_x_plane(tmp_path: Path) -> None:
    """A pack made before the water photos: its terrain files name the masks until a start with
    X-Plane not running makes them again, the masks copied with them gone; then once for each
    change of the rivers' setting, never under X-Plane."""
    pack = live_pack(tmp_path)
    assert water_record(pack) is None
    live_names(pack).make(TILE)  # as before: the sea's terrain names its mask
    sea = pack / "terrain" / f"{SEA}_sea_overlay.ter"
    assert f"BORDER_TEX ../textures/{mask_name(SEA + '.dds')}".encode() in sea.read_bytes()
    assert (pack / "textures" / mask_name(f"{SEA}.dds")).exists()
    said: list[str] = []
    set_water(pack, False, log=said.append, xplane_running=lambda: True)
    assert sea.exists() and water_record(pack) is None  # X-Plane may read them
    assert "Live's next start" in said[-1]
    set_water(pack, False, log=said.append)
    assert list((pack / "terrain").iterdir()) == []
    assert not any(MASK_RE.match(p.name) for p in (pack / "textures").iterdir())
    assert water_record(pack) == WaterRecord(rivers_fade=False)
    count = len(said)
    set_water(pack, False, log=said.append)  # recorded: nothing more
    assert len(said) == count
    live_names(pack).make(TILE)
    set_water(pack, True, log=said.append)
    assert water_record(pack) == WaterRecord(rivers_fade=True)
    assert list((pack / "terrain").iterdir()) == []
    assert "faded at their banks" in " ".join(said[count:])


def test_the_two_borders_are_written_at_each_start(tmp_path: Path) -> None:
    """Uniform grey 150 for rivers and lakes that do not fade, 0 for those without a mask: names
    of Live's own, never taken for a water mask or a texture, and written again when changed."""
    from orthostudio.live.geo import parse_texture_name
    from orthostudio.live.region import _to_squares

    pack = live_pack(tmp_path)
    set_water(pack, False, log=quiet, xplane_running=lambda: True)  # written whatever X-Plane does
    for name, grey in ((EVEN_BORDER, 150), (NO_BORDER, 0)):
        with Image.open(pack / "textures" / name) as im:
            assert im.mode == "L" and im.size == (16, 16)
            assert set(np.asarray(im).ravel()) == {grey}
        assert not MASK_RE.match(name) and parse_texture_name(name) is None
    (pack / "textures" / EVEN_BORDER).write_bytes(b"spoilt")
    set_water(pack, False, log=quiet)
    with Image.open(pack / "textures" / EVEN_BORDER) as im:
        assert set(np.asarray(im).ravel()) == {150}
    _to_squares(pack, quiet)  # what a pack of before has taken away: not them
    assert (pack / "textures" / EVEN_BORDER).exists() and (pack / "textures" / NO_BORDER).exists()


# --- the water photo ----------------------------------------------------------------------------


def land_block(level: int) -> bytes:
    """A BC1 block of one colour of its own for each level, in four-colour mode as Live's are."""
    c = 0x0841 * (level + 3)
    return struct.pack("<HHI", c, c, 0)


def land_file(pack: Path, name: str, level: int, file_top: int, water: bool = True) -> Path:
    """A texture built from ``level`` in a file whose sharpest level is ``file_top``, each level of
    its own colour, the larger ones a hole; its water mask beside it, and with ``water`` the
    placeholder of its water photo, as the squares' files make them (orthostudio.live.names)."""
    textures = pack / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    layout_ = dds.level_layout(file_top)[level - file_top :]
    levels = [land_block(level + k) * (length // 8) for k, (_, length) in enumerate(layout_)]
    path = textures / f"{name}.dds"
    write_sparse(path, level, levels, file_top=file_top)
    (textures / mask_name(path.name)).write_bytes(mask_png())
    if water:
        WaterPool(pack).link(water_top(file_top), path.with_name(water_name(path.name)))
    return path


def water_levels(water: Path, first: int) -> list[np.ndarray]:
    """The BC3 blocks, (blocks, 16) bytes, of a water photo's levels from ``first`` down."""
    data = water.read_bytes()
    top = dds.read_top(water)
    return [
        np.frombuffer(data[offset : offset + length], dtype=np.uint8).reshape(-1, 16)
        for offset, length in layout(top)[first - top :]
    ]


def test_the_water_photo_s_layout_and_header() -> None:
    """BC3, every level down to 1 x 1, 16 bytes a block: 5.6 MB for 2048 px; a header Pillow and
    Live read as a DXT5 texture of that size."""
    lay = layout(1)
    assert len(lay) == 12 and lay[0] == (128, 512 * 512 * 16) and lay[-1][1] == 16
    assert file_size(1) == 5_592_560
    assert file_size(2) == 128 + 16 * sum(max(1, (1024 >> k) // 4) ** 2 for k in range(11))
    head = header(1)
    assert head[:4] == b"DDS " and len(head) == 128 and head[84:88] == b"DXT5"
    assert struct.unpack_from("<4I", head, 12) == (2048, 2048, 2048 * 2048, 0)
    assert struct.unpack_from("<I", head, 28)[0] == 12  # its levels
    assert (water_top(0), water_top(-1), water_top(1), water_top(2)) == (1, 1, 2, 3)
    assert (first_level(0, 0), first_level(2, 0), first_level(-1, -1), first_level(1, 1)) == (
        1,
        3,
        1,
        2,
    )


def test_the_placeholder_is_x_plane_s_water_alone(tmp_path: Path) -> None:
    """Its header, then a hole: zeros, alpha 0 at every level, no disk taken but its header's."""
    from orthostudio.live.filesystem import allocated

    path = tmp_path / "water-2048.dds"
    write_placeholder(path, 1)
    data = path.read_bytes()
    assert data[:128] == header(1) and len(data) == file_size(1)
    assert set(data[128:]) == {0}
    assert dds.read_top(path) == 1
    assert allocated(path) < 1 << 20
    with Image.open(path) as im:
        im.load()
        assert im.mode == "RGBA" and im.size == (2048, 2048)
        assert np.asarray(im)[..., 3].max() == 0


@pytest.mark.parametrize(
    ("file_top", "level", "first"),
    [(0, 0, 1), (0, 2, 3), (-1, -1, 1), (-1, 2, 3), (1, 1, 2), (1, 3, 4)],
)
def test_a_water_photo_is_made_from_its_land_s_own_levels(
    tmp_path: Path, file_top: int, level: int, first: int
) -> None:
    """2048 px for a 4096 or 8192 px land file, 1024 for a 2048 px one; its levels from one coarser
    than the land's built, a hole above; their colours the land's own blocks, nothing encoded
    again; its placeholder's other names untouched."""
    pack = tmp_path / "pack"
    land = land_file(pack, SEA, level, file_top)
    water = land.with_name(water_name(land.name))
    wt = water_top(file_top)
    assert first_level(level, file_top) == first
    assert make(land, level) == level
    data = water.read_bytes()
    assert data[:128] == header(wt) and len(data) == file_size(wt)
    assert set(data[128 : layout(wt)[first - wt][0]]) <= {0}  # sharper than the land's: a hole
    colours = levels_from(land, first, file_top)
    blocks = water_levels(water, first)
    assert len(blocks) == len(colours)
    for got, colour in zip(blocks, colours, strict=True):
        assert got[:, 8:].tobytes() == colour
    assert set(WaterPool(pack).path(wt).read_bytes()[128:]) == {0}


def test_the_water_photo_s_alpha_is_the_mask(tmp_path: Path) -> None:
    """Read as X-Plane reads it: the land's colour, and the mask resized to its side as alpha."""
    pack = tmp_path / "pack"
    land = land_file(pack, SEA, 0, 0)
    make(land, 0)
    with Image.open(land.with_name(water_name(land.name))) as im:
        im.load()
        rgba = np.asarray(im)
    with Image.open(land.with_name(mask_name(land.name))) as im:
        mask = np.asarray(im.resize((2048, 2048), Image.Resampling.BILINEAR)).astype(int)
    alpha = rgba[..., 3].astype(int)
    assert np.abs(alpha - mask).max() <= 20 and np.abs(alpha - mask).mean() < 1.5
    assert alpha.max() == 255 and alpha.min() == 0
    assert len({tuple(px) for px in rgba[::256, ::256, :3].reshape(-1, 3)}) == 1


def test_no_water_photo_for_a_texture_without_one(tmp_path: Path) -> None:
    """Whether a texture has one is the squares' files' choice: none beside it, none made."""
    pack = tmp_path / "pack"
    land = land_file(pack, LAND, 0, 0, water=False)
    assert make(land, 0) is None and follow(land, 0) is None
    assert not land.with_name(water_name(land.name)).exists()


def test_the_water_photo_follows_its_land(tmp_path: Path) -> None:
    """Made coarser, it keeps its levels from one below the land's; set to a new zoom, made again at
    the new file's size; made neutral, back to the placeholder of its size; the 8192 px copy of a
    land leaves it as it is."""
    from orthostudio.live.region import ensure_neutral
    from orthostudio.live.texture import copy_up, downgrade, resize

    pack = tmp_path / "pack"
    land = land_file(pack, SEA, 0, 0)
    water = land.with_name(water_name(land.name))
    make(land, 0)
    assert downgrade(land, 4, ensure_neutral(pack, 0), file_top=0) == 4
    data = water.read_bytes()
    assert set(data[128 : layout(1)[4][0]]) == {0} and any(data[layout(1)[4][0] :])
    assert water_levels(water, 5)[0][:, 8:].tobytes() == levels_from(land, 5, 0)[0]
    assert resize(land, 4, 0, 1, ensure_neutral(pack, 1)) == 4  # ZL15: a 2048 px land
    assert dds.read_top(water) == 2 and len(water.read_bytes()) == file_size(2)
    assert water_levels(water, 5)[0][:, 8:].tobytes() == levels_from(land, 5, 1)[0]
    assert downgrade(land, 5, ensure_neutral(pack, 1), file_top=1) is None
    assert os.path.samefile(water, WaterPool(pack).path(2))
    sharp = land_file(pack, BOTH, 0, -1)  # an 8192 px file built from its 4096 px level
    sharp_water = sharp.with_name(water_name(sharp.name))
    make(sharp, 0)
    before = sharp_water.read_bytes()
    copy_up(sharp, ensure_neutral(pack, -1), -1)
    assert sharp_water.read_bytes() == before


def test_a_water_photo_that_cannot_be_made_fails_nothing(tmp_path: Path) -> None:
    """Its mask gone: the water photo back to its placeholder, never a photo its land no longer
    has; the land built all the same, the build saying why."""
    from orthostudio.live.geo import parse_texture_name
    from orthostudio.live.texture import build_job, piece_keys

    pack = tmp_path / "pack"
    land = land_file(pack, SEA, 0, 0)
    water = land.with_name(water_name(land.name))
    make(land, 0)
    land.with_name(mask_name(land.name)).unlink()
    with pytest.raises(OSError):
        make(land, 0)
    assert follow(land, 0) is None
    assert os.path.samefile(water, WaterPool(pack).path(1))
    keys = piece_keys(parse_texture_name(land.name), 4)
    pieces = {k: jpeg_piece((90, 140, 60)) for k in keys}
    got = build_job((land.name, 4, pieces, {}, str(land)))
    assert got[1] == 4 and isinstance(got[4], str) and "FileNotFoundError" in got[4]
    assert dds.read_top(land) == 0


def jpeg_piece(rgb) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (256, 256), rgb).save(out, "JPEG", quality=95)
    return out.getvalue()


def test_the_water_photo_has_the_land_s_colours_the_season_s_included(tmp_path: Path) -> None:
    """Built with the land, from its blocks as encoded in the season's colours: the bank never
    changes colour between the land and the water."""
    from orthostudio.live.geo import parse_texture_name
    from orthostudio.live.look import DEFAULT_LOOKS
    from orthostudio.live.texture import build_job, piece_keys

    keys = piece_keys(parse_texture_name(f"{SEA}.dds"), 2)
    pieces = {k: jpeg_piece((90 + 9 * (k[1] % 4), 140, 60)) for k in keys}
    halves = []
    for k, look in enumerate((None, DEFAULT_LOOKS["autumn"].values())):
        pack = tmp_path / f"pack{k}"
        land = land_file(pack, SEA, 4, 0)  # its mask and its water photo's name
        got = build_job((land.name, 2, pieces, {}, str(land), None, 0, False, None, look))
        assert got[4] == 2
        water = land.with_name(water_name(land.name))
        colours = levels_from(land, 3, 0)
        blocks = water_levels(water, 3)
        assert [b[:, 8:].tobytes() for b in blocks] == colours
        halves.append(colours[0])
    assert halves[0] != halves[1]  # autumn's colours, on the land and its water alike


# --- the disk -----------------------------------------------------------------------------------


def test_the_disk_counts_the_water_photos(tmp_path: Path) -> None:
    """5.6 MB for a sharp one, 2048 px at most whatever the zoom; nothing for a neutral texture;
    counted with its texture, and a texture made coarser frees what its water photo held."""
    from orthostudio.live.evict import held_bytes, pick, texture_bytes, usage, water_bytes

    assert 5_590_000 < water_bytes(0, 0) < 5_610_000
    assert water_bytes(-1, -1) == water_bytes(0, -1) == water_bytes(0, 0)
    assert water_bytes(1, 1) < water_bytes(0, 0) // 3  # 1024 px for a 2048 px land
    assert water_bytes(4, 0) < 100_000 and water_bytes(5, 0) == 0
    built = {"a.dds": 0, "b.dds": 0}
    water = {"a.dds": 0}
    assert usage(built, water=water) == usage(built) + water_bytes(0, 0)
    assert usage(built, lambda _: 0, water=water) == usage(built) + water_bytes(0, 0)
    assert held_bytes("a.dds", 0, 0, water=water) == texture_bytes(0) + water_bytes(0, 0)
    # a budget met once "a.dds" and its water photo are coarser: "b.dds" kept
    coarse = texture_bytes(0) + texture_bytes(4) + water_bytes(4, 0)
    budget = int(coarse / 0.9) + 1
    seen = {"a.dds": 1.0, "b.dds": 2.0}
    assert pick(built, seen, lambda n: True, budget, water=water) == [("a.dds", 4)]


def test_the_window_counts_the_water_photos_live_stopped(tmp_path: Path) -> None:
    """From the textures' record, as the streamer counts them (evict.usage)."""
    import json

    from orthostudio.live.control import _textures_bytes
    from orthostudio.live.evict import usage, water_bytes
    from orthostudio.live.pack import MARKER
    from orthostudio.live.region import file_tops

    pack = live_pack(tmp_path)
    built = {f"{SEA}.dds": 0, f"{LAND}.dds": 0}
    (pack / MARKER).write_text(json.dumps({"textures": built, "water": {f"{SEA}.dds": 0}}))
    assert _textures_bytes(pack) == usage(built, file_tops(pack)) + water_bytes(0, 0)


def test_forgetting_the_textures_forgets_their_water_photos(tmp_path: Path) -> None:
    """Every built texture back to the neutral one, its water photo back to X-Plane's water."""
    import json

    from orthostudio.live.pack import MARKER, load_built
    from orthostudio.live.region import reset_built

    pack = live_pack(tmp_path)
    land = land_file(pack, SEA, 0, 0)
    make(land, 0)
    (pack / MARKER).write_text(json.dumps({"textures": {land.name: 0}, "water": {land.name: 0}}))
    reset_built(pack, quiet)
    assert os.path.samefile(land.with_name(water_name(land.name)), WaterPool(pack).path(1))
    assert load_built(pack)["water"] == {}


def test_a_water_photo_is_never_taken_for_a_texture(tmp_path: Path) -> None:
    """Its name reads as its texture's: a new zoom lays out the texture alone, which its water
    photo follows."""
    from orthostudio.live.region import Zooms, _layout_plan, _textures_by_zoom

    pack = live_pack(tmp_path)
    land = land_file(pack, SEA, 0, 0)
    make(land, 0)
    assert _textures_by_zoom(pack) == {16: [land.name]}
    assert _layout_plan(pack, Zooms(15, 18)) == {16: [(land.name, 1)]}


# --- the squares' files -------------------------------------------------------------------------


def named_files(pack: Path) -> set[str]:
    """Every file the pack's terrain files name in its ``textures``."""
    out = set()
    for ter in (pack / "terrain").iterdir():
        for line in ter.read_text(encoding="ascii").splitlines():
            if line.startswith(("BASE_TEX", "BORDER_TEX")):
                out.add(line.split("../textures/")[1])
    return out


def ter_of(pack: Path, name: str, kind: str = "") -> str:
    return (pack / "terrain" / f"{name}{kind}.ter").read_text(encoding="ascii")


def test_what_the_water_terrain_files_name() -> None:
    """The sea's names its water photo, its side on LOAD_CENTER, no border; rivers and lakes the
    even grey, their water photo with soft banks, or the border of X-Plane's water alone without
    a mask; the rest of each file as its name makes it."""
    from orthostudio.live.names import made_ter
    from orthostudio.live.waterphoto import ter_text

    sea = made_ter("19024_33744_BI16_sea_overlay.ter")
    river = made_ter("19024_33744_BI16_water_overlay.ter")
    head = b"A\n800\nTERRAIN\n\nLOAD_CENTER 59.99899 5.40527 4892 "
    photo = ("photo", "19024_33744_BI16_water.dds", 2048)
    named = head + b"2048\nBASE_TEX_NOWRAP ../textures/19024_33744_BI16_water.dds\nWET\nNO_SHADOW\n"
    assert ter_text(sea, photo) == named
    assert ter_text(river, photo) == named  # soft banks: the sea's own way
    for border in (EVEN_BORDER, NO_BORDER):
        assert ter_text(river, ("border", border)) == (
            head + b"4096\nBASE_TEX_NOWRAP ../textures/19024_33744_BI16.dds\n"
            b"BORDER_TEX ../textures/" + border.encode() + b"\nWET\nNO_SHADOW\n"
        )
    assert ter_text(sea, ("photo", "x_water.dds", 1024)).startswith(head + b"1024\n")


@pytest.mark.parametrize("rivers_fade", [False, True])
def test_a_square_of_live_s_mesh_names_its_water_photos(tmp_path: Path, rivers_fade: bool) -> None:
    """Every water terrain file as the record says, never one naming a file not there: the water
    photos and their masks made first, a river's mask from rivers/textures; none for the land."""
    pack = live_pack(tmp_path)
    set_water(pack, rivers_fade, log=quiet)
    names = live_names(pack, water=water_record(pack))
    names.make(TILE)
    textures = pack / "textures"
    assert named_files(pack) <= {p.name for p in textures.iterdir()}
    photo = {SEA, BOTH} | ({RIVER} if rivers_fade else set())
    for name in KINDS:
        has = (textures / water_name(f"{name}.dds")).exists()
        assert has == (name in photo), name
        assert (textures / mask_name(f"{name}.dds")).exists() == (name in photo), name
    for name in (SEA, BOTH):
        assert f"../textures/{name}_water.dds" in ter_of(pack, name, "_sea_overlay")
        assert "LOAD_CENTER" in ter_of(pack, name, "_sea_overlay")
        assert "BORDER" not in ter_of(pack, name, "_sea_overlay")
    rivers = {name: ter_of(pack, name, "_water_overlay") for name in (RIVER, BOTH, BARE)}
    if rivers_fade:
        assert f"../textures/{RIVER}_water.dds" in rivers[RIVER]
        assert f"../textures/{BOTH}_water.dds" in rivers[BOTH]
        assert f"BORDER_TEX ../textures/{NO_BORDER}" in rivers[BARE]  # no mask: as before
    else:
        for name, text in rivers.items():
            assert f"BASE_TEX_NOWRAP ../textures/{name}.dds" in text  # the land's photo
            assert f"BORDER_TEX ../textures/{EVEN_BORDER}" in text
            assert "_water.dds" not in text
    assert "_water.dds" not in ter_of(pack, LAND) and "BORDER" not in ter_of(pack, LAND)


def test_the_water_photos_are_made_before_the_terrain_files_naming_them(tmp_path: Path) -> None:
    """In the first operations, with the textures set to their size: the masks, then the textures,
    then the water photos; the terrain files after, once they are all there."""
    pack = live_pack(tmp_path)
    set_water(pack, True, log=quiet)
    names = live_names(pack, water=water_record(pack))
    calls: list[list[str]] = []
    real = names._run_ops
    names._run_ops = lambda ops: calls.append([op[0] for op in ops]) or real(ops)
    names.make(TILE)
    first, then = calls
    assert set(first) == {"file", "fit", "water"} and "make" in then and "water" not in then
    assert first.index("water") > max(i for i, kind in enumerate(first) if kind == "fit")
    assert max(i for i, kind in enumerate(first) if kind == "file") < first.index("fit")


def test_a_square_of_a_region_of_before_or_of_autoortho_is_as_it_was(tmp_path: Path) -> None:
    """The record of before (None): the sea's terrain file names its mask, as its name makes it;
    an AutoOrtho region's terrain files are copied as they are, whatever the record."""
    from live_helpers import make_source
    from orthostudio.live.names import made_ter

    pack = live_pack(tmp_path)
    live_names(pack).make(TILE)  # no record: the files of before
    sea = pack / "terrain" / f"{SEA}_sea_overlay.ter"
    assert sea.read_bytes() == made_ter(sea.name)
    assert (pack / "textures" / mask_name(f"{SEA}.dds")).exists()
    assert not any(p.name.endswith("_water.dds") for p in (pack / "textures").iterdir())
    ao = tmp_path / "ao"
    ao.mkdir()
    src = make_source(ao, tile=TILE, textures=(SEA,))
    ao_pack = ao / "pack"
    setup_region(src, ao_pack, log=quiet)
    set_water(ao_pack, True, log=quiet)
    live_names(ao_pack, water=water_record(ao_pack)).make(TILE)
    for ter in (f"{SEA}.ter", f"{SEA}_water_overlay.ter"):
        assert (ao_pack / "terrain" / ter).read_bytes() == (src / "terrain" / ter).read_bytes()
    assert not any(p.name.endswith("_water.dds") for p in (ao_pack / "textures").iterdir())


def test_a_land_built_before_its_square_gets_its_water_photo_with_it(tmp_path: Path) -> None:
    """Built before its square's files (far ahead, or before the water photos): its water photo
    made from it as the square is made, said to the streamer; one being built gets its placeholder,
    and the build makes it."""
    pack = live_pack(tmp_path)
    set_water(pack, False, log=quiet)
    land = land_file(pack, SEA, 2, 0, water=False)  # its mask as the square will copy it
    told: dict[str, int] = {}
    names = live_names(
        pack,
        water=water_record(pack),
        level_of={land.name: 2}.get,
        building=lambda name: name == f"{BOTH}.dds",
        watered=told.__setitem__,
    )
    names.make(TILE)
    water = land.with_name(water_name(land.name))
    assert told == {land.name: 2}
    assert not os.path.samefile(water, WaterPool(pack).path(1))
    assert water_levels(water, 3)[0][:, 8:].tobytes() == levels_from(land, 3, 0)[0]
    both = pack / "textures" / water_name(f"{BOTH}.dds")
    assert os.path.samefile(both, WaterPool(pack).path(1))  # its build makes it
    told.clear()
    names.make(TILE)  # made again (a square made again): said again, not made again
    assert told == {land.name: 2}


def test_a_water_photo_made_stays_with_its_texture_when_its_square_goes(tmp_path: Path) -> None:
    """Taken away with its square, a water photo goes with its mask while it holds no photo; one
    made stays with its mask, as its texture built does, or each flight back over the same water
    would have it made again."""
    pack = live_pack(tmp_path)
    set_water(pack, False, log=quiet)
    names = live_names(pack, water=water_record(pack), busy=lambda name: name == f"{SEA}.dds")
    names.make(TILE)
    textures = pack / "textures"
    sea = textures / f"{SEA}.dds"
    sea.unlink()
    write_sparse(sea, 0, [land_block(k) * (n // 8) for k, (_, n) in enumerate(dds.mip_layout())])
    make(sea, 0)
    names.done.add(TILE)
    names.take_away(TILE)
    assert (textures / water_name(sea.name)).exists() and (textures / mask_name(sea.name)).exists()
    both = textures / water_name(f"{BOTH}.dds")
    assert not both.exists() and not (textures / mask_name(f"{BOTH}.dds")).exists()
    assert not (pack / "terrain" / f"{SEA}_sea_overlay.ter").exists()


def test_the_streamer_follows_the_record_and_counts_what_its_squares_make(tmp_path: Path) -> None:
    """Its squares' files name what the record says; a water photo made is counted, saved with the
    textures' record and read back; a texture made neutral takes its water photo back to the
    placeholder, or away with its mask once on no square made."""
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.pack import load_built
    from orthostudio.live.streamer import Streamer

    pack = live_pack(tmp_path)
    set_water(pack, True, log=quiet)
    land = land_file(pack, SEA, 0, 0, water=False)
    cache = ChunkCache(tmp_path / "c.sqlite")
    try:
        s = Streamer(pack, cache, lambda on_piece: None, log=quiet)
        assert s.names.water == WaterRecord(rivers_fade=True)
        s.built[land.name] = 0
        s.names.make(TILE)
        assert s.water == {land.name: 0}
        s.save()
        assert load_built(pack)["water"] == {land.name: 0}
        again = Streamer(pack, cache, lambda on_piece: None, log=quiet)
        assert again.water == {land.name: 0}
        assert s.disk_used() > s.disk_used({}) > 0  # its water photo
        s.names.done.add(TILE)
        s._neutral(land.name)
        assert os.path.samefile(land.with_name(water_name(land.name)), WaterPool(pack).path(1))
        assert s.water == {}
        s.names.done.discard(TILE)
        make_again = land_file(pack, SEA, 0, 0, water=False)
        make(make_again, 0)
        s._neutral(land.name)  # on no square made: gone, its mask too
        assert not land.exists() and not land.with_name(water_name(land.name)).exists()
        assert not land.with_name(mask_name(land.name)).exists()
    finally:
        cache.close()


def test_a_build_that_missed_its_water_photo_s_name_makes_it_after(tmp_path: Path) -> None:
    """The water photo's name made after the build looked for it, before the streamer heard of the
    build: made from the land just built, counted; a failure said once."""
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.streamer import Streamer

    pack = live_pack(tmp_path)
    set_water(pack, False, log=quiet)
    land = land_file(pack, SEA, 0, 0)
    said: list[str] = []
    cache = ChunkCache(tmp_path / "c.sqlite")
    try:
        s = Streamer(pack, cache, lambda on_piece: None, log=said.append)
        s._water_built(land.name, 0, None)
        assert s.water == {land.name: 0}
        assert not os.path.samefile(land.with_name(water_name(land.name)), WaterPool(pack).path(1))
        s._water_built(land.name, 0, "OSError: no")
        s._water_built(land.name, 0, "OSError: no")
        assert s.water == {} and sum("could not be made" in line for line in said) == 1
    finally:
        cache.close()


# --- river masks put in place (2026-11-17) -----------------------------------------------------


def test_river_masks_put_in_place_wait_for_the_next_start(tmp_path: Path) -> None:
    """Soft banks chosen, a region whose river masks come while Live streams (Live installs them
    then): the running stream keeps X-Plane's water alone on its rivers, as on the squares it made
    before them; the next start sets the region up again, its terrain files made anew, the rivers
    faded. A region without them keeps its fingerprints of before: no set-up for nothing."""
    import shutil

    from orthostudio.live.region import REGION_MARKER, _fingerprints

    pack = live_pack(tmp_path)
    src = tmp_path / "z_osxp_test"
    shutil.rmtree(src / "rivers")  # a region without river masks
    setup_region(src, pack, log=quiet)
    lines: list[str] = []
    setup_region(src, pack, log=lines.append)
    assert lines == []
    assert len(_fingerprints([src])[str(src)]) == 2
    set_water(pack, True, log=quiet)
    names = live_names(pack, water=water_record(pack))
    (src / "rivers" / "textures").mkdir(parents=True)  # put in place while it streams
    (src / "rivers" / "textures" / mask_name(f"{RIVER}.dds")).write_bytes(mask_png())
    names.make(TILE)
    assert f"BORDER_TEX ../textures/{NO_BORDER}" in ter_of(pack, RIVER, "_water_overlay")
    assert not (pack / "textures" / water_name(f"{RIVER}.dds")).exists()
    setup_region(src, pack, log=lines.append)  # the next start
    assert any("changed since the pack was set up" in line for line in lines)
    assert not (pack / "terrain" / f"{RIVER}_water_overlay.ter").exists()
    assert len(json.loads((pack / REGION_MARKER).read_text())["fingerprints"][str(src)]) == 3
    live_names(pack, water=water_record(pack)).make(TILE)
    assert f"../textures/{RIVER}_water.dds" in ter_of(pack, RIVER, "_water_overlay")


# --- the window ---------------------------------------------------------------------------------


def test_the_window_asks_about_rivers_and_lakes_between_the_night_and_the_decal() -> None:
    """Two answers, even first and recommended, soft banks saying what it costs, the regions it
    applies to and when; sent with the settings as rivers_fade, in both languages."""
    import re

    from orthostudio.live.web import UI_DIR

    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    cards = re.findall(r'<fieldset class="question" id="(q-[a-z-]+)">', html)
    assert cards.index("q-rivers") == cards.index("q-night") + 1 == cards.index("q-decal") - 1
    block = html[html.index('id="q-rivers"') : html.index('id="q-decal"')]
    assert re.findall(r'name="rivers_fade" value="(\w+)"', block) == ["even", "soft"]
    for key in ("title", "help", "even", "even_note", "soft", "soft_note", "new_mesh", "restart"):
        assert f'data-i18n="rivers.{key}"' in block
    js = (UI_DIR / "live.js").read_text(encoding="utf-8")
    assert js.count('input[name="rivers_fade"]') == 2  # filled from the settings, and sent
    assert "rivers_fade: (" in js
    strings = (UI_DIR / "strings.js").read_text(encoding="utf-8")
    assert '"rivers.soft_note": "The photo fades into the water near the banks' in strings
    assert "up to 3 GB" in strings and "jusqu'à 3 Go de plus" in strings
    assert strings.count('"rivers.title"') == 2  # English and French
