"""Adversarial fidelity review of P1 (grid, names, paths, ``.ter``, parent crop) against Ortho4XP.

Every reference here is a line-by-line transliteration of the Ortho4XP code named in
the docstrings (``src/O4_Geo_Utils.py``, ``src/O4_File_Names.py``, ``src/O4_DSF_Utils.py``,
``src/O4_Imagery_Utils.py``); no Ortho4XP module is imported. The transliterations are exercised
in both hemispheres, on degree edges and on texture edges, at ZL 10-19.
"""

from __future__ import annotations

import io
from math import atan, cos, exp, floor, log, pi, tan
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.imagery.grid import (
    TextureId,
    parse_texture_name,
    texture_at,
    texture_name,
    textures_covering,
    tile_to_wgs84,
)
from orthostudio.imagery.providers import load_registry
from orthostudio.textures.assemble import parent_fallback
from orthostudio.textures.ter import TerKind, TerParams, ter_center, ter_filename, ter_text
from orthostudio.tilefiles import paths as lpaths
from orthostudio.tilefiles.terrain import TerKind as ReaderTerKind
from orthostudio.tilefiles.terrain import parse_ter_name

# --- Ortho4XP transliterations
# ------------------------------------------------------------------------


def reference_gtile_to_wgs84(til_x: float, til_y: float, zl: int) -> tuple[float, float]:
    """``O4_Geo_Utils.py:66-77``."""
    rat_x = til_x / (2 ** (zl - 1)) - 1
    rat_y = 1 - til_y / (2 ** (zl - 1))
    lon = rat_x * 180
    lat = 360 / pi * atan(exp(pi * rat_y)) - 90
    return (lat, lon)


def reference_wgs84_to_orthogrid(lat: float, lon: float, zl: int) -> tuple[int, int]:
    """``O4_Geo_Utils.py:127-134``."""
    ratio_x = lon / 180
    ratio_y = log(tan((90 + lat) * pi / 360)) / pi
    mult = 2 ** (zl - 5)
    til_x = int((ratio_x + 1) * mult) * 16
    til_y = int((1 - ratio_y) * mult) * 16
    return (til_x, til_y)


def reference_webmercator_pixel_size(lat: float, zl: int) -> float:
    """``O4_Geo_Utils.py:32-34``."""
    return 2 * pi * 6378137 * cos(pi * lat / 180) / (2 ** (zl + 8))


def reference_short_latlon(lat: int, lon: int) -> str:
    """``O4_File_Names.py:24-27``."""
    return f"{lat:+.0f}".zfill(3) + f"{lon:+.0f}".zfill(4)


def reference_round_latlon(lat: int, lon: int) -> str:
    """``O4_File_Names.py:30-33``."""
    return f"{floor(lat / 10) * 10:+.0f}".zfill(3) + f"{floor(lon / 10) * 10:+.0f}".zfill(4)


def reference_create_terrain_file(
    texture_file_name: str,
    til_x_left: int,
    til_y_top: int,
    zoomlevel: int,
    tri_type: int,
    is_overlay: bool,
    *,
    water_tech: str,
    imprint_masks_to_dds: bool,
    mask_zl: int,
    use_decal_on_terrain: bool,
    terrain_casts_shadows: bool,
    use_test_texture: bool,
) -> tuple[str, str]:
    """``O4_DSF_Utils.py:261-357`` (``create_terrain_file``), file writes turned into a string."""
    suffix = "_water" if tri_type == 1 else "_sea" if tri_type == 2 else ""
    if is_overlay:
        suffix += "_overlay"
    ter_file_name = texture_file_name[:-4] + suffix + ".ter"
    if use_test_texture:
        texture_file_name = "test_texture.dds"
    out = "A\n800\nTERRAIN\n\n"
    lat_med, lon_med = reference_gtile_to_wgs84(til_x_left + 8, til_y_top + 8, zoomlevel)
    texture_approx_size = int(reference_webmercator_pixel_size(lat_med, zoomlevel) * 4096)
    out += (
        "LOAD_CENTER "
        + "{:.5f}".format(lat_med)  # noqa: UP032 - kept as Ortho4XP writes it
        + " "
        + "{:.5f}".format(lon_med)  # noqa: UP032
        + " "
        + str(texture_approx_size)
        + " 4096\n"
    )
    out += "BASE_TEX_NOWRAP ../textures/" + texture_file_name + "\n"
    if tri_type in (1, 2) and (not is_overlay):
        out += "WATER_COLOR_MASK\n"
    elif (tri_type == 1) or ((tri_type == 2) and (is_overlay == "ratio_water")):
        out += "BORDER_TEX ../textures/water_transition.png\n"
    elif (tri_type == 2) and (not imprint_masks_to_dds):
        out += (
            "LOAD_CENTER_BORDER "
            + "{:.5f}".format(lat_med)  # noqa: UP032
            + " "
            + "{:.5f}".format(lon_med)  # noqa: UP032
            + " "
            + str(texture_approx_size)
            + " "
            + str(4096 // 2 ** (zoomlevel - mask_zl))
            + "\n"
        )
        out += (
            "BORDER_TEX ../textures/"
            + str(til_y_top)
            + "_"
            + str(til_x_left)
            + "_ZL"
            + str(zoomlevel)
            + ".png"
            + "\n"
        )
    if (tri_type != 1) and use_decal_on_terrain:
        out += "DECAL_LIB lib/g10/decals/maquify_2_green_key.dcl\n"
    out += "WET\n" if tri_type in (1, 2) else "NO_ALPHA\n"
    if (tri_type in (1, 2)) or (not terrain_casts_shadows):
        out += "NO_SHADOW\n"
    return ter_file_name, out


# --- sample textures in every hemisphere ----------------------------------------------------------

SAMPLE_POINTS = [
    (43.3, 5.4),  # Marseille (N, E)
    (-34.6, -58.4),  # Buenos Aires (S, W)
    (35.7, 139.7),  # Tokyo (N, E, far east)
    (-41.3, 174.8),  # Wellington (S, E)
    (64.1, -21.9),  # Reykjavik (N, W, high latitude)
    (-0.5, 0.5),  # just south of the equator, east of Greenwich
    (0.5, -0.5),  # just north, west
    (-84.9, 179.9),  # web-mercator extreme corner
]


def _sample_textures(zls: tuple[int, ...] = (10, 12, 14, 16, 19)) -> list[TextureId]:
    return [texture_at(lat, lon, zl, "BI") for lat, lon in SAMPLE_POINTS for zl in zls]


# --- names ----------------------------------------------------------------------------------------


def test_texture_and_ter_names_round_trip_for_every_registry_code() -> None:
    """``texture_name``/``parse_texture_name`` and the ``tilefiles`` ``.ter`` parser agree for
    every registry code (including ``PDOK18``, ``PDOK20``, ``Arc@``) at ZL 10-19."""
    rng = np.random.default_rng(20260912)
    for code in load_registry():
        for zl in range(10, 20):
            for _ in range(20):
                lat = float(rng.uniform(-84, 84))
                lon = float(rng.uniform(-180, 180))
                t = texture_at(lat, lon, zl, code)
                assert parse_texture_name(texture_name(t)) == t, (code, zl)
                assert parse_texture_name(texture_name(t) + ".dds") == t
                for kind in TerKind:
                    name = ter_filename(t, kind)
                    parsed, parsed_kind = parse_ter_name(name)
                    assert parsed == t, (name, parsed)
                    assert parsed_kind.value == kind.value
                    # the two TerKind enums (writer and tilefiles reader) share their values
                    assert ReaderTerKind(kind.value).suffix == kind.suffix


def test_texture_name_zl_below_ten_with_digit_ending_code() -> None:
    """imagery-grid.md s. 3: ``PDOK189`` -> ``(PDOK18, 9)``; ``PDOK1814`` -> ``(PDOK18, 14)``."""
    assert parse_texture_name("5952_8416_PDOK189") == TextureId(8416, 5952, 9, "PDOK18")
    assert parse_texture_name("5952_8416_PDOK1814") == TextureId(8416, 5952, 14, "PDOK18")
    assert parse_texture_name("5952_8416_Arc@14") == TextureId(8416, 5952, 14, "Arc@")


# --- grid -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(43, 5), (-34, -59), (-1, 0), (0, -1), (0, 0), (60, -150), (-45, 170), (-85, -180)],
)
@pytest.mark.parametrize("zl", [12, 14, 16])
def test_textures_covering_equals_the_ortho4xp_dsf_enumeration(lat: int, lon: int, zl: int) -> None:
    """``O4_DSF_Utils.py:176-180``: corners from ``wgs84_to_orthogrid``, inclusive ranges, both
    hemispheres, the equator and the Greenwich meridian (corners exactly on texture edges)."""
    til_x_min, til_y_min = reference_wgs84_to_orthogrid(lat + 1, lon, zl)
    til_x_max, til_y_max = reference_wgs84_to_orthogrid(lat, lon + 1, zl)
    reference = {
        (x, y)
        for x in range(til_x_min, til_x_max + 1, 16)
        for y in range(til_y_min, til_y_max + 1, 16)
    }
    ours = textures_covering(lat, lon, lat + 1, lon + 1, zl, "BI")
    assert {(t.til_x, t.til_y) for t in ours} == reference
    assert len(ours) == len(reference)
    # every texture is inside the grid and named unambiguously
    for t in ours:
        assert 0 <= t.til_x < 2**zl and 0 <= t.til_y < 2**zl
        assert t.til_x % 16 == 0 and t.til_y % 16 == 0
        assert parse_texture_name(texture_name(t)) == t


def test_texture_at_and_tile_to_wgs84_are_the_ortho4xp_formulas_in_every_hemisphere() -> None:
    rng = np.random.default_rng(7)
    for _ in range(20_000):
        lat = float(rng.uniform(-85, 85))
        lon = float(rng.uniform(-180, 180))
        zl = int(rng.integers(10, 20))
        t = texture_at(lat, lon, zl, "X")
        assert (t.til_x, t.til_y) == reference_wgs84_to_orthogrid(lat, lon, zl)
        assert tile_to_wgs84(t.til_x + 8, t.til_y + 8, zl) == reference_gtile_to_wgs84(
            t.til_x + 8, t.til_y + 8, zl
        )
        assert ter_center(t) == reference_gtile_to_wgs84(t.til_x + 8, t.til_y + 8, zl)


# --- paths ----------------------------------------------------------------------------------------


def test_latlon_formatters_in_every_hemisphere() -> None:
    """``O4_File_Names.py:24-41``."""
    for lat in range(-90, 90):
        for lon in range(-180, 180, 7):
            assert lpaths.short_latlon(lat, lon) == reference_short_latlon(lat, lon), (lat, lon)
            assert lpaths.round_latlon(lat, lon) == reference_round_latlon(lat, lon), (lat, lon)
            assert Path(lpaths.long_latlon(lat, lon)).as_posix() == (
                reference_round_latlon(lat, lon) + "/" + reference_short_latlon(lat, lon)
            )


# --- .ter -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("water_tech", ["XP11 + bathy", "XP12"])
@pytest.mark.parametrize("imprint", [True, False])
@pytest.mark.parametrize("decal", [False, True])
@pytest.mark.parametrize("shadows", [True, False])
@pytest.mark.parametrize("test_texture", [False, True])
def test_ter_text_equals_create_terrain_file_for_every_kind_and_hemisphere(
    water_tech: str, imprint: bool, decal: bool, shadows: bool, test_texture: bool
) -> None:
    """Byte equality with the transliterated ``create_terrain_file`` on 40 textures x 5 kinds,
    ``mask_zl`` at, below and above the texture zoom level."""
    for t in _sample_textures():
        for mask_zl in (14, t.zl, t.zl + 1):
            params = TerParams(
                water_tech=water_tech,  # type: ignore[arg-type]
                imprint_masks_to_dds=imprint,
                mask_zl=mask_zl,
                use_decal_on_terrain=decal,
                # Ortho4XP writes the decal on the sea too; OrthoStudio XP does it when asked
                decal_on_sea=True,
                terrain_casts_shadows=shadows,
                use_test_texture=test_texture,
            )
            lat_med, lon_med = ter_center(t)
            for kind in TerKind:
                name, text = reference_create_terrain_file(
                    texture_name(t) + ".dds",
                    t.til_x,
                    t.til_y,
                    t.zl,
                    kind.tri_type,
                    kind.overlay,
                    water_tech=water_tech,
                    imprint_masks_to_dds=imprint,
                    mask_zl=mask_zl,
                    use_decal_on_terrain=decal,
                    terrain_casts_shadows=shadows,
                    use_test_texture=test_texture,
                )
                assert ter_filename(t, kind) == name
                ours = ter_text(t, kind, lat_med=lat_med, lon_med=lon_med, params=params)
                assert ours == text, (t, kind, params)
                assert ours.encode("ascii")  # Ortho4XP writes plain ASCII


# --- parent fallback crop -------------------------------------------------------------------------


def _png(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("d", [1, 2, 3, 4, 5])
def test_parent_crop_and_resize_equal_get_wmts_image(d: int) -> None:
    """``O4_Imagery_Utils.py:1256-1272``: crop window of the parent and BICUBIC upsampling,
    pixel-identical to the OrthoStudio XP fallback for chunks in every quadrant of the parent."""
    rng = np.random.default_rng(d)
    parent_rgb = rng.integers(0, 256, size=(256, 256, 3), dtype=np.uint8)
    parent_body = _png(parent_rgb)
    zl = 14
    for _ in range(12):
        til_x_left = int(rng.integers(0, 2**zl // 16)) * 16
        til_y_top = int(rng.integers(0, 2**zl // 16)) * 16
        t = TextureId(til_x_left, til_y_top, zl, "BI")
        i = int(rng.integers(0, 256))
        col, row = i % 16, i // 16
        til_x_orig, til_y_orig = til_x_left + col, til_y_top + row
        # Ortho4XP: halve the indices d times, then crop/resize the parent image
        til_x, til_y = til_x_orig, til_y_orig
        for _ in range(d):
            til_x //= 2
            til_y //= 2
        x0 = (til_x_orig - 2**d * til_x) * 256 // (2**d)
        y0 = (til_y_orig - 2**d * til_y) * 256 // (2**d)
        x1 = x0 + 256 // (2**d)
        y1 = y0 + 256 // (2**d)
        reference = np.asarray(
            Image.fromarray(parent_rgb).crop((x0, y0, x1, y1)).resize((256, 256), Image.BICUBIC)
        )

        def get_parent(x: int, y: int, z: int, want=(til_x, til_y, zl - d)) -> bytes | None:
            return parent_body if (x, y, z) == want else None

        ours = parent_fallback(t, get_parent, max_levels=5)(i)
        assert ours is not None
        assert np.array_equal(ours, reference), (t, i, d)


def test_parent_fallback_never_asks_a_sixth_level() -> None:
    """Ortho4XP returns white once ``down_sample`` reaches 6 (``:1280-1284``): d = 1..5 only."""
    asked: list[tuple[int, int, int]] = []

    def get_parent(x: int, y: int, z: int) -> bytes | None:
        asked.append((x, y, z))
        return None

    t = TextureId(8448, 6016, 14, "BI")
    assert parent_fallback(t, get_parent)(0) is None
    assert asked == [(8448 >> d, 6016 >> d, 14 - d) for d in range(1, 6)]
