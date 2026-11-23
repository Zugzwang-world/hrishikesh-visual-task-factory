"""`vtf validate <run>`: incremental, resumable validation, then a collect step.

Per scene (`validate_scene`): compile questions on the settled scene for every camera, run gates
G1, G2, G3, G4 and G7 on each candidate, package the tasks that pass (tasks/<task_id>/, image as
a hard link) and write the scene's results under scenes/<scene_id>/validate/:

    accepted.jsonl   ManifestRow per accepted task of this scene
    rejected.jsonl   RejectedRow per rejection, with its stage and reason
    g6.json          G6 decisions and twin results for this scene's tasks (vtf.validate.gates.g6)
    report.json      gate counts, per-camera render checks, and the validation key (written last)

A scene is skipped when its report.json carries the same validation key: the hash of the
settled scene file, the render manifest, the sampled scene file and the code versions (vtf,
programs, validate). A crash leaves at most one scene half-done, and it is simply redone.

`collect` rebuilds the run-level manifest.jsonl, rejected.jsonl and validate_report.json from the
per-scene results (twin tasks included when their original's G6 passed) and prunes task folders
no scene refers to. `validate_run` validates whatever is rendered and not yet validated, runs G6
twins on a sample when asked, then collects.
"""

import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from vtf import __version__ as VTF_VERSION
from vtf.core import layout
from vtf.core.hashing import sha256_array, sha256_bytes, sha256_file, sha256_json
from vtf.core.io import read_jsonl, read_manifest, read_model, read_scene, read_task, write_model
from vtf.core.program import CompiledQuestion
from vtf.core.render_manifest import RenderManifest
from vtf.core.rng import derive_seed
from vtf.core.scene import RunInfo, SceneSpec
from vtf.core.task import GateResult, ManifestRow, RejectedRow
from vtf.package.writer import build_task, task_difficulty, task_versions, write_task
from vtf.validate.gates import g3_evidence_visible as g3
from vtf.validate.gates.g1_truth import check_g1
from vtf.validate.gates.g2_second_computation import check_g2, render_vs_scene
from vtf.validate.gates.g4_no_near_ties import check_g4
from vtf.validate.gates.g7_determinism import check_g7
from vtf.validate.pixels import CameraPasses

VALIDATE_VERSION = "0.2.0"
GATES = ("G1", "G2", "G3", "G4", "G7")  # per question at validate time; G6 comes from twins
ALL_GATES = ("G1", "G2", "G3", "G4", "G6", "G7")
SCENE_VALIDATE_DIR = "validate"
SCENE_ACCEPTED = "accepted.jsonl"
SCENE_REJECTED = "rejected.jsonl"
SCENE_REPORT = "report.json"
G6_FILE = "g6.json"
# A scene is used only if the settle came to rest (expert, 23 Nov 2026).
MAX_SETTLE_DRIFT_M = 0.03
MAX_SUPPORT_GAP_M = 0.002
# Render-vs-scene ray cast: every scene when all objects are primitives (about 0.8 s per camera
# at 1024x768); a seeded 20% of scenes once asset meshes are in (brief 05 fallback).
RAYCAST_FRACTION_ASSETS = 0.2


# ---------------------------------------------------------------------------------------------
# small file helpers


def scene_validate_dir(scene_dir: Path) -> Path:
    return Path(scene_dir) / SCENE_VALIDATE_DIR


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def atomic_write_jsonl(path: Path, rows: list) -> None:
    lines = []
    for row in rows:
        data = row.model_dump(mode="json", by_alias=True) if hasattr(row, "model_dump") else row
        lines.append(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")
    atomic_write_text(path, "".join(lines))


def read_rows(path: Path) -> list[dict]:
    return list(read_jsonl(path)) if Path(path).exists() else []


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_g6(vdir: Path) -> dict[str, dict]:
    return read_json(Path(vdir) / G6_FILE, {}) or {}


def save_g6(vdir: Path, records: dict[str, dict]) -> None:
    atomic_write_text(
        Path(vdir) / G6_FILE, json.dumps(dict(sorted(records.items())), indent=2) + "\n"
    )


def g6_gate(record: dict | None) -> GateResult:
    """The G6 gate a task carries, from its G6 record (absent: not sampled)."""
    if not record or record.get("status") in (None, "pending"):
        return GateResult(passed=True, evidence={"status": "not_sampled"})
    evidence = {k: v for k, v in record.items() if k != "passed"}
    return GateResult(passed=record.get("status") != "unchanged", evidence=evidence)


# ---------------------------------------------------------------------------------------------
# scene-level checks


def settle_problems(manifest: RenderManifest, settled: SceneSpec | None = None) -> dict:
    """Unsettled or fallen scenes. The support gap is checked for objects placed on the table
    only (stacked and contained objects rest on other objects, stage 3)."""
    s = manifest.settle
    problems: dict[str, Any] = {}
    if s.moving_at_end:
        problems["moving_at_end"] = s.moving_at_end
    if s.off_table:
        problems["off_table"] = s.off_table
    if s.max_displacement_m > MAX_SETTLE_DRIFT_M:
        problems["drift_m"] = s.max_displacement_m
    on_table = None
    if settled is not None:
        on_table = {o.id for o in settled.objects if o.placement.kind == "table"}
    gaps = {
        k: v
        for k, v in s.support_gap_m.items()
        if abs(v) > MAX_SUPPORT_GAP_M and (on_table is None or k in on_table)
    }
    if gaps:
        problems["support_gap_m"] = gaps
    return problems


def question_seed(settled: SceneSpec, camera_id: str) -> int:
    return derive_seed(settled.meta.seed, "questions", camera_id)


def regenerate_scene_bytes(run_info: RunInfo | None, run_dir: Path, scene_id: str) -> bytes | None:
    """Re-sample a scene from its recipe and seed; None when the recipe is not available."""
    if run_info is None or scene_id not in run_info.scenes or not Path(run_info.recipe).is_file():
        return None
    try:
        from vtf.scene.generate import regenerate_scene_json
    except ImportError:
        return None
    return regenerate_scene_json(run_info, scene_id)


def scene_determinism(
    scene_file: Path,
    render_dir: Path,
    manifest: RenderManifest,
    index: np.ndarray,
    depth: np.ndarray,
    regenerated: bytes | None,
) -> dict:
    scene_sha = sha256_file(scene_file)
    return {
        "scene_sha256": scene_sha,
        "settled_sha256": sha256_file(render_dir / manifest.settled_scene_file),
        "index_sha256": sha256_array(index),
        "depth_sha256": sha256_array(depth),
        "render_matches_scene_file": manifest.scene_sha256 == scene_sha,
        "scene_regenerates_identically": (
            None if regenerated is None else regenerated == Path(scene_file).read_bytes()
        ),
    }


def code_versions() -> dict:
    try:
        from vtf.programs import PROGRAMS_VERSION
    except ImportError:
        PROGRAMS_VERSION = "missing"  # noqa: N806
    return {"vtf": VTF_VERSION, "programs": PROGRAMS_VERSION, "validate": VALIDATE_VERSION}


def validation_key(scene_file: Path, render_dir: Path) -> str:
    manifest_path = render_dir / layout.RENDER_MANIFEST
    manifest = read_manifest(manifest_path)
    return sha256_json(
        {
            "scene": sha256_file(scene_file),
            "manifest": sha256_file(manifest_path),
            "settled": sha256_file(render_dir / manifest.settled_scene_file),
            "versions": code_versions(),
        }
    )


def is_rendered(scene_file: Path, render_dir: Path) -> bool:
    """A finished render made from this exact scene file (G7: render_matches_scene_file)."""
    mpath = render_dir / layout.RENDER_MANIFEST
    if not mpath.exists() or not Path(scene_file).exists():
        return False
    try:
        manifest = read_manifest(mpath)
        return (
            manifest.scene_sha256 == sha256_file(scene_file)
            and (render_dir / manifest.settled_scene_file).exists()
        )
    except Exception:  # unreadable manifest: not rendered
        return False


def raycast_selected(scene_id: str, settled: SceneSpec, fraction: float | None = None) -> bool:
    if fraction is None:
        if all(o.source == "primitive" for o in settled.objects):
            return True
        fraction = RAYCAST_FRACTION_ASSETS
    u = int(sha256_bytes(f"raycast/{scene_id}".encode())[:8], 16) / 0xFFFFFFFF
    return u < fraction


# ---------------------------------------------------------------------------------------------
# per-question gates and packaging


def question_gates(
    q: CompiledQuestion,
    settled: SceneSpec,
    passes: CameraPasses,
    vis: dict,
    hashes: dict,
    rig: str,
    render_check: dict | None,
) -> dict[str, GateResult]:
    assert q.result is not None
    pass_index_of = {o.id: o.pass_index for o in settled.objects}
    return {
        "G1": check_g1(q, settled),
        "G2": check_g2(q, settled, passes, render_check),
        "G3": g3.check_g3(
            q.result.evidence_object_ids,
            pass_index_of,
            vis,
            family=q.family,
            params=q.program.params,
            scene=settled,
        ),
        "G4": check_g4(q, settled, rig, passes),
        "G7": check_g7(q.result.answer, hashes),
    }


def min_visible_fraction(q: CompiledQuestion, settled: SceneSpec, vis: dict) -> float:
    pass_index_of = {o.id: o.pass_index for o in settled.objects}
    fr = [
        vis[pass_index_of[o]].fraction for o in q.result.evidence_object_ids if o in pass_index_of
    ]
    return min(fr) if fr else 1.0


class CameraContext:
    """Everything one camera of one rendered scene gives the gates."""

    def __init__(self, settled: SceneSpec, render_dir: Path, cam_out, hashes_fn, do_raycast: bool):
        self.cam = settled.camera(cam_out.camera_id)
        self.rig = self.cam.rig
        self.out = cam_out
        self.passes = CameraPasses.load(render_dir, self.cam, cam_out)
        self.vis = g3.visibility_table(
            self.passes.index, self.passes.amodal, [o.pass_index for o in settled.objects]
        )
        self.render_check = render_vs_scene(settled, self.passes, do_raycast)
        self.hashes = hashes_fn(self.passes.index, self.passes.depth)

    def summary(self, settled: SceneSpec, manifest: RenderManifest) -> dict:
        return {
            "rig": self.rig,
            "render_agreement": self.render_check,
            "determinism": self.hashes,
            "off_table": manifest.settle.off_table,
            "visibility": {
                o.id: {
                    "visible_pixels": self.vis[o.pass_index].visible_pixels,
                    "visible_fraction": round(self.vis[o.pass_index].fraction, 4),
                }
                for o in settled.objects
            },
        }


def _rejected(
    q: CompiledQuestion, scene_id: str, stage: str, reason: str, detail: dict
) -> RejectedRow:
    return RejectedRow(
        scene_id=scene_id,
        camera_id=q.camera_id,
        family=q.family,
        template_id=q.template_id,
        question=q.question,
        stage=stage,
        reason=reason,
        detail=detail,
    )


def _remove_tasks(lay: layout.RunLayout, task_ids) -> None:
    import shutil

    for tid in task_ids:
        tdir = lay.task_dir(tid)
        if tdir.exists():
            shutil.rmtree(tdir)


# ---------------------------------------------------------------------------------------------
# one scene


def validate_scene(
    lay: layout.RunLayout,
    scene_id: str,
    run_info: RunInfo | None = None,
    force: bool = False,
    raycast_fraction: float | None = None,
) -> str:
    """Validate one rendered scene; returns validated, skipped, not_rendered or settle."""
    from vtf.programs import PROGRAMS_VERSION, compile_questions

    t0 = time.perf_counter()
    scene_file, rdir = lay.scene_file(scene_id), lay.render_dir(scene_id)
    vdir = scene_validate_dir(lay.scene_dir(scene_id))
    if not is_rendered(scene_file, rdir):
        return "not_rendered"
    key = validation_key(scene_file, rdir)
    previous = read_json(vdir / SCENE_REPORT, {}) or {}
    old_rows = read_rows(vdir / SCENE_ACCEPTED)
    if (
        not force
        and previous.get("key") == key
        and all((lay.root / r["path"]).exists() for r in old_rows)
    ):
        return "skipped"

    manifest = read_manifest(rdir / layout.RENDER_MANIFEST)
    settled = read_scene(rdir / manifest.settled_scene_file)
    g6 = load_g6(vdir)
    accepted: list[ManifestRow] = []
    rejected: list[RejectedRow] = []
    gate_stats = {g: Counter() for g in GATES}
    families: dict[str, Counter] = defaultdict(Counter)
    cameras: dict[str, dict] = {}
    status = "validated"

    unsettled = settle_problems(manifest, settled)
    if unsettled:
        status = "settle"
        rejected.append(
            RejectedRow(
                scene_id=scene_id,
                stage="settle",
                reason="settle_" + "_".join(sorted(unsettled)),
                detail=unsettled,
            )
        )
    else:
        scene_hash = sha256_json(settled)
        versions = task_versions(settled, manifest, PROGRAMS_VERSION)
        regenerated = regenerate_scene_bytes(run_info, lay.root, scene_id)
        do_raycast = raycast_selected(scene_id, settled, raycast_fraction)

        def hashes_fn(index, depth):
            return scene_determinism(scene_file, rdir, manifest, index, depth, regenerated)

        for cam_out in manifest.cameras:
            ctx = CameraContext(settled, rdir, cam_out, hashes_fn, do_raycast)
            cameras[cam_out.camera_id] = ctx.summary(settled, manifest)
            if not ctx.render_check["camera_ok"]:
                rejected.append(
                    RejectedRow(
                        scene_id=scene_id,
                        camera_id=cam_out.camera_id,
                        stage="render",
                        reason="camera_mismatch",
                        detail=ctx.render_check,
                    )
                )
                continue
            questions = compile_questions(
                settled, cam_out.camera_id, question_seed(settled, cam_out.camera_id)
            )
            for q in questions:
                families[q.family]["candidates"] += 1
                if q.rejected or q.result is None:
                    rejected.append(
                        _rejected(
                            q, scene_id, "compile", q.rejected or "no_result", q.rejected_detail
                        )  # fmt: skip
                    )
                    continue
                gates = question_gates(
                    q, settled, ctx.passes, ctx.vis, ctx.hashes, ctx.rig, ctx.render_check
                )
                for g, res in gates.items():
                    gate_stats[g]["run"] += 1
                    gate_stats[g]["pass"] += int(res.passed)
                failed = [g for g, res in gates.items() if not res.passed]
                if failed:
                    first = failed[0]
                    rejected.append(
                        _rejected(
                            q,
                            scene_id,
                            first,
                            gates[first].evidence.get("reason", "failed"),
                            {"failed_gates": failed, **gates[first].evidence},
                        )  # fmt: skip
                    )
                    continue
                difficulty = task_difficulty(
                    settled, q, ctx.rig, min_visible_fraction(q, settled, ctx.vis)
                )
                task = build_task(q, settled, scene_hash, gates, versions, difficulty, ctx.rig)
                record = g6.get(task.task_id)
                task.gates["G6"] = g6_gate(record)
                if record and record.get("status") == "changed":
                    task.twin_id = record.get("twin_task_id")
                if record and record.get("status") == "unchanged":
                    rejected.append(
                        _rejected(
                            q,
                            scene_id,
                            "G6",
                            "answer_unchanged_on_twin",
                            {"task_id": task.task_id, **record},
                        )  # fmt: skip
                    )
                    continue
                accepted.append(write_task(lay, task, rdir / cam_out.beauty))

    new_ids = {r.task_id for r in accepted}
    _remove_tasks(lay, [r["task_id"] for r in old_rows if r["task_id"] not in new_ids])
    g6_rejected = {r.detail.get("task_id") for r in rejected if r.stage == "G6"}
    kept = {t: r for t, r in g6.items() if t in new_ids or t in g6_rejected}
    if kept != g6:
        save_g6(vdir, kept)
    atomic_write_jsonl(vdir / SCENE_ACCEPTED, accepted)
    atomic_write_jsonl(vdir / SCENE_REJECTED, rejected)
    report = {
        "validate_version": VALIDATE_VERSION,
        "key": key,
        "scene_id": scene_id,
        "status": status,
        "seconds": round(time.perf_counter() - t0, 3),
        "gates": {g: dict(c) for g, c in gate_stats.items()},
        "families": {f: dict(c) for f, c in sorted(families.items())},
        "cameras": cameras,
    }
    atomic_write_text(vdir / SCENE_REPORT, json.dumps(report, indent=2) + "\n")
    return status


def refresh_g6(lay: layout.RunLayout, scene_id: str) -> None:
    """Apply this scene's G6 records to its packaged tasks: G6 gate and twin_id on each task; a
    task whose valid twin kept the same answer moves to the rejected log (stage G6)."""
    vdir = scene_validate_dir(lay.scene_dir(scene_id))
    if not (vdir / SCENE_REPORT).exists():
        return
    g6 = load_g6(vdir)
    rows = read_rows(vdir / SCENE_ACCEPTED)
    rejected = read_rows(vdir / SCENE_REJECTED)
    keep = []
    for row in rows:
        tpath = lay.root / row["path"]
        if not tpath.exists():
            continue
        task = read_task(tpath)
        record = g6.get(task.task_id)
        if record and record.get("status") == "unchanged":
            rejected.append(
                RejectedRow(
                    scene_id=task.scene_id, camera_id=task.camera_id, family=task.family,
                    template_id=task.template_id, question=task.question, stage="G6",
                    reason="answer_unchanged_on_twin", detail={"task_id": task.task_id, **record},
                ).model_dump(mode="json")
            )  # fmt: skip
            _remove_tasks(lay, [task.task_id])
            continue
        gate = g6_gate(record)
        twin_id = (
            record.get("twin_task_id") if record and record.get("status") == "changed" else None
        )
        if task.gates.get("G6") != gate or task.twin_id != twin_id:
            task.gates["G6"] = gate
            task.twin_id = twin_id
            write_model(tpath, task)
        keep.append(row)
    atomic_write_jsonl(vdir / SCENE_ACCEPTED, keep)
    atomic_write_jsonl(vdir / SCENE_REJECTED, rejected)


# ---------------------------------------------------------------------------------------------
# collect


def collect(run_dir: Path, prune: bool = True, quiet: bool = False) -> dict:
    """Rebuild manifest.jsonl, rejected.jsonl and validate_report.json from per-scene results."""
    lay = layout.RunLayout(run_dir)
    twins = lay.twin_layout()
    accepted: list[dict] = []
    rejected: list[dict] = []
    gate_stats = {g: Counter() for g in GATES}
    families: dict[str, Counter] = defaultdict(Counter)
    statuses: Counter = Counter()
    per_scene: dict[str, dict] = {}
    g6_stats: Counter = Counter()
    g6_reasons: Counter = Counter()
    twin_ok: dict[str, str] = {}  # twin task id -> original task id

    for sid in lay.scene_ids():
        vdir = scene_validate_dir(lay.scene_dir(sid))
        rep = read_json(vdir / SCENE_REPORT)
        if rep is None:
            statuses["not_validated" if is_rendered(lay.scene_file(sid), lay.render_dir(sid))
                     else "not_rendered"] += 1  # fmt: skip
            if not lay.render_manifest(sid).exists():
                rejected.append(
                    RejectedRow(scene_id=sid, stage="render", reason="not_rendered").model_dump(
                        mode="json"
                    )
                )
            continue
        statuses[rep.get("status", "validated")] += 1
        rows = read_rows(vdir / SCENE_ACCEPTED)
        accepted.extend(rows)
        rejected.extend(read_rows(vdir / SCENE_REJECTED))
        for g, c in rep.get("gates", {}).items():
            gate_stats.setdefault(g, Counter()).update(c)
        for f, c in rep.get("families", {}).items():
            families[f]["candidates"] += c.get("candidates", 0)
        for cam_id, cam in rep.get("cameras", {}).items():
            per_scene[f"{sid}/{cam_id}"] = cam
        accepted_ids = {r["task_id"] for r in rows}
        for tid, rec in load_g6(vdir).items():
            status = rec.get("status", "pending")
            g6_stats[status] += 1
            if rec.get("reason"):
                g6_reasons[f"{status}:{rec['reason']}"] += 1
            if status == "changed" and tid in accepted_ids and rec.get("twin_task_id"):
                twin_ok[rec["twin_task_id"]] = tid

    twin_rows = []
    for tsid in twins.scene_ids():
        for row in read_rows(scene_validate_dir(twins.scene_dir(tsid)) / SCENE_ACCEPTED):
            if row["task_id"] in twin_ok and (lay.root / row["path"]).exists():
                twin_rows.append(row)
    accepted.extend(twin_rows)

    twin_ids = {r["task_id"] for r in twin_rows}
    for row in accepted:
        families[row["family"]]["twin_tasks" if row["task_id"] in twin_ids else "accepted"] += 1
    for row in rejected:
        if row.get("family"):
            families[row["family"]][f"rejected_{row['stage']}"] += 1

    write_rows(lay.task_manifest, accepted)
    write_rows(lay.rejected, rejected)
    if prune:
        keep = {r["task_id"] for r in accepted}
        if lay.tasks_dir.exists():
            _remove_tasks(
                lay, [p.name for p in lay.tasks_dir.iterdir() if p.is_dir() and p.name not in keep]
            )

    sampled = sum(v for k, v in g6_stats.items() if k not in ("not_sampled",))
    valid = g6_stats["changed"] + g6_stats["unchanged"]
    gates_out = {
        g: {
            "run": s["run"],
            "pass": s["pass"],
            "pass_rate": round(s["pass"] / s["run"], 4) if s["run"] else None,
        }
        for g, s in gate_stats.items()
    }
    gates_out["G6"] = {
        "run": valid,
        "pass": g6_stats["changed"],
        "pass_rate": round(g6_stats["changed"] / valid, 4) if valid else None,
        "sampled": sampled,
        "statuses": dict(sorted(g6_stats.items())),
        "reasons": dict(g6_reasons.most_common()),
    }
    reasons = Counter(f"{r['stage']}:{r['reason']}" for r in rejected)
    originals = len(accepted) - len(twin_rows)
    rigs = Counter(r.get("rig", "front") for r in accepted)
    conflicts = Counter(
        str(r.get("shortcut_conflict")) for r in accepted if r.get("shortcut_conflict") is not None
    )
    report = {
        "validate_version": VALIDATE_VERSION,
        "scenes": len(lay.scene_ids()),
        "scene_status": dict(sorted(statuses.items())),
        "candidates": sum(c["candidates"] for c in families.values()),
        "accepted": originals,
        "twin_tasks": len(twin_rows),
        "tasks_in_manifest": len(accepted),
        "rejected": len(rejected),
        "gates": dict(sorted(gates_out.items())),
        "rigs": dict(sorted(rigs.items())),
        "shortcut_conflict": dict(sorted(conflicts.items())),
        "families": {f: dict(c) for f, c in sorted(families.items())},
        "rejection_reasons": dict(reasons.most_common()),
        "per_scene": per_scene,
    }
    atomic_write_text(lay.validate_report, json.dumps(report, indent=2) + "\n")
    if not quiet:
        _print_summary(report)
    return report


def write_rows(path: Path, rows: list[dict]) -> None:
    atomic_write_jsonl(path, rows)


# ---------------------------------------------------------------------------------------------
# run


def validate_run(
    run_dir: Path,
    force: bool = False,
    twins: bool = False,
    twin_fraction: float = 0.2,
    device: str = "gpu",
    scene_ids: list[str] | None = None,
    raycast_fraction: float | None = None,
) -> dict:
    """Validate every rendered, not yet validated scene; G6 twins on a sample; collect."""
    run_dir = Path(run_dir)
    lay = layout.RunLayout(run_dir)
    run_info = read_model(lay.run_file, RunInfo) if lay.run_file.exists() else None
    ids = scene_ids if scene_ids is not None else lay.scene_ids()
    counts: Counter = Counter()
    for sid in ids:
        counts[
            validate_scene(lay, sid, run_info, force=force, raycast_fraction=raycast_fraction)
        ] += 1
    print(f"validate: {dict(counts)}", flush=True)
    if twins:
        from vtf.validate.gates.g6_counterfactual_twin import run_g6

        run_g6(lay, ids, fraction=twin_fraction, device=device)
    return collect(run_dir)


def _print_summary(report: dict) -> None:
    print(
        f"validate: {report['scenes']} scenes, {report['candidates']} candidate questions, "
        f"{report['accepted']} tasks accepted (+{report['twin_tasks']} twin tasks), "
        f"{report['rejected']} rejections"
    )
    for g, s in report["gates"].items():
        rate = "n/a" if s["pass_rate"] is None else f"{s['pass_rate']:.0%}"
        extra = f", {s['sampled']} sampled" if g == "G6" else ""
        print(f"  {g}: {s['pass']}/{s['run']} pass ({rate}){extra}")
    for f, c in report["families"].items():
        print(f"  {f}: {c.get('accepted', 0)} accepted of {c.get('candidates', 0)}")
    for reason, n in list(report["rejection_reasons"].items())[:15]:
        print(f"  rejected {n} x {reason}")
