"""Rendered passes of one camera, back-projected to world points (the pixel route of G2).

Everything here comes from the render outputs: the index pass, the planar depth pass, the amodal
masks, and the manifest's K and world_to_camera. Nothing reads an object's pose from the scene
file. With lens distortion (CameraQuality.distortion_k1) the passes are warped like the image, so
pixel coordinates are undistorted before back-projection.

Distortion convention (assumed, shared with vtf.camera): x_d = x_u * (1 + k1 * r_u^2) on
normalised coordinates x = (u - cx) / fx, y = (v - cy) / fy, with r_u^2 = x_u^2 + y_u^2. If
vtf.camera normalises differently, change `undistort_normalised` to match.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from vtf.core.io import read_depth, read_index_png, read_mask_png
from vtf.core.render_manifest import CameraOutputs
from vtf.core.scene import TABLE_PASS_INDEX, CameraSpec

UNDISTORT_ITERATIONS = 20


def undistort_normalised(xd: np.ndarray, yd: np.ndarray, k1: float) -> tuple[np.ndarray, ...]:
    """Invert x_d = x_u (1 + k1 r_u^2) by fixed-point iteration (exact enough for |k1| < 0.5)."""
    if not k1:
        return xd, yd
    xu, yu = xd.copy(), yd.copy()
    for _ in range(UNDISTORT_ITERATIONS):
        scale = 1.0 + k1 * (xu * xu + yu * yu)
        xu, yu = xd / scale, yd / scale
    return xu, yu


@dataclass
class CameraPasses:
    """One camera's rendered passes plus lazily back-projected world points per pass index."""

    cam: CameraSpec
    out: CameraOutputs
    index: np.ndarray
    depth: np.ndarray
    amodal: dict[int, np.ndarray]
    _points: dict[int, np.ndarray] = field(default_factory=dict, repr=False)
    _counts: dict[int, int] | None = field(default=None, repr=False)

    @classmethod
    def load(cls, render_dir: Path, cam: CameraSpec, out: CameraOutputs) -> "CameraPasses":
        index = read_index_png(render_dir / out.index)
        depth = read_depth(render_dir / out.depth)
        amodal = {int(k): read_mask_png(render_dir / p) for k, p in out.amodal.items()}
        return cls(cam=cam, out=out, index=index, depth=depth, amodal=amodal)

    @property
    def k1(self) -> float:
        """Lens distortion the render actually applied (the manifest's quality; empty = clean).
        Never the scene file's request: a renderer without the dial leaves the passes unwarped."""
        q = self.out.quality or {}
        return float(q.get("distortion_k1") or 0.0)

    @property
    def K(self) -> np.ndarray:  # noqa: N802 - the usual name
        return np.asarray(self.out.K, dtype=float)

    @property
    def w2c(self) -> np.ndarray:
        return np.asarray(self.out.world_to_camera, dtype=float)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.index.shape
        return w, h

    def pixel_counts(self) -> dict[int, int]:
        if self._counts is None:
            ids, counts = np.unique(self.index, return_counts=True)
            self._counts = dict(zip(ids.tolist(), counts.tolist(), strict=True))
        return self._counts

    def visible_pixels(self, pass_index: int) -> int:
        return int(self.pixel_counts().get(pass_index, 0))

    def object_indices(self, min_pixels: int = 1) -> list[int]:
        """Object pass indices (1..999) with at least min_pixels visible pixels."""
        return sorted(
            p
            for p, n in self.pixel_counts().items()
            if 1 <= p < TABLE_PASS_INDEX and n >= min_pixels
        )

    def back_project(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        """(N,) pixel rows and columns -> (N, 3) world points; pixels without depth are dropped."""
        d = self.depth[rows, cols].astype(float)
        ok = d > 0
        rows, cols, d = rows[ok], cols[ok], d[ok]
        k = self.K
        u, v = cols + 0.5, rows + 0.5
        if self.k1:
            # The dial's exact inverse (k1 plus its fill-the-frame zoom), from vtf.camera.
            from vtf.camera import undistort_points

            uv = undistort_points(np.stack([u, v], axis=1), self.k1, k)
            keep = np.isfinite(uv).all(axis=1)
            u, v, d = uv[keep, 0], uv[keep, 1], d[keep]
        x = (u - k[0, 2]) / k[0, 0]
        y = (v - k[1, 2]) / k[1, 1]
        pc = np.stack([x * d, y * d, d], axis=1)
        w2c = self.w2c
        rot, t = w2c[:3, :3], w2c[:3, 3]
        return (pc - t) @ rot  # R^T (p_c - t)

    def points(self, pass_index: int) -> np.ndarray:
        """World points of every visible pixel of one pass index (cached)."""
        if pass_index not in self._points:
            rows, cols = np.nonzero(self.index == pass_index)
            self._points[pass_index] = self.back_project(rows, cols)
        return self._points[pass_index]

    def camera_right_xy(self) -> np.ndarray:
        """Image right (camera +x) projected onto the table plane, unit length."""
        right = self.w2c[0, :3]  # rows of R are the camera axes in world coordinates
        v = np.array([right[0], right[1]])
        n = float(np.linalg.norm(v))
        if n < 1e-9:
            raise ValueError("camera x axis is vertical: left/right undefined on the table")
        return v / n

    def ring(self, pass_index: int, radius_px: int = 5) -> tuple[np.ndarray, np.ndarray]:
        """Pixels within radius_px of the object's visible mask but not on it (rows, cols)."""
        import cv2

        mask = (self.index == pass_index).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius_px + 1,) * 2)
        grown = cv2.dilate(mask, kernel) > 0
        return np.nonzero(grown & (mask == 0))
