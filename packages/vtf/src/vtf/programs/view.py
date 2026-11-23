"""One camera's exact view of a settled scene: ray-cast index and amodal masks, visibility,
projections and the scale-in-frame rule.

The ray cast is `vtf.core.raycast.raycast`. Until it handles asset meshes, a fallback here casts
the same rays: the analytic primitives of vtf.core.raycast for the table, the gripper and
primitive objects, and trimesh's ray intersector for asset meshes. Results are cached per scene
content hash and camera.
"""

from dataclasses import dataclass

import numpy as np
import trimesh

from vtf.core.geometry import Pose
from vtf.core.hashing import sha256_json
from vtf.core.projection import pixel_rays, project, to_camera
from vtf.core.raycast import HITTERS, _local_rays, raycast
from vtf.core.scene import CameraSpec, SceneSpec
from vtf.programs.geometry import _LRU, ObjGeom, convex_hull, scene_geom

# G3 thresholds (spec/families.md): evidence objects, and counted objects in count families.
EVIDENCE_MIN_FRACTION = 0.4
EVIDENCE_MIN_PX = 300
COUNTED_MIN_FRACTION = 0.15
COUNTED_MIN_PX = 120
GRID_MIN_TABLE_SHARE = 0.05  # the grid counts as in view when the table fills 5% of the image
FRAME_MARGIN_PX = 16


@dataclass
class View:
    camera: CameraSpec
    index: np.ndarray  # (H, W) uint16
    amodal: dict[int, np.ndarray]  # pass_index -> (H, W) bool
    visible_px: dict[str, int]
    amodal_px: dict[str, int]
    table_px: int
    raycaster: str

    @property
    def size(self) -> tuple[int, int]:
        return self.camera.resolution

    def fraction(self, object_id: str) -> float:
        a = self.amodal_px.get(object_id, 0)
        return self.visible_px.get(object_id, 0) / a if a else 0.0

    def visible(self, object_id: str, counted: bool = False) -> bool:
        min_f, min_px = (
            (COUNTED_MIN_FRACTION, COUNTED_MIN_PX)
            if counted
            else (EVIDENCE_MIN_FRACTION, EVIDENCE_MIN_PX)
        )
        return self.fraction(object_id) >= min_f and self.visible_px.get(object_id, 0) >= min_px

    def visibility_row(self, object_id: str) -> dict:
        return {
            "object_id": object_id,
            "visible_px": self.visible_px.get(object_id, 0),
            "amodal_px": self.amodal_px.get(object_id, 0),
            "visible_fraction": round(self.fraction(object_id), 4),
        }


def _intersector(mesh: trimesh.Trimesh):
    try:
        from trimesh.ray.ray_pyembree import RayMeshIntersector
    except ImportError:  # pragma: no cover - embreex is a core dependency
        from trimesh.ray.ray_triangle import RayMeshIntersector
    return RayMeshIntersector(mesh)


def hidden_evidence(view: View, evidence: list[str], family: str, params: dict) -> list[dict]:
    """Evidence objects that fail the G3 thresholds in this view (counted objects of count
    families use rule A's lower thresholds; a named container uses the evidence thresholds)."""
    counted = family in ("count", "count_attribute")
    container = params.get("container")
    return [view.visibility_row(i) for i in evidence
            if not view.visible(i, counted=counted and i != container)]  # fmt: skip


def _mesh_raycast(scene: SceneSpec, camera_id: str):
    """index, amodal for scenes with asset meshes (fallback until vtf.core.raycast has meshes)."""
    cam = scene.camera(camera_id)
    origin, dirs = pixel_rays(cam)
    height, width = dirs.shape[:2]
    best_t = np.full((height, width), np.inf)
    best_idx = np.zeros((height, width), dtype=np.uint16)
    amodal: dict[int, np.ndarray] = {}

    def compose(pass_index: int, t_hit: np.ndarray) -> None:
        nonlocal best_t, best_idx
        if pass_index <= 999:
            amodal[pass_index] = np.isfinite(t_hit)
        closer = t_hit < best_t
        best_t = np.where(closer, t_hit, best_t)
        best_idx = np.where(closer, pass_index, best_idx).astype(np.uint16)

    def analytic(shape, dims, pose: Pose) -> np.ndarray:
        o, d = _local_rays(pose, origin, dirs)
        return HITTERS[shape](dims, np.broadcast_to(o, d.shape), d)

    t = scene.table
    table_pose = Pose(position=(0.0, 0.0, t.height - t.thickness / 2))
    compose(t.pass_index, analytic("box", (*t.size_xy, t.thickness), table_pose))
    for part in scene.gripper.parts:
        compose(scene.gripper.pass_index, analytic(part.shape, part.dimensions, part.pose))
    sg = scene_geom(scene)
    for obj in sg.objects:
        if obj.source == "primitive":
            compose(obj.pass_index, analytic(obj.shape, obj.dimensions, obj.pose))
            continue
        g = sg.geom[obj.id]
        t_hit = np.full((height, width), np.inf)
        uv, z = project(cam, np.asarray(g.mesh.vertices))
        if (z > cam.clip_start).all():
            u0, v0 = np.floor(uv.min(axis=0)).astype(int) - 1
            u1, v1 = np.ceil(uv.max(axis=0)).astype(int) + 1
            u0, v0 = max(u0, 0), max(v0, 0)
            u1, v1 = min(u1, width), min(v1, height)
            if u1 > u0 and v1 > v0:
                sub = dirs[v0:v1, u0:u1].reshape(-1, 3)
                locs, idx_ray, _ = _intersector(g.mesh).intersects_location(
                    np.repeat(origin[None], len(sub), axis=0), sub, multiple_hits=False
                )
                ts = np.full(len(sub), np.inf)
                if len(locs):
                    tt = ((locs - origin) * sub[idx_ray]).sum(-1) / (sub[idx_ray] ** 2).sum(-1)
                    np.minimum.at(ts, idx_ray, np.where(tt > 1e-9, tt, np.inf))
                t_hit[v0:v1, u0:u1] = ts.reshape(v1 - v0, u1 - u0)
        compose(obj.pass_index, t_hit)
    return best_idx, amodal


def _build_view(scene: SceneSpec, camera_id: str) -> View:
    try:
        rc = raycast(scene, camera_id)
        index, amodal, how = rc.index, rc.amodal, "vtf.core.raycast"
    except ValueError:  # assets: vtf.core.raycast handles primitives only for now
        index, amodal = _mesh_raycast(scene, camera_id)
        how = "vtf.programs.view mesh fallback"
    by_pass = {o.pass_index: o.id for o in scene.objects}
    counts = np.bincount(index.ravel(), minlength=1002)
    visible = {oid: int(counts[p]) for p, oid in by_pass.items()}
    amodal_px = {by_pass[p]: int(m.sum()) for p, m in amodal.items() if p in by_pass}
    return View(
        camera=scene.camera(camera_id),
        index=index,
        amodal=amodal,
        visible_px=visible,
        amodal_px=amodal_px,
        table_px=int(counts[scene.table.pass_index]),
        raycaster=how,
    )


_VIEWS = _LRU(12)


def camera_view(scene: SceneSpec, camera_id: str) -> View:
    scene.camera(camera_id)
    return _VIEWS.get_or((sha256_json(scene), camera_id), lambda: _build_view(scene, camera_id))


# ---------------------------------------------------------------------------------------------
# projection helpers


def image_point(cam: CameraSpec, point) -> np.ndarray:
    uv, _ = project(cam, np.asarray(point, dtype=float).reshape(1, 3))
    return uv[0]


def image_footprint(cam: CameraSpec, g: ObjGeom) -> np.ndarray:
    """Convex hull (pixels) of the object's projected mesh vertices."""
    uv, z = project(cam, np.asarray(g.mesh.vertices))
    return convex_hull(uv[z > cam.clip_start])


def in_frame(cam: CameraSpec, g: ObjGeom, margin_px: float = FRAME_MARGIN_PX) -> bool:
    uv, z = project(cam, np.asarray(g.mesh.vertices))
    w, h = cam.resolution
    return bool(
        (z > cam.clip_start).all()
        and (uv[:, 0] >= margin_px).all()
        and (uv[:, 0] <= w - margin_px).all()
        and (uv[:, 1] >= margin_px).all()
        and (uv[:, 1] <= h - margin_px).all()
    )


def camera_right_xy(cam: CameraSpec) -> np.ndarray:
    """The camera's image-right direction projected onto the table plane (unit, world xy)."""
    right = cam.pose.rotation_matrix()[:, 0]
    v = np.array([right[0], right[1]])
    n = np.linalg.norm(v)
    if n < 1e-9:
        raise ValueError(f"camera {cam.id}: image right is vertical")
    return v / n


def planar_depth(cam: CameraSpec, point) -> float:
    return float(to_camera(cam, np.asarray(point, dtype=float).reshape(1, 3))[0, 2])


def scale_reference(scene: SceneSpec, view: View) -> dict | None:
    """Rule A, scale in frame: a printed grid on a table in view, or a visible proc_* reference."""
    w, h = view.size
    if scene.table.grid_cell_m and view.table_px >= GRID_MIN_TABLE_SHARE * w * h:
        return {"kind": "grid", "grid_cell_m": scene.table.grid_cell_m, "table_px": view.table_px}
    for obj in sorted(scene.objects, key=lambda o: o.id):
        if obj.source == "asset" and obj.shape.startswith("proc_") and view.visible(obj.id):
            return {"kind": "reference", "object_id": obj.id, "asset": obj.shape,
                    **view.visibility_row(obj.id)}  # fmt: skip
    return None
