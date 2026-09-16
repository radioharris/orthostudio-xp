"""Content digests (blake3) of bytes, files and directories."""

from __future__ import annotations

import os
import re
from pathlib import Path

import blake3

from orthostudio.graph.errors import InvalidDigestError

__all__ = [
    "DIR_MANIFEST_FORMAT",
    "check_digest",
    "digest_bytes",
    "digest_dir",
    "digest_file",
    "digest_path",
    "dir_manifest",
]

DIR_MANIFEST_FORMAT = "osxp-dir-manifest-1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def check_digest(value: str, what: str = "digest") -> str:
    """Return ``value`` if it is a 64-char lowercase hex string, else raise InvalidDigestError."""
    if not isinstance(value, str) or not _HEX64.match(value):
        raise InvalidDigestError(f"{what} must be 64 lowercase hex characters, got {value!r}")
    return value


def digest_bytes(data: bytes) -> str:
    """blake3 hex digest of ``data``."""
    return blake3.blake3(data).hexdigest()


def digest_file(path: Path | str) -> str:
    """blake3 hex digest of a regular file (memory-mapped, multi-threaded)."""
    h = blake3.blake3(max_threads=blake3.blake3.AUTO)
    h.update_mmap(os.fspath(path))
    return h.hexdigest()


def dir_manifest(root: Path | str) -> bytes:
    """Manifest of a directory tree: one ``relpath\\0size\\0digest\\n`` line per regular file.

    Files are sorted by their POSIX relative path. Empty directories are not recorded.
    Symbolic links are refused.
    """
    root = Path(root)
    lines = [f"{DIR_MANIFEST_FORMAT}\n"]
    for rel, full in _walk_files(root):
        size = full.stat().st_size
        lines.append(f"{rel}\0{size}\0{digest_file(full)}\n")
    return "".join(lines).encode("utf-8")


def digest_dir(root: Path | str) -> tuple[str, int]:
    """Return ``(digest, total_bytes)`` of a directory: blake3 of its manifest."""
    root = Path(root)
    total = 0
    lines = [f"{DIR_MANIFEST_FORMAT}\n"]
    for rel, full in _walk_files(root):
        size = full.stat().st_size
        total += size
        lines.append(f"{rel}\0{size}\0{digest_file(full)}\n")
    return digest_bytes("".join(lines).encode("utf-8")), total


def digest_path(path: Path | str) -> tuple[str, int]:
    """Return ``(digest, size)`` of a regular file or of a directory tree."""
    p = Path(path)
    if p.is_symlink():
        raise OSError(f"refusing to digest a symbolic link: {p}")
    if p.is_dir():
        return digest_dir(p)
    if p.is_file():
        return digest_file(p), p.stat().st_size
    raise FileNotFoundError(p)


def _walk_files(root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        base = Path(dirpath)
        for name in filenames:
            full = base / name
            if full.is_symlink():
                raise OSError(f"refusing to digest a symbolic link inside an artefact: {full}")
            if not full.is_file():
                continue
            found.append((full.relative_to(root).as_posix(), full))
    found.sort(key=lambda item: item[0])
    return found
