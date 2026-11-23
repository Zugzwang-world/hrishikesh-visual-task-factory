"""G4 No near-ties: compared quantities differ by a metric and a pixel margin (spec/families.md).

Metric and pixel margins come from the program (ProgramResult.margin in metres, margin_px in the
task camera's pixels); thresholds depend on the family and the camera rig (the front rig is
stricter where the spec says so). Occlusion and gripper_view are checked on the rendered passes
(coverage shares, the centre disc). When a program reports no pixel margin for nearest_to_gripper
or front_to_back, the fallback is the pixel distance between the two evidence objects' projected
centres (the spec's "image points"), recorded as such.
"""

from typing import Any

import numpy as np

from vtf.core.program import CompiledQuestion
from vtf.core.projection import project
from vtf.core.scene import SceneSpec
from vtf.core.task import GateResult
from vtf.validate.names import resolve
from vtf.validate.pixels import CameraPasses

METRIC_MARGIN_M: dict[str, dict[str, float]] = {
    "nearest_to_gripper": {"default": 0.03, "front": 0.05},
    "front_to_back": {"default": 0.04, "front": 0.06},
    "relation": {"default": 0.03},
    "reachability": {"default": 0.02},
    "free_space": {"default": 0.015},
    "containment": {"default": 0.01},
}
PIXEL_MARGIN_PX: dict[str, float] = {
    "nearest_to_gripper": 10.0,
    "front_to_back": 10.0,
    "relation": 10.0,
    "reachability": 6.0,
    "free_space": 6.0,
}
IMAGE_POINT_FALLBACK = ("nearest_to_gripper", "front_to_back")
DISTANCE_MIN_CM = 8.0
OCCLUSION_RUNNER_UP_MAX = 0.03
OCCLUSION_RUNNER_UP_RATIO = 0.5
GRIPPER_DISC_PX = 15.0


def metric_threshold(family: str, rig: str) -> float | None:
    table = METRIC_MARGIN_M.get(family)
    if table is None:
        return None
    return table.get(rig, table["default"])


def pixel_threshold(family: str) -> float | None:
    return PIXEL_MARGIN_PX.get(family)


def _image_point_gap(scene: SceneSpec, camera_id: str, object_ids: list[str]) -> float | None:
    if len(object_ids) < 2:
        return None
    cam = scene.camera(camera_id)
    pts = np.array([scene.object(o).pose.position for o in object_ids[:2]], dtype=float)
    uv, _ = project(cam, pts)
    return float(np.linalg.norm(uv[0] - uv[1]))


def _occlusion(question: CompiledQuestion, scene: SceneSpec, passes: CameraPasses) -> dict:
    occ_id = resolve(scene, question.program.params.get("occluder"))
    winner_id = resolve(scene, question.result.answer)
    if occ_id is None or winner_id is None:
        return {"ok": False, "reason": "unresolvable_objects"}
    occ_px = passes.index == scene.object(occ_id).pass_index
    shares = {}
    for o in scene.objects:
        mask = passes.amodal.get(o.pass_index)
        if o.id == occ_id or mask is None or not mask.any():
            continue
        shares[o.id] = float((mask & occ_px).sum()) / float(mask.sum())
    winner = shares.get(winner_id, 0.0)
    others = {k: v for k, v in shares.items() if k != winner_id}
    runner_id = max(others, key=lambda k: (others[k], k)) if others else None
    runner = others.get(runner_id, 0.0) if runner_id else 0.0
    ok = runner < OCCLUSION_RUNNER_UP_MAX or runner <= OCCLUSION_RUNNER_UP_RATIO * winner
    return {
        "ok": bool(ok),
        "winner_share": round(winner, 4),
        "runner_up": runner_id,
        "runner_up_share": round(runner, 4),
        "rule": f"runner-up < {OCCLUSION_RUNNER_UP_MAX} or <= {OCCLUSION_RUNNER_UP_RATIO} x winner",
    }


def _gripper_disc(question: CompiledQuestion, scene: SceneSpec, passes: CameraPasses) -> dict:
    w, h = passes.size
    jj, ii = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    disc = (jj - w / 2) ** 2 + (ii - h / 2) ** 2 <= GRIPPER_DISC_PX**2
    values = sorted(set(np.unique(passes.index[disc]).tolist()))
    truth = resolve(scene, question.result.answer)
    expected = scene.object(truth).pass_index if truth and truth != "table" else None
    return {"ok": values == [expected], "disc_px": GRIPPER_DISC_PX, "disc_indices": values}


def check_g4(
    question: CompiledQuestion,
    scene: SceneSpec,
    rig: str,
    passes: CameraPasses | None = None,
) -> GateResult:
    result = question.result
    if result is None:
        return GateResult(passed=False, evidence={"reason": "no_result"})
    family = question.family
    ev: dict[str, Any] = {"family": family, "rig": rig}
    problems: list[str] = []
    # A single candidate (e.g. one object in a relation's direction cone) has no runner-up, so
    # there is no tie to measure: the answer is unique by construction (brief 05 scale run).
    no_runner_up = result.margin is None and result.difficulty.get("n_candidates") == 1
    if no_runner_up:
        ev["no_runner_up"] = True

    m_thr = metric_threshold(family, rig)
    if m_thr is not None and not no_runner_up:
        ev.update(metric_margin_m=result.margin, metric_threshold_m=m_thr)
        if result.margin is None:
            problems.append("margin_missing")
        elif round(float(result.margin), 9) < m_thr:
            problems.append("metric_near_tie")

    p_thr = pixel_threshold(family)
    if p_thr is not None and not (no_runner_up and result.margin_px is None):
        margin_px, source = result.margin_px, "program"
        if margin_px is None and family in IMAGE_POINT_FALLBACK:
            margin_px = _image_point_gap(scene, question.camera_id, result.evidence_object_ids)
            source = "fallback:image_points"
        ev.update(pixel_margin_px=None if margin_px is None else round(float(margin_px), 2),
                  pixel_threshold_px=p_thr, pixel_margin_source=source)  # fmt: skip
        if margin_px is None:
            problems.append("margin_px_missing")
        elif margin_px < p_thr:
            problems.append("pixel_near_tie")

    if family == "distance":
        ev.update(truth_cm=result.answer, min_truth_cm=DISTANCE_MIN_CM)
        if float(result.answer) < DISTANCE_MIN_CM:
            problems.append("distance_too_short")
    elif family == "occlusion":
        if passes is None:
            problems.append("no_passes")
        else:
            ev["occlusion"] = _occlusion(question, scene, passes)
            if not ev["occlusion"]["ok"]:
                problems.append("occlusion_runner_up_close")
    elif family == "gripper_view":
        if passes is None:
            problems.append("no_passes")
        else:
            ev["disc"] = _gripper_disc(question, scene, passes)
            if not ev["disc"]["ok"]:
                problems.append("disc_not_single_object")

    if m_thr is None and p_thr is None and family not in ("distance", "occlusion", "gripper_view"):
        ev["note"] = "no margin rule for this family (exact answer)"
    if problems:
        ev["reason"] = problems[0]
        ev["failing"] = problems
    return GateResult(passed=not problems, evidence=ev)
