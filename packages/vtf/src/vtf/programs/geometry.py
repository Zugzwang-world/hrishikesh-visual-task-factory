"""Exact scene geometry for the programs: object meshes, footprints, support and containment.

Every object's world mesh comes from `vtf.core.assets.object_mesh` (primitives and assets alike).
Primitives keep their exact analytic lowest point, top and point distance (`vtf.programs.shapes`);
assets use their mesh. Footprints are convex hulls of the mesh vertices projected onto the table
plane (spec/families.md). Results are cached per scene content hash, so programs stay pure.
"""

import math
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import trimesh

from vtf.core.assets import AssetEntry, ContainerInfo, load_asset_manifest, object_mesh
from vtf.core.hashing import sha256_json
from vtf.core.scene import SceneObject, SceneSpec
from vtf.core.vocab import unique_name
from vtf.programs import shapes

RESTING_TOL_M = 0.01  # lowest point within 1 cm of the support's top surface
CONTAINER_SHRINK_M = 0.005  # interior footprint shrunk by 5 mm
CONTAINER_FLOOR_TOL_M = 0.01  # lowest point at most 1 cm below the interior floor
UP = np.array([0.0, 0.0, 1.0])


# ---------------------------------------------------------------------------------------------
# 2D convex polygons (CCW vertex arrays)


def convex_hull(points: np.ndarray) -> np.ndarray:
    """Counter-clockwise convex hull of (N, 2) points (Andrew's monotone chain)."""
    pts = np.unique(np.round(np.asarray(points, dtype=float).reshape(-1, 2), 9), axis=0)
    if len(pts) <= 2:
        return pts

    def half(seq):
        out: list = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def _cross(o, a, b) -> float:
    return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))


def _edges(poly: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def point_segment_distance(p, a, b) -> float:
    p, a, b = (np.asarray(v, dtype=float) for v in (p, a, b))
    ab = b - a
    denom = float(ab @ ab)
    t = 0.0 if denom == 0.0 else min(1.0, max(0.0, float((p - a) @ ab) / denom))
    return float(np.linalg.norm(p - (a + t * ab)))


def point_in_polygon(p, poly: np.ndarray) -> bool:
    if len(poly) < 3:
        return False
    return all(_cross(a, b, p) >= -1e-12 for a, b in _edges(poly))


def point_polygon_signed_distance(p, poly: np.ndarray) -> float:
    """Distance from p to the polygon boundary: positive inside, negative outside."""
    d = min(point_segment_distance(p, a, b) for a, b in _edges(poly))
    return d if point_in_polygon(p, poly) else -d


def _axes(poly: np.ndarray) -> list[np.ndarray]:
    out = []
    for a, b in _edges(poly):
        e = b - a
        n = np.array([-e[1], e[0]])
        norm = np.linalg.norm(n)
        if norm > 0:
            out.append(n / norm)
    return out


def penetration_depth(pa: np.ndarray, pb: np.ndarray) -> float:
    """Minimum translation that separates two convex polygons (0 when they do not overlap)."""
    depth = math.inf
    for axis in _axes(pa) + _axes(pb):
        a, b = pa @ axis, pb @ axis
        overlap = min(a.max(), b.max()) - max(a.min(), b.min())
        if overlap <= 0:
            return 0.0
        depth = min(depth, float(overlap))
    return 0.0 if depth is math.inf else depth


def polygon_distance(pa: np.ndarray, pb: np.ndarray) -> float:
    """Minimum distance between two convex polygons (0 when they overlap)."""
    if penetration_depth(pa, pb) > 0:
        return 0.0
    d = min(point_segment_distance(p, a, b) for p in pa for a, b in _edges(pb))
    return min(d, min(point_segment_distance(p, a, b) for p in pb for a, b in _edges(pa)))


def closest_points(pa: np.ndarray, pb: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Closest pair of points (on A, on B) between two disjoint convex polygons, and their gap."""
    best = (math.inf, pa[0], pb[0])
    for src, dst, flip in ((pa, pb, False), (pb, pa, True)):
        for p in src:
            for a, b in _edges(dst):
                ab = b - a
                denom = float(ab @ ab)
                t = 0.0 if denom == 0 else min(1.0, max(0.0, float((p - a) @ ab) / denom))
                q = a + t * ab
                d = float(np.linalg.norm(p - q))
                if d < best[0]:
                    best = (d, q, p) if flip else (d, p, q)
    return np.asarray(best[1]), np.asarray(best[2]), best[0]


def diameter(poly: np.ndarray) -> float:
    """Largest distance between two points of the polygon."""
    if len(poly) < 2:
        return 0.0
    diff = poly[:, None, :] - poly[None, :, :]
    return float(np.sqrt((diff**2).sum(-1)).max())


def min_width(poly: np.ndarray) -> float:
    """Smallest caliper width of a convex polygon."""
    if len(poly) < 3:
        return 0.0
    best = math.inf
    for a, b in _edges(poly):
        e = b - a
        n = np.array([-e[1], e[0]]) / max(np.linalg.norm(e), 1e-12)
        best = min(best, float(np.abs((poly - a) @ n).max()))
    return best


def rect_polygon(center, size, yaw_deg: float = 0.0) -> np.ndarray:
    hx, hy = size[0] / 2.0, size[1] / 2.0
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    corners = np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]])
    rot = np.array([[c, -s], [s, c]])
    return corners @ rot.T + np.asarray(center, dtype=float)


# ---------------------------------------------------------------------------------------------
# per-object geometry


@dataclass
class ObjGeom:
    obj: SceneObject
    mesh: trimesh.Trimesh
    centre: np.ndarray
    lowest_z: float
    top_z: float
    hull: np.ndarray  # footprint, CCW (K, 2)
    entry: AssetEntry | None = None
    _ray: Any = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.obj.id

    @property
    def xy(self) -> np.ndarray:
        return self.centre[:2]

    @property
    def height(self) -> float:
        return self.top_z - self.lowest_z

    @property
    def container(self) -> ContainerInfo | None:
        return self.entry.container if self.entry else None

    def surface_distance(self, point) -> float:
        """Distance from a world point to the nearest surface (0 inside a primitive)."""
        if self.obj.source == "primitive":
            return shapes.point_distance(self.obj, point)
        _, dist, _ = trimesh.proximity.closest_point(self.mesh, [list(point)])
        return float(dist[0])

    def closest_point(self, point) -> np.ndarray:
        if self.obj.source == "primitive" and self.surface_distance(point) == 0.0:
            return np.asarray(point, dtype=float)
        pts, _, _ = trimesh.proximity.closest_point(self.mesh, [list(point)])
        return pts[0]

    def surface_below(self, x: float, y: float, z: float) -> float | None:
        """Highest point of this mesh straight below (x, y, z), or None."""
        if self._ray is None:
            self._ray = trimesh.ray.ray_triangle.RayMeshIntersector(self.mesh)
        locs, _, _ = self._ray.intersects_location([[x, y, z + 1e-6]], [[0.0, 0.0, -1.0]])
        zs = [float(p[2]) for p in locs if p[2] <= z + 1e-6]
        return max(zs) if zs else None


def _entry(obj: SceneObject) -> AssetEntry | None:
    if obj.source != "asset":
        return None
    return load_asset_manifest().get(obj.shape)


def build_geom(obj: SceneObject) -> ObjGeom:
    mesh = object_mesh(obj)
    verts = np.asarray(mesh.vertices)
    if obj.source == "primitive":
        lowest = shapes.lowest_z(obj)
        up_local = obj.pose.rotation_matrix().T @ UP
        top = float(obj.pose.position[2]) + shapes.support(shapes.solid_kind(obj),
                                                           obj.dimensions, up_local)
    else:
        lowest, top = float(verts[:, 2].min()), float(verts[:, 2].max())
    return ObjGeom(
        obj=obj,
        mesh=mesh,
        centre=np.asarray(obj.pose.position, dtype=float),
        lowest_z=lowest,
        top_z=top,
        hull=convex_hull(verts[:, :2]),
        entry=_entry(obj),
    )


class _LRU(OrderedDict):
    def __init__(self, size: int) -> None:
        super().__init__()
        self.size = size

    def get_or(self, key, make: Callable[[], Any]):
        if key in self:
            self.move_to_end(key)
            return self[key]
        value = make()
        self[key] = value
        while len(self) > self.size:
            self.popitem(last=False)
        return value


_GEOMS = _LRU(2048)
_SCENES = _LRU(64)


def obj_geom(obj: SceneObject) -> ObjGeom:
    return _GEOMS.get_or(obj.model_dump_json(), lambda: build_geom(obj))


# ---------------------------------------------------------------------------------------------
# scene-level relations


@dataclass
class Support:
    """What an object rests on: `support_id` (an object id or 'table'), or None when unclear."""

    object_id: str
    support_id: str | None
    surface_z: float | None
    gap_m: float | None
    candidates: list[dict]
    reason: str = ""


@dataclass
class Inside:
    object_id: str
    container_id: str
    inside: bool
    boundary_m: float  # centre's distance to the shrunk interior boundary (+ inside, - outside)
    rel_low_m: float  # lowest point above the container's lowest point
    xy_inside: bool
    z_inside: bool


class SceneGeom:
    """Geometry of one settled scene: object geometry, unique names, support and containment."""

    def __init__(self, scene: SceneSpec) -> None:
        self.scene = scene
        self.objects = sorted(scene.objects, key=lambda o: o.id)
        self.geom = {o.id: obj_geom(o) for o in self.objects}
        self.names = {o.id: unique_name(o, self.objects) for o in self.objects}
        sx, sy = scene.table.size_xy
        self.table_hull = rect_polygon((0.0, 0.0), (sx, sy))
        self._support: dict[str, Support] = {}
        self._inside: dict[tuple[str, str], Inside] = {}

    def name(self, object_id: str) -> str | None:
        return self.names[object_id]

    def containers(self) -> list[str]:
        return [o.id for o in self.objects if self.geom[o.id].container is not None]

    def on_table_footprint(self, object_id: str) -> bool:
        return point_in_polygon(self.geom[object_id].xy, self.table_hull)

    def support(self, object_id: str) -> Support:
        if object_id not in self._support:
            self._support[object_id] = self._compute_support(object_id)
        return self._support[object_id]

    def _compute_support(self, object_id: str) -> Support:
        g = self.geom[object_id]
        x, y, z = (float(v) for v in g.centre)
        cands: list[dict] = []
        if self.on_table_footprint(object_id):
            cands.append({"support_id": "table", "surface_z": float(self.scene.table.height)})
        for other in self.objects:
            if other.id == object_id:
                continue
            og = self.geom[other.id]
            if not point_in_polygon((x, y), og.hull) or og.lowest_z > z:
                continue
            sz = og.surface_below(x, y, z)
            if sz is not None:
                cands.append({"support_id": other.id, "surface_z": sz})
        for c in cands:
            c["gap_m"] = g.lowest_z - c["surface_z"]
            c["resting"] = abs(round(c["gap_m"], 9)) <= RESTING_TOL_M
        if not cands:
            return Support(object_id, None, None, None, cands, "nothing_below")
        cands.sort(key=lambda c: (-c["surface_z"], c["support_id"]))
        top = cands[0]
        if not top["resting"]:
            return Support(object_id, None, top["surface_z"], top["gap_m"], cands, "not_resting")
        rivals = [c for c in cands[1:] if c["resting"] and top["surface_z"] - c["surface_z"] < 1e-3]
        if rivals:
            return Support(object_id, None, top["surface_z"], top["gap_m"], cands, "two_supports")
        return Support(object_id, top["support_id"], top["surface_z"], top["gap_m"], cands)

    def supported_by(self, support_id: str) -> list[str]:
        """Objects resting directly on this object."""
        return [o.id for o in self.objects if self.support(o.id).support_id == support_id]

    def inside(self, object_id: str, container_id: str) -> Inside:
        key = (object_id, container_id)
        if key not in self._inside:
            self._inside[key] = self._compute_inside(object_id, container_id)
        return self._inside[key]

    def _compute_inside(self, object_id: str, container_id: str) -> Inside:
        g, cg = self.geom[object_id], self.geom[container_id]
        info = cg.container
        if info is None:
            raise ValueError(f"{container_id} is not a container")
        s = cg.obj.scale
        local = cg.obj.pose.rotation_matrix().T @ (g.centre - cg.centre)
        if info.shape == "circle":
            boundary = (info.inner_radius_m or 0.0) * s - CONTAINER_SHRINK_M - math.hypot(
                local[0], local[1]
            )
        else:
            w, h = info.inner_size_xy or (0.0, 0.0)
            hx, hy = w * s / 2 - CONTAINER_SHRINK_M, h * s / 2 - CONTAINER_SHRINK_M
            dx, dy = abs(local[0]) - hx, abs(local[1]) - hy
            if dx <= 0 and dy <= 0:
                boundary = min(-dx, -dy)
            else:
                boundary = -math.hypot(max(dx, 0.0), max(dy, 0.0))
        rel_low = g.lowest_z - cg.lowest_z
        z_in = info.floor_z_m * s - CONTAINER_FLOOR_TOL_M <= rel_low <= info.rim_z_m * s
        xy_in = boundary >= 0
        return Inside(object_id, container_id, xy_in and z_in, float(boundary), float(rel_low),
                      xy_in, z_in)  # fmt: skip

    def contents(self, container_id: str) -> list[str]:
        return [
            o.id
            for o in self.objects
            if o.id != container_id and self.inside(o.id, container_id).inside
        ]

    def container_of(self, object_id: str) -> str | None:
        for cid in self.containers():
            if cid != object_id and self.inside(object_id, cid).inside:
                return cid
        return None

    def placement_kind(self, object_id: str) -> str:
        """table, stacked, contained or unclear (for difficulty and proposals)."""
        if self.container_of(object_id):
            return "contained"
        sup = self.support(object_id).support_id
        if sup == "table":
            return "table"
        return "stacked" if sup else "unclear"


def scene_geom(scene: SceneSpec) -> SceneGeom:
    return _SCENES.get_or(sha256_json(scene), lambda: SceneGeom(scene))
