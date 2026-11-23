"""File helpers for the renderer: EXR reading, PNG writing without metadata, stable JSON.

Runs inside Blender's Python (numpy and OpenImageIO ship with Blender), but imports no bpy.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import OpenImageIO as oiio


def r6(value: float) -> float:
    """Round to 6 digits (one micrometre) with no negative zero: stable JSON bytes."""
    return round(float(value), 6) + 0.0


def write_json(path: Path, data: Any) -> None:
    """Pretty JSON with a trailing newline, the same layout as vtf.core.io.model_json."""
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def read_exr_channels(path: Path) -> dict[str, np.ndarray]:
    """Every channel of every subimage of a multilayer EXR as float32 (H, W) arrays.

    Blender 5.x writes multilayer EXR as multi-part: one subimage per pass. Row 0 is the top row.
    """
    channels: dict[str, np.ndarray] = {}
    nsubimages = oiio.ImageBuf(str(path)).nsubimages
    if nsubimages == 0:
        raise RuntimeError(f"Cannot open {path}: {oiio.geterror()}")
    for subimage in range(nsubimages):
        buf = oiio.ImageBuf(str(path), subimage, 0)
        pixels = buf.get_pixels(oiio.FLOAT)
        for i, name in enumerate(buf.spec().channelnames):
            channels[name] = np.asarray(pixels[:, :, i], dtype=np.float32)
    return channels


def find_channel(channels: dict[str, np.ndarray], *suffixes: str) -> np.ndarray:
    for name, data in channels.items():
        if name.endswith(suffixes):
            return data
    raise KeyError(f"No channel ending in {suffixes!r}; have {sorted(channels)}")


def read_index_and_depth(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Object index as uint16 and planar depth as float32 (0.0 where nothing is hit)."""
    channels = read_exr_channels(path)
    # Blender 5.x names the pass "Object Index"; older releases used "IndexOB".
    raw = find_channel(channels, "Object Index.X", "IndexOB.X")
    index = np.rint(raw).astype(np.uint16)  # never uint8: table 1000 and gripper 1001
    try:
        z = find_channel(channels, "Depth.Z")
    except KeyError:
        return index, None
    depth = np.where(z < 1e9, z, 0.0).astype(np.float32)
    return index, depth


def write_png(path: Path, pixels: np.ndarray, fmt: str) -> None:
    """Write a PNG with OpenImageIO and no metadata, so equal pixels give equal bytes."""
    if pixels.ndim == 2:
        pixels = pixels[:, :, None]
    h, w, c = pixels.shape
    out = oiio.ImageOutput.create(str(path))
    if out is None:
        raise RuntimeError(f"Cannot create {path}: {oiio.geterror()}")
    out.open(str(path), oiio.ImageSpec(w, h, c, fmt))
    out.write_image(np.ascontiguousarray(pixels))
    out.close()


def read_png_rgb(path: Path) -> np.ndarray:
    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise RuntimeError(f"Cannot open {path}: {oiio.geterror()}")
    spec = inp.spec()
    pixels = np.asarray(inp.read_image(0, 3, "uint8")).reshape(spec.height, spec.width, 3)
    inp.close()
    return pixels
