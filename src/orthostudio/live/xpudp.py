# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""X-Plane's UDP dataref subscription (RREF), as AutoOrtho uses it: no plugin, no setting to
change."""

from __future__ import annotations

import socket
import struct
from collections.abc import Sequence

XPLANE_PORT = 49000


class XPlaneUDP:
    """Subscribes to datarefs on X-Plane's UDP port; X-Plane answers to our socket's own port."""

    def __init__(
        self,
        datarefs: Sequence[str],
        freq_hz: int = 5,
        host: str = "127.0.0.1",
        port: int = XPLANE_PORT,
    ):
        self.datarefs = list(datarefs)
        self.freq_hz = freq_hz
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))

    def _send(self, freq: int) -> None:
        for idx, name in enumerate(self.datarefs):
            msg = struct.pack("<5sii400s", b"RREF\x00", freq, idx, name.encode("ascii"))
            self.sock.sendto(msg, self.addr)

    def subscribe(self) -> None:
        self._send(self.freq_hz)

    def unsubscribe(self) -> None:
        self._send(0)

    def command(self, name: str) -> None:
        """Run an X-Plane command, e.g. ``sim/operation/reload_scenery``."""
        self.sock.sendto(b"CMND\x00" + name.encode("ascii") + b"\x00", self.addr)

    def receive(self, timeout: float) -> dict[int, float] | None:
        """Values of one RREF packet, {index: value}, or None on timeout or another packet."""
        self.sock.settimeout(timeout)
        try:
            data, _ = self.sock.recvfrom(8192)
        except TimeoutError:
            return None
        return decode_rref(data)

    def close(self) -> None:
        try:
            self.unsubscribe()
        finally:
            self.sock.close()


def decode_rref(data: bytes) -> dict[int, float] | None:
    if data[:4] != b"RREF":
        return None
    out: dict[int, float] = {}
    body = data[5:]
    for i in range(len(body) // 8):
        idx, value = struct.unpack_from("<if", body, i * 8)
        out[idx] = value
    return out


def encode_rref(values: list[tuple[int, float]]) -> bytes:
    """An RREF answer as X-Plane sends it (for tests)."""
    return b"RREF," + b"".join(struct.pack("<if", i, v) for i, v in values)
