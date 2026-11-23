"""Post-render camera-quality dial: noise, blur, motion blur, lens distortion, low light, JPEG and
a CCTV look. Pure numpy and OpenCV, seeded from CameraQuality.seed; Blender never sees it.

- `vtf.camera.dial`: the image operations and the lens warp (`warp_index`, `distort_points`).
- `vtf.camera.apply`: applies each camera's dial to a finished render folder (the bridge calls it).
- `vtf.camera.presets`: clean, mild, harsh and cctv CameraQuality values for the sampler.
"""

from vtf.camera.dial import (
    apply_to_beauty,
    distort_points,
    is_clean,
    undistort_points,
    warp_beauty,
    warp_index,
    warp_nearest,
)
from vtf.camera.presets import PRESETS, cctv, clean, harsh, mild, preset

__all__ = [
    "PRESETS",
    "apply_to_beauty",
    "cctv",
    "clean",
    "distort_points",
    "harsh",
    "is_clean",
    "mild",
    "preset",
    "undistort_points",
    "warp_beauty",
    "warp_index",
    "warp_nearest",
]
