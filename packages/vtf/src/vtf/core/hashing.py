"""Canonical JSON and content hashes (G7, task IDs)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", by_alias=True)
    return obj


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8: the bytes every content hash is taken over."""
    return json.dumps(
        to_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    """Hash of decoded pixels: dtype, shape and C-order bytes (independent of file encoding)."""
    array = np.ascontiguousarray(array)
    h = hashlib.sha256()
    h.update(f"{array.dtype.str}{array.shape}".encode())
    h.update(array.tobytes())
    return h.hexdigest()


def short_id(prefix: str, hex_digest: str, length: int = 16) -> str:
    return f"{prefix}{hex_digest[:length]}"
