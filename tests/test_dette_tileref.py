"""Dette D2: one ``TileRef``, re-exported by the two modules that used to duplicate it."""

from __future__ import annotations

import inspect
from pathlib import Path

import orthostudio.install as install_pkg
import orthostudio.install._model as install_model
import orthostudio.overlays as overlays_pkg
import orthostudio.overlays._tileref as overlays_tileref
from orthostudio.model import TileRef


def test_every_import_path_gives_the_same_class() -> None:
    for candidate in (
        install_pkg.TileRef,
        install_model.TileRef,
        overlays_pkg.TileRef,
        overlays_tileref.TileRef,
    ):
        assert candidate is TileRef


def test_the_duplicates_are_gone_from_the_source() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "orthostudio"
    for path in (root / "install" / "_model.py", root / "overlays" / "_tileref.py"):
        text = path.read_text("utf-8")
        assert "class " not in text, f"{path.name} defines a class again"
        assert "from orthostudio.model import TileRef" in text


def test_the_shared_class_keeps_what_the_duplicates_offered() -> None:
    # install/_model.py and overlays/_tileref.py both had name + folder; model adds the rest.
    t = TileRef(43, 5)
    assert (t.lat, t.lon) == (43, 5) and t.name == "+43+005" and t.folder == "+40+000"
    assert t.dsf_relpath == Path("Earth nav data/+40+000/+43+005.dsf")
    assert TileRef.parse("+43+005") == t and str(t) == "+43+005"
    # the folder is floored towards minus infinity, as both stand-ins did
    assert TileRef(-3, -5).name == "-03-005" and TileRef(-3, -5).folder == "-10-010"
    assert TileRef(-30, -180).folder == "-30-180"


def test_it_is_still_a_named_tuple_two_fields_wide() -> None:
    assert issubclass(TileRef, tuple) and TileRef._fields == ("lat", "lon")
    assert TileRef(43, 5) == (43, 5)  # structural equality kept for old call sites
    assert "lat" in inspect.signature(TileRef).parameters
