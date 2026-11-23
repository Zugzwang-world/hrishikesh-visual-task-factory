"""Analytic ray caster for scene primitives, in numpy.

Renders exact object-index, planar depth and amodal masks for a SceneSpec without Blender. Used
to make the contract fixtures, and as an independent check of the Blender passes (camera
conventions, poses). It is not the renderer.
"""

from dataclasses import dataclass

import numpy as np

from vtf.core.geometry import Pose
from vtf.core.projection import pixel_rays
from vtf.core.scene import CameraSpec, SceneSpec

INF = np.inf


def _local_rays(pose: Pose, origin: np.ndarray, dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rot = pose.rotation_matrix()
    o = (origin - np.asarray(pose.position)) @ rot  # R^T (origin - p)
    d = dirs @ rot
    return o, d


def _smallest_positive(*ts: np.ndarray) -> np.ndarray:
    stack = np.stack([np.where(t > 1e-9, t, INF) for t in ts])
    return stack.min(axis=0)


def hit_box(dims, o, d) -> np.ndarray:
    half = np.asarray(dims) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / d
        t1 = (-half - o) * inv
        t2 = (half - o) * inv
    t1 = np.nan_to_num(t1, nan=-INF)
    t2 = np.nan_to_num(t2, nan=INF)
    tmin = np.minimum(t1, t2).max(axis=-1)
    tmax = np.maximum(t1, t2).min(axis=-1)
    hit = (tmax >= np.maximum(tmin, 0.0)) & np.isfinite(tmax)
    t = np.where(tmin > 1e-9, tmin, tmax)
    return np.where(hit & (t > 1e-9), t, INF)


def hit_sphere(dims, o, d) -> np.ndarray:
    r = dims[0] / 2.0
    a = (d * d).sum(-1)
    b = 2.0 * (o * d).sum(-1)
    c = (o * o).sum(-1) - r * r
    disc = b * b - 4 * a * c
    sq = np.sqrt(np.maximum(disc, 0.0))
    t0 = (-b - sq) / (2 * a)
    t1 = (-b + sq) / (2 * a)
    t = _smallest_positive(t0, t1)
    return np.where(disc >= 0, t, INF)


def _caps(o, d, z_values, radius_at) -> list[np.ndarray]:
    out = []
    with np.errstate(divide="ignore", invalid="ignore"):
        for z, r in zip(z_values, radius_at, strict=True):
            t = (z - o[..., 2]) / d[..., 2]
            x = o[..., 0] + t * d[..., 0]
            y = o[..., 1] + t * d[..., 1]
            ok = np.isfinite(t) & (x * x + y * y <= r * r)
            out.append(np.where(ok, t, INF))
    return out


def hit_cylinder(dims, o, d) -> np.ndarray:
    r, hz = dims[0] / 2.0, dims[2] / 2.0
    a = d[..., 0] ** 2 + d[..., 1] ** 2
    b = 2.0 * (o[..., 0] * d[..., 0] + o[..., 1] * d[..., 1])
    c = o[..., 0] ** 2 + o[..., 1] ** 2 - r * r
    disc = b * b - 4 * a * c
    sq = np.sqrt(np.maximum(disc, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        sides = [(-b - sq) / (2 * a), (-b + sq) / (2 * a)]
    cands = []
    for t in sides:
        z = o[..., 2] + t * d[..., 2]
        cands.append(np.where((disc >= 0) & (a > 1e-12) & (np.abs(z) <= hz), t, INF))
    cands += _caps(o, d, [-hz, hz], [r, r])
    return _smallest_positive(*cands)


def hit_cone(dims, o, d) -> np.ndarray:
    """Cone with base radius r at local z = -h/2 and apex at z = +h/2."""
    r, hz = dims[0] / 2.0, dims[2] / 2.0
    k2 = (r / (2 * hz)) ** 2
    w = hz - o[..., 2]
    a = d[..., 0] ** 2 + d[..., 1] ** 2 - k2 * d[..., 2] ** 2
    b = 2.0 * (o[..., 0] * d[..., 0] + o[..., 1] * d[..., 1]) + 2.0 * k2 * w * d[..., 2]
    c = o[..., 0] ** 2 + o[..., 1] ** 2 - k2 * w * w
    disc = b * b - 4 * a * c
    sq = np.sqrt(np.maximum(disc, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        sides = [(-b - sq) / (2 * a), (-b + sq) / (2 * a)]
    cands = []
    for t in sides:
        z = o[..., 2] + t * d[..., 2]
        ok = (disc >= 0) & (np.abs(a) > 1e-12) & (z >= -hz) & (z <= hz)
        cands.append(np.where(ok, t, INF))
    cands += _caps(o, d, [-hz], [r])
    return _smallest_positive(*cands)


HITTERS = {"box": hit_box, "sphere": hit_sphere, "cylinder": hit_cylinder, "cone": hit_cone}


@dataclass
class RaycastResult:
    index: np.ndarray  # (H, W) uint16
    depth: np.ndarray  # (H, W) float32, planar, 0.0 = no hit
    amodal: dict[int, np.ndarray]  # pass_index -> (H, W) bool
    colour: np.ndarray  # (H, W, 3) linear rgb of the nearest hit (background grey)


def raycast(scene: SceneSpec, camera_id: str) -> RaycastResult:
    cam: CameraSpec = scene.camera(camera_id)
    origin, dirs = pixel_rays(cam)
    width, height = cam.resolution

    prims: list[tuple[int, str, tuple, Pose, tuple]] = []
    t = scene.table
    table_pose = Pose(position=(0.0, 0.0, t.height - t.thickness / 2))
    prims.append((t.pass_index, "box", (*t.size_xy, t.thickness), table_pose, (0.35, 0.2, 0.1)))
    for part in scene.gripper.parts:
        prims.append((scene.gripper.pass_index, part.shape, part.dimensions, part.pose,
                      (0.3, 0.3, 0.32)))  # fmt: skip
    for obj in scene.objects:
        if obj.source != "primitive":
            raise ValueError(f"raycast handles primitives only, not {obj.shape}")
        prims.append((obj.pass_index, obj.shape, obj.dimensions, obj.pose, obj.rgb))

    best_t = np.full((height, width), INF)
    best_idx = np.zeros((height, width), dtype=np.uint16)
    colour = np.full((height, width, 3), 0.18)
    amodal: dict[int, np.ndarray] = {}
    for pass_index, shape, dims, pose, rgb in prims:
        o, d = _local_rays(pose, origin, dirs)
        o = np.broadcast_to(o, d.shape)
        t_hit = HITTERS[shape](dims, o, d)
        if pass_index <= 999:
            amodal[pass_index] = np.isfinite(t_hit)
        closer = t_hit < best_t
        best_t = np.where(closer, t_hit, best_t)
        best_idx = np.where(closer, pass_index, best_idx).astype(np.uint16)
        colour = np.where(closer[..., None], np.asarray(rgb), colour)

    depth = np.where(np.isfinite(best_t), best_t, 0.0).astype(np.float32)
    return RaycastResult(index=best_idx, depth=depth, amodal=amodal, colour=colour)
