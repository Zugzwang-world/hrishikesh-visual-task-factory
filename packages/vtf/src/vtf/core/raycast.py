"""Ray caster for scene files: exact analytic primitives plus asset meshes, in numpy.

Renders object-index, planar depth and amodal masks for a SceneSpec without Blender. Used to make
the contract fixtures, as the independent second route for G2, and as validate's render-agreement
check (camera conventions, poses). It is not the renderer.

- Table, gripper parts, primitive objects and procedural assets (`proc_*`, which are exact
  primitives) are hit analytically, as before.
- Poly Haven assets are hit on their mesh (vtf.core.assets.object_local_mesh) with trimesh's
  Embree intersector, in the object's local frame (rotation only, so ray parameters are
  unchanged). Embree finds the first triangle; the hit distance is then computed in float64
  from that triangle's plane.
- Each object is only traced over the pixels inside the projection of its bounding box (when
  every box corner is in front of the camera), which keeps 25 objects at 1024x768 fast.
"""

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from vtf.core.geometry import Pose, transform_points
from vtf.core.projection import pixel_rays, project
from vtf.core.scene import MAX_OBJECT_PASS_INDEX, CameraSpec, SceneSpec

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


@lru_cache(maxsize=128)
def _mesh_intersector(cache_dir: str, file: str, scale: float):
    """Embree intersector on an asset's centred, Z-up local mesh (built once per asset size)."""
    from trimesh.ray.ray_pyembree import RayMeshIntersector

    from vtf.core.assets import _asset_local_mesh

    return RayMeshIntersector(_asset_local_mesh(cache_dir, file, scale))


def hit_mesh(intersector, o: np.ndarray, d: np.ndarray) -> np.ndarray:
    """First-hit ray parameter (in units of d) on a mesh, INF on a miss. o (3,), d (N, 3)."""
    n = len(d)
    t = np.full(n, INF)
    if n == 0:
        return t
    origins = np.broadcast_to(o, d.shape)
    tri, ray = intersector.intersects_id(origins, d, multiple_hits=False)
    if len(ray):
        mesh = intersector.mesh
        p0 = mesh.triangles[tri, 0, :]
        normal = mesh.face_normals[tri]
        with np.errstate(divide="ignore", invalid="ignore"):
            hit_t = ((p0 - origins[ray]) * normal).sum(-1) / (d[ray] * normal).sum(-1)
        t[ray] = np.where(np.isfinite(hit_t) & (hit_t > 1e-9), hit_t, INF)
    return t


@dataclass
class RaycastResult:
    index: np.ndarray  # (H, W) uint16
    depth: np.ndarray  # (H, W) float32, planar, 0.0 = no hit
    amodal: dict[int, np.ndarray]  # pass_index -> (H, W) bool
    colour: np.ndarray  # (H, W, 3) linear rgb of the nearest hit (background grey)


@dataclass
class _Target:
    pass_index: int
    pose: Pose
    dims: tuple
    rgb: tuple
    shape: str | None = None  # analytic primitive
    mesh_key: tuple | None = None  # (cache_dir, file, scale) for an asset mesh


def _targets(scene: SceneSpec, manifest=None) -> list[_Target]:
    out: list[_Target] = []
    t = scene.table
    table_pose = Pose(position=(0.0, 0.0, t.height - t.thickness / 2))
    out.append(_Target(t.pass_index, table_pose, (*t.size_xy, t.thickness), (0.35, 0.2, 0.1),
                       shape="box"))  # fmt: skip
    for part in scene.gripper.parts:
        out.append(_Target(scene.gripper.pass_index, part.pose, part.dimensions,
                           (0.3, 0.3, 0.32), shape=part.shape))  # fmt: skip
    for obj in scene.objects:
        if obj.source == "primitive":
            out.append(_Target(obj.pass_index, obj.pose, obj.dimensions, obj.rgb, shape=obj.shape))
            continue
        from vtf.core.assets import asset_cache_dir, load_asset_manifest

        manifest = manifest or load_asset_manifest()
        entry = manifest.get(obj.shape)
        if entry.source == "procedural":
            out.append(_Target(obj.pass_index, obj.pose, obj.dimensions, obj.rgb,
                               shape=entry.shape or "box"))  # fmt: skip
            continue
        if not entry.file:
            raise ValueError(f"asset {entry.id} has no file")
        key = (str(asset_cache_dir(manifest)), entry.file, round(entry.scale * obj.scale, 9))
        out.append(_Target(obj.pass_index, obj.pose, obj.dimensions, obj.rgb, mesh_key=key))
    return out


def _pixel_window(cam: CameraSpec, target: _Target) -> tuple[slice, slice] | None:
    """Rows and columns that can see the target's bounding box; None if none can."""
    width, height = cam.resolution
    half = np.asarray(target.dims, dtype=float) / 2
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    corners = transform_points(target.pose.matrix(), signs * half)
    uv, depth = project(cam, corners)
    if not np.all(depth > 1e-6) or not np.all(np.isfinite(uv)):
        return slice(0, height), slice(0, width)  # straddles the camera plane: trace everything
    u0 = max(math.floor(uv[:, 0].min() - 0.5) - 1, 0)
    u1 = min(math.ceil(uv[:, 0].max() - 0.5) + 2, width)
    v0 = max(math.floor(uv[:, 1].min() - 0.5) - 1, 0)
    v1 = min(math.ceil(uv[:, 1].max() - 0.5) + 2, height)
    if u0 >= u1 or v0 >= v1:
        return None
    return slice(v0, v1), slice(u0, u1)


def raycast(scene: SceneSpec, camera_id: str, manifest=None) -> RaycastResult:
    """Index, planar depth and amodal masks of a scene in one of its cameras.

    `manifest` (vtf.core.assets.AssetManifest) is loaded on demand when the scene has assets.
    """
    cam: CameraSpec = scene.camera(camera_id)
    origin, dirs = pixel_rays(cam)
    width, height = cam.resolution

    best_t = np.full((height, width), INF)
    best_idx = np.zeros((height, width), dtype=np.uint16)
    colour = np.full((height, width, 3), 0.18)
    amodal: dict[int, np.ndarray] = {}
    for target in _targets(scene, manifest):
        window = _pixel_window(cam, target)
        if target.pass_index <= MAX_OBJECT_PASS_INDEX:
            amodal[target.pass_index] = np.zeros((height, width), dtype=bool)
        if window is None:
            continue
        rows, cols = window
        sub = dirs[rows, cols]
        o, d = _local_rays(target.pose, origin, sub)
        if target.shape is not None:
            t_hit = HITTERS[target.shape](target.dims, np.broadcast_to(o, d.shape), d)
        else:
            intersector = _mesh_intersector(*target.mesh_key)  # type: ignore[misc]
            t_hit = hit_mesh(intersector, o, d.reshape(-1, 3)).reshape(d.shape[:2])
        if target.pass_index <= MAX_OBJECT_PASS_INDEX:
            amodal[target.pass_index][rows, cols] = np.isfinite(t_hit)
        bt = best_t[rows, cols]
        closer = t_hit < bt
        best_t[rows, cols] = np.where(closer, t_hit, bt)
        best_idx[rows, cols] = np.where(closer, target.pass_index, best_idx[rows, cols])
        colour[rows, cols] = np.where(closer[..., None], np.asarray(target.rgb), colour[rows, cols])

    depth = np.where(np.isfinite(best_t), best_t, 0.0).astype(np.float32)
    return RaycastResult(index=best_idx, depth=depth, amodal=amodal, colour=colour)
