"""`vtf twin check <twins run>`: prove gate G8 on the digital twins.

Every rendered view of every layout goes through G8 as if it were a photo (markers, pose, mat
comparison, placement), against the layout's settled scene; the render's own camera is the
ground truth for the pose error. The negative control (<run>/misplaced: one object moved 4 cm,
rendered the same way) must be rejected. Writes <run>/g8_check.json, overlays in
<run>/g8_overlays/, and the human summary docs/twins/g8_digital_check.md.
"""

import json
import statistics
from pathlib import Path

import cv2

from vtf.core.io import read_scene
from vtf.core.layout import RunLayout
from vtf.twin.digital import MISPLACED_RUN, layout_label
from vtf.twin.register import PLACEMENT_TOL_M, G8Result, overlay, run_g8, tolerances

CHECK_FILE = "g8_check.json"
OVERLAY_DIR = "g8_overlays"


def _views(run: RunLayout, scene_id: str, settled_run: RunLayout, label: str, kind: str,
           overlays: Path) -> list[dict]:  # fmt: skip
    settled = read_scene(settled_run.settled_scene(scene_id))
    truth = read_scene(run.settled_scene(scene_id))
    rows = []
    for cam in truth.cameras:
        beauty = run.render_dir(scene_id) / cam.id / "beauty.png"
        result, work = run_g8(beauty, settled, cam.id, cam.rig, truth=cam)
        if work is not None:
            tag = f"{label}_{cam.id}" + ("_misplaced" if kind == "misplaced" else "")
            cv2.imwrite(str(overlays / f"{tag}.jpg"), overlay(work, result, settled, tag),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])  # fmt: skip
        rows.append({"label": label, "scene_id": scene_id, "kind": kind, "camera_id": cam.id,
                     "result": result.model_dump(mode="json")})  # fmt: skip
        print(f"  {label} {scene_id} {kind:9s} {cam.id:8s} "
              f"{'pass' if result.passed else 'REJECT'}  {result.reason}", flush=True)
    return rows


def digital_check(run_dir: Path, selection: dict, doc: Path | None = None) -> dict:
    run = RunLayout(run_dir)
    neg = RunLayout(Path(run_dir) / MISPLACED_RUN)
    overlays = Path(run_dir) / OVERLAY_DIR
    overlays.mkdir(parents=True, exist_ok=True)
    labels = {sid: layout_label(i) for i, sid in enumerate(selection["scene_ids"])}
    rows = []
    for sid, label in labels.items():
        rows += _views(run, sid, run, label, "correct", overlays)
    control = json.loads((neg.root / "misplacement.json").read_text())
    rows += _views(neg, control["scene_id"], run, labels[control["scene_id"]], "misplaced",
                   overlays)  # fmt: skip
    summary = summarise(rows, control)
    out = {"tolerances": tolerances(), "control": control, "summary": summary, "views": rows}
    (Path(run_dir) / CHECK_FILE).write_text(json.dumps(out, indent=2) + "\n")
    if doc is not None:
        write_doc(doc, out, Path(run_dir))
    s = summary
    print(
        f"vtf twin check: correct views {s['correct_pass']}/{s['correct_views']} pass; "
        f"misplaced views {s['misplaced_reject']}/{s['misplaced_views']} rejected; pose error "
        f"median {s['pose_position_mm_median']} mm, {s['pose_rotation_deg_median']} deg"
    )
    return out


def summarise(rows: list[dict], control: dict) -> dict:
    correct = [r for r in rows if r["kind"] == "correct"]
    moved = [r for r in rows if r["kind"] == "misplaced"]
    pose = [r["result"]["pose_error"] for r in rows if r["result"]["pose_error"]]
    errs = [o["error_mm"] for r in correct for o in r["result"]["objects"] if o["measured"]]
    reproj = [r["result"]["pose"]["reprojection_rms_px"] for r in rows
              if r["result"]["pose"]["reprojection_rms_px"] is not None]  # fmt: skip
    moved_err = [o["error_mm"] for r in moved for o in r["result"]["objects"]
                 if o["object_id"] == control["object_id"] and o["measured"]]  # fmt: skip
    markers = [len(r["result"]["pose"]["marker_ids"]) for r in rows]
    return {
        "correct_views": len(correct),
        "correct_pass": sum(r["result"]["passed"] for r in correct),
        "misplaced_views": len(moved),
        "misplaced_reject": sum(not r["result"]["passed"] for r in moved),
        "pose_position_mm_median": round(statistics.median(p["position_mm"] for p in pose), 2),
        "pose_position_mm_max": round(max(p["position_mm"] for p in pose), 2),
        "pose_rotation_deg_median": round(statistics.median(p["rotation_deg"] for p in pose), 3),
        "pose_rotation_deg_max": round(max(p["rotation_deg"] for p in pose), 3),
        "reprojection_px_max": round(max(reproj), 3),
        "markers_found_min": min(markers),
        "placement_error_mm_median": round(statistics.median(errs), 1) if errs else None,
        "placement_error_mm_max": round(max(errs), 1) if errs else None,
        "objects_measured": len(errs),
        "objects_not_checked": sum(not o["measured"] for r in correct
                                   for o in r["result"]["objects"]),  # fmt: skip
        "misplaced_object_error_mm": moved_err,
    }


def write_doc(path: Path, out: dict, run_dir: Path) -> Path:
    s, c, tol = out["summary"], out["control"], out["tolerances"]
    lines = [
        "# G8 on the digital twins",
        "",
        "Gate G8 registers a photo of a physical twin onto its layout and checks that every "
        "object is where the layout sheet says. Before any real photo exists, it is proved here "
        "on the digital twins: each of the 12 layouts rendered in Blender with the printed mat on "
        "the table, from the three photo spots of the guide, and each render put through G8 as if "
        "it were a phone photo (`uv run vtf twin check out/twins_digital`). Ground truth for the "
        "pose is the render's own camera.",
        "",
        "## Result",
        "",
        f"- Correctly placed layouts: **{s['correct_pass']} of {s['correct_views']} views pass**.",
        f"- Negative control ({c['scene_id']}, the {c['object_name']} moved "
        f"{abs(c['shift_m'][0] or c['shift_m'][1]) * 100:.0f} cm): **{s['misplaced_reject']} of "
        f"{s['misplaced_views']} views rejected**; measured displacement "
        f"{', '.join(f'{e:.0f}' for e in s['misplaced_object_error_mm'])} mm.",
        f"- Camera pose from the markers: position error median {s['pose_position_mm_median']} mm "
        f"(max {s['pose_position_mm_max']}), rotation median {s['pose_rotation_deg_median']} deg "
        f"(max {s['pose_rotation_deg_max']}); marker reprojection at most "
        f"{s['reprojection_px_max']} px; at least {s['markers_found_min']} of 4 markers in every "
        "view.",
        f"- Placement error of correctly placed objects: median "
        f"{s['placement_error_mm_median']} mm, max {s['placement_error_mm_max']} mm over "
        f"{s['objects_measured']} object views; {s['objects_not_checked']} object views not "
        "checked (white on the white mat, or hidden behind or inside another object).",
        "",
        "## Tolerances",
        "",
        f"- At least {tol['min_markers']} of the 4 ArUco markers; marker-corner reprojection at "
        f"most {tol['max_reprojection_px_at_1024']} px (at 1024 px width).",
        f"- Each object within {tol['placement_m'] * 100:g} cm of its layout position (searched "
        f"+-{tol['search_m'] * 100:g} cm on the table plane), and found: at least "
        f"{tol['min_coverage']:.0%} of its outline differs from the printed mat at the best "
        "position.",
        "- Not checked automatically: white objects (the tape roll and the spray bottle look like "
        "the white paper with a shadow), and objects with under "
        f"{tol['min_measurable_fraction']:.0%} of their outline clear of other objects in that "
        "view. Their outlines are drawn on the overlay for a glance.",
        "",
        "## Per view",
        "",
        "| Layout | Scene | View | Pose error (mm, deg) | Reprojection (px) | Placement error per "
        "object (mm) | G8 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in out["views"]:
        res = G8Result.model_validate(r["result"])
        pe = res.pose_error or {}
        objs = ", ".join(
            f"{o.name} {o.error_mm:.0f}" if o.measured else f"{o.name} n/c" for o in res.objects
        )
        view = r["camera_id"] + (" (misplaced)" if r["kind"] == "misplaced" else "")
        lines.append(
            f"| {r['label']} | {r['scene_id']} | {view} | {pe.get('position_mm', '-')}, "
            f"{pe.get('rotation_deg', '-')} | {res.pose.reprojection_rms_px} | {objs} | "
            f"{'pass' if res.passed else 'reject'} |"
        )
    lines += [
        "",
        f"n/c = not checked. Overlays: {run_dir}/{OVERLAY_DIR}/ (green placed, red rejected with "
        "the measured position, yellow not checked). Data: "
        f"{run_dir}/{CHECK_FILE}.",
        "",
    ]
    path = Path(path)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


__all__ = ["digital_check", "PLACEMENT_TOL_M"]
