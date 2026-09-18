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
