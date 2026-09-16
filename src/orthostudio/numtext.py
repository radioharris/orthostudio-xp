"""The whitespace-separated numbers of a text, parsed fast on every system.

``np.fromstring(text, sep=" ")`` parses with the C library's ``strtod`` and ``strtoll``, slow on
Windows: the vertices of a ZL16 mesh took 1.15 s on a Windows runner against 0.40 s on a Linux
one, 2.94 s on a Windows ARM one, and ``read_mesh`` 8 s against 0.6 s. The DSF of each tile reads
its mesh, and on a user's Windows every Imagery waited "pending" behind it (2026-09-15). Python's
own parsing of the split tokens took 0.32 s there, with the same numbers bit for bit (it rounds
correctly, like the C libraries of macOS and Linux); elsewhere ``np.fromstring`` stays, 4 to 5
times faster than splitting for integers.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
from numpy.typing import NDArray

__all__ = ["SPLIT_TOKENS", "parse_numbers"]

SPLIT_TOKENS = os.name == "nt"
"""Whether :func:`parse_numbers` splits the text and parses the tokens (Windows) rather than handing
it to ``np.fromstring``."""


def parse_numbers(
    text: bytes | str, dtype: Any = np.float64, *, split: bool | None = None
) -> NDArray[Any]:
    """The numbers of ``text``, separated by any whitespace, as a flat array of ``dtype``.

    ``ValueError`` for a token that is not a number, both ways (``np.fromstring`` raises since
    numpy 2.3). The callers check the count against the text's own header. ``split`` chooses the
    way (:data:`SPLIT_TOKENS` by default)."""
    if not (SPLIT_TOKENS if split is None else split):
        return np.fromstring(text, dtype=dtype, sep=" ")
    return np.array(text.split(), dtype=dtype)
