"""G6 Counterfactual twin: perturb one object, re-render, the answer must change.

For a stratified sample of accepted tasks (default 20%, by family x rig, at least one per stratum
present), the family's twin rule (`vtf.programs.make_twin`) moves or removes one evidence object.
The twin scene is the settled scene with that edit (other objects keep their settled poses,
meta.settled False, meta.twin_of the original scene, only the task's camera), written to
<run>/twins/<twin_scene_id>/scene.json and rendered (and re-settled) through the bridge. The same
program then runs on the twin's settled scene, and gates G1, G2, G3, G4 and G7 run on the twin's
passes.

Statuses recorded per task in scenes/<scene_id>/validate/g6.json:

- not_sampled: not in the sample (G6 passes, nothing checked);
- no_twin_rule: make_twin returned None (G6 passes, nothing checked);
- twin_invalid: the twin did not render, settle or pass its own gates (G6 passes, no evidence);
- unchanged: a valid twin gave the same answer: the original is rejected (stage G6);
- changed: a valid twin changed the answer. G6 passes with the twin's task id and both answers;
  the twin is packaged as its own task (twin_of) and the original gets twin_id.

The G6 pass rate reported is changed / (changed + unchanged).
"""

import json
import math
from collections import defaultdict

import numpy as np

from vtf.core import layout
from vtf.core.assets import object_mesh
from vtf.core.hashing import sha256_bytes, sha256_json
from vtf.core.io import model_json, read_manifest, read_scene, read_task
from vtf.core.program import CompiledQuestion, ProgramReject, TwinEdit
from vtf.core.rng import derive_seed
from vtf.core.scene import Placement, SceneSpec
from vtf.core.task import GateResult, TaskPackage
from vtf.package.writer import (
    build_task,
    task_difficulty,
    task_versions,
    visible_object_ids,
    write_task,
)
from vtf.validate import pipeline as pl

DEFAULT_FRACTION = 0.2
TWIN_GATES = ("G1", "G2", "G3", "G4", "G7")
TABLE_PLACE_TOL_M = 0.03


def twin_scene_id(scene_id: str, task_id: str) -> str:
    return f"{scene_id}_twin_{task_id.removeprefix('t')[:10]}"


def twin_seed(settled: SceneSpec, task_id: str) -> int:
    return derive_seed(settled.meta.seed, "twin", task_id)


def stratified_sample(rows: list[dict], fraction: float) -> set[str]:
    """Per (family, rig) stratum, the ceil(fraction x n) tasks with the smallest seeded hash."""
    if fraction <= 0:
        return set()
    strata: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        strata[(r["family"], r.get("rig", "front"))].append(r["task_id"])
    picked: set[str] = set()
    for ids in strata.values():
        k = min(len(ids), math.ceil(fraction * len(ids) - 1e-9))
        picked.update(sorted(ids, key=lambda t: sha256_bytes(f"g6/{t}".encode()))[:k])
    return picked


def _placement_for(scene: SceneSpec, obj_id: str) -> Placement:
    """The sampler-intent placement of a moved object: on the table when it sits near the top,
    else on (or in) the highest object under its centre."""
    obj = scene.object(obj_id)
    mesh = object_mesh(obj)
    lowest = float(mesh.bounds[0][2])
    if lowest - scene.table.height < TABLE_PLACE_TOL_M:
        return Placement(kind="table")
    x, y = obj.pose.position[0], obj.pose.position[1]
    best, best_top = None, -math.inf
    for other in scene.objects:
        if other.id == obj_id:
            continue
        lo, hi = object_mesh(other).bounds
        if (
            lo[0] <= x <= hi[0]
            and lo[1] <= y <= hi[1]
            and hi[2] <= lowest + 0.01
            and hi[2] > best_top
        ):
            best, best_top = other, float(hi[2])
    if best is None:
        return Placement(kind="table")
    from vtf.validate.gates.g2_second_computation import container_info

    return Placement(kind="in" if container_info(best) else "on", parent_id=best.id)


def apply_edit(settled: SceneSpec, edit: TwinEdit, twin_id: str, camera_id: str) -> SceneSpec:
    """The twin scene file: settled poses, one object moved or removed, one camera."""
    data = settled.model_dump(mode="json")
    objects = data["objects"]
    if edit.remove:
        objects = [o for o in objects if o["id"] != edit.object_id]
        for o in objects:
            if o["placement"].get("parent_id") == edit.object_id:
                o["placement"] = {"kind": "table", "parent_id": None}
    else:
        if edit.new_pose is None:
            raise ValueError("TwinEdit without new_pose must set remove")
        for o in objects:
            if o["id"] == edit.object_id:
                o["pose"] = edit.new_pose.model_dump(mode="json")
    data["objects"] = objects
    data["meta"].update(scene_id=twin_id, settled=False, twin_of=settled.meta.scene_id)
    data["cameras"] = [c for c in data["cameras"] if c["id"] == camera_id]
    twin = SceneSpec.model_validate(data)
    if not edit.remove:
        placement = _placement_for(twin, edit.object_id)
        for o in twin.objects:
            if o.id == edit.object_id:
                o.placement = placement
        twin = SceneSpec.model_validate(twin.model_dump(mode="json"))
    return twin


def make_twin_scene(
    settled: SceneSpec, task: TaskPackage
) -> tuple[SceneSpec | None, TwinEdit | None, str]:
    """(twin scene, edit, '') or (None, None, reason)."""
    try:
        from vtf.programs import make_twin
    except ImportError:
        return None, None, "no_make_twin"
    edit = make_twin(
        task.program.name, dict(task.program.params), settled, task.camera_id,
        twin_seed(settled, task.task_id),
    )  # fmt: skip
    if edit is None:
        return None, None, "no_twin_rule"
    twin = apply_edit(
        settled, edit, twin_scene_id(settled.meta.scene_id, task.task_id), task.camera_id
    )
    return twin, edit, ""


def scene_bytes(scene: SceneSpec) -> bytes:
    return model_json(scene).encode("utf-8")


# ---------------------------------------------------------------------------------------------
# planning


def plan(lay: layout.RunLayout, scene_ids: list[str], fraction: float) -> list[tuple[str, str]]:
    """Record the G6 decision for every accepted task without one; return pending (scene, task)."""
    new_rows, records = [], {}
    for sid in scene_ids:
        vdir = pl.scene_validate_dir(lay.scene_dir(sid))
        if not (vdir / pl.SCENE_REPORT).exists():
            continue
        records[sid] = pl.load_g6(vdir)
        new_rows += [
            r for r in pl.read_rows(vdir / pl.SCENE_ACCEPTED) if r["task_id"] not in records[sid]
        ]
    picked = stratified_sample(new_rows, fraction)
    by_scene = defaultdict(list)
    for r in new_rows:
        by_scene[r["scene_id"]].append(r["task_id"])
    for sid, tids in by_scene.items():
        for tid in tids:
            records[sid][tid] = {"status": "pending" if tid in picked else "not_sampled"}
        pl.save_g6(pl.scene_validate_dir(lay.scene_dir(sid)), records[sid])
    return [(sid, tid) for sid, recs in records.items() for tid, r in sorted(recs.items())
            if r.get("status") == "pending"]  # fmt: skip


# ---------------------------------------------------------------------------------------------
# evaluation


def _moved_distance(original: SceneSpec, twin_settled: SceneSpec, edit: TwinEdit) -> dict:
    if edit.remove:
        return {"removed": True, "distance_moved_m": None}
    a = np.asarray(original.object(edit.object_id).pose.position)
    b = np.asarray(twin_settled.object(edit.object_id).pose.position)
    return {
        "removed": False,
        "distance_moved_m": round(float(np.linalg.norm(b - a)), 4),
        "distance_moved_xy_m": round(float(np.linalg.norm(b[:2] - a[:2])), 4),
    }


def evaluate_twin(
    lay: layout.RunLayout,
    task: TaskPackage,
    settled: SceneSpec,
    twin: SceneSpec,
    edit: TwinEdit,
) -> dict:
    """Run the task's program and gates on the rendered twin; package it when the answer changed."""
    from vtf.programs import PROGRAMS_VERSION, run_program

    tl = lay.twin_layout()
    tsid = twin.meta.scene_id
    scene_file, rdir = tl.scene_file(tsid), tl.render_dir(tsid)
    record = {
        "twin_scene_id": tsid,
        "moved_object": edit.object_id,
        "edit_reason": edit.reason,
        "original_answer": task.answer,
        "expected_answer": edit.expected_answer,
    }
    if not pl.is_rendered(scene_file, rdir):
        return {**record, "status": "twin_invalid", "reason": "render_failed"}
    manifest = read_manifest(rdir / layout.RENDER_MANIFEST)
    twin_settled = read_scene(rdir / manifest.settled_scene_file)
    record.update(_moved_distance(settled, twin_settled, edit))
    unsettled = pl.settle_problems(manifest, twin_settled)
    if unsettled:
        return {**record, "status": "twin_invalid", "reason": "settle", "settle": unsettled}
    try:
        result = run_program(
            task.program.name, dict(task.program.params), twin_settled, task.camera_id
        )
    except ProgramReject as exc:
        return {**record, "status": "twin_invalid", "reason": f"program_rejected:{exc.reason}"}
    except (KeyError, ValueError) as exc:  # e.g. a removed object named in the params
        return {**record, "status": "twin_invalid", "reason": f"program_error:{exc}"[:200]}
    record["twin_answer"] = result.answer
    q = CompiledQuestion(
        family=task.family, template_id=task.template_id, question=task.question,
        question_alt=task.question_alt, camera_id=task.camera_id, program=task.program,
        result=result,
    )  # fmt: skip
    regenerated, _, _ = make_twin_scene(settled, task)
    regen_bytes = scene_bytes(regenerated) if regenerated is not None else None

    def hashes_fn(index, depth):
        return pl.scene_determinism(scene_file, rdir, manifest, index, depth, regen_bytes)

    cam_out = manifest.camera(task.camera_id)
    ctx = pl.CameraContext(twin_settled, rdir, cam_out, hashes_fn, do_raycast=True)
    gates = pl.question_gates(
        q, twin_settled, ctx.passes, ctx.vis, ctx.hashes, ctx.rig, ctx.render_check
    )
    record["twin_gates"] = {g: r.passed for g, r in gates.items()}
    failed = [g for g in TWIN_GATES if not gates[g].passed]
    if failed:
        reason = gates[failed[0]].evidence.get("reason", "failed")
        return {**record, "status": "twin_invalid", "reason": f"twin_{failed[0]}:{reason}"}
    if sha256_json(result.answer) == sha256_json(task.answer):
        return {**record, "status": "unchanged", "reason": "answer_unchanged"}

    versions = task_versions(twin_settled, manifest, PROGRAMS_VERSION)
    difficulty = task_difficulty(
        twin_settled, q, ctx.rig, pl.min_visible_fraction(q, twin_settled, ctx.vis)
    )
    difficulty["twin"] = 1
    twin_task = build_task(q, twin_settled, sha256_json(twin_settled), gates, versions, difficulty,
                           ctx.rig, twin_of=task.task_id,
                           visible_ids=visible_object_ids(twin_settled, ctx.vis))  # fmt: skip
    twin_task.gates["G6"] = GateResult(
        passed=True, evidence={"status": "is_twin", "twin_of": task.task_id, **{
            k: record[k] for k in ("moved_object", "original_answer", "twin_answer")}},
    )  # fmt: skip
    row = write_task(lay, twin_task, rdir / cam_out.beauty)
    vdir = pl.scene_validate_dir(tl.scene_dir(tsid))
    pl.atomic_write_jsonl(vdir / pl.SCENE_ACCEPTED, [row])
    pl.atomic_write_text(
        vdir / pl.SCENE_REPORT,
        json.dumps(
            {"scene_id": tsid, "twin_of_task": task.task_id, "status": "twin", "record": record},
            indent=2,
        )
        + "\n",  # fmt: skip
    )
    return {**record, "status": "changed", "twin_task_id": twin_task.task_id}


def run_g6(
    lay: layout.RunLayout,
    scene_ids: list[str],
    fraction: float = DEFAULT_FRACTION,
    device: str = "gpu",
    batch_size: int = 25,
    timeout: float = 300.0,
    log=print,
    keep_beauty_raw: bool = True,
) -> dict:
    """Plan, build, render and evaluate twins for these scenes; update the original tasks."""
    from vtf.validate.render_batch import render_many

    pending = plan(lay, scene_ids, fraction)
    tl = lay.twin_layout()
    jobs = []
    results: dict[str, dict[str, dict]] = defaultdict(dict)
    for sid, tid in pending:
        tpath = lay.task_dir(tid) / layout.TASK_FILE
        if not tpath.exists():
            results[sid][tid] = {"status": "twin_invalid", "reason": "task_missing"}
            continue
        task = read_task(tpath)
        settled = read_scene(lay.settled_scene(sid))
        try:
            twin, edit, reason = make_twin_scene(settled, task)
        except Exception as exc:  # a twin rule that cannot place the object
            twin, edit, reason = None, None, f"make_twin_error:{type(exc).__name__}: {exc}"[:200]
        if twin is None:
            status = (
                "no_twin_rule" if reason in ("no_twin_rule", "no_make_twin") else "twin_invalid"
            )
            results[sid][tid] = {"status": status, "reason": reason}
            continue
        sfile = tl.scene_file(twin.meta.scene_id)
        data = scene_bytes(twin)
        if not sfile.exists() or sfile.read_bytes() != data:
            sfile.parent.mkdir(parents=True, exist_ok=True)
            sfile.write_bytes(data)
        jobs.append((sid, task, settled, twin, edit))

    todo = [j for j in jobs if not pl.is_rendered(tl.scene_file(j[3].meta.scene_id),
                                                   tl.render_dir(j[3].meta.scene_id))]  # fmt: skip
    if todo:
        log(f"G6: rendering {len(todo)} twin scenes")
        render_many(
            [tl.scene_file(j[3].meta.scene_id) for j in todo],
            [tl.render_dir(j[3].meta.scene_id) for j in todo],
            device=device, batch_size=batch_size, timeout=timeout, log=log,
            keep_beauty_raw=keep_beauty_raw,
        )  # fmt: skip
    for sid, task, settled, twin, edit in jobs:
        try:
            results[sid][task.task_id] = evaluate_twin(lay, task, settled, twin, edit)
        except Exception as exc:  # one broken twin never stops the run
            reason = f"error:{type(exc).__name__}: {exc}"[:200]
            results[sid][task.task_id] = {"status": "twin_invalid", "reason": reason}

    summary: dict[str, int] = defaultdict(int)
    for sid, recs in results.items():
        vdir = pl.scene_validate_dir(lay.scene_dir(sid))
        records = pl.load_g6(vdir)
        records.update(recs)
        pl.save_g6(vdir, records)
        pl.refresh_g6(lay, sid)
        for r in recs.values():
            summary[r["status"]] += 1
    if pending:
        log(
            f"G6: {len(pending)} twins: "
            + ", ".join(f"{k} {v}" for k, v in sorted(summary.items()))
        )
    return dict(summary)
