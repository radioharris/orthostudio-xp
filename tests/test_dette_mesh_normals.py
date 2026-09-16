"""Dette D5: the float32 normals contract of ``MeshData`` (documentation, with the numbers).

``docs/specs/mesh-file.md`` section 8 states why float32 is exact for an Ortho4XP ``.mesh`` file
and why the DSF encoder must still round to 2 decimals in float64. These tests hold the
claims of that section; nothing here asks for a code change.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from orthostudio.dsf.encode import NORMAL_DECIMALS, _normals_as_ortho4xp

SPEC = Path(__file__).resolve().parents[1] / "docs" / "specs" / "mesh-file.md"


def _u16(values: np.ndarray, sign: int) -> np.ndarray:
    """What the DSF encoder stores: ``round((1 +- n) / 2 * 65535)``."""
    return np.round((1 + sign * values) / 2 * 65535).astype(np.int64)


def test_float32_holds_every_value_an_ortho4xp_mesh_can_contain() -> None:
    """A normal in the text file is one of the 201 values -1.00 .. 1.00 (2 decimals)."""
    grid = np.round(np.arange(-100, 101) / 100, NORMAL_DECIMALS)
    as_f32 = grid.astype(np.float32).astype(np.float64)
    assert np.array_equal(np.round(as_f32, NORMAL_DECIMALS), grid)
    assert np.abs(as_f32 - grid).max() < 1e-7


def test_the_encoder_rounds_because_the_grid_lands_on_quantisation_ties() -> None:
    n = np.float32(-0.40)
    assert _u16(np.array([float(n)]), -1)[0] != _u16(np.array([-0.40]), -1)[0]
    assert float(np.round(np.float64(n), NORMAL_DECIMALS)) == -0.40


def test_normals_as_ortho4xp_rounds_float32_and_passes_float64_through() -> None:
    f32 = np.array([[-0.4, 0.07]], dtype=np.float32)
    out = _normals_as_ortho4xp(f32)
    assert out.dtype == np.float64 and out.tolist() == [[-0.4, 0.07]]
    f64 = np.array([[0.123456789, -0.5]], dtype=np.float64)
    assert _normals_as_ortho4xp(f64) is f64  # a natively computed normal is never quantised


def test_the_spec_documents_the_contract() -> None:
    text = SPEC.read_text("utf-8")
    assert "## 8. Normals: the float32 contract (dette D5)" in text
    assert "1 493" in text and "_normals_as_ortho4xp" in text
