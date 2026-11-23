"""`vtf validate <run>`: compile questions on each settled scene, run G1, G3 and G7, package the
tasks that pass and log every rejection with its reason."""

import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from vtf.core import layout
from vtf.core.hashing import sha256_array, sha256_file, sha256_json
from vtf.core.io import (
    read_depth,
    read_index_png,
    read_manifest,
    read_mask_png,
    read_model,
    read_scene,
    write_jsonl,
)
from vtf.core.projection import intrinsics, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.render_manifest import CameraOutputs, RenderManifest
from vtf.core.rng import derive_seed
from vtf.core.scene import RunInfo, SceneSpec
from vtf.core.task import RejectedRow
from vtf.package.writer import build_task, task_versions, write_task
from vtf.validate.gates import g3_evidence_visible as g3
from vtf.validate.gates.g1_truth import check_g1
from vtf.validate.gates.g3_evidence_visible import check_g3, visibility_table
from vtf.validate.gates.g7_determinism import check_g7

GATES = ("G1", "G3", "G7")
CAMERA_TOLERANCE = 1e-3
# A scene is used only if the settle came to rest on the table (expert, 23 Nov 2026).
MAX_SETTLE_DRIFT_M = 0.03
MAX_SUPPORT_GAP_M = 0.002


def settle_problems(manifest: RenderManifest) -> dict:
    s = manifest.settle
    problems = {}
    if s.moving_at_end:
        problems["moving_at_end"] = s.moving_at_end
    if s.off_table:
        problems["off_table"] = s.off_table
    if s.max_displacement_m > MAX_SETTLE_DRIFT_M:
        problems["drift_m"] = s.max_displacement_m
    gaps = {k: v for k, v in s.support_gap_m.items() if abs(v) > MAX_SUPPORT_GAP_M}
    if gaps:
        problems["support_gap_m"] = gaps
    return problems


def question_seed(settled: SceneSpec, camera_id: str) -> int:
    return derive_seed(settled.meta.seed, "questions", camera_id)


def regenerate_scene_bytes(run_info: RunInfo | None, run_dir: Path, scene_id: str) -> bytes | None:
    """Re-sample a scene from its recipe and seed; None when the recipe is not available."""
    if run_info is None or scene_id not in run_info.scenes:
        return None
    try:
        from vtf.scene.generate import regenerate_scene_json
    except ImportError:
        return None
    return regenerate_scene_json(run_info, scene_id)


def scene_determinism(
    lay: layout.RunLayout,
    scene_id: str,
    manifest: RenderManifest,
    cam_out: CameraOutputs,
    index: np.ndarray,
    depth: np.ndarray,
    run_info: RunInfo | None,
) -> dict:
    scene_bytes = lay.scene_file(scene_id).read_bytes()
    hashes: dict = {
        "scene_sha256": sha256_file(lay.scene_file(scene_id)),
        "settled_sha256": sha256_file(lay.render_dir(scene_id) / manifest.settled_scene_file),
        "index_sha256": sha256_array(index),
        "depth_sha256": sha256_array(depth),
        "render_matches_scene_file": manifest.scene_sha256 == sha256_file(lay.scene_file(scene_id)),
    }
    regenerated = regenerate_scene_bytes(run_info, lay.root, scene_id)
    hashes["scene_regenerates_identically"] = (
        None if regenerated is None else regenerated == scene_bytes
    )
    return hashes


def render_agreement(settled: SceneSpec, cam_out: CameraOutputs, index: np.ndarray) -> dict:
    """Does the render agree with the scene file? Camera matrices, and per-object IoU between the
    Blender index pass and an exact ray cast of the settled scene."""
    cam = settled.camera(cam_out.camera_id)
    k_err = float(np.abs(np.asarray(cam_out.K) - intrinsics(cam)).max())
    w2c_err = float(np.abs(np.asarray(cam_out.world_to_camera) - world_to_camera(cam)).max())
    iou = {}
    try:
        rc = raycast(settled, cam.id)
        for obj in settled.objects:
            a = index == obj.pass_index
            b = rc.index == obj.pass_index
            union = int((a | b).sum())
            iou[obj.id] = round(int((a & b).sum()) / union, 4) if union else 1.0
    except ValueError:  # assets the ray caster does not handle
        pass
    return {
        "K_max_abs_error": round(k_err, 6),
        "world_to_camera_max_abs_error": round(w2c_err, 6),
        "camera_ok": k_err < CAMERA_TOLERANCE * max(1.0, float(np.abs(cam_out.K).max()))
        and w2c_err < CAMERA_TOLERANCE,
        "raycast_iou": iou,
        "min_raycast_iou": min(iou.values()) if iou else None,
    }


def validate_run(run_dir: Path) -> dict:
    from vtf.programs import PROGRAMS_VERSION, compile_questions

    run_dir = Path(run_dir)
    lay = layout.RunLayout(run_dir)
    run_info = read_model(lay.run_file, RunInfo) if lay.run_file.exists() else None
    if lay.tasks_dir.exists():
        shutil.rmtree(lay.tasks_dir)

    accepted, rejected = [], []
    gate_stats = {g: Counter() for g in GATES}
    by_family = defaultdict(Counter)
    scene_reports = {}

    for sid in lay.scene_ids():
        mpath = lay.render_manifest(sid)
        if not mpath.exists():
            rejected.append(RejectedRow(scene_id=sid, stage="render", reason="not_rendered"))
            continue
        manifest = read_manifest(mpath)
        rdir = lay.render_dir(sid)
        unsettled = settle_problems(manifest)
        if unsettled:
            rejected.append(
                RejectedRow(
                    scene_id=sid,
                    stage="settle",
                    reason="settle_" + "_".join(sorted(unsettled)),
                    detail=unsettled,
                )
            )
            continue
        settled = read_scene(rdir / manifest.settled_scene_file)
        scene_hash = sha256_json(settled)
        versions = task_versions(settled, manifest, PROGRAMS_VERSION)
        pass_index_of = {o.id: o.pass_index for o in settled.objects}

        for cam_out in manifest.cameras:
            index = read_index_png(rdir / cam_out.index)
            depth = read_depth(rdir / cam_out.depth)
            amodal = {int(k): read_mask_png(rdir / p) for k, p in cam_out.amodal.items()}
            vis = visibility_table(index, amodal, list(pass_index_of.values()))
            agreement = render_agreement(settled, cam_out, index)
            hashes = scene_determinism(lay, sid, manifest, cam_out, index, depth, run_info)
            scene_reports[f"{sid}/{cam_out.camera_id}"] = {
                "render_agreement": agreement,
                "determinism": hashes,
                "off_table": manifest.settle.off_table,
                "visibility": {
                    oid: {
                        "visible_pixels": vis[p].visible_pixels,
                        "visible_fraction": round(vis[p].fraction, 4),
                    }
                    for oid, p in pass_index_of.items()
                },
            }
            if not agreement["camera_ok"]:
                rejected.append(
                    RejectedRow(
                        scene_id=sid,
                        camera_id=cam_out.camera_id,
                        stage="render",
                        reason="camera_mismatch",
                        detail=agreement,
                    )
                )
                continue

            questions = compile_questions(
                settled, cam_out.camera_id, question_seed(settled, cam_out.camera_id)
            )
            for q in questions:
                base = {
                    "scene_id": sid,
                    "camera_id": cam_out.camera_id,
                    "family": q.family,
                    "template_id": q.template_id,
                    "question": q.question,
                }
                by_family[q.family]["candidates"] += 1
                if q.rejected or q.result is None:
                    rejected.append(
                        RejectedRow(
                            **base,
                            stage="compile",
                            reason=q.rejected or "no_result",
                            detail=q.rejected_detail,
                        )
                    )
                    by_family[q.family]["rejected_compile"] += 1
                    continue
                gates = {
                    "G1": check_g1(q, settled),
                    "G3": check_g3(
                        q.result.evidence_object_ids,
                        pass_index_of,
                        vis,
                        min_pixels=g3.MIN_VISIBLE_PIXELS,
                        min_fraction=g3.MIN_VISIBLE_FRACTION,
                    ),
                    "G7": check_g7(q.result.answer, hashes),
                }
                for g, res in gates.items():
                    gate_stats[g]["run"] += 1
                    gate_stats[g]["pass"] += int(res.passed)
                failed = [g for g, res in gates.items() if not res.passed]
                if failed:
                    first = failed[0]
                    rejected.append(
                        RejectedRow(
                            **base,
                            stage=first,
                            reason=gates[first].evidence.get("reason", "failed"),
                            detail={"failed_gates": failed, **gates[first].evidence},
                        )
                    )
                    by_family[q.family][f"rejected_{first}"] += 1
                    continue
                evidence_vis = [
                    vis[pass_index_of[o]].fraction for o in q.result.evidence_object_ids
                ]
                difficulty = {
                    "n_objects": len(settled.objects),
                    "min_visible_fraction": round(min(evidence_vis), 4) if evidence_vis else 1.0,
                }
                if q.result.margin is not None:
                    difficulty["margin_m"] = round(q.result.margin, 4)
                task = build_task(q, settled, scene_hash, gates, versions, difficulty)
                accepted.append(write_task(lay, task, rdir / cam_out.beauty))
                by_family[q.family]["accepted"] += 1

    write_jsonl(lay.task_manifest, accepted)
    write_jsonl(lay.rejected, rejected)
    reasons = Counter(f"{r.stage}:{r.reason}" for r in rejected)
    candidates = sum(c["candidates"] for c in by_family.values())
    report = {
        "scenes": len(lay.scene_ids()),
        "candidates": candidates,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "gates": {
            g: {
                "run": s["run"],
                "pass": s["pass"],
                "pass_rate": round(s["pass"] / s["run"], 4) if s["run"] else None,
            }
            for g, s in gate_stats.items()
        },
        "families": {f: dict(c) for f, c in sorted(by_family.items())},
        "rejection_reasons": dict(reasons.most_common()),
        "per_scene": scene_reports,
    }
    lay.validate_report.write_text(json.dumps(report, indent=2) + "\n")
    _print_summary(report)
    return report


def _print_summary(report: dict) -> None:
    print(
        f"validate: {report['scenes']} scenes, {report['candidates']} candidate questions, "
        f"{report['accepted']} tasks accepted, {report['rejected']} rejections"
    )
    for g, s in report["gates"].items():
        rate = "n/a" if s["pass_rate"] is None else f"{s['pass_rate']:.0%}"
        print(f"  {g}: {s['pass']}/{s['run']} pass ({rate})")
    for f, c in report["families"].items():
        print(f"  {f}: {c.get('accepted', 0)} accepted of {c.get('candidates', 0)}")
    for reason, n in report["rejection_reasons"].items():
        print(f"  rejected {n} x {reason}")
