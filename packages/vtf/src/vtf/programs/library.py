"""The v0 question programs and `run_program`.

Every program is a pure function of the settled SceneSpec, a camera and JSON-safe params. It
returns a ProgramResult whose trace records each value it computed, or raises ProgramReject when
the question would be ambiguous for this scene.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from vtf.core.program import ProgramReject, ProgramResult
from vtf.core.projection import to_camera
from vtf.core.scene import CameraSpec, SceneObject, SceneSpec
from vtf.core.vocab import normalise_object, object_name
from vtf.programs.shapes import lowest_z, point_distance, solid_kind

PROGRAMS_VERSION = "0.1.0"

TABLE_CONTACT_TOL_M = 0.01  # lowest point within 1 cm of the table top means "resting on it"
NEAR_TIE_GRIPPER_M = 0.03  # nearest_to_gripper: runner-up must be at least 3 cm farther
NEAR_TIE_DEPTH_M = 0.04  # front_to_back: depths must differ by at least 4 cm

_CMP_DIGITS = 9  # threshold tests ignore sub-nanometre float noise
_OUT_DIGITS = 6  # one micrometre in outputs, like geometry.round_vec


def _r(value: float) -> float:
    return round(float(value), _OUT_DIGITS) + 0.0


def _vec(values) -> list[float]:
    return [_r(v) for v in values]


def _below(gap: float, threshold: float) -> bool:
    return round(float(gap), _CMP_DIGITS) < threshold


def canonical_name(obj: SceneObject) -> str:
    return object_name(obj.color_name, obj.category)


def _by_id(scene: SceneSpec) -> list[SceneObject]:
    return sorted(scene.objects, key=lambda o: o.id)


def _check_params(name: str, params: dict[str, Any], required: tuple[str, ...]) -> None:
    missing = [k for k in required if k not in params]
    extra = sorted(set(params) - set(required))
    if missing or extra:
        raise ValueError(f"{name}: params must be exactly {list(required)}, got {sorted(params)}")


def _require_answerable_name(scene: SceneSpec, obj: SceneObject) -> str:
    """An object named in a question or answer must be unique and scorable."""
    name = canonical_name(obj)
    same = sorted(o.id for o in scene.objects if canonical_name(o) == name)
    if len(same) > 1:
        raise ProgramReject("duplicate_name", name=name, object_ids=same)
    if normalise_object(name) != name:
        raise ProgramReject("unscorable_name", name=name, object_id=obj.id)
    return name


# ---------------------------------------------------------------------------------------------
# count


def support_row(scene: SceneSpec, obj: SceneObject) -> dict[str, Any]:
    """The on-table test for one object, as a trace row.

    on_table: lowest point within 1 cm of the table top and centre over the table.
    off_table: neither (for example fallen to the floor).
    ambiguous: one but not the other (stacked, floating, sunk in, or overhanging).
    """
    sx, sy = scene.table.size_xy
    x, y, _ = obj.pose.position
    inside = abs(x) <= sx / 2.0 and abs(y) <= sy / 2.0
    low = lowest_z(obj)
    resting = round(abs(low - scene.table.height), _CMP_DIGITS) <= TABLE_CONTACT_TOL_M
    if inside and resting:
        status = "on_table"
    elif not inside and not resting:
        status = "off_table"
    else:
        status = "ambiguous"
    return {
        "object_id": obj.id,
        "name": canonical_name(obj),
        "solid": solid_kind(obj),
        "centre": _vec(obj.pose.position),
        "lowest_z": _r(low),
        "inside_footprint": inside,
        "resting": resting,
        "status": status,
    }


def _count(scene: SceneSpec, rows: list[dict[str, Any]], rule: dict[str, Any]) -> ProgramResult:
    ambiguous = [r["object_id"] for r in rows if r["status"] == "ambiguous"]
    if ambiguous:
        raise ProgramReject("ambiguous_support", object_ids=ambiguous)
    counted = [r["object_id"] for r in rows if r["status"] == "on_table"]
    trace = [
        {
            "step": "on_table_rule",
            "table_height": _r(scene.table.height),
            "footprint_xy": _vec(scene.table.size_xy),
            "tolerance_m": TABLE_CONTACT_TOL_M,
            **rule,
        },
        *rows,
        {"step": "count", "object_ids": counted, "value": len(counted)},
    ]
    return ProgramResult(
        answer=len(counted),
        answer_type="int",
        answer_text=str(len(counted)),
        evidence_object_ids=counted,
        trace=trace,
    )


def count_all(scene: SceneSpec, camera: CameraSpec, params: dict[str, Any]) -> ProgramResult:
    """How many objects rest on the table top."""
    _check_params("count_all", params, ())
    rows = [support_row(scene, o) for o in _by_id(scene)]
    return _count(scene, rows, {})


def count_color(scene: SceneSpec, camera: CameraSpec, params: dict[str, Any]) -> ProgramResult:
    """How many objects of one colour rest on the table top."""
    _check_params("count_color", params, ("color",))
    colour = str(params["color"])
    rows = [support_row(scene, o) for o in _by_id(scene) if o.color_name == colour]
    return _count(scene, rows, {"colour": colour})


# ---------------------------------------------------------------------------------------------
# nearest_to_gripper


def nearest_to_gripper(
    scene: SceneSpec, camera: CameraSpec, params: dict[str, Any]
) -> ProgramResult:
    """Which object's nearest surface is closest to the gripper tip."""
    _check_params("nearest_to_gripper", params, ())
    tip = scene.gripper.tip.position
    rows = sorted(
        (
            {
                "object_id": o.id,
                "name": canonical_name(o),
                "solid": solid_kind(o),
                "distance_m": point_distance(o, tip),
            }
            for o in scene.objects
        ),
        key=lambda r: (r["distance_m"], r["object_id"]),
    )
    if len(rows) < 2:
        raise ProgramReject("too_few_objects", n_objects=len(rows))
    first, second = rows[0], rows[1]
    gap = second["distance_m"] - first["distance_m"]
    if _below(gap, NEAR_TIE_GRIPPER_M):
        raise ProgramReject(
            "near_tie_gripper",
            nearest=first["object_id"],
            runner_up=second["object_id"],
            nearest_m=_r(first["distance_m"]),
            runner_up_m=_r(second["distance_m"]),
            gap_m=_r(gap),
            threshold_m=NEAR_TIE_GRIPPER_M,
        )
    nearest = scene.object(first["object_id"])
    name = _require_answerable_name(scene, nearest)
    trace = [
        {"step": "gripper_tip", "position": _vec(tip)},
        *({**r, "distance_m": _r(r["distance_m"])} for r in rows),
        {
            "step": "decision",
            "nearest": first["object_id"],
            "runner_up": second["object_id"],
            "gap_m": _r(gap),
            "threshold_m": NEAR_TIE_GRIPPER_M,
        },
    ]
    return ProgramResult(
        answer=name,
        answer_type="object",
        answer_text=name,
        evidence_object_ids=[first["object_id"], second["object_id"]],
        trace=trace,
        margin=_r(gap),
    )


# ---------------------------------------------------------------------------------------------
# front_to_back


def front_to_back(scene: SceneSpec, camera: CameraSpec, params: dict[str, Any]) -> ProgramResult:
    """Which of two objects is closer to the camera, by planar depth of the centres.

    The Euclidean camera distance of the centres must agree with the same margin, so the answer
    does not depend on which notion of "closer" the reader has in mind.
    """
    _check_params("front_to_back", params, ("a", "b"))
    a_id, b_id = str(params["a"]), str(params["b"])
    if a_id == b_id:
        raise ValueError("front_to_back: a and b must be different objects")
    try:
        a, b = scene.object(a_id), scene.object(b_id)
    except KeyError as err:
        raise ValueError(f"front_to_back: no object {err}") from None
    points = to_camera(camera, np.array([a.pose.position, b.pose.position], dtype=float))
    depth = [float(points[0, 2]), float(points[1, 2])]
    dist = [float(np.linalg.norm(points[0])), float(np.linalg.norm(points[1]))]
    rows = [
        {
            "object_id": o.id,
            "name": canonical_name(o),
            "centre": _vec(o.pose.position),
            "camera_xyz": _vec(points[i]),
            "planar_depth_m": _r(depth[i]),
            "euclidean_m": _r(dist[i]),
        }
        for i, o in enumerate((a, b))
    ]
    if min(depth) <= camera.clip_start:
        raise ProgramReject("behind_camera", object_ids=[a_id, b_id], depth_m=_vec(depth))
    gap = abs(depth[0] - depth[1])
    if _below(gap, NEAR_TIE_DEPTH_M):
        raise ProgramReject(
            "near_tie_depth",
            object_ids=[a_id, b_id],
            depth_m=_vec(depth),
            gap_m=_r(gap),
            threshold_m=NEAR_TIE_DEPTH_M,
        )
    closer, farther = (a, b) if depth[0] < depth[1] else (b, a)
    signed_dist_gap = (dist[1] - dist[0]) if closer is a else (dist[0] - dist[1])
    if _below(signed_dist_gap, NEAR_TIE_DEPTH_M):
        raise ProgramReject(
            "near_tie_distance",
            object_ids=[a_id, b_id],
            depth_m=_vec(depth),
            euclidean_m=_vec(dist),
            threshold_m=NEAR_TIE_DEPTH_M,
        )
    _require_answerable_name(scene, a)
    _require_answerable_name(scene, b)
    name = canonical_name(closer)
    trace = [
        {"step": "camera", "camera_id": camera.id, "position": _vec(camera.pose.position)},
        *rows,
        {
            "step": "decision",
            "closer": closer.id,
            "farther": farther.id,
            "gap_m": _r(gap),
            "euclidean_gap_m": _r(signed_dist_gap),
            "threshold_m": NEAR_TIE_DEPTH_M,
        },
    ]
    return ProgramResult(
        answer=name,
        answer_type="object",
        answer_text=name,
        evidence_object_ids=[a_id, b_id],
        trace=trace,
        margin=_r(gap),
    )


# ---------------------------------------------------------------------------------------------
# registry


@dataclass(frozen=True)
class ProgramDef:
    name: str
    family: str
    fn: Callable[[SceneSpec, CameraSpec, dict[str, Any]], ProgramResult]


PROGRAMS: dict[str, ProgramDef] = {
    p.name: p
    for p in (
        ProgramDef("count_all", "count", count_all),
        ProgramDef("count_color", "count", count_color),
        ProgramDef("nearest_to_gripper", "nearest_to_gripper", nearest_to_gripper),
        ProgramDef("front_to_back", "front_to_back", front_to_back),
    )
}


def run_program(name: str, params: dict, scene: SceneSpec, camera_id: str) -> ProgramResult:
    """Run one registered program on a settled scene. Pure and deterministic."""
    if not scene.meta.settled:
        raise ValueError(f"{scene.meta.scene_id}: programs run on a settled scene only")
    if name not in PROGRAMS:
        raise ValueError(f"unknown program {name!r}; known: {sorted(PROGRAMS)}")
    camera = scene.camera(camera_id)
    return PROGRAMS[name].fn(scene, camera, dict(params))
