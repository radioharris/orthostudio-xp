# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Pictures read several to a detector's patch (``clouds.model.classify_many``): each answered in
its own place, made ready on its own; one larger than a patch read alone; one showing nothing not
read. A network that answers pixel by pixel stands for the detector."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from orthostudio.clouds import model


class _Net:
    """Cloud where a pixel's red, made ready, is above its picture's mean: no neighbour counts."""

    def setInput(self, blob: np.ndarray) -> None:  # noqa: N802 (OpenCV's name)
        self.blob = blob

    def forward(self) -> np.ndarray:
        out = np.zeros((1, 4, *self.blob.shape[2:]), np.float32)
        out[0, model.THICK] = self.blob[0, 0]
        return out


def test_pictures_read_together_are_each_answered_in_their_own_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model, "_networks", lambda: [_Net(), _Net()])
    rng = np.random.default_rng(3)
    sizes = [(120, 300), (400, 90), (37, 37), (600, 700), (700, 500), (1200, 40), (5, 5)]
    images = [(rng.random((h, w, 3)) * 255).astype(np.uint8) for h, w in sizes]
    valids = [np.ones((h, w), bool) for h, w in sizes]
    valids[1][:10] = False  # no image there: clear, and out of its statistics
    valids[6][:] = False  # nothing shown: nothing read
    said: list[Any] = []
    got = model.classify_many(images, valids, progress=lambda d, t: said.append((d, t)))
    for im, ok, cls in list(zip(images, valids, got, strict=True))[:5]:
        expect = np.where((model._normalised(im, ok)[0] > 0) & ok, model.THICK, model.CLEAR)
        assert np.array_equal(cls, expect)
    assert np.array_equal(got[5], model.classify(images[5], valids[5]))  # too tall: alone
    assert not got[6].any() and got[6].shape == (5, 5)
    assert said[-1][0] == said[-1][1] == 3  # two patches, then the tall one
