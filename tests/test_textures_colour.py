"""The colours of the photo (``textures/colour.py``, docs/specs/pipeline-textures.md 6).

A user of the X-Plane.Org page found aerial imagery too bright and too saturated (2026-09-18).
The maths is checked on values computed by hand, and the neutral case is checked to return the
image itself, because that is what keeps the artefact key of every tile built before.
"""

from __future__ import annotations

import numpy as np

from orthostudio.textures.colour import LUMA, adjust_photo, photo_unchanged


def _image() -> np.ndarray:
    return np.array([[[0, 0, 0], [255, 255, 255], [200, 100, 50], [10, 20, 30]]], dtype=np.uint8)


def test_zero_leaves_the_image_itself() -> None:
    img = _image()
    assert photo_unchanged(0.0, 0.0, 0.0)
    assert adjust_photo(img) is img  # the same object: no copy, no key change upstream


def test_brightness_scales_every_channel_and_clips() -> None:
    out = adjust_photo(_image(), brightness=-0.5)
    assert out[0, 1].tolist() == [128, 128, 128]  # 255 * 0.5 = 127.5, rounded
    assert out[0, 2].tolist() == [100, 50, 25]
    assert adjust_photo(_image(), brightness=1.0)[0, 2].tolist() == [255, 200, 100]  # clipped


def test_contrast_pushes_away_from_mid_grey() -> None:
    out = adjust_photo(_image(), contrast=0.5)
    # 200 -> (200 - 127.5) * 1.5 + 127.5 = 236.25
    assert out[0, 2, 0] == 236  # 236.25
    # mid grey does not move
    grey = np.full((1, 1, 3), 128, dtype=np.uint8)
    assert adjust_photo(grey, contrast=0.5)[0, 0].tolist() == [128, 128, 128]


def test_saturation_moves_towards_the_pixel_grey() -> None:
    img = _image()
    full_grey = adjust_photo(img, saturation=-1.0)
    r, g, b = (int(v) for v in img[0, 2])
    expected = round(r * LUMA[0] + g * LUMA[1] + b * LUMA[2])
    assert full_grey[0, 2].tolist() == [expected] * 3
    # a grey pixel has nothing to take away
    assert adjust_photo(np.full((1, 1, 3), 90, dtype=np.uint8), saturation=-0.5)[0, 0, 0] == 90
    # and more colour pulls the channels apart
    assert adjust_photo(img, saturation=0.3)[0, 2, 0] > img[0, 2, 0]


def test_a_large_image_is_treated_in_blocks_and_keeps_its_shape() -> None:
    img = (np.arange(1200 * 3 * 3, dtype=np.uint8) % 251).reshape(1200, 3, 3)
    out = adjust_photo(img, brightness=-0.1, saturation=-0.2)
    assert out.shape == img.shape and out.dtype == np.uint8
    assert not np.array_equal(out, img)


def test_a_neutral_look_keeps_the_key_of_every_texture_built_before() -> None:
    """``TextureDdsParams.canonical`` drops the three while they are zero (2026-09-18)."""
    from orthostudio.pipeline.rule import TextureDdsParams

    common = {"provider": "BI", "zl": 16, "encoder": "ispc", "encoder_version": "1"}
    neutral = TextureDdsParams(**common).canonical()
    assert not [k for k in neutral if k.startswith("photo_")]
    softer = TextureDdsParams(**common, photo_saturation=-0.15).canonical()
    assert softer["photo_saturation"] == -0.15 and softer["photo_brightness"] == 0.0
    assert {k: v for k, v in softer.items() if not k.startswith("photo_")} == neutral


def test_a_zone_colours_the_pixels_it_covers_whatever_its_size() -> None:
    """A zone used to colour whole texture files, the one holding its centre. A zone smaller than
    a texture (6.4 km at ZL16) then held no centre, changed nothing, and said nothing either (a
    user, 2026-09-23). The colours follow the ring now, with a soft edge."""
    import numpy as np

    from orthostudio.imagery.grid import texture_at
    from orthostudio.pipeline.build import photo_zone_shapes
    from orthostudio.textures.colour import adjust_photo_inside

    # a zone of about 4 km inside one texture: the case that did nothing at all
    ring = [46.62, 6.52, 46.62, 6.58, 46.65, 6.58, 46.65, 6.52, 46.62, 6.52]
    texture = texture_at(46.63, 6.55, 16, "BI")
    (shape,) = photo_zone_shapes([[ring, 0.06, 0.0, 0.0]], texture)
    assert shape[1:] == (0.06, 0.0, 0.0)
    assert len(shape[0]) == len(ring)  # the ring, in the texture's own pixels

    # a texture the zone does not reach keeps nothing of it
    assert photo_zone_shapes([[ring, 0.06, 0.0, 0.0]], texture_at(40.0, 0.0, 16, "BI")) == ()
    assert photo_zone_shapes([], texture) == ()

    # and the pixels inside are the ones that change
    flat = np.full((256, 256, 3), 100, dtype=np.uint8)
    square = [64, 64, 192, 64, 192, 192, 64, 192, 64, 64]
    out = adjust_photo_inside(flat, square, brightness=0.5, feather_px=8)
    assert out[128, 128][0] == 150  # inside
    assert out[10, 10][0] == 100  # outside, untouched
    assert 100 < out[64, 128][0] < 150  # the soft edge, so the join does not show
    assert adjust_photo_inside(flat, square, brightness=0.0).tobytes() == flat.tobytes()


def test_the_zones_of_a_tile_carry_their_look_to_the_build() -> None:
    """``zones.photo_zone_entries`` clips them per tile, and only for a zone that names one."""
    from orthostudio.config.overrides import PHOTO_LOOKS
    from orthostudio.model import TileRef
    from orthostudio.zones import Zone, photo_zone_entries, with_photo_zones

    square = [[6.1, 46.1], [6.4, 46.1], [6.4, 46.4], [6.1, 46.4]]
    toned = Zone.model_validate(
        {"id": "a", "zl": 17, "photo": {"look": "much_softer"}, "polygon": square}
    )
    plain = Zone.model_validate({"id": "b", "zl": 17, "polygon": square})
    (entry,) = photo_zone_entries([toned, plain], TileRef(46, 6))
    assert tuple(entry[1:]) == PHOTO_LOOKS["much_softer"]
    assert len(entry[0]) % 2 == 0 and entry[0][:2] == entry[0][-2:]  # a closed ring of lat, lon
    assert photo_zone_entries([toned], TileRef(45, 6)) == []  # another tile: nothing
    assert photo_zone_entries([plain], TileRef(46, 6)) == []  # no look of its own: nothing
    assert "photo_zones" not in with_photo_zones({}, [plain], TileRef(46, 6))
    assert with_photo_zones({}, [toned], TileRef(46, 6))["photo_zones"] == [entry]


def test_the_three_levels_are_settings_then_tile_then_zone() -> None:
    """Each level inherits the one above until it names its own (a user, 2026-09-18)."""
    from orthostudio.config.overrides import PHOTO_LOOKS
    from orthostudio.model import TileRef
    from orthostudio.zones import PhotoChoice, TileChoice, Zone, with_photo_zones, with_tile_photo

    settings = {"photo_brightness": 0.0, "photo_contrast": 0.0, "photo_saturation": 0.0}
    # a tile that says nothing keeps the settings' answer
    assert with_tile_photo(settings, None) == settings
    assert with_tile_photo(settings, TileChoice()) == settings
    # a tile that names its own wins over the settings
    tile = TileChoice(photo=PhotoChoice(look="softer"))
    got = with_tile_photo(settings, tile)
    assert (got["photo_brightness"], got["photo_contrast"], got["photo_saturation"]) == PHOTO_LOOKS[
        "softer"
    ]
    # its own numbers, when it asks for them
    mine = TileChoice(photo=PhotoChoice(look="custom", saturation=-0.45))
    assert with_tile_photo(settings, mine)["photo_saturation"] == -0.45
    # a zone inside wins in its polygon, and a zone that says nothing leaves the tile alone
    square = [[6.1, 46.1], [6.4, 46.1], [6.4, 46.4], [6.1, 46.4]]
    inherits = Zone.model_validate({"id": "a", "zl": 17, "polygon": square})
    assert "photo_zones" not in with_photo_zones(got, [inherits], TileRef(46, 6))
    own = Zone.model_validate(
        {"id": "b", "zl": 17, "photo": {"look": "as_delivered"}, "polygon": square}
    )
    with_zone = with_photo_zones(got, [own], TileRef(46, 6))
    assert with_zone["photo_zones"][0][1:] == [0.0, 0.0, 0.0]  # as delivered, inside the zone
    assert with_zone["photo_saturation"] == PHOTO_LOOKS["softer"][2]  # the tile's, everywhere else


def test_a_zone_bigger_than_a_texture_reaches_the_ones_it_covers() -> None:
    """Asking whether a corner of the ring lands in the texture answers no for a texture in the
    middle of a large zone, so a zone drawn over a city coloured its four corners and left a
    checkerboard of untouched squares inside it (2026-09-23). The question is whether the two
    boxes overlap."""
    from orthostudio.imagery.grid import texture_at
    from orthostudio.pipeline.build import photo_zone_shapes

    # a zone of about 30 km, far larger than a texture (6.4 km at ZL16)
    ring = [46.4, 6.2, 46.4, 6.8, 46.7, 6.8, 46.7, 6.2, 46.4, 6.2]
    zones = [[ring, 0.0, 0.0, -0.15]]
    reached = 0
    covered = 0
    for lat in (46.42, 46.48, 46.55, 46.62, 46.68):
        for lon in (6.22, 6.35, 6.5, 6.65, 6.78):
            covered += 1
            if photo_zone_shapes(zones, texture_at(lat, lon, 16, "BI")):
                reached += 1
    assert reached == covered, "every texture the zone covers takes its colours"
    assert photo_zone_shapes(zones, texture_at(40.0, 0.0, 16, "BI")) == ()


def test_a_zone_replaces_the_squares_colours_and_does_not_add_to_them() -> None:
    """A square softened, a zone inside it set back to what was delivered: the zone must show the
    photograph as it came. Applying the zone's three numbers on top of the square's left the
    softening in place, so the zone did nothing at all, which is the complaint zones were made
    for (2026-09-23)."""
    import numpy as np

    from orthostudio.textures.colour import adjust_photo, adjust_photo_inside

    delivered = np.full((256, 256, 3), 160, dtype=np.uint8)
    square = adjust_photo(delivered, saturation=-0.4, brightness=-0.06)
    assert square[128, 128][0] != 160

    ring = [64, 64, 192, 64, 192, 192, 64, 192, 64, 64]
    as_delivered = adjust_photo_inside(square, ring, feather_px=0, source=delivered)
    assert as_delivered[128, 128][0] == 160, "inside the zone, the photograph as delivered"
    assert as_delivered[10, 10][0] == square[10, 10][0], "outside it, the square's own"

    # and a zone that does ask for something gets its own, not its own over the square's
    own = adjust_photo_inside(square, ring, brightness=0.25, feather_px=0, source=delivered)
    assert own[128, 128][0] == adjust_photo(delivered, brightness=0.25)[128, 128][0]


def test_two_zones_that_overlap_are_resolved_as_the_page_paints_them() -> None:
    """The page paints the last first so the first ends on top (decision M4). The build applied
    them in document order, so the last won and the two disagreed wherever they overlapped."""
    from orthostudio.imagery.grid import texture_at
    from orthostudio.pipeline.build import photo_zone_shapes

    first = [46.62, 6.52, 46.62, 6.58, 46.65, 6.58, 46.65, 6.52, 46.62, 6.52]
    second = [46.63, 6.53, 46.63, 6.59, 46.66, 6.59, 46.66, 6.53, 46.63, 6.53]
    shapes = photo_zone_shapes([[first, 0.1, 0.0, 0.0], [second, -0.1, 0.0, 0.0]],
                               texture_at(46.63, 6.55, 16, "BI"))  # fmt: skip
    assert len(shapes) == 2
    assert shapes[-1][1] == 0.1, "the first of the list is applied last, so it wins"
