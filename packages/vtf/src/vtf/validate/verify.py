"""`vtf verify <run> --sample N`: rebuild sampled scenes from their seeds and check G7.

G6 twin scenes are rebuilt from the original's settled scene and the twin rule instead of the
recipe, then checked the same way.

For each sampled scene that produced tasks:

1. re-sample the scene file from the recipe and seed: bytes must be identical;
2. re-render it into <run>/verify/<scene_id>/: the settled scene file must be byte-identical, the
   decoded index, depth and amodal masks exactly equal, and the beauty image SSIM >= 0.999
   (byte-identical when both renders used the CPU);
3. re-run every task's program on the re-rendered settled scene: answers and the G7 hashes in
   each task must match.
"""

import json
from pathlib import Path

import numpy as np
from skimage.metrics import structural_similarity

from vtf.core import layout
from vtf.core.hashing import sha256_array, sha256_file, sha256_json
from vtf.core.io import (
    read_depth,
    read_index_png,
    read_jsonl,
    read_manifest,
    read_mask_png,
    read_model,
    read_rgb_png,
    read_scene,
    read_task,
)
from vtf.core.rng import derive_seed, make_rng
from vtf.core.scene import RunInfo
from vtf.validate.pipeline import regenerate_scene_bytes

MIN_SSIM = 0.999


def _scene_source(lay: layout.RunLayout, run_info, sid: str, tasks: list) -> tuple:
    """(scene file, render dir, regenerated scene bytes or None) for a scene or a G6 twin."""
    if lay.scene_file(sid).exists():
        return (
            lay.scene_file(sid),
            lay.render_dir(sid),
            regenerate_scene_bytes(run_info, lay.root, sid),
        )
    tl = lay.twin_layout()
    regenerated = None
    original_id = next((t.twin_of for t in tasks if t.twin_of), None)
    if original_id and (lay.task_dir(original_id) / layout.TASK_FILE).exists():
        from vtf.validate.gates.g6_counterfactual_twin import make_twin_scene, scene_bytes

        original = read_task(lay.task_dir(original_id) / layout.TASK_FILE)
        twin, _, _ = make_twin_scene(read_scene(lay.settled_scene(original.scene_id)), original)
        regenerated = scene_bytes(twin) if twin is not None else None
    return tl.scene_file(sid), tl.render_dir(sid), regenerated


def _compare_render(orig_dir: Path, new_dir: Path) -> dict:
    a, b = (
        read_manifest(orig_dir / layout.RENDER_MANIFEST),
        read_manifest(new_dir / layout.RENDER_MANIFEST),
    )
    out: dict = {
        "settled_identical": sha256_file(orig_dir / a.settled_scene_file)
        == sha256_file(new_dir / b.settled_scene_file),
        "cameras": {},
    }
    for cam_a in a.cameras:
        cam_b = b.camera(cam_a.camera_id)
        ia, ib = read_index_png(orig_dir / cam_a.index), read_index_png(new_dir / cam_b.index)
        da, db = read_depth(orig_dir / cam_a.depth), read_depth(new_dir / cam_b.depth)
        amodal_equal = set(cam_a.amodal) == set(cam_b.amodal) and all(
            np.array_equal(
                read_mask_png(orig_dir / cam_a.amodal[k]), read_mask_png(new_dir / cam_b.amodal[k])
            )
            for k in cam_a.amodal
        )
        ba, bb = read_rgb_png(orig_dir / cam_a.beauty), read_rgb_png(new_dir / cam_b.beauty)
        ssim = float(structural_similarity(ba, bb, channel_axis=2, data_range=255))
        out["cameras"][cam_a.camera_id] = {
            "index_equal": bool(np.array_equal(ia, ib)),
            "depth_equal": bool(np.array_equal(da, db)),
            "amodal_equal": bool(amodal_equal),
            "beauty_ssim": round(ssim, 6),
            "beauty_identical": bool(np.array_equal(ba, bb)),
            "index_sha256": sha256_array(ib),
            "depth_sha256": sha256_array(db),
        }
    return out


def verify_run(
    run_dir: Path,
    sample: int = 5,
    device: str = "gpu",
    out_dir: Path | None = None,
    scenes: list[str] | None = None,
    timeout: float | None = None,
    quiet: bool = False,
) -> dict:
    """Verify `sample` seeded scenes (or exactly `scenes`); re-renders go to out_dir (default
    <run>/verify/), so a frozen run can be checked without writing into it."""
    from vtf.programs import run_program
    from vtf.render.bridge import DEFAULT_TIMEOUT_S, render_scene

    run_dir = Path(run_dir)
    lay = layout.RunLayout(run_dir)
    verify_dir = Path(out_dir) if out_dir is not None else lay.verify_dir
    run_info = read_model(lay.run_file, RunInfo) if lay.run_file.exists() else None
    wanted = set(scenes) if scenes else None
    tasks_by_scene: dict[str, list] = {}
    for row in read_jsonl(lay.task_manifest):
        if wanted is None or row["scene_id"] in wanted:
            tasks_by_scene.setdefault(row["scene_id"], []).append(read_task(run_dir / row["path"]))
    scene_ids = sorted(tasks_by_scene)
    if wanted is not None:
        missing = sorted(wanted - set(scene_ids))
        if missing:
            raise ValueError(f"no accepted tasks for {', '.join(missing)} in {run_dir}")
        picked = scene_ids
    else:
        rng = make_rng(derive_seed(run_info.run_seed if run_info else 0, "verify"))
        size = min(sample, len(scene_ids))
        picked = sorted(rng.choice(scene_ids, size=size, replace=False).tolist())

    results = {}
    for sid in picked:
        res: dict = {}
        scene_file, render_dir, regenerated = _scene_source(lay, run_info, sid, tasks_by_scene[sid])
        res["scene_identical"] = (
            None if regenerated is None else regenerated == scene_file.read_bytes()
        )
        vdir = verify_dir / sid
        rr = render_scene(scene_file, vdir, device=device, timeout=timeout or DEFAULT_TIMEOUT_S)
        res["render_seconds"] = round(rr.seconds, 2)
        if not rr.ok:
            res.update(ok=False, error=rr.error)
            results[sid] = res
            continue
        res.update(_compare_render(render_dir, vdir))
        settled = read_scene(vdir / layout.SETTLED_SCENE)
        answers_ok, hashes_ok = True, True
        for task in tasks_by_scene[sid]:
            rerun = run_program(task.program.name, task.program.params, settled, task.camera_id)
            answers_ok &= sha256_json(rerun.answer) == sha256_json(task.answer)
            g7 = task.gates["G7"].evidence
            cam = res["cameras"][task.camera_id]
            hashes_ok &= (
                g7.get("index_sha256") == cam["index_sha256"]
                and g7.get("depth_sha256") == cam["depth_sha256"]
                and g7.get("settled_sha256") == sha256_file(vdir / layout.SETTLED_SCENE)
                and g7.get("answer_sha256") == sha256_json(rerun.answer)
            )
        res["answers_identical"] = answers_ok
        res["task_hashes_match"] = hashes_ok
        res["n_tasks"] = len(tasks_by_scene[sid])
        cams = res["cameras"].values()
        res["ok"] = bool(
            res["scene_identical"] is not False
            and res["settled_identical"]
            and all(c["index_equal"] and c["depth_equal"] and c["amodal_equal"] for c in cams)
            and all(c["beauty_ssim"] >= MIN_SSIM for c in cams)
            and answers_ok
            and hashes_ok
        )
        results[sid] = res

    report = {
        "device": device,
        "sampled": picked,
        "all_ok": all(r.get("ok") for r in results.values()) and bool(results),
        "scenes": results,
    }
    verify_dir.mkdir(parents=True, exist_ok=True)
    (verify_dir / "verify_report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not quiet:
        _print(report)
    return report


def _yn(v: object) -> str:
    return "n/a" if v is None else ("yes" if v else "NO")


def _print(report: dict) -> None:
    print(
        f"verify: {len(report['sampled'])} scenes re-sampled and re-rendered on {report['device']}"
    )
    for sid, r in report["scenes"].items():
        if "cameras" not in r:
            print(f"  {sid}: render failed: {r.get('error')}")
            continue
        for cam_id, c in r["cameras"].items():
            print(
                f"  {sid}/{cam_id}: scene file {_yn(r['scene_identical'])}, "
                f"settled {_yn(r['settled_identical'])}, index {_yn(c['index_equal'])}, "
                f"depth {_yn(c['depth_equal'])}, amodal {_yn(c['amodal_equal'])}, "
                f"answers {_yn(r['answers_identical'])} ({r['n_tasks']} tasks), "
                f"hashes {_yn(r['task_hashes_match'])}, beauty SSIM {c['beauty_ssim']:.6f}"
                f"{' (identical)' if c['beauty_identical'] else ''}"
            )
    print("verify: ALL MATCH" if report["all_ok"] else "verify: MISMATCH (see verify_report.json)")
