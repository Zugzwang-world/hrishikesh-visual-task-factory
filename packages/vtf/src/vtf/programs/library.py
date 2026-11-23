"""The question programs (spec/families.md) and `run_program`.

Every program is a pure function of the settled SceneSpec, a camera and JSON-safe params. It
returns a ProgramResult whose trace records each value it computed, with the G4 margins (metres
and pixels), the scoring tolerance, the 2D-shortcut answer for spatial families and a
question-level difficulty vector, or raises ProgramReject when the question would be ambiguous
(near ties, unnameable objects, no scale in frame, ...). The G4 metric and pixel margins are
applied here, so an accepted question already clears them.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from vtf.core.program import FAMILIES, PROGRAM_FAMILY, ProgramReject, ProgramResult
from vtf.core.scene import CameraSpec, SceneObject, SceneSpec, Zone
from vtf.core.vocab import NONE_ANSWER, TABLE_ANSWER, full_name, noun_class, plural
from vtf.programs.geometry import (
    RESTING_TOL_M,
    SceneGeom,
    closest_points,
    convex_hull,
    diameter,
    min_width,
    penetration_depth,
    point_in_polygon,
    point_segment_distance,
    polygon_distance,
    rect_polygon,
    scene_geom,
)
from vtf.programs.view import (
    View,
    camera_right_xy,
    camera_view,
    image_footprint,
    image_point,
    planar_depth,
    scale_reference,
)

PROGRAMS_VERSION = "0.2.2"
ZONE_IN_FRAME_MARGIN_PX = 4  # the whole reach-zone outline must be this far inside the image

TABLE_CONTACT_TOL_M = RESTING_TOL_M
# G4 margins (spec/families.md)
NEAR_TIE_GRIPPER_M = 0.03  # nearest_to_gripper, overhead and gripper rigs
NEAR_TIE_GRIPPER_FRONT_M = 0.05
NEAR_TIE_DEPTH_M = 0.04  # front_to_back, overhead and gripper rigs
NEAR_TIE_DEPTH_FRONT_M = 0.06
MIN_PIXEL_GAP = 10.0  # nearest, front_to_back, relation: image points at least 10 px apart
RELATION_CONE_DEG = 30.0
RELATION_EDGE_DEG = 5.0
RELATION_RUNNER_UP_M = 0.03
RELATION_OVERLAP_M = 0.002  # objects whose footprint overlaps the anchor's are not candidates
DISTANCE_MIN_M = 0.08
DISTANCE_TOL_CM = 2.0
DISTANCE_TOL_FRACTION = 0.10
CONTAINER_BOUNDARY_M = 0.01
OCCLUSION_MIN_SHARE = 0.10
OCCLUSION_MIN_VISIBLE = 0.30
OCCLUSION_RUNNER_UP_ABS = 0.03
ZONE_BOUNDARY_M = 0.02
ZONE_BOUNDARY_PX = 6.0
ZONE_EVIDENCE_M = 0.05  # non-members this close outside the zone are evidence too
FREE_YES_M = 0.02  # yes: gap >= width + 2 cm
FREE_NO_M = 0.015  # no: gap <= width - 1 cm (spec), raised to the 1.5 cm G4 margin
FREE_BLOCK_M = 0.01  # a blocker enters the corridor by >= 1 cm; none closer than 1 cm otherwise
FREE_PX = 6.0
GRIPPER_DISC_PX = 15.0
GRIPPER_MARGIN_WINDOW_PX = 64

_CMP_DIGITS = 9  # threshold tests ignore sub-nanometre float noise
_OUT_DIGITS = 6  # one micrometre in outputs, like geometry.round_vec


def _r(value: float, digits: int = _OUT_DIGITS) -> float:
    return round(float(value), digits) + 0.0


def _vec(values) -> list[float]:
    return [_r(v) for v in values]


def _below(value: float, threshold: float) -> bool:
    return round(float(value), _CMP_DIGITS) < threshold


def canonical_name(obj: SceneObject) -> str:
    """Full descriptive name ([size] [colour] category); not necessarily unique."""
    return full_name(obj)


def _check_params(name: str, params: dict[str, Any], required: tuple[str, ...]) -> None:
    missing = [k for k in required if k not in params]
    extra = sorted(set(params) - set(required))
    if missing or extra:
        raise ValueError(f"{name}: params must be exactly {list(required)}, got {sorted(params)}")


class Ctx:
    """One program call: the scene, its geometry and the task camera."""

    def __init__(self, scene: SceneSpec, camera: CameraSpec) -> None:
        self.scene = scene
        self.camera = camera
        self.sg: SceneGeom = scene_geom(scene)
        self._view: View | None = None

    @property
    def rig(self) -> str:
        return self.camera.rig

    @property
    def view(self) -> View:
        if self._view is None:
            self._view = camera_view(self.scene, self.camera.id)
        return self._view

    @property
    def frame(self) -> str:
        return "gripper camera" if self.rig == "gripper" else "camera"

    def obj(self, name: str, object_id: Any) -> SceneObject:
        try:
            return self.scene.object(str(object_id))
        except KeyError:
            raise ValueError(f"{name}: no object {object_id!r}") from None

    def require_name(self, object_id: str, role: str) -> str:
        n = self.sg.name(object_id)
        if n is None:
            obj = self.scene.object(object_id)
            raise ProgramReject("unnameable", role=role, object_id=object_id,
                                full_name=full_name(obj))  # fmt: skip
        return n

    def label(self, object_id: str) -> str:
        """Unique name, or the full name for an unnameable object (shortcut answers, traces)."""
        return self.sg.name(object_id) or full_name(self.scene.object(object_id))

    def image_point(self, object_id: str) -> np.ndarray:
        return image_point(self.camera, self.sg.geom[object_id].centre)

    def footprint_px(self, object_id: str) -> np.ndarray:
        return image_footprint(self.camera, self.sg.geom[object_id])

    def difficulty(self, evidence: list[str], target: str | None = None, **extra) -> dict:
        objs = self.sg.objects
        out: dict[str, Any] = {"n_objects": len(objs), "n_distractors": len(objs) - len(evidence)}
        if target is not None:
            t = self.scene.object(target)
            out["n_same_category"] = sum(o.category == t.category for o in objs) - 1
            out["n_same_color"] = (
                sum(o.color_name == t.color_name for o in objs) - 1 if t.color_name else 0
            )
            out["placement"] = self.sg.placement_kind(target)
        out.update({k: (_r(v, 4) if isinstance(v, float) else v) for k, v in extra.items()})
        return out


def _result(
    answer_type: str,
    answer,
    evidence: list[str],
    trace: list[dict],
    difficulty: dict,
    *,
    answer_text: str | None = None,
    margin: float | None = None,
    margin_px: float | None = None,
    tolerance: float | None = None,
    shortcut: tuple[Any, bool] | None = None,
) -> ProgramResult:
    if margin is not None:
        difficulty["margin_m"] = _r(margin, 4)
    if margin_px is not None:
        difficulty["margin_px"] = _r(margin_px, 2)
    if shortcut is not None:
        difficulty["shortcut_conflict"] = shortcut[1]
    return ProgramResult(
        answer=answer,
        answer_type=answer_type,  # type: ignore[arg-type]
        answer_text=answer_text if answer_text is not None else str(answer),
        evidence_object_ids=evidence,
        trace=trace,
        margin=None if margin is None else _r(margin),
        margin_px=None if margin_px is None else _r(margin_px, 3),
        tolerance=tolerance,
        shortcut_conflict=None if shortcut is None else shortcut[1],
        shortcut_answer=None if shortcut is None else shortcut[0],
        difficulty=difficulty,
    )


def _set_text(names: list[str]) -> str:
    return ", ".join(names) if names else NONE_ANSWER


# ---------------------------------------------------------------------------------------------
# count and count_attribute


def _on_table_check(ctx: Ctx, object_ids: list[str]) -> list[dict]:
    """Rows for counted objects; rejects when one is off the table (the phrasing says so)."""
    h = ctx.scene.table.height
    rows, off = [], []
    for oid in object_ids:
        g = ctx.sg.geom[oid]
        over = ctx.sg.on_table_footprint(oid)
        above = round(g.lowest_z - h, _CMP_DIGITS) >= -RESTING_TOL_M
        rows.append({"object_id": oid, "name": full_name(g.obj), "centre": _vec(g.centre),
                     "lowest_z": _r(g.lowest_z), "over_table": over, "placement":
                     ctx.sg.placement_kind(oid)})  # fmt: skip
        if not (over and above):
            off.append(oid)
    if off:
        raise ProgramReject("off_table", object_ids=off)
    return rows


def _count_result(ctx: Ctx, counted: list[str], rows: list[dict], rule: dict,
                  extra_evidence: list[str] = (), **difficulty) -> ProgramResult:  # fmt: skip
    trace = [
        {"step": "rule", **rule},
        *rows,
        {"step": "count", "object_ids": counted, "value": len(counted)},
    ]
    evidence = sorted(set(counted) | set(extra_evidence))
    diff = ctx.difficulty(evidence, **difficulty)
    diff["n_counted"] = len(counted)
    raised = [r for r in rows if r.get("placement") in ("stacked", "contained")]
    diff["n_stacked_or_contained"] = sum(r["object_id"] in counted for r in raised)
    return _result("int", len(counted), evidence, trace, diff)


def count_all(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Every object in the scene, stacked and contained objects included."""
    _check_params("count_all", params, ())
    ids = [o.id for o in ctx.sg.objects]
    rows = _on_table_check(ctx, ids)
    return _count_result(ctx, ids, rows, {"rule": "every object in the scene"})


def count_category(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Every object of one category."""
    _check_params("count_category", params, ("category",))
    category = str(params["category"])
    rivals = sorted({o.category for o in ctx.sg.objects if o.category != category
                     and o.category in noun_class(category)})  # fmt: skip
    if rivals:
        raise ProgramReject("ambiguous_category", category=category, synonyms_present=rivals)
    ids = [o.id for o in ctx.sg.objects if o.category == category]
    rows = _on_table_check(ctx, ids)
    return _count_result(ctx, ids, rows, {"rule": "objects of a category", "category": category,
                                          "plural": plural(category)})  # fmt: skip


def count_color(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Every object with one colour word."""
    _check_params("count_color", params, ("color",))
    colour = str(params["color"])
    ids = [o.id for o in ctx.sg.objects if o.color_name == colour]
    rows = _on_table_check(ctx, ids)
    return _count_result(ctx, ids, rows, {"rule": "objects of a colour", "colour": colour})


def _container(ctx: Ctx, name: str, container_id: Any) -> SceneObject:
    c = ctx.obj(name, container_id)
    if ctx.sg.geom[c.id].container is None:
        raise ValueError(f"{name}: {c.id} is not a container")
    return c


def _inside_rows(ctx: Ctx, container_id: str, object_ids: list[str]) -> tuple[list[dict], float]:
    """Inside-rule rows; rejects a near tie (centre within 1 cm of the interior boundary, or the
    lowest point within 1 cm of the rim with the centre over the interior)."""
    info = ctx.sg.geom[container_id].container
    rim = info.rim_z_m * ctx.sg.geom[container_id].obj.scale
    rows, margin, near = [], math.inf, []
    for oid in object_ids:
        ins = ctx.sg.inside(oid, container_id)
        rows.append({"object_id": oid, "name": full_name(ctx.scene.object(oid)),
                     "boundary_m": _r(ins.boundary_m), "lowest_above_container_m":
                     _r(ins.rel_low_m), "xy_inside": ins.xy_inside, "z_inside": ins.z_inside,
                     "inside": ins.inside})  # fmt: skip
        margin = min(margin, abs(ins.boundary_m))
        if _below(abs(ins.boundary_m), CONTAINER_BOUNDARY_M):
            near.append({"object_id": oid, "boundary_m": _r(ins.boundary_m)})
        elif ins.xy_inside and _below(abs(ins.rel_low_m - rim), CONTAINER_BOUNDARY_M):
            near.append({"object_id": oid, "rim_gap_m": _r(ins.rel_low_m - rim)})
    if near:
        raise ProgramReject("near_container_boundary", container=container_id, objects=near,
                            threshold_m=CONTAINER_BOUNDARY_M)  # fmt: skip
    return rows, margin


def count_color_in(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Objects of one colour inside a container."""
    _check_params("count_color_in", params, ("color", "container"))
    colour = str(params["color"])
    c = _container(ctx, "count_color_in", params["container"])
    ctx.require_name(c.id, "container")
    ids = [o.id for o in ctx.sg.objects if o.color_name == colour and o.id != c.id]
    rows, margin = _inside_rows(ctx, c.id, ids)
    counted = [r["object_id"] for r in rows if r["inside"]]
    result = _count_result(
        ctx, counted, rows,
        {"rule": "objects of a colour inside a container", "colour": colour, "container": c.id},
        extra_evidence=[c.id],
    )  # fmt: skip
    if ids:
        result.margin = _r(margin)
        result.difficulty["margin_m"] = _r(margin, 4)
    return result


def contents(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """The objects inside a container (sorted names; may be none)."""
    _check_params("contents", params, ("container",))
    c = _container(ctx, "contents", params["container"])
    ctx.require_name(c.id, "container")
    others = [o.id for o in ctx.sg.objects if o.id != c.id]
    rows, margin = _inside_rows(ctx, c.id, others)
    members = [r["object_id"] for r in rows if r["inside"]]
    names = sorted(ctx.require_name(oid, "member") for oid in members)
    info = ctx.sg.geom[c.id].container
    trace = [
        {"step": "container", "object_id": c.id, "interior": info.model_dump(mode="json"),
         "shrink_m": 0.005, "scale": c.scale},
        *[r for r in rows if r["inside"] or abs(r["boundary_m"]) < 0.1],
        {"step": "contents", "object_ids": members, "names": names},
    ]  # fmt: skip
    evidence = sorted([c.id, *members])
    diff = ctx.difficulty(evidence, target=c.id, n_inside=len(members))
    return _result("set", names, evidence, trace, diff, answer_text=_set_text(names),
                   margin=margin if others else None)  # fmt: skip


# ---------------------------------------------------------------------------------------------
# nearest_to_gripper


def nearest_to_gripper(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """The object whose mesh surface is nearest the gripper tip (3D)."""
    _check_params("nearest_to_gripper", params, ())
    tip = np.asarray(ctx.scene.gripper.tip.position, dtype=float)
    rows = sorted(
        ({"object_id": o.id, "name": full_name(o),
          "distance_m": ctx.sg.geom[o.id].surface_distance(tip)} for o in ctx.sg.objects),
        key=lambda r: (r["distance_m"], r["object_id"]),
    )  # fmt: skip
    if len(rows) < 2:
        raise ProgramReject("too_few_objects", n_objects=len(rows))
    first, second = rows[0], rows[1]
    gap = second["distance_m"] - first["distance_m"]
    threshold = NEAR_TIE_GRIPPER_FRONT_M if ctx.rig == "front" else NEAR_TIE_GRIPPER_M
    if _below(gap, threshold):
        raise ProgramReject("near_tie_gripper", nearest=first["object_id"],
                            runner_up=second["object_id"], nearest_m=_r(first["distance_m"]),
                            runner_up_m=_r(second["distance_m"]), gap_m=_r(gap),
                            threshold_m=threshold)  # fmt: skip
    win, run = first["object_id"], second["object_id"]
    p_win = ctx.sg.geom[win].closest_point(tip)
    p_run = ctx.sg.geom[run].closest_point(tip)
    gap_px = float(np.linalg.norm(image_point(ctx.camera, p_win) - image_point(ctx.camera, p_run)))
    if _below(gap_px, MIN_PIXEL_GAP):
        raise ProgramReject("near_tie_pixels", nearest=win, runner_up=run, gap_px=_r(gap_px, 2),
                            threshold_px=MIN_PIXEL_GAP)  # fmt: skip
    name = ctx.require_name(win, "answer")
    # 2D heuristic: the image footprint that comes closest to the projected tip point
    tip_px = image_point(ctx.camera, tip)
    heur = []
    for o in ctx.sg.objects:
        hull = ctx.footprint_px(o.id)
        d = 0.0 if point_in_polygon(tip_px, hull) else min(
            point_segment_distance(tip_px, hull[i], hull[(i + 1) % len(hull)])
            for i in range(len(hull))
        )
        heur.append((round(d, 6), round(float(np.linalg.norm(ctx.image_point(o.id) - tip_px)), 6),
                     o.id))  # fmt: skip
    heur.sort()
    h_id = heur[0][2]
    trace = [
        {"step": "gripper_tip", "position": _vec(tip), "image_point": _vec(tip_px)},
        *({**r, "distance_m": _r(r["distance_m"])} for r in rows),
        {"step": "decision", "nearest": win, "runner_up": run, "gap_m": _r(gap),
         "threshold_m": threshold, "nearest_point": _vec(p_win), "runner_up_point": _vec(p_run),
         "gap_px": _r(gap_px, 3)},
        {"step": "shortcut", "rule": "image footprint closest to the projected tip",
         "object_id": h_id, "footprint_px_distance": heur[0][0]},
    ]  # fmt: skip
    evidence = [win, run]
    diff = ctx.difficulty(evidence, target=win, nearest_m=_r(first["distance_m"], 4))
    return _result("object", name, evidence, trace, diff, margin=gap, margin_px=gap_px,
                   shortcut=(ctx.label(h_id), h_id != win))  # fmt: skip


# ---------------------------------------------------------------------------------------------
# distance


def _require_scale(ctx: Ctx) -> dict:
    ref = scale_reference(ctx.scene, ctx.view)
    if ref is None:
        raise ProgramReject("no_scale_in_frame", camera_id=ctx.camera.id,
                            grid=ctx.scene.table.grid_cell_m)  # fmt: skip
    return ref


def distance_cm(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Horizontal distance between two objects' centres on the table plane, in cm."""
    _check_params("distance_cm", params, ("a", "b"))
    a, b = ctx.obj("distance_cm", params["a"]), ctx.obj("distance_cm", params["b"])
    if a.id == b.id:
        raise ValueError("distance_cm: a and b must be different objects")
    ref = _require_scale(ctx)
    ga, gb = ctx.sg.geom[a.id], ctx.sg.geom[b.id]
    d = float(np.linalg.norm(ga.xy - gb.xy))
    if _below(d, DISTANCE_MIN_M):
        raise ProgramReject("too_close", object_ids=[a.id, b.id], distance_m=_r(d),
                            threshold_m=DISTANCE_MIN_M)  # fmt: skip
    ctx.require_name(a.id, "a")
    ctx.require_name(b.id, "b")
    truth = round(d * 100.0, 1)
    tol = round(max(DISTANCE_TOL_CM, DISTANCE_TOL_FRACTION * truth), 2)
    trace = [
        {"step": "scale_in_frame", **ref},
        {"object_id": a.id, "centre_xy": _vec(ga.xy)},
        {"object_id": b.id, "centre_xy": _vec(gb.xy)},
        {"step": "distance", "metres": _r(d), "centimetres": truth, "tolerance_cm": tol},
    ]
    evidence = [a.id, b.id]
    diff = ctx.difficulty(evidence, target=b.id, distance_cm=truth, scale=ref["kind"])
    return _result("number", truth, evidence, trace, diff, answer_text=f"{truth:.1f} cm",
                   tolerance=tol)  # fmt: skip


# ---------------------------------------------------------------------------------------------
# front_to_back


def front_to_back(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Which of two objects is closer to the camera, by planar depth of the centres; the
    straight-line distance must agree with the same margin."""
    _check_params("front_to_back", params, ("a", "b"))
    a, b = ctx.obj("front_to_back", params["a"]), ctx.obj("front_to_back", params["b"])
    if a.id == b.id:
        raise ValueError("front_to_back: a and b must be different objects")
    cam = ctx.camera
    pos = np.array([ctx.sg.geom[a.id].centre, ctx.sg.geom[b.id].centre])
    depth = [planar_depth(cam, p) for p in pos]
    eye = np.asarray(cam.pose.position, dtype=float)
    dist = [float(np.linalg.norm(p - eye)) for p in pos]
    if min(depth) <= cam.clip_start:
        raise ProgramReject("behind_camera", object_ids=[a.id, b.id], depth_m=_vec(depth))
    threshold = NEAR_TIE_DEPTH_FRONT_M if ctx.rig == "front" else NEAR_TIE_DEPTH_M
    gap = abs(depth[0] - depth[1])
    if _below(gap, threshold):
        raise ProgramReject("near_tie_depth", object_ids=[a.id, b.id], depth_m=_vec(depth),
                            gap_m=_r(gap), threshold_m=threshold)  # fmt: skip
    closer, farther = (a, b) if depth[0] < depth[1] else (b, a)
    signed_dist_gap = (dist[1] - dist[0]) if closer is a else (dist[0] - dist[1])
    if _below(signed_dist_gap, threshold):
        raise ProgramReject("near_tie_distance", object_ids=[a.id, b.id], depth_m=_vec(depth),
                            euclidean_m=_vec(dist), threshold_m=threshold)  # fmt: skip
    pa, pb = ctx.image_point(a.id), ctx.image_point(b.id)
    gap_px = float(np.linalg.norm(pa - pb))
    if _below(gap_px, MIN_PIXEL_GAP):
        raise ProgramReject("near_tie_pixels", object_ids=[a.id, b.id], gap_px=_r(gap_px, 2),
                            threshold_px=MIN_PIXEL_GAP)  # fmt: skip
    ctx.require_name(a.id, "a")
    ctx.require_name(b.id, "b")
    name = ctx.require_name(closer.id, "answer")
    low = {o.id: float(ctx.footprint_px(o.id)[:, 1].max()) for o in (a, b)}
    h_id = a.id if (low[a.id], b.id) > (low[b.id], a.id) else b.id
    trace = [
        {"step": "camera", "camera_id": cam.id, "rig": ctx.rig, "position": _vec(eye)},
        *({"object_id": o.id, "name": full_name(o), "centre": _vec(pos[i]),
           "planar_depth_m": _r(depth[i]), "euclidean_m": _r(dist[i]),
           "image_point": _vec((pa, pb)[i]), "footprint_max_v": _r(low[o.id], 3)}
          for i, o in enumerate((a, b))),
        {"step": "decision", "closer": closer.id, "farther": farther.id, "gap_m": _r(gap),
         "euclidean_gap_m": _r(signed_dist_gap), "threshold_m": threshold,
         "gap_px": _r(gap_px, 3)},
        {"step": "shortcut", "rule": "footprint reaching lower in the image is closer",
         "object_id": h_id},
    ]  # fmt: skip
    evidence = [a.id, b.id]
    diff = ctx.difficulty(evidence, target=closer.id, depth_gap_m=_r(gap, 4))
    return _result("object", name, evidence, trace, diff, margin=gap, margin_px=gap_px,
                   shortcut=(ctx.label(h_id), h_id != closer.id))  # fmt: skip


# ---------------------------------------------------------------------------------------------
# relation


def relation(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """In the camera's left/right on the table plane: the nearest object within 30 degrees of
    the asked direction from the anchor."""
    _check_params("relation", params, ("anchor", "direction"))
    anchor = ctx.obj("relation", params["anchor"])
    direction = str(params["direction"])
    if direction not in ("left", "right"):
        raise ValueError("relation: direction must be left or right")
    right = camera_right_xy(ctx.camera)
    want = right if direction == "right" else -right
    ga = ctx.sg.geom[anchor.id]
    rows = []
    for o in ctx.sg.objects:
        if o.id == anchor.id:
            continue
        g = ctx.sg.geom[o.id]
        if penetration_depth(g.hull, ga.hull) > RELATION_OVERLAP_M:
            rows.append({"object_id": o.id, "excluded": "footprint overlaps the anchor"})
            continue
        d = g.xy - ga.xy
        dist = float(np.linalg.norm(d))
        angle = math.degrees(math.acos(max(-1.0, min(1.0, float(d @ want) / dist))))
        rows.append({"object_id": o.id, "distance_m": dist, "angle_deg": angle})
    scored = [r for r in rows if "distance_m" in r]
    cands = sorted((r for r in scored if r["angle_deg"] <= RELATION_CONE_DEG),
                   key=lambda r: (r["distance_m"], r["object_id"]))  # fmt: skip
    if not cands:
        raise ProgramReject("no_candidate", anchor=anchor.id, direction=direction)
    win = cands[0]
    edge = [r["object_id"] for r in scored
            if abs(r["angle_deg"] - RELATION_CONE_DEG) <= RELATION_EDGE_DEG
            and r["distance_m"] < win["distance_m"]]  # fmt: skip
    if edge:
        raise ProgramReject("near_cone_edge", anchor=anchor.id, answer=win["object_id"],
                            edge_objects=edge, edge_deg=RELATION_EDGE_DEG)  # fmt: skip
    margin = margin_px = None
    evidence = [anchor.id, win["object_id"]]
    if len(cands) > 1:
        run = cands[1]
        margin = run["distance_m"] - win["distance_m"]
        if _below(margin, RELATION_RUNNER_UP_M):
            raise ProgramReject("near_tie_relation", answer=win["object_id"],
                                runner_up=run["object_id"], gap_m=_r(margin),
                                threshold_m=RELATION_RUNNER_UP_M)  # fmt: skip
        margin_px = float(np.linalg.norm(ctx.image_point(win["object_id"])
                                         - ctx.image_point(run["object_id"])))  # fmt: skip
        if _below(margin_px, MIN_PIXEL_GAP):
            raise ProgramReject("near_tie_pixels", answer=win["object_id"],
                                runner_up=run["object_id"], gap_px=_r(margin_px, 2),
                                threshold_px=MIN_PIXEL_GAP)  # fmt: skip
        evidence.append(run["object_id"])
    ctx.require_name(anchor.id, "anchor")
    name = ctx.require_name(win["object_id"], "answer")
    # 2D heuristic: nearest image point on the asked side of the anchor's image point
    pa = ctx.image_point(anchor.id)
    sign = 1.0 if direction == "right" else -1.0
    pool = [r["object_id"] for r in scored]  # objects overlapping the anchor are never answers
    side = sorted((float(np.linalg.norm(ctx.image_point(oid) - pa)), oid) for oid in pool
                  if (ctx.image_point(oid)[0] - pa[0]) * sign > 0)  # fmt: skip
    h_id = side[0][1] if side else None
    trace = [
        {"step": "frame", "camera_id": ctx.camera.id, "image_right_xy": _vec(right),
         "direction": direction, "cone_deg": RELATION_CONE_DEG, "anchor": anchor.id,
         "anchor_xy": _vec(ga.xy)},
        *({**r, **({"distance_m": _r(r["distance_m"]), "angle_deg": _r(r["angle_deg"], 3)}
                   if "distance_m" in r else {})} for r in rows),
        {"step": "decision", "answer": win["object_id"],
         "runner_up": cands[1]["object_id"] if len(cands) > 1 else None},
        {"step": "shortcut", "rule": "nearest image point on the asked side (candidates only)",
         "object_id": h_id},
    ]  # fmt: skip
    diff = ctx.difficulty(evidence, target=win["object_id"], n_candidates=len(cands),
                          answer_angle_deg=_r(win["angle_deg"], 2))  # fmt: skip
    return _result("object", name, evidence, trace, diff, margin=margin, margin_px=margin_px,
                   shortcut=(ctx.label(h_id) if h_id else None, h_id != win["object_id"]))


# ---------------------------------------------------------------------------------------------
# support


def support(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """What the object rests on: the object directly beneath it, or the table."""
    _check_params("support", params, ("a",))
    a = ctx.obj("support", params["a"])
    s = ctx.sg.support(a.id)
    cands = [{k: (_r(v) if isinstance(v, float) else v) for k, v in c.items()}
             for c in s.candidates]  # fmt: skip
    if s.support_id is None:
        raise ProgramReject("unclear_support", object_id=a.id, reason=s.reason, candidates=cands)
    ctx.require_name(a.id, "a")
    if s.support_id == "table":
        answer, evidence = TABLE_ANSWER, [a.id]
    else:
        answer, evidence = ctx.require_name(s.support_id, "answer"), [a.id, s.support_id]
    trace = [
        {"step": "rule", "resting_tol_m": RESTING_TOL_M, "object_id": a.id,
         "lowest_z": _r(ctx.sg.geom[a.id].lowest_z), "centre": _vec(ctx.sg.geom[a.id].centre)},
        *cands,
        {"step": "decision", "support": s.support_id, "gap_m": _r(s.gap_m)},
    ]  # fmt: skip
    diff = ctx.difficulty(evidence, target=a.id, supported_by_table=s.support_id == "table")
    return _result("object", answer, evidence, trace, diff)


# ---------------------------------------------------------------------------------------------
# occlusion


def occluded_by(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """The object with the largest share of its amodal silhouette covered by the occluder."""
    _check_params("occluded_by", params, ("occluder",))
    occ = ctx.obj("occluded_by", params["occluder"])
    view = ctx.view
    occ_mask = view.index == occ.pass_index
    rows = []
    for o in ctx.sg.objects:
        if o.id == occ.id or o.pass_index not in view.amodal:
            continue
        amodal = view.amodal[o.pass_index]
        a_px = int(amodal.sum())
        if a_px == 0:
            continue
        covered = int((amodal & occ_mask).sum())
        rows.append({"object_id": o.id, "amodal_px": a_px, "covered_px": covered,
                     "share": covered / a_px, "visible": view.fraction(o.id)})  # fmt: skip
    rows.sort(key=lambda r: (-r["share"], r["object_id"]))
    if not rows or rows[0]["share"] < OCCLUSION_MIN_SHARE:
        raise ProgramReject("not_occluded", occluder=occ.id,
                            best_share=_r(rows[0]["share"], 4) if rows else 0.0)  # fmt: skip
    win = rows[0]
    # The object the occluder rests on or sits inside is "hidden" by it only in a degenerate
    # sense (a lemon in a bowl hides the bowl's floor); such questions are rejected (brief 05
    # check run: models read them as containment, not occlusion).
    holders = {ctx.sg.support(occ.id).support_id, ctx.sg.container_of(occ.id)}
    if win["object_id"] in holders:
        raise ProgramReject("occluder_rests_on_answer", occluder=occ.id, object_id=win["object_id"])
    if win["visible"] < OCCLUSION_MIN_VISIBLE:
        raise ProgramReject("too_hidden", occluder=occ.id, object_id=win["object_id"],
                            visible=_r(win["visible"], 4))  # fmt: skip
    run_share = rows[1]["share"] if len(rows) > 1 else 0.0
    if not (run_share < OCCLUSION_RUNNER_UP_ABS or run_share <= win["share"] / 2):
        raise ProgramReject("near_tie_occlusion", occluder=occ.id, answer=win["object_id"],
                            runner_up=rows[1]["object_id"], share=_r(win["share"], 4),
                            runner_up_share=_r(run_share, 4))  # fmt: skip
    ctx.require_name(occ.id, "occluder")
    name = ctx.require_name(win["object_id"], "answer")
    po = ctx.image_point(occ.id)
    near = sorted((float(np.linalg.norm(ctx.image_point(o.id) - po)), o.id)
                  for o in ctx.sg.objects if o.id != occ.id)  # fmt: skip
    h_id = near[0][1]
    trace = [
        {"step": "raycast", "camera_id": ctx.camera.id, "raycaster": view.raycaster,
         "occluder": occ.id, "occluder_pass_index": occ.pass_index},
        *({**r, "share": _r(r["share"], 4), "visible": _r(r["visible"], 4)}
          for r in rows if r["covered_px"] > 0),
        {"step": "decision", "answer": win["object_id"], "share": _r(win["share"], 4),
         "runner_up_share": _r(run_share, 4)},
        {"step": "shortcut", "rule": "image point nearest the occluder's", "object_id": h_id},
    ]  # fmt: skip
    evidence = [occ.id, win["object_id"]]
    diff = ctx.difficulty(evidence, target=win["object_id"], occlusion_share=win["share"],
                          runner_up_share=run_share, visible_fraction=win["visible"])
    return _result("object", name, evidence, trace, diff,
                   shortcut=(ctx.label(h_id), h_id != win["object_id"]))


# ---------------------------------------------------------------------------------------------
# reachability


def zone_label(scene: SceneSpec, zone: Zone) -> str | None:
    """'green reach zone' (colour unique among zones), 'reach zone' (only zone), else None."""
    same = [z for z in scene.zones if z.kind == zone.kind]
    if zone.color_name and sum(z.color_name == zone.color_name for z in same) == 1:
        return f"{zone.color_name} {zone.kind} zone"
    return f"{zone.kind} zone" if len(same) == 1 else None


def zone_outline(zone: Zone, n: int = 128) -> np.ndarray:
    """Boundary points on the table plane (closed polygon, CCW)."""
    if zone.shape == "circle":
        th = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
        r = float(zone.radius or 0.0)
        return np.c_[zone.center_xy[0] + r * np.cos(th), zone.center_xy[1] + r * np.sin(th)]
    return rect_polygon(zone.center_xy, zone.size_xy or (0.0, 0.0), zone.yaw_deg)


def zone_signed_distance(zone: Zone, xy) -> float:
    """Distance of a table point to the zone boundary: positive inside, negative outside."""
    if zone.shape == "circle":
        return float(zone.radius or 0.0) - float(np.linalg.norm(np.asarray(xy) - zone.center_xy))
    c, s = math.cos(math.radians(zone.yaw_deg)), math.sin(math.radians(zone.yaw_deg))
    d = np.asarray(xy, dtype=float) - np.asarray(zone.center_xy)
    local = np.array([c * d[0] + s * d[1], -s * d[0] + c * d[1]])
    hx, hy = (zone.size_xy[0] / 2, zone.size_xy[1] / 2) if zone.size_xy else (0.0, 0.0)
    dx, dy = abs(local[0]) - hx, abs(local[1]) - hy
    if dx <= 0 and dy <= 0:
        return float(min(-dx, -dy))
    return -float(math.hypot(max(dx, 0.0), max(dy, 0.0)))


def _zone(ctx: Ctx, zone_id: Any) -> Zone:
    for z in ctx.scene.zones:
        if z.id == str(zone_id):
            return z
    raise ValueError(f"in_zone: no zone {zone_id!r}")


def in_zone(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Objects whose centre (x, y) lies inside the zone."""
    _check_params("in_zone", params, ("zone",))
    zone = _zone(ctx, params["zone"])
    label = zone_label(ctx.scene, zone)
    if label is None:
        raise ProgramReject("ambiguous_zone", zone=zone.id)
    cam = ctx.camera
    outline = zone_outline(zone)
    h = ctx.scene.table.height
    uv_outline, z_outline = _project_outline(cam, outline, h)
    # The whole outline must be in frame, or "inside the zone" cannot be judged from the image
    # (brief 05 check run: a wrist view showed one corner of the zone).
    w, hgt = cam.resolution
    m = ZONE_IN_FRAME_MARGIN_PX
    in_frame = (
        (np.asarray(z_outline) > 0)
        & (uv_outline[:, 0] >= m) & (uv_outline[:, 0] <= w - m)
        & (uv_outline[:, 1] >= m) & (uv_outline[:, 1] <= hgt - m)
    )  # fmt: skip
    if not bool(np.all(in_frame)):
        raise ProgramReject("zone_not_in_frame", zone=zone.id,
                            outside_share=_r(1.0 - float(np.mean(in_frame)), 4))  # fmt: skip
    rows, margin, margin_px, near = [], math.inf, math.inf, []
    for o in ctx.sg.objects:
        g = ctx.sg.geom[o.id]
        sd = zone_signed_distance(zone, g.xy)
        ip = ctx.image_point(o.id)
        px = min(point_segment_distance(ip, uv_outline[i], uv_outline[(i + 1) % len(uv_outline)])
                 for i in range(len(uv_outline)))  # fmt: skip
        rows.append({"object_id": o.id, "name": full_name(o), "centre_xy": _vec(g.xy),
                     "signed_distance_m": sd, "image_point": _vec(ip), "boundary_px": px,
                     "inside": sd >= 0, "image_inside": point_in_polygon(ip, uv_outline)})
        margin, margin_px = min(margin, abs(sd)), min(margin_px, px)
        if _below(abs(sd), ZONE_BOUNDARY_M) or _below(px, ZONE_BOUNDARY_PX):
            near.append({"object_id": o.id, "signed_distance_m": _r(sd), "boundary_px": _r(px, 2)})
    if near:
        raise ProgramReject("near_zone_boundary", zone=zone.id, objects=near,
                            threshold_m=ZONE_BOUNDARY_M, threshold_px=ZONE_BOUNDARY_PX)
    members = [r["object_id"] for r in rows if r["inside"]]
    names = sorted(ctx.require_name(oid, "member") for oid in members)
    heur_ids = [r["object_id"] for r in rows if r["image_inside"]]
    heur = sorted(ctx.label(oid) for oid in heur_ids)
    trace = [
        {"step": "zone", **zone.model_dump(mode="json"), "label": label,
         "outline_in_front_of_camera": bool((z_outline > cam.clip_start).all())},
        *({**r, "signed_distance_m": _r(r["signed_distance_m"]),
           "boundary_px": _r(r["boundary_px"], 3)} for r in rows),
        {"step": "members", "object_ids": members, "names": names},
        {"step": "shortcut", "rule": "image point inside the projected outline",
         "object_ids": heur_ids},
    ]  # fmt: skip
    evidence = sorted(set(members) | {r["object_id"] for r in rows
                                      if -ZONE_EVIDENCE_M <= r["signed_distance_m"] < 0})
    diff = ctx.difficulty(evidence, n_inside=len(members))
    return _result("set", names, evidence, trace, diff, answer_text=_set_text(names),
                   margin=margin if rows else None, margin_px=margin_px if rows else None,
                   shortcut=(heur, sorted(heur_ids) != sorted(members)))


def _project_outline(cam: CameraSpec, outline: np.ndarray, height: float):
    pts = np.c_[outline, np.full(len(outline), height)]
    from vtf.core.projection import project

    uv, z = project(cam, pts)
    return convex_hull(uv), z


# ---------------------------------------------------------------------------------------------
# free_space


def room_between(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """Is there room for the item on the table between a and b?"""
    _check_params("room_between", params, ("item", "a", "b"))
    item = ctx.obj("room_between", params["item"])
    a, b = ctx.obj("room_between", params["a"]), ctx.obj("room_between", params["b"])
    if len({item.id, a.id, b.id}) != 3:
        raise ValueError("room_between: item, a and b must be three different objects")
    ref = _require_scale(ctx)
    off = [o.id for o in (a, b) if ctx.sg.support(o.id).support_id != "table"]
    if off:
        raise ProgramReject("not_on_table", object_ids=off)
    ga, gb, gi = ctx.sg.geom[a.id], ctx.sg.geom[b.id], ctx.sg.geom[item.id]
    gap = polygon_distance(ga.hull, gb.hull)
    w_max, w_min = diameter(gi.hull), min_width(gi.hull)
    blockers, borderline, corridor = [], [], None
    if gap > 0:
        pa, pb, _ = closest_points(ga.hull, gb.hull)
        u = (pb - pa) / gap
        n = np.array([-u[1], u[0]]) * (w_max / 2)
        corridor = convex_hull(np.array([pa + n, pa - n, pb + n, pb - n]))
        for o in ctx.sg.objects:
            if o.id in (item.id, a.id, b.id):
                continue
            hull = ctx.sg.geom[o.id].hull
            pen = penetration_depth(hull, corridor)
            if pen > 0 and not _below(pen, FREE_BLOCK_M):
                blockers.append({"object_id": o.id, "penetration_m": _r(pen)})
            elif pen > 0 or _below(polygon_distance(hull, corridor), FREE_BLOCK_M):
                borderline.append({"object_id": o.id, "penetration_m": _r(pen)})
    if not _below(w_min - gap, FREE_NO_M):
        answer, reason, margin = False, "gap", w_min - gap
    elif not _below(gap - w_max, FREE_YES_M):
        if blockers:
            answer, reason = False, "blocked"
            margin = max(bk["penetration_m"] for bk in blockers)
        elif borderline:
            raise ProgramReject("near_tie_blocker", objects=borderline, threshold_m=FREE_BLOCK_M)
        else:
            answer, reason, margin = True, "gap", gap - w_max
    else:
        raise ProgramReject("near_tie_gap", gap_m=_r(gap), width_max_m=_r(w_max),
                            width_min_m=_r(w_min), yes_margin_m=FREE_YES_M,
                            no_margin_m=FREE_NO_M)  # fmt: skip
    fa, fb, fi = ctx.footprint_px(a.id), ctx.footprint_px(b.id), ctx.footprint_px(item.id)
    gap_px, width_px = polygon_distance(fa, fb), diameter(fi)
    margin_px = abs(gap_px - width_px)
    if _below(margin_px, FREE_PX):
        raise ProgramReject("near_tie_pixels", gap_px=_r(gap_px, 2), width_px=_r(width_px, 2),
                            threshold_px=FREE_PX)  # fmt: skip
    for oid, role in ((item.id, "item"), (a.id, "a"), (b.id, "b")):
        ctx.require_name(oid, role)
    heur = bool(gap_px >= width_px)
    trace = [
        {"step": "scale_in_frame", **ref},
        {"step": "footprints", "gap_m": _r(gap), "item_width_max_m": _r(w_max),
         "item_width_min_m": _r(w_min), "yes_if_gap_at_least_m": _r(w_max + FREE_YES_M),
         "no_if_gap_at_most_m": _r(w_min - FREE_NO_M)},
        {"step": "corridor", "polygon_xy": [_vec(p) for p in corridor] if corridor is not None
         else None, "blockers": blockers, "borderline": borderline},
        {"step": "decision", "answer": answer, "reason": reason},
        {"step": "shortcut", "rule": "pixel gap vs item's image width", "gap_px": _r(gap_px, 3),
         "width_px": _r(width_px, 3), "answer": heur},
    ]  # fmt: skip
    evidence = [item.id, a.id, b.id] + [bk["object_id"] for bk in blockers]
    diff = ctx.difficulty(evidence, target=item.id, gap_cm=_r(gap * 100, 2),
                          width_cm=_r(w_max * 100, 2), blocked=bool(blockers), scale=ref["kind"])
    return _result("bool", answer, evidence, trace, diff, answer_text="yes" if answer else "no",
                   margin=margin, margin_px=margin_px, shortcut=(heur, heur != answer))


# ---------------------------------------------------------------------------------------------
# gripper_view


def gripper_center(ctx: Ctx, params: dict[str, Any]) -> ProgramResult:
    """In the gripper camera: the object hit by the ray through the image centre, covering a
    15 px disc around it alone."""
    _check_params("gripper_center", params, ())
    if ctx.rig != "gripper":
        raise ProgramReject("not_gripper_camera", camera_id=ctx.camera.id, rig=ctx.rig)
    view = ctx.view
    w, h = view.size
    cu, cv = w / 2.0, h / 2.0
    r = GRIPPER_MARGIN_WINDOW_PX
    v0, v1 = max(int(cv - r), 0), min(int(cv + r) + 1, h)
    u0, u1 = max(int(cu - r), 0), min(int(cu + r) + 1, w)
    win = view.index[v0:v1, u0:u1]
    vv, uu = np.mgrid[v0:v1, u0:u1]
    dist = np.hypot(uu + 0.5 - cu, vv + 0.5 - cv)
    disc = dist <= GRIPPER_DISC_PX
    values = sorted(int(x) for x in np.unique(win[disc]))
    by_pass = {o.pass_index: o for o in ctx.sg.objects}
    centre_vals = win[dist == dist.min()]
    centre_idx = int(np.bincount(centre_vals.ravel()).argmax())
    if centre_idx not in by_pass:
        raise ProgramReject("no_object_at_centre", index=centre_idx)
    if values != [centre_idx]:
        raise ProgramReject("disc_not_uniform", indices=values, radius_px=GRIPPER_DISC_PX)
    others = win != centre_idx
    margin_px = float(dist[others].min()) if others.any() else float(r)
    obj = by_pass[centre_idx]
    name = ctx.require_name(obj.id, "answer")
    trace = [
        {"step": "raycast", "camera_id": ctx.camera.id, "raycaster": view.raycaster,
         "image_centre": [cu, cv], "disc_radius_px": GRIPPER_DISC_PX},
        {"step": "disc", "indices": values, "object_id": obj.id,
         "nearest_other_px": _r(margin_px, 3)},
    ]  # fmt: skip
    diff = ctx.difficulty([obj.id], target=obj.id)
    return _result("object", name, [obj.id], trace, diff, margin_px=margin_px)


# ---------------------------------------------------------------------------------------------
# registry


@dataclass(frozen=True)
class ProgramDef:
    name: str
    family: str
    fn: Callable[[Ctx, dict[str, Any]], ProgramResult]


_FNS: dict[str, Callable[[Ctx, dict[str, Any]], ProgramResult]] = {
    "count_all": count_all,
    "count_category": count_category,
    "count_color": count_color,
    "count_color_in": count_color_in,
    "nearest_to_gripper": nearest_to_gripper,
    "distance_cm": distance_cm,
    "front_to_back": front_to_back,
    "relation": relation,
    "support": support,
    "contents": contents,
    "occluded_by": occluded_by,
    "in_zone": in_zone,
    "room_between": room_between,
    "gripper_center": gripper_center,
}
assert set(_FNS) == set(PROGRAM_FAMILY), "every program in FAMILIES needs an implementation"

PROGRAMS: dict[str, ProgramDef] = {
    name: ProgramDef(name, PROGRAM_FAMILY[name], fn) for name, fn in _FNS.items()
}


def run_program(name: str, params: dict, scene: SceneSpec, camera_id: str) -> ProgramResult:
    """Run one registered program on a settled scene. Pure and deterministic."""
    if not scene.meta.settled:
        raise ValueError(f"{scene.meta.scene_id}: programs run on a settled scene only")
    if name not in PROGRAMS:
        raise ValueError(f"unknown program {name!r}; known: {sorted(PROGRAMS)}")
    camera = scene.camera(camera_id)
    family = FAMILIES[PROGRAMS[name].family]
    if camera.rig not in family.rigs:
        raise ProgramReject("wrong_rig", program=name, rig=camera.rig, rigs=list(family.rigs))
    ctx = Ctx(scene, camera)
    try:
        result = PROGRAMS[name].fn(ctx, dict(params))
    except ProgramReject as rej:
        rej.detail = plain(rej.detail)
        raise
    if family.spatial and result.shortcut_conflict is None:
        raise AssertionError(f"{name}: spatial families must set shortcut_conflict")
    if result.evidence_object_ids:  # occlusion level of the evidence in this camera
        result.difficulty["evidence_min_visible"] = _r(
            min(ctx.view.fraction(i) for i in result.evidence_object_ids), 4
        )
    result.trace = plain(result.trace)
    result.difficulty = plain(result.difficulty)
    return result


def plain(value: Any) -> Any:
    """JSON-safe copy: numpy scalars and arrays become Python numbers, bools and lists."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value
