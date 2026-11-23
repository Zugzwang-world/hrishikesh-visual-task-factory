"""G2 Second computation: recompute the answer from the rendered index and depth passes.

Each family has an independent pixel route (spec/families.md, "G2"). Visible pixels of each
object come from the index pass and are back-projected to world points with the planar depth and
the manifest's K and world_to_camera (`vtf.validate.pixels`). The scene file is read only for the
question's parameters, the gripper tip, zone geometry, container interiors, and object
attributes (category, colour) looked up by pass index; never for the poses of evidence objects.
The recomputed answer must equal G1's.

G2 also keeps the render-versus-scene check: per-object IoU between the index pass and an exact
ray cast of the settled scene (`render_vs_scene`), warped like the render when the camera has
lens distortion. Evidence objects must reach MIN_RAYCAST_IOU wherever the ray cast ran.
"""

import inspect
import math
from typing import Any

import numpy as np

from vtf.core.program import CompiledQuestion
from vtf.core.projection import intrinsics, world_to_camera
from vtf.core.scene import TABLE_PASS_INDEX, SceneObject, SceneSpec, Zone
from vtf.core.task import GateResult
from vtf.validate.names import TABLE, name_of, resolve, resolve_param
from vtf.validate.pixels import CameraPasses

MIN_OBJECT_PIXELS = 20  # an object the pixel route can see at all
COUNT_MIN_PIXELS = 120  # count families (spec/families.md)
MIN_RAYCAST_IOU = 0.9
CAMERA_TOLERANCE = 1e-3
RELATION_CONE_DEG = 30.0
SUPPORT_RING_PX = 5
SUPPORT_XY_REACH_M = 0.03
SUPPORT_ABOVE_LOW_M = 0.01
SUPPORT_MIN_PIXELS = 10
CONTAINER_SHRINK_M = 0.005
CONTAINER_FLOOR_TOL_M = 0.01
CONTAINER_RIM_TOL_M = 0.02
OCCLUSION_MIN_SHARE = 0.10
OCCLUSION_MIN_VISIBLE = 0.30
FREE_SPACE_YES_M = 0.02
FREE_SPACE_NO_M = 0.01
CORRIDOR_MIN_PIXELS = 10
GRIPPER_DISC_PX = 15.0


class Undecided(Exception):  # noqa: N818 - a gate outcome
    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _r(v: float, nd: int = 4) -> float:
    return round(float(v), nd) + 0.0


class PixelRoute:
    """The data one question's pixel route may use."""

    def __init__(self, scene: SceneSpec, passes: CameraPasses) -> None:
        self.scene = scene
        self.passes = passes
        self.by_index = scene.object_by_pass_index()
        self.index_of = {o.id: o.pass_index for o in scene.objects}

    def obj(self, pass_index: int) -> SceneObject:
        return self.by_index[pass_index]

    def oid(self, pass_index: int) -> str:
        return TABLE if pass_index == TABLE_PASS_INDEX else self.by_index[pass_index].id

    def param_object(self, params: dict, key: str) -> SceneObject:
        oid = resolve_param(self.scene, params.get(key))
        if oid is None or oid == TABLE:
            raise Undecided("param_not_an_object", param=key, value=params.get(key))
        return self.scene.object(oid)

    def visible(self, min_pixels: int = MIN_OBJECT_PIXELS) -> list[int]:
        return self.passes.object_indices(min_pixels)

    def points(self, pass_index: int) -> np.ndarray:
        return self.passes.points(pass_index)

    def median(self, pass_index: int) -> np.ndarray:
        pts = self.points(pass_index)
        if len(pts) < MIN_OBJECT_PIXELS:
            raise Undecided("object_not_visible", object_id=self.oid(pass_index), pixels=len(pts))
        return np.median(pts, axis=0)


# ---------------------------------------------------------------------------------------------
# geometry helpers that may use the scene file (containers, zones)


def container_info(obj: SceneObject):
    """ContainerInfo of an asset (scaled by the object's scale), or None."""
    if obj.source != "asset":
        return None
    try:
        from vtf.core.assets import load_asset_manifest

        info = load_asset_manifest().get(obj.shape).container
    except (KeyError, FileNotFoundError, ValueError):
        return None
    if info is None or obj.scale == 1.0:
        return info
    s = obj.scale
    return info.model_copy(
        update={
            "inner_radius_m": info.inner_radius_m * s if info.inner_radius_m else None,
            "inner_size_xy": tuple(v * s for v in info.inner_size_xy)
            if info.inner_size_xy
            else None,
            "floor_z_m": info.floor_z_m * s,
            "rim_z_m": info.rim_z_m * s,
        }
    )


def inside_container(points: np.ndarray, container: SceneObject, info) -> tuple[bool, dict]:
    """Inside rule on back-projected points: median xy within the interior footprint (shrunk by
    5 mm), median z above the floor (1 cm slack), lowest visible point below the rim (2 cm)."""
    rot = container.pose.rotation_matrix()
    local = (points - np.asarray(container.pose.position)) @ rot
    med = np.median(local, axis=0)
    bottom = -container.dimensions[2] / 2.0
    floor, rim = bottom + info.floor_z_m, bottom + info.rim_z_m
    if info.shape == "circle":
        edge = info.inner_radius_m - CONTAINER_SHRINK_M - math.hypot(med[0], med[1])
    else:
        sx, sy = info.inner_size_xy
        edge = min(
            sx / 2 - CONTAINER_SHRINK_M - abs(med[0]), sy / 2 - CONTAINER_SHRINK_M - abs(med[1])
        )
    low = float(np.percentile(local[:, 2], 2))
    z_ok = med[2] >= floor - CONTAINER_FLOOR_TOL_M and low <= rim + CONTAINER_RIM_TOL_M
    inside = bool(edge >= 0 and z_ok)
    return inside, {"median_local": [_r(v) for v in med], "edge_m": _r(edge), "z_ok": bool(z_ok)}


def in_zone(xy: np.ndarray, zone: Zone) -> tuple[bool, float]:
    """Inside test on the table plane; also the signed distance to the boundary (+ inside)."""
    dx, dy = float(xy[0]) - zone.center_xy[0], float(xy[1]) - zone.center_xy[1]
    if zone.shape == "circle":
        margin = zone.radius - math.hypot(dx, dy)
    else:
        yaw = math.radians(zone.yaw_deg)
        lx = math.cos(yaw) * dx + math.sin(yaw) * dy
        ly = -math.sin(yaw) * dx + math.cos(yaw) * dy
        sx, sy = zone.size_xy
        margin = min(sx / 2 - abs(lx), sy / 2 - abs(ly))
    return margin >= 0, margin


def _zone(scene: SceneSpec, zone_id: str) -> Zone:
    for z in scene.zones:
        if z.id == zone_id:
            return z
    raise Undecided("unknown_zone", zone=zone_id)


def _min_xy_distance(a: np.ndarray, b: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    from scipy.spatial import cKDTree

    tree = cKDTree(b[:, :2])
    dist, idx = tree.query(a[:, :2])
    i = int(np.argmin(dist))
    return float(dist[i]), a[i, :2], b[int(idx[i]), :2]


# ---------------------------------------------------------------------------------------------
# routes: each returns (answer in id form, detail)


def _count(route: PixelRoute, keep) -> tuple[int, dict]:
    counted = [p for p in route.visible(COUNT_MIN_PIXELS) if keep(route.obj(p))]
    return len(counted), {"counted": [route.oid(p) for p in counted],
                          "min_pixels": COUNT_MIN_PIXELS}  # fmt: skip


def r_count_all(route, params):
    return _count(route, lambda o: True)


def r_count_category(route, params):
    return _count(route, lambda o: o.category == params["category"])


def r_count_color(route, params):
    return _count(route, lambda o: o.color_name == params["color"])


def r_count_color_in(route, params):
    container = route.param_object(params, "container")
    info = container_info(container)
    if info is None:
        raise Undecided("no_container_info", container=container.id)
    counted, rows = [], {}
    for p in route.visible(COUNT_MIN_PIXELS):
        o = route.obj(p)
        if o.id == container.id or o.color_name != params["color"]:
            continue
        inside, row = inside_container(route.points(p), container, info)
        rows[o.id] = row
        if inside:
            counted.append(o.id)
    return len(counted), {"counted": counted, "objects": rows}


def r_nearest_to_gripper(route, params):
    tip = np.asarray(route.scene.gripper.tip.position)
    dists = {}
    for p in route.visible():
        dists[route.oid(p)] = float(np.linalg.norm(route.points(p) - tip, axis=1).min())
    if len(dists) < 2:
        raise Undecided("too_few_visible_objects")
    ranked = sorted(dists, key=lambda k: (dists[k], k))
    gap = dists[ranked[1]] - dists[ranked[0]]
    return ranked[0], {"nearest_point_m": {k: _r(v) for k, v in dists.items()},
                       "runner_up": ranked[1], "gap_m": _r(gap)}  # fmt: skip


def r_distance_cm(route, params):
    a, b = route.param_object(params, "a"), route.param_object(params, "b")
    ma, mb = route.median(a.pass_index), route.median(b.pass_index)
    d = float(np.hypot(*(ma[:2] - mb[:2]))) * 100.0
    return round(d, 1), {
        "median_xy": {a.id: [_r(v) for v in ma[:2]], b.id: [_r(v) for v in mb[:2]]}
    }


def r_front_to_back(route, params):
    a, b = route.param_object(params, "a"), route.param_object(params, "b")
    idx, depth = route.passes.index, route.passes.depth
    med = {}
    for o in (a, b):
        d = depth[(idx == o.pass_index) & (depth > 0)]
        if d.size < MIN_OBJECT_PIXELS:
            raise Undecided("object_not_visible", object_id=o.id, pixels=int(d.size))
        med[o.id] = float(np.median(d))
    closer = min(med, key=lambda k: (med[k], k))
    return closer, {"median_depth_m": {k: _r(v) for k, v in med.items()},
                    "gap_m": _r(abs(med[a.id] - med[b.id]))}  # fmt: skip


def r_relation(route, params):
    anchor = route.param_object(params, "anchor")
    direction = str(params.get("direction"))
    if direction not in ("left", "right"):
        raise Undecided("bad_direction", direction=direction)
    sign = 1.0 if direction == "right" else -1.0
    axis = sign * route.passes.camera_right_xy()
    m0 = route.median(anchor.pass_index)[:2]
    cands = {}
    for p in route.visible():
        o = route.obj(p) if p in route.by_index else None
        if o is None or o.id == anchor.id:
            continue
        v = np.median(route.points(p), axis=0)[:2] - m0
        dist = float(np.hypot(*v))
        if dist < 1e-6:
            continue
        angle = math.degrees(math.acos(max(-1.0, min(1.0, float(v @ axis) / dist))))
        if angle <= RELATION_CONE_DEG:
            cands[o.id] = (dist, angle)
    if not cands:
        raise Undecided("no_candidate")
    ranked = sorted(cands, key=lambda k: (cands[k][0], k))
    return ranked[0], {"candidates": {k: {"distance_m": _r(d), "angle_deg": _r(a, 2)}
                                      for k, (d, a) in cands.items()}}  # fmt: skip


def r_support(route, params):
    obj = route.param_object(params, "a")
    pts = route.points(obj.pass_index)
    if len(pts) < MIN_OBJECT_PIXELS:
        raise Undecided("object_not_visible", object_id=obj.id, pixels=len(pts))
    low = float(np.percentile(pts[:, 2], 1))
    lo_xy, hi_xy = pts[:, :2].min(0) - SUPPORT_XY_REACH_M, pts[:, :2].max(0) + SUPPORT_XY_REACH_M
    rows, cols = route.passes.ring(obj.pass_index, SUPPORT_RING_PX)
    idx = route.passes.index[rows, cols]
    keep = (idx != 0) & (idx != route.scene.gripper.pass_index) & (idx != obj.pass_index)
    rows, cols, idx = rows[keep], cols[keep], idx[keep]
    ring = route.passes.back_project(rows, cols)
    ok_depth = route.passes.depth[rows, cols] > 0
    idx = idx[ok_depth]
    near = (
        (ring[:, 0] >= lo_xy[0]) & (ring[:, 0] <= hi_xy[0])
        & (ring[:, 1] >= lo_xy[1]) & (ring[:, 1] <= hi_xy[1])
        & (ring[:, 2] <= low + SUPPORT_ABOVE_LOW_M)
    )  # fmt: skip
    groups = {}
    for p in np.unique(idx[near]).tolist():
        z = ring[near][idx[near] == p][:, 2]
        if z.size >= SUPPORT_MIN_PIXELS:
            groups[int(p)] = (float(np.median(z)), int(z.size))
    if not groups:
        raise Undecided("no_support_pixels", lowest_z=_r(low))
    best = max(groups, key=lambda p: (groups[p][0], groups[p][1]))
    return route.oid(best), {"lowest_visible_z": _r(low),
                             "support_groups": {route.oid(p): {"median_z": _r(z), "pixels": n}
                                                for p, (z, n) in groups.items()}}  # fmt: skip


def r_contents(route, params):
    container = route.param_object(params, "container")
    info = container_info(container)
    if info is None:
        raise Undecided("no_container_info", container=container.id)
    inside_ids, rows = [], {}
    for p in route.visible():
        o = route.obj(p)
        if o.id == container.id:
            continue
        inside, row = inside_container(route.points(p), container, info)
        if inside or row["edge_m"] > -0.05:
            rows[o.id] = row
        if inside:
            inside_ids.append(o.id)
    return sorted(inside_ids), {"objects": rows}


def r_occluded_by(route, params):
    occ = route.param_object(params, "occluder")
    idx, amodal = route.passes.index, route.passes.amodal
    occluder_px = idx == occ.pass_index
    shares = {}
    for p, mask in amodal.items():
        if p == occ.pass_index or p not in route.by_index:
            continue
        total = int(mask.sum())
        if not total:
            continue
        covered = int((mask & occluder_px).sum())
        visible = int((idx == p).sum())
        shares[route.oid(p)] = (covered / total, visible / total)
    cands = {k: v for k, v in shares.items()
             if v[0] >= OCCLUSION_MIN_SHARE and v[1] >= OCCLUSION_MIN_VISIBLE}  # fmt: skip
    ranked = sorted(shares, key=lambda k: (-shares[k][0], k))
    detail = {"shares": {k: {"covered": _r(shares[k][0]), "visible": _r(shares[k][1])}
                         for k in ranked[:5]}}  # fmt: skip
    if not cands:
        raise Undecided("no_occluded_object", **detail)
    winner = max(cands, key=lambda k: (cands[k][0], k))
    return winner, detail


def r_in_zone(route, params):
    zone = _zone(route.scene, str(params.get("zone")))
    members, rows = [], {}
    for p in route.visible():
        o = route.obj(p)
        inside, margin = in_zone(np.median(route.points(p), axis=0)[:2], zone)
        rows[o.id] = _r(margin)
        if inside:
            members.append(o.id)
    return sorted(members), {"boundary_margin_m": rows}


def _item_width(route: PixelRoute, params: dict, truth_trace: list[dict]) -> tuple[float, str]:
    for row in truth_trace or []:
        for key in ("item_width_m", "width_m"):
            if isinstance(row.get(key), int | float):
                return float(row[key]), f"trace:{key}"
    item = route.param_object(params, "item")
    return float(max(item.dimensions[0], item.dimensions[1])), "scene:item_dimensions"


def r_room_between(route, params, truth_trace=None):
    a, b = route.param_object(params, "a"), route.param_object(params, "b")
    pa_all, pb_all = route.points(a.pass_index), route.points(b.pass_index)
    for o, pts in ((a, pa_all), (b, pb_all)):
        if len(pts) < MIN_OBJECT_PIXELS:
            raise Undecided("object_not_visible", object_id=o.id, pixels=len(pts))
    width, width_source = _item_width(route, params, truth_trace)
    gap, pa, pb = _min_xy_distance(pa_all, pb_all)
    item_id = resolve_param(route.scene, params.get("item"))
    seg = pb - pa
    seg_len2 = float(seg @ seg)
    blockers = {}
    for p in route.visible():
        o = route.obj(p)
        if o.id in (a.id, b.id, item_id) or seg_len2 < 1e-12:
            continue
        xy = route.points(p)[:, :2] - pa
        t = xy @ seg / seg_len2
        perp = np.abs(xy[:, 0] * seg[1] - xy[:, 1] * seg[0]) / math.sqrt(seg_len2)
        n = int(((t > 0) & (t < 1) & (perp <= width / 2)).sum())
        if n >= CORRIDOR_MIN_PIXELS:
            blockers[o.id] = n
    detail = {"gap_m": _r(gap), "width_m": _r(width), "width_source": width_source,
              "blockers": blockers}  # fmt: skip
    if blockers or gap <= width - FREE_SPACE_NO_M:
        return False, detail
    if gap >= width + FREE_SPACE_YES_M:
        return True, detail
    raise Undecided("near_tie_gap", **detail)


def r_gripper_center(route, params):
    w, h = route.passes.size
    jj, ii = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    disc = (jj - w / 2) ** 2 + (ii - h / 2) ** 2 <= GRIPPER_DISC_PX**2
    values = sorted(set(np.unique(route.passes.index[disc]).tolist()))
    detail = {"disc_px": GRIPPER_DISC_PX, "disc_indices": values}
    if (
        len(values) != 1
        or not (1 <= values[0] < TABLE_PASS_INDEX)
        or values[0] not in route.by_index
    ):
        raise Undecided("disc_not_single_object", **detail)
    return route.oid(values[0]), detail


ROUTES = {
    "count_all": r_count_all,
    "count_category": r_count_category,
    "count_color": r_count_color,
    "count_color_in": r_count_color_in,
    "nearest_to_gripper": r_nearest_to_gripper,
    "distance_cm": r_distance_cm,
    "front_to_back": r_front_to_back,
    "relation": r_relation,
    "support": r_support,
    "contents": r_contents,
    "occluded_by": r_occluded_by,
    "in_zone": r_in_zone,
    "room_between": r_room_between,
    "gripper_center": r_gripper_center,
}


# ---------------------------------------------------------------------------------------------
# comparison


def truth_in_id_form(scene: SceneSpec, answer_type: str, answer: Any) -> Any:
    if answer_type == "object":
        return resolve(scene, answer)
    if answer_type == "set":
        ids = [resolve(scene, a) for a in answer]
        return None if any(i is None for i in ids) else sorted(ids)
    return answer


def _names(scene: SceneSpec, answer_type: str, value: Any) -> Any:
    if answer_type == "object" and isinstance(value, str):
        return name_of(scene, value)
    if answer_type == "set" and isinstance(value, list):
        return sorted(name_of(scene, v) for v in value)
    return value


def number_tolerance(truth: float, tolerance: float | None) -> float:
    return float(tolerance) if tolerance is not None else max(2.0, 0.1 * abs(float(truth)))


def recompute(question: CompiledQuestion, scene: SceneSpec, passes: CameraPasses) -> tuple:
    """(answer in id form, detail) from pixels; raises Undecided when the route cannot decide."""
    name = question.program.name
    if name not in ROUTES:
        raise Undecided("no_pixel_route", program=name)
    route = PixelRoute(scene, passes)
    params = dict(question.program.params)
    if name == "room_between":
        trace = question.result.trace if question.result else []
        return r_room_between(route, params, trace)
    return ROUTES[name](route, params)


def check_g2(
    question: CompiledQuestion,
    scene: SceneSpec,
    passes: CameraPasses,
    render_check: dict | None = None,
) -> GateResult:
    result = question.result
    if result is None:
        return GateResult(passed=False, evidence={"reason": "no_result"})
    truth = truth_in_id_form(scene, result.answer_type, result.answer)
    evidence: dict[str, Any] = {"route": f"pixels:{question.program.name}",
                                "g1_answer": result.answer}  # fmt: skip
    try:
        value, detail = recompute(question, scene, passes)
    except Undecided as exc:
        evidence.update(reason=f"undecided:{exc.reason}", detail=exc.detail, agree=False)
        return GateResult(passed=False, evidence=evidence)
    except KeyError as exc:  # a parameter the route needs is missing
        evidence.update(reason="bad_params", detail={"missing": str(exc)}, agree=False)
        return GateResult(passed=False, evidence=evidence)
    if result.answer_type == "number":
        tol = number_tolerance(result.answer, result.tolerance)
        agree = abs(float(value) - float(result.answer)) <= tol
        evidence["tolerance"] = _r(tol)
    else:
        agree = truth is not None and value == truth
    evidence.update(g2_answer=_names(scene, result.answer_type, value), agree=bool(agree),
                    detail=detail)  # fmt: skip
    if truth is None:
        evidence["reason"] = "g1_answer_unresolvable"
    elif not agree:
        evidence["reason"] = "pixel_answer_differs"

    raycast_ok = True
    if render_check is not None:
        ious = render_check.get("raycast_iou") or {}
        ev = {o: ious[o] for o in result.evidence_object_ids if o in ious}
        low = {o: v for o, v in ev.items() if v < MIN_RAYCAST_IOU}
        evidence["render_vs_scene"] = {
            "raycast": render_check.get("raycast"),
            "evidence_iou": ev,
            "min_iou": MIN_RAYCAST_IOU,
        }
        if low:
            raycast_ok = False
            evidence.setdefault("reason", "render_disagrees_with_scene")
            evidence["render_vs_scene"]["failing"] = sorted(low)
    return GateResult(passed=bool(agree) and raycast_ok and truth is not None, evidence=evidence)


# ---------------------------------------------------------------------------------------------
# render versus scene file


def _warp_index_fn():
    try:
        import vtf.camera as camera_mod
    except ImportError:
        return None
    for name in ("warp_index", "warp_index_image", "distort_index"):
        fn = getattr(camera_mod, name, None)
        if callable(fn):
            return fn
    return None


def _call_warp(fn, index: np.ndarray, cam, k: np.ndarray, k1: float):
    """Call the render lane's warp function, mapping its parameter names to our values."""
    values = {
        "index": index, "image": index, "array": index, "img": index, "mask": index,
        "k1": k1, "distortion_k1": k1,
        "quality": cam.quality, "cam": cam, "camera": cam, "camera_spec": cam,
        "K": k, "k": k, "intrinsics": k,
    }  # fmt: skip
    kwargs = {}
    for p in inspect.signature(fn).parameters.values():
        if p.name in values:
            kwargs[p.name] = values[p.name]
        elif p.default is inspect.Parameter.empty and p.kind not in (
            p.VAR_POSITIONAL,
            p.VAR_KEYWORD,
        ):
            raise TypeError(f"unknown parameter {p.name}")
    return np.asarray(fn(**kwargs))


def render_vs_scene(scene: SceneSpec, passes: CameraPasses, do_raycast: bool = True) -> dict:
    """Camera matrices against the scene file, and per-object IoU of the index pass against an
    exact ray cast of the settled scene."""
    cam = scene.camera(passes.out.camera_id)
    k_err = float(np.abs(passes.K - intrinsics(cam)).max())
    w2c_err = float(np.abs(passes.w2c - world_to_camera(cam)).max())
    out: dict[str, Any] = {
        "K_max_abs_error": round(k_err, 6),
        "world_to_camera_max_abs_error": round(w2c_err, 6),
        "camera_ok": k_err < CAMERA_TOLERANCE * max(1.0, float(np.abs(passes.K).max()))
        and w2c_err < CAMERA_TOLERANCE,
        "raycast": "ok",
        "raycast_iou": {},
        "min_raycast_iou": None,
    }
    if not do_raycast:
        out["raycast"] = "not_sampled"
        return out
    warp = None
    if passes.k1:  # the distortion the render applied (manifest), not the scene's request
        warp = _warp_index_fn()
        if warp is None:
            out["raycast"] = "skipped:distortion_warp_unavailable"
            return out
    try:
        from vtf.core.raycast import raycast

        rc = raycast(scene, cam.id)
        index = rc.index
        if warp is not None:
            index = _call_warp(warp, index, cam, passes.K, passes.k1)
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        # a missing cached asset, or a warp function whose signature we cannot map: record it
        out["raycast"] = f"skipped:{type(exc).__name__}: {str(exc)[:120]}"
        return out
    iou = {}
    for obj in scene.objects:
        a = passes.index == obj.pass_index
        b = index == obj.pass_index
        union = int((a | b).sum())
        iou[obj.id] = round(int((a & b).sum()) / union, 4) if union else 1.0
    out["raycast_iou"] = iou
    out["min_raycast_iou"] = min(iou.values()) if iou else None
    return out
