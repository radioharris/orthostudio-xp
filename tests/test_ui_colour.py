"""The page's colour maths and the engine's are the same (docs/specs/ui.md 2.4).

The Settings screen previews the photo colours before a build downloads gigabytes, which is
only honest if ``ui/colour.js`` computes what ``textures/colour.py`` will encode. Both are fed
the same pixels here. They may differ by one step: numpy rounds half to even, JavaScript rounds
half up.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from orthostudio.config.overrides import PHOTO_LOOKS
from orthostudio.textures.colour import adjust_photo

UI = Path(__file__).resolve().parent.parent / "src" / "orthostudio" / "ui"
NODE = shutil.which("node")

PIXELS = [
    (0, 0, 0), (255, 255, 255), (127, 128, 129), (200, 100, 50), (10, 20, 30),
    (250, 5, 130), (60, 180, 75), (33, 33, 33),
]  # fmt: skip
LOOKS = [
    {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0},
    {"brightness": -0.03, "contrast": 0.0, "saturation": -0.15},
    {"brightness": -0.06, "contrast": -0.03, "saturation": -0.3},
    {"brightness": 0.2, "contrast": 0.5, "saturation": -1.0},
    {"brightness": -0.5, "contrast": -0.5, "saturation": 0.5},
]


def _js(pixels: list[tuple[int, int, int]], look: dict[str, float]) -> list[list[int]]:
    if NODE is None:
        pytest.skip("node is not installed")
    script = (
        "import('./colour.js').then(m => process.stdout.write(JSON.stringify("
        f"{json.dumps(pixels)}.map(([r, g, b]) => m.adjustPixel(r, g, b, {json.dumps(look)})))))"
    )
    out = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        cwd=UI,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


def test_the_page_computes_what_the_engine_encodes() -> None:
    image = np.array([list(PIXELS)], dtype=np.uint8)
    for look in LOOKS:
        engine = adjust_photo(image, **look)[0]
        page = np.array(_js(list(PIXELS), look), dtype=np.int16)
        diff = np.abs(engine.astype(np.int16) - page)
        assert diff.max() <= 1, (look, engine.tolist(), page.tolist())


def test_the_page_and_the_engine_agree_on_the_named_looks() -> None:
    """``PHOTO_LOOKS`` is written twice, once per language: hold the two equal."""
    if NODE is None:
        pytest.skip("node is not installed")
    script = "import('./colour.js').then(m => process.stdout.write(JSON.stringify(m.PHOTO_LOOKS)))"
    page = json.loads(
        subprocess.run(
            [NODE, "--input-type=module"],
            input=script,
            cwd=UI,
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout
    )
    assert set(page) == set(PHOTO_LOOKS)
    for name, (brightness, contrast, saturation) in PHOTO_LOOKS.items():
        assert page[name] == {
            "brightness": brightness,
            "contrast": contrast,
            "saturation": saturation,
        }, name
