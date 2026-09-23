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


def test_the_map_paints_a_zone_over_the_square_it_is_drawn_in() -> None:
    """The map repaints what carries its own colours, and a canvas keeps what is drawn last.

    A user saw a zone keep the colours of its square (2026-09-18): the squares were painted
    after the zones, covering them. The order must end with what a build applies -- the zone
    inside its polygon, and, where two overlap, the one higher in the list, since a build paints a
    zone's colours on the ground it covers (``build.photo_zone_shapes``).
    """
    if NODE is None:
        pytest.skip("node is not installed")
    zones = [
        {"id": "first", "polygon": [[0, 0], [1, 0], [1, 1]], "photo": {"look": "softer"}},
        {"id": "second", "polygon": [[0, 0], [1, 0], [1, 1]], "photo": {"look": "much_softer"}},
        {"id": "plain", "polygon": [[0, 0], [1, 0], [1, 1]], "photo": {"look": None}},
        {"id": "sliver", "polygon": [[0, 0], [1, 0]], "photo": {"look": "softer"}},
    ]
    tiles = {"+43+005": {"photo": {"look": "custom", "brightness": 0.5}}}
    script = (
        "import('./geo.js').then(m => process.stdout.write(JSON.stringify("
        f"m.colouredRegions({json.dumps(zones)}, {json.dumps(tiles)})"
        ".map(r => r.square ? 'square' : r.photo.look))))"
    )
    out = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        cwd=UI,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    # the square first, then the zones from the last to the first: what wins is painted last
    assert json.loads(out.stdout) == ["square", "much_softer", "softer"]


def test_the_mock_switch_is_read_from_every_way_it_is_written() -> None:
    """A user said the mock did not work: ``#plan?mock=1`` -- the query after the route -- and
    ``?mock=true`` showed the real engine without a word (2026-09-18). ``osxp serve --mock`` opens
    the right address; these are the ones typed by hand."""
    if NODE is None:
        pytest.skip("node is not installed")
    addresses = [
        "http://127.0.0.1:8641/?mock=1",
        "http://127.0.0.1:8641/#plan?mock=1",
        "http://127.0.0.1:8641/?mock=true",
        "http://127.0.0.1:8641/?mock",
        "http://127.0.0.1:8641/?mock=0",
        "http://127.0.0.1:8641/#plan",
        "http://127.0.0.1:8641/?mock=1&speed=10",
    ]
    script = (
        "import('./app.js').then(m => process.stdout.write(JSON.stringify("
        f"{json.dumps(addresses)}.map(href => {{ const p = m.pageParams(href); "
        "return [m.isOn(p.get('mock')), p.get('speed')]; }))))"
    )
    out = subprocess.run(
        [NODE, "--input-type=module"],
        input=script,
        cwd=UI,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == [
        [True, None],
        [True, None],
        [True, None],
        [True, None],
        [False, None],
        [False, None],
        [True, "10"],
    ]
