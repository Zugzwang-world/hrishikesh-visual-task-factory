"""`vtf verify-showcase`: rule C (honest repeatability) shown on a frozen run, without writing
into it.

Runs `vtf verify` on the same scenes three times, each into its own folder under --out:
gpu (the default device, as the frozen run was rendered), cpu_1 and cpu_2. Each pass re-samples
the scene files from the recipe and seed, re-renders them, compares every pass against the frozen
files and re-runs every task's program (vtf.validate.verify). Then cpu_1 and cpu_2 are compared
with each other file by file. What it shows, per scene and camera:

- exact: scene file re-sampled byte-identical; settled scene byte-identical; object index, depth
  and amodal masks equal to the frozen ones (decoded arrays); every answer and every G7 hash
  identical;
- near-exact: beauty image vs the frozen GPU image, SSIM (gpu pass and cpu pass);
- byte-identical: the two CPU renders' beauty PNG files (sha256) and pixels.

Writes <out>/showcase.json and <out>/verify_showcase.md (the tables for the docs).
"""

import json
import time
from pathlib import Path

import numpy as np

from vtf.core import layout
from vtf.core.hashing import sha256_file
from vtf.core.io import read_depth, read_index_png, read_manifest, read_mask_png, read_rgb_png

DEFAULT_SCENES = ("scene_0004", "scene_0170", "scene_0014")
PASSES = (("gpu", "gpu"), ("cpu_1", "cpu"), ("cpu_2", "cpu"))


def compare_dirs(dir_a: Path, dir_b: Path) -> dict:
    """Two renders of one scene, file by file: settled scene, and per camera the beauty PNG
    (bytes and pixels), object index, depth and amodal masks."""
    ma = read_manifest(dir_a / layout.RENDER_MANIFEST)
    mb = read_manifest(dir_b / layout.RENDER_MANIFEST)
    out: dict = {
        "settled_identical": sha256_file(dir_a / ma.settled_scene_file)
        == sha256_file(dir_b / mb.settled_scene_file),
        "cameras": {},
    }
    for ca in ma.cameras:
        cb = mb.camera(ca.camera_id)
        out["cameras"][ca.camera_id] = {
            "beauty_png_sha256_identical": sha256_file(dir_a / ca.beauty)
            == sha256_file(dir_b / cb.beauty),
            "beauty_pixels_identical": bool(
                np.array_equal(read_rgb_png(dir_a / ca.beauty), read_rgb_png(dir_b / cb.beauty))
            ),
            "beauty_sha256": sha256_file(dir_a / ca.beauty),
            "index_equal": bool(
                np.array_equal(read_index_png(dir_a / ca.index), read_index_png(dir_b / cb.index))
            ),
            "depth_equal": bool(
                np.array_equal(read_depth(dir_a / ca.depth), read_depth(dir_b / cb.depth))
            ),
            "amodal_equal": set(ca.amodal) == set(cb.amodal)
            and all(
                np.array_equal(
                    read_mask_png(dir_a / ca.amodal[k]), read_mask_png(dir_b / cb.amodal[k])
                )
                for k in ca.amodal
            ),
        }
    return out


def verify_showcase(
    run_dir: Path,
    scenes: list[str] | None = None,
    out: Path = Path("out/verify_showcase"),
    timeout: float = 1800.0,
    echo=print,
) -> dict:
    from vtf.validate.verify import verify_run

    run_dir, out = Path(run_dir), Path(out)
    scenes = list(scenes or DEFAULT_SCENES)
    passes: dict[str, dict] = {}
    for label, device in PASSES:
        echo(f"verify-showcase: pass {label} ({device}) on {', '.join(scenes)}")
        t0 = time.perf_counter()
        rep = verify_run(
            run_dir, device=device, out_dir=out / label, scenes=scenes, timeout=timeout, quiet=True
        )
        rep["wall_seconds"] = round(time.perf_counter() - t0, 1)
        passes[label] = rep
        echo(f"  {label}: {rep['wall_seconds']} s, all_ok={rep['all_ok']}")

    cpu_vs_cpu = {
        sid: compare_dirs(out / "cpu_1" / sid, out / "cpu_2" / sid)
        for sid in scenes
        if (out / "cpu_1" / sid / layout.RENDER_MANIFEST).exists()
        and (out / "cpu_2" / sid / layout.RENDER_MANIFEST).exists()
    }
    rows = []
    for sid in scenes:
        g, c1 = passes["gpu"]["scenes"].get(sid, {}), passes["cpu_1"]["scenes"].get(sid, {})
        c2 = passes["cpu_2"]["scenes"].get(sid, {})
        for cam, cg in g.get("cameras", {}).items():
            cc1 = c1.get("cameras", {}).get(cam, {})
            cc2 = c2.get("cameras", {}).get(cam, {})
            cc = cpu_vs_cpu.get(sid, {}).get("cameras", {}).get(cam, {})
            exact = [cg, cc1, cc2]
            rows.append(
                {
                    "scene": sid,
                    "camera": cam,
                    "n_tasks": g.get("n_tasks"),
                    "scene_file_identical": all(p.get("scene_identical") for p in (g, c1, c2) if p),
                    "settled_identical": all(p.get("settled_identical") for p in (g, c1, c2) if p),
                    "masks_depth_exact_vs_frozen": all(
                        e.get("index_equal") and e.get("depth_equal") and e.get("amodal_equal")
                        for e in exact
                    ),
                    "answers_identical": all(p.get("answers_identical") for p in (g, c1, c2) if p),
                    "g7_hashes_match": all(p.get("task_hashes_match") for p in (g, c1, c2) if p),
                    "ssim_gpu_vs_frozen_gpu": cg.get("beauty_ssim"),
                    "gpu_identical_to_frozen": cg.get("beauty_identical"),
                    "ssim_cpu_vs_frozen_gpu": min(
                        cc1.get("beauty_ssim", 0), cc2.get("beauty_ssim", 0)
                    ),
                    "cpu_vs_cpu_png_bytes_identical": cc.get("beauty_png_sha256_identical"),
                    "cpu_vs_cpu_pixels_identical": cc.get("beauty_pixels_identical"),
                    "cpu_vs_cpu_masks_depth_identical": bool(
                        cc.get("index_equal") and cc.get("depth_equal") and cc.get("amodal_equal")
                    ),
                }
            )
    timings = {
        label: {
            "wall_seconds": p["wall_seconds"],
            "render_seconds": {s: r.get("render_seconds") for s, r in p["scenes"].items()},
        }
        for label, p in passes.items()
    }
    result = {
        "run": str(run_dir),
        "scenes": scenes,
        "rows": rows,
        "timings": timings,
        "verify_all_ok": {label: p["all_ok"] for label, p in passes.items()},
        "cpu_vs_cpu": cpu_vs_cpu,
        "exact_all": all(
            r["scene_file_identical"]
            and r["settled_identical"]
            and r["masks_depth_exact_vs_frozen"]
            and r["answers_identical"]
            and r["g7_hashes_match"]
            for r in rows
        ),
        "cpu_bytes_all": all(r["cpu_vs_cpu_png_bytes_identical"] for r in rows),
        "gpu_ssim_min": min((r["ssim_gpu_vs_frozen_gpu"] or 0) for r in rows) if rows else None,
        "cpu_vs_gpu_ssim_min": min((r["ssim_cpu_vs_frozen_gpu"] or 0) for r in rows)
        if rows
        else None,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "showcase.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "verify_showcase.md").write_text(markdown(result))
    echo(markdown(result))
    return result


def _yn(v) -> str:
    return "yes" if v else ("n/a" if v is None else "NO")


def markdown(r: dict) -> str:
    lines = [
        "| Scene | Camera | Tasks | Scene file | Settled | Masks + depth vs frozen | Answers | G7 "
        "hashes | Beauty SSIM, GPU vs frozen GPU | Beauty SSIM, CPU vs frozen GPU | CPU vs CPU "
        "PNG bytes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in r["rows"]:
        lines.append(
            f"| {row['scene']} | {row['camera']} | {row['n_tasks']} "
            f"| {_yn(row['scene_file_identical'])} | {_yn(row['settled_identical'])} "
            f"| {_yn(row['masks_depth_exact_vs_frozen'])} | {_yn(row['answers_identical'])} "
            f"| {_yn(row['g7_hashes_match'])} | {row['ssim_gpu_vs_frozen_gpu']:.6f}"
            f"{' (identical)' if row['gpu_identical_to_frozen'] else ''} "
            f"| {row['ssim_cpu_vs_frozen_gpu']:.6f} "
            f"| {'identical' if row['cpu_vs_cpu_png_bytes_identical'] else 'DIFFERENT'} |"
        )
    lines.append("")
    lines.append("| Pass | Wall time (s) | Render time per scene (s) |")
    lines.append("| --- | --- | --- |")
    for label, t in r["timings"].items():
        per = ", ".join(f"{s} {v}" for s, v in t["render_seconds"].items())
        lines.append(f"| {label} | {t['wall_seconds']} | {per} |")
    return "\n".join(lines) + "\n"
