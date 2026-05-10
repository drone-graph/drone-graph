"""Float32 vector blobs for SQLite storage (stdlib only)."""

from __future__ import annotations

import array


def floats_to_blob(values: list[float]) -> bytes:
    """Little-endian float32 bytes matching ``array.array('f')`` native order."""
    return array.array("f", values).tobytes()


def blob_to_floats(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()
