"""Directory and file naming of Ortho4XP (``O4_File_Names.py``)."""

from __future__ import annotations

from math import floor
from pathlib import Path


def short_latlon(lat: int, lon: int) -> str:
    """``+43+005`` (O4_File_Names.py:24-27)."""
    return f"{lat:+03d}{lon:+04d}"


def round_latlon(lat: int, lon: int) -> str:
    """``+40+000``: the 10x10 degree folder (O4_File_Names.py:30-33)."""
    return f"{floor(lat / 10) * 10:+03d}{floor(lon / 10) * 10:+04d}"


def long_latlon(lat: int, lon: int) -> Path:
    """``+40+000/+43+005`` (O4_File_Names.py:36-41)."""
    return Path(round_latlon(lat, lon)) / short_latlon(lat, lon)


def tile_cfg_path(build_dir: Path, lat: int, lon: int) -> Path:
    """``<build>/Ortho4XP_+43+005.cfg`` (O4_Config_Utils.py:525-528)."""
    return Path(build_dir) / f"Ortho4XP_{short_latlon(lat, lon)}.cfg"
