"""Camera math in the OpenCV convention, computed from a CameraSpec.

- Camera frame: x right, y down, z forward (metres). Depth means planar depth: camera-frame z.
- Pixel coordinates: (u, v) with (0, 0) at the top-left corner of the image, so the centre of
  pixel (row i, column j) is (j + 0.5, i + 0.5) and a centred lens has principal point (W/2, H/2).
- Square pixels, horizontal sensor fit: fx = fy = focal_mm / sensor_width_mm * W.

The renderer writes K and world_to_camera into the RenderManifest from Blender's own camera; they
must agree with these functions (the validate step checks it).
"""

import numpy as np

from vtf.core.geometry import quat_to_matrix
from vtf.core.scene import CameraSpec

# Blender camera axes (x right, y up, z backward) -> OpenCV axes (x right, y down, z forward).
BLENDER_TO_CV = np.diag([1.0, -1.0, -1.0])


def intrinsics(cam: CameraSpec) -> np.ndarray:
    width, height = cam.resolution
    f = cam.focal_mm / cam.sensor_width_mm * width
    return np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])


def camera_to_world(cam: CameraSpec) -> np.ndarray:
    """4x4 transform from the OpenCV camera frame to the world frame."""
    m = np.eye(4)
    m[:3, :3] = quat_to_matrix(cam.pose.rotation) @ BLENDER_TO_CV
    m[:3, 3] = cam.pose.position
    return m


def world_to_camera(cam: CameraSpec) -> np.ndarray:
    """4x4 transform from the world frame to the OpenCV camera frame."""
    c2w = camera_to_world(cam)
    rot = c2w[:3, :3]
    m = np.eye(4)
    m[:3, :3] = rot.T
    m[:3, 3] = -rot.T @ c2w[:3, 3]
    return m


def to_camera(cam: CameraSpec, points_world: np.ndarray) -> np.ndarray:
    """(N, 3) world points -> (N, 3) OpenCV camera-frame points; column 2 is planar depth."""
    w2c = world_to_camera(cam)
    pts = np.asarray(points_world, dtype=float).reshape(-1, 3)
    return pts @ w2c[:3, :3].T + w2c[:3, 3]


def project(cam: CameraSpec, points_world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(N, 3) world points -> ((N, 2) pixel coordinates u, v; (N,) planar depth)."""
    pc = to_camera(cam, points_world)
    k = intrinsics(cam)
    z = pc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = k[0, 0] * pc[:, 0] / z + k[0, 2]
        v = k[1, 1] * pc[:, 1] / z + k[1, 2]
    return np.stack([u, v], axis=1), z


def pixel_rays(cam: CameraSpec) -> tuple[np.ndarray, np.ndarray]:
    """Ray origin (3,) and (H, W, 3) world directions through every pixel centre.

    Directions are scaled so their camera-frame z is 1: the ray parameter t of a hit is its
    planar depth.
    """
    width, height = cam.resolution
    k = intrinsics(cam)
    u = np.arange(width) + 0.5
    v = np.arange(height) + 0.5
    uu, vv = np.meshgrid(u, v)
    dirs_cam = np.stack(
        [(uu - k[0, 2]) / k[0, 0], (vv - k[1, 2]) / k[1, 1], np.ones_like(uu)], axis=-1
    )
    c2w = camera_to_world(cam)
    return c2w[:3, 3].copy(), dirs_cam @ c2w[:3, :3].T
