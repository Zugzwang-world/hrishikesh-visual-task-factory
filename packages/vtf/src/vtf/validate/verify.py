"""`vtf verify <run> --sample N`: rebuild sampled scenes from their seeds and check G7.

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


def verify_run(run_dir: Path, sample: int = 5, device: str = "gpu") -> dict:
    from vtf.programs import run_program
    from vtf.render.bridge import render_scene

    run_dir = Path(run_dir)
    lay = layout.RunLayout(run_dir)
    run_info = read_model(lay.run_file, RunInfo) if lay.run_file.exists() else None
    tasks_by_scene: dict[str, list] = {}
    for row in read_jsonl(lay.task_manifest):
        tasks_by_scene.setdefault(row["scene_id"], []).append(read_task(run_dir / row["path"]))
    scene_ids = sorted(tasks_by_scene)
    rng = make_rng(derive_seed(run_info.run_seed if run_info else 0, "verify"))
    picked = sorted(rng.choice(scene_ids, size=min(sample, len(scene_ids)), replace=False).tolist())

    results = {}
    for sid in picked:
        res: dict = {}
        regenerated = regenerate_scene_bytes(run_info, run_dir, sid)
        res["scene_identical"] = (
            None if regenerated is None else regenerated == lay.scene_file(sid).read_bytes()
        )
        vdir = lay.verify_dir / sid
        rr = render_scene(lay.scene_file(sid), vdir, device=device)
        res["render_seconds"] = round(rr.seconds, 2)
        if not rr.ok:
            res.update(ok=False, error=rr.error)
            results[sid] = res
            continue
        res.update(_compare_render(lay.render_dir(sid), vdir))
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
    lay.verify_dir.mkdir(parents=True, exist_ok=True)
    (lay.verify_dir / "verify_report.json").write_text(json.dumps(report, indent=2) + "\n")
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
