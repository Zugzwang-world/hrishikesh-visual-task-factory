"""Exact geometry of the v0 primitives: extent along a direction and distance to a point.

Conventions are SceneSpec's (see `vtf.core.scene`): an object's pose is the centre of its local
bounding box and `dimensions` are its full local extents. Box (dx, dy, dz); cylinder and cone along
local z with radius dx/2 and half-height dz/2, the cone's base at -dz/2 and apex at +dz/2; sphere
radius dx/2. Assets have no exact shape in v0 and are treated as their bounding box.
"""

import math
from collections.abc import Sequence

import numpy as np

from vtf.core.scene import PRIMITIVE_SHAPES, SceneObject

DOWN = np.array([0.0, 0.0, -1.0])
Point2 = tuple[float, float]


def solid_kind(obj: SceneObject) -> str:
    """The solid used for geometry: the primitive itself, or 'box' (bounding box) for an asset."""
    if obj.source == "primitive":
        if obj.shape not in PRIMITIVE_SHAPES:
            raise ValueError(f"{obj.id}: unknown primitive shape {obj.shape!r}")
        return obj.shape
    return "box"


def support(kind: str, dims: Sequence[float], u: Sequence[float]) -> float:
    """max over points x of the solid of x . u, with x measured from the centre, in local axes."""
    ux, uy, uz = (float(v) for v in u)
    dx, dy, dz = (float(v) for v in dims)
    if kind == "box":
        return (abs(ux) * dx + abs(uy) * dy + abs(uz) * dz) / 2.0
    if kind == "sphere":
        return dx / 2.0 * math.sqrt(ux * ux + uy * uy + uz * uz)
    r, h = dx / 2.0, dz / 2.0
    perp = math.hypot(ux, uy)
    if kind == "cylinder":
        return h * abs(uz) + r * perp
    if kind == "cone":  # the farthest point is the apex or a point on the base rim
        return max(h * uz, -h * uz + r * perp)
    raise ValueError(f"unknown solid {kind!r}")


def lowest_z(obj: SceneObject) -> float:
    """World z of the object's lowest point, from its shape, dimensions and full rotation."""
    down_local = obj.pose.rotation_matrix().T @ DOWN
    return float(obj.pose.position[2]) - support(solid_kind(obj), obj.dimensions, down_local)


def to_local(obj: SceneObject, point: Sequence[float]) -> np.ndarray:
    """A world point in the object's local frame (origin at its centre)."""
    offset = np.asarray(point, dtype=float) - np.asarray(obj.pose.position, dtype=float)
    return obj.pose.rotation_matrix().T @ offset


def _segment_distance(p: Point2, a: Point2, b: Point2) -> float:
    abx, aby = b[0] - a[0], b[1] - a[1]
    apx, apy = p[0] - a[0], p[1] - a[1]
    length2 = abx * abx + aby * aby
    t = 0.0 if length2 == 0.0 else min(1.0, max(0.0, (apx * abx + apy * aby) / length2))
    return math.hypot(apx - t * abx, apy - t * aby)


def local_distance(kind: str, dims: Sequence[float], q: Sequence[float]) -> float:
    """Distance from local point q to the solid's nearest surface; 0 when q is inside."""
    qx, qy, qz = (float(v) for v in q)
    dx, dy, dz = (float(v) for v in dims)
    if kind == "box":
        ex = max(abs(qx) - dx / 2.0, 0.0)
        ey = max(abs(qy) - dy / 2.0, 0.0)
        ez = max(abs(qz) - dz / 2.0, 0.0)
        return math.sqrt(ex * ex + ey * ey + ez * ez)
    if kind == "sphere":
        return max(math.sqrt(qx * qx + qy * qy + qz * qz) - dx / 2.0, 0.0)
    # Solids of revolution: the nearest point lies in q's meridian half-plane, so the problem is
    # 2D in (rho, z) against the solid's half cross-section.
    r, h = dx / 2.0, dz / 2.0
    rho = math.hypot(qx, qy)
    if kind == "cylinder":
        return math.hypot(max(rho - r, 0.0), max(abs(qz) - h, 0.0))
    if kind == "cone":
        if -h <= qz <= h and rho <= r * (h - qz) / (2.0 * h):
            return 0.0
        corners = [(0.0, -h), (r, -h), (0.0, h)]  # axis foot, base rim, apex
        return min(_segment_distance((rho, qz), corners[i], corners[(i + 1) % 3]) for i in range(3))
    raise ValueError(f"unknown solid {kind!r}")


def point_distance(obj: SceneObject, point: Sequence[float]) -> float:
    """Distance from a world point to the object's nearest surface; 0 when the point is inside."""
    return local_distance(solid_kind(obj), obj.dimensions, to_local(obj, point))
