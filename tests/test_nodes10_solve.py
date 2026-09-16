"""The 2x2 solve of the noder, unit-tested against the one Ortho4XP runs.

``are_encroached`` (``O4_Vector_Utils.py:280-281``) calls ``numpy.linalg.solve`` on the 2x2
system ``alpha * ab + beta * dc = ac``; the crossing coordinate is then
``(1 - alpha) * a + alpha * b`` on the edge being inserted. The last bits of ``alpha``
therefore decide on which side of a 9-decimal tie the node lands, and the orthophoto grid
abscissae are exactly such ties, so ``_solve_2x2`` is not free to use any algebraically
equivalent formula: see ``docs/specs/vectors-pslg.md`` 2.8.

Two levels are checked here:

* **hard**, machine-independent: the solution is a solution, and it is as close to
  ``numpy.linalg.solve`` as two backward-stable solves can be -- a few ulps times the condition
  number of the system. This is what protects the function from a real bug.
* **informational**, LAPACK-dependent: it agrees with ``numpy.linalg.solve`` *bit for bit*.
  This is the property the parity with Ortho4XP was measured on; it is skipped, not failed, when
  the local LAPACK rounds differently, because then Ortho4XP's own result would move too.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from orthostudio.vectors.noding import _solve_2x2


def _cases(n: int = 200_000, seed: int = 11) -> tuple[NDArray, NDArray, NDArray]:
    """Segment pairs in tile-local coordinates, a quarter of them axis-aligned."""
    rng = np.random.default_rng(seed)

    def segment(count: int) -> tuple[NDArray, NDArray]:
        start = rng.uniform(-0.03, 1.03, size=(count, 2))
        scale = rng.choice([1e-5, 1e-3, 1.0625], size=(count, 1))
        return start, start + rng.normal(size=(count, 2)) * scale

    a_old, b_old = segment(n)
    a_new, b_new = segment(n)
    q = n // 4
    b_old[:q, 0] = a_old[:q, 0]  # vertical grid lines
    b_new[q : 2 * q, 1] = a_new[q : 2 * q, 1]  # horizontal grid lines
    a_new[2 * q : 3 * q, 1] = b_new[2 * q : 3 * q, 1] = 0.0  # the y = 0 tile border
    return b_new - a_new, a_old - b_old, a_old - a_new


def _reference(ab: NDArray, dc: NDArray, ac: NDArray) -> NDArray:
    matrix = np.stack([np.stack([ab[:, 0], dc[:, 0]], 1), np.stack([ab[:, 1], dc[:, 1]], 1)], 1)
    return np.linalg.solve(matrix, ac[..., None])[..., 0]


def test_it_solves_the_system() -> None:
    """The defining property, independent of any rounding convention."""
    ab, dc, ac = _cases(20_000)
    with np.errstate(all="ignore"):
        alpha, beta = _solve_2x2(ab, dc, ac)
    residual = alpha[:, None] * ab + beta[:, None] * dc - ac
    scale = np.maximum(np.abs(ac).max(axis=1), 1e-300)
    finite = np.isfinite(alpha) & np.isfinite(beta)
    assert finite.sum() > 19_000
    assert np.all(np.abs(residual[finite]).max(axis=1) <= 1e-9 * scale[finite])


def test_it_stays_within_a_few_ulps_of_numpy() -> None:
    """Hard bound, whatever the local LAPACK does: the gap to ``numpy.linalg.solve`` stays within
    the forward error of a backward-stable solve, ``cond(M) x eps x |x|`` up to a small factor.

    Measured per system on the norm of the solution, not per component: a component that is 0
    for one library can be 3e-17 for another. A plain "8 ulps" held with Apple's Accelerate and
    failed with OpenBLAS (Linux, Windows) on the ill-conditioned systems of nearly parallel
    segments, where each library pivots its own way.
    """
    ab, dc, ac = _cases(100_000)
    with np.errstate(all="ignore"):
        alpha, beta = _solve_2x2(ab, dc, ac)
    reference = _reference(ab, dc, ac)
    ours = np.column_stack([alpha, beta])
    finite = np.isfinite(ours).all(axis=1) & np.isfinite(reference).all(axis=1)
    matrix = np.stack([np.stack([ab[:, 0], dc[:, 0]], 1), np.stack([ab[:, 1], dc[:, 1]], 1)], 1)
    cond = np.maximum(np.linalg.cond(matrix[finite]), 1.0)
    gap = np.abs(ours[finite] - reference[finite]).max(axis=1)
    size = np.abs(reference[finite]).max(axis=1)
    tolerance = 8 * cond * np.finfo(np.float64).eps * np.maximum(size, np.finfo(np.float64).tiny)
    ratio = gap / tolerance
    worst = int(np.argmax(ratio))
    assert ratio[worst] <= 1.0, (
        f"gap {gap[worst]:.3e} for |x| {size[worst]:.3e}, cond {cond[worst]:.3e}: "
        f"{ratio[worst]:.1f} times the bound ({int((ratio > 1).sum())} systems above it)"
    )


def test_it_matches_numpy_bit_for_bit() -> None:
    """The property the Ortho4XP parity rests on; a different LAPACK skips instead of failing."""
    ab, dc, ac = _cases()
    with np.errstate(all="ignore"):
        alpha, beta = _solve_2x2(ab, dc, ac)
    reference = _reference(ab, dc, ac)
    ours = np.column_stack([alpha, beta])
    same = ours.view(np.int64) == reference.view(np.int64)
    agreement = float(same.all(axis=1).mean())
    if agreement < 1.0:
        pytest.skip(
            "numpy.linalg.solve rounds differently here "
            f"({agreement:.4%} of {len(ours)} pairs identical): this LAPACK is not the one "
            "the noder was measured with, so the parity of "
            "docs/specs/vectors-pslg.md 2.8 must be re-measured on this machine"
        )
    assert agreement == 1.0


def test_the_grid_abscissae_are_9_decimal_ties() -> None:
    """Why the last bits matter at all: those five x are exactly halfway at the 9th decimal.

    ``145 / 1024 = 0.1416015625`` is exact in binary and exactly between ``0.141601562`` and
    ``0.141601563``; ``round`` and ``"{:.9f}"`` break the tie to even, any noise above it
    breaks it the other way. The five abscissae below are the ones tile +43+005 hits.
    """
    for numerator in (145, 235, 415, 685, 955):
        x = numerator / 1024
        assert x * 1e9 == np.floor(x * 1e9) + 0.5  # an exact tie, not a near miss
        assert round(x, 9) == float(f"{x:.9f}")  # round() and the writer agree
        assert round(x, 9) != x  # and they move it


def test_a_horizontal_line_crossing_a_vertical_one_lands_off_the_tie() -> None:
    """The ten-node mechanism in four lines, on the real numbers of tile +43+005.

    The horizontal grid line y = 0 spans ``[-2**-5, 1 + 2**-5]``; where it meets the vertical
    grid line ``x = 145 / 1024`` Ortho4XP computes the crossing *on the horizontal*, and the
    result is one ulp above the tie, so it rounds to ``…563`` while the tile-border vertex at
    the same place (exactly ``145 / 1024``) rounds to ``…562``.
    """
    eps = 2.0**-5
    x = 145 / 1024
    ab = np.array([[1.0 + 2 * eps, 0.0]])  # the horizontal line, being inserted
    dc = np.array([[0.0, -0.5 - eps]])  # the vertical line piece, c - d
    ac = np.array([[x + eps, -eps]])  # c - a
    with np.errstate(all="ignore"):
        alpha, _ = _solve_2x2(ab, dc, ac)
    crossing = (1 - alpha[0]) * -eps + alpha[0] * (1 + eps)
    assert crossing > x  # the noise is above the tie
    assert f"{crossing:.9f}" == "0.141601563"
    assert f"{x:.9f}" == "0.141601562"
