# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Texture pipeline: mip chains, BC1/BC3 encoding and the DDS container for X-Plane."""

from orthostudio.textures.bcdecode import decode_dds
from orthostudio.textures.dds import DdsHeader, build_header, expected_size, parse_header
from orthostudio.textures.encode import (
    EncoderUnavailableError,
    available_encoders,
    encode_dds,
    encode_level,
)
from orthostudio.textures.mips import mip_chain

__all__ = [
    "DdsHeader",
    "EncoderUnavailableError",
    "available_encoders",
    "build_header",
    "decode_dds",
    "encode_dds",
    "encode_level",
    "expected_size",
    "mip_chain",
    "parse_header",
]
