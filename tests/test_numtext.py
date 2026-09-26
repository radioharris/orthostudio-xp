# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Numbers read from text the same way on every system (``orthostudio.numtext``).

``np.fromstring`` parses with the C library, slow on Windows: a ZL16 mesh took 8 s to read there
against 0.6 s on Linux, and every Imagery of a user's tiles waited behind its DSF (2026-09-15).
Windows splits the text and lets Python parse the tokens; both ways give the same numbers.
"""

from __future__ import annotations

import numpy as np
import pytest

from orthostudio.numtext import parse_numbers

FLOATS = (
    b"  0.1 -2.5e-3\t3.0000000000000004\r\n1e308 -0.0 +7 .5 5. 123456789.123456789\n"
    b"4.9406564584124654e-324 2.2250738585072014e-308 0.30000000000000004"
)


def test_both_ways_give_the_same_numbers_bit_for_bit() -> None:
    a = parse_numbers(FLOATS, np.float64, split=False)
    b = parse_numbers(FLOATS, np.float64, split=True)
    assert a.size == 12 and a.tobytes() == b.tobytes()
    text = FLOATS.decode("ascii")
    assert parse_numbers(text, np.float64, split=True).tobytes() == a.tobytes()
    ints = b"1 2 3 -4\n1024 0 +5 9223372036854775807\n"
    assert np.array_equal(
        parse_numbers(ints, np.int64, split=False), parse_numbers(ints, np.int64, split=True)
    )
    assert parse_numbers(b"", np.float64, split=True).size == 0


def test_a_word_among_the_numbers_is_refused_both_ways() -> None:
    """The readers turn it into their format error, whichever way parsed."""
    for split in (False, True):
        with pytest.raises(ValueError):
            parse_numbers(b"1 2 three 4", np.float64, split=split)
