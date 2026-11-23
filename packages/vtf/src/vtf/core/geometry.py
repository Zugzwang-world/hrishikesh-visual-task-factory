"""Poses and rotations.

Conventions used everywhere in the factory:

- Units are metres.
- The world frame is Blender's: right-handed, Z up.
- Quaternions are unit quaternions in w, x, y, z order.
"""

from collections.abc import Iterable, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

IDENTITY_QUAT: Quat = (1.0, 0.0, 0.0, 0.0)


class Pose(BaseModel):
    """A rigid pose in the world frame."""

    model_config = ConfigDict(extra="forbid")

    position: Vec3 = Field(description="Position in metres, world frame (Z up).")
    rotation: Quat = Field(
        default=IDENTITY_QUAT, description="Unit quaternion in w, x, y, z order."
    )

    def rotation_matrix(self) -> np.ndarray:
        return quat_to_matrix(self.rotation)

    def matrix(self) -> np.ndarray:
        """4x4 local-to-world transform."""
        m = np.eye(4)
        m[:3, :3] = quat_to_matrix(self.rotation)
        m[:3, 3] = self.position
        return m


def quat_to_matrix(q: Sequence[float]) -> np.ndarray:
    """3x3 rotation matrix from a w, x, y, z quaternion (normalised first)."""
    w, x, y, z = np.asarray(q, dtype=float) / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def matrix_to_quat(m: np.ndarray) -> Quat:
    """w, x, y, z quaternion (with w >= 0) from a 3x3 rotation matrix."""
    m = np.asarray(m, dtype=float)[:3, :3]
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * np.sqrt(trace + 1.0)
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = 2.0 * np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.asarray(q) / np.linalg.norm(q)
    if q[0] < 0:
        q = -q
    return tuple(float(v) for v in q)  # type: ignore[return-value]


def quat_from_axis_angle(axis: Sequence[float], angle_rad: float) -> Quat:
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    s = np.sin(angle_rad / 2.0)
    return (float(np.cos(angle_rad / 2.0)), *(float(v) for v in axis * s))  # type: ignore[return-value]


def quat_multiply(a: Sequence[float], b: Sequence[float]) -> Quat:
    """Hamilton product a * b (apply b first, then a)."""
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return (
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    )


def look_at_camera_quat(
    eye: Sequence[float], target: Sequence[float], up: Sequence[float] = (0.0, 0.0, 1.0)
) -> Quat:
    """Rotation of a Blender camera at `eye` looking at `target`.

    A Blender camera looks down its local -Z axis with local +Y as image up and +X as image right.
    """
    eye, target, up = (np.asarray(v, dtype=float) for v in (eye, target, up))
    forward = target - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    cam_up = np.cross(right, forward)
    rot = np.column_stack([right, cam_up, -forward])
    return matrix_to_quat(rot)


def transform_points(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Apply a 4x4 transform to an (N, 3) array of points."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def round_vec(values: Iterable[float], ndigits: int = 6) -> tuple[float, ...]:
    """Round floats for stable, byte-identical JSON (6 digits is one micrometre)."""
    return tuple(round(float(v), ndigits) + 0.0 for v in values)
