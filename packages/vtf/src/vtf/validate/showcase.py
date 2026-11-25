"""`vtf verify-showcase`: rule C (honest repeatability) measured on a frozen run, without writing
into it.

1. Runs `vtf verify` on the same scenes three times, each into its own folder under --out: gpu
   (the default device), cpu_1 and cpu_2. Each pass re-samples the scene files from the recipe
   and seed, re-renders each scene alone in a fresh Blender process, compares it with the frozen
   files and re-runs every task's program (vtf.validate.verify).
2. Compares cpu_1 with cpu_2 file by file (beauty PNG bytes and pixels, raw render, index, depth,
   amodal masks).
3. Replays the frozen run's first Blender batch: scenes 0..N-1 rendered in one Blender process
   with the scene.glb export after each, as the scale run rendered them, and compares each with
   the frozen files. A scene's beauty image depends on the scenes rendered before it in the same
   process (the glTF export flattens the cached asset materials in place), so only this replay
   can reproduce frozen images; a scene rendered alone gets the full materials.

What it shows, per scene and camera: exact (scene file re-sampled byte-identical, settled scene,
object index, depth and amodal masks, every answer and G7 hash); and how close the beauty images
come (SSIM, largest difference in colour levels, share of pixels that differ).

Writes <out>/showcase.json and <out>/verify_showcase.md (the tables for the docs).
"""

import json
import time
from pathlib import Path

import numpy as np
from skimage.metrics import structural_similarity

from vtf.core import layout
from vtf.core.hashing import sha256_file
from vtf.core.io import read_depth, read_index_png, read_manifest, read_mask_png, read_rgb_png

DEFAULT_SCENES = ("scene_0004", "scene_0170", "scene_0014")
PASSES = (("gpu", "gpu"), ("cpu_1", "cpu"), ("cpu_2", "cpu"))
REPLAY_DIR = "gpu_batch_replay"


def image_stats(a: np.ndarray, b: np.ndarray) -> dict:
    """SSIM, byte-equal pixels, largest per-channel difference and the share of pixels that
    differ at all, for two 8-bit RGB images."""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return {
        "ssim": round(float(structural_similarity(a, b, channel_axis=2, data_range=255)), 6),
        "identical": bool(np.array_equal(a, b)),
        "max_diff": int(diff.max()),
        "pixels_differing": round(float((diff.max(axis=2) > 0).mean()), 7),
    }


def compare_dirs(dir_a: Path, dir_b: Path) -> dict:
    """Two renders of one scene, file by file: settled scene, and per camera the beauty PNG
    (bytes and pixels), the raw render when both kept it, object index, depth and amodal masks."""
    ma = read_manifest(dir_a / layout.RENDER_MANIFEST)
    mb = read_manifest(dir_b / layout.RENDER_MANIFEST)
    out: dict = {
        "settled_identical": sha256_file(dir_a / ma.settled_scene_file)
        == sha256_file(dir_b / mb.settled_scene_file),
        "cameras": {},
    }
    for ca in ma.cameras:
        cb = mb.camera(ca.camera_id)
        cam = {
            "beauty_png_sha256_identical": sha256_file(dir_a / ca.beauty)
            == sha256_file(dir_b / cb.beauty),
            "beauty": image_stats(read_rgb_png(dir_a / ca.beauty), read_rgb_png(dir_b / cb.beauty)),
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
        if ca.beauty_raw and cb.beauty_raw:
            cam["raw"] = image_stats(
                read_rgb_png(dir_a / ca.beauty_raw), read_rgb_png(dir_b / cb.beauty_raw)
            )
        out["cameras"][ca.camera_id] = cam
    return out


def replay_batch(run_dir: Path, out: Path, n: int, timeout: float, device: str = "gpu") -> dict:
    """Render the run's first n scenes in one Blender process with the scene.glb export, as the
    scale run did, and compare each with the frozen render."""
    from vtf.render.bridge import render_scenes

    lay = layout.RunLayout(run_dir)
    ids = [layout.scene_id_for(i) for i in range(n)]
    t0 = time.perf_counter()
    results = render_scenes(
        [lay.scene_file(s) for s in ids], [out / s for s in ids], device=device,
        batch_size=n, timeout_per_scene=timeout, gltf=True,
    )  # fmt: skip
    report: dict = {"device": device, "scenes": {}, "wall_seconds": 0.0}
    for sid, r in zip(ids, results, strict=True):
        if not r.ok:
            report["scenes"][sid] = {"ok": False, "error": r.error}
            continue
        report["scenes"][sid] = {
            "ok": True,
            "render_seconds": round(r.seconds, 1),
            **compare_dirs(lay.render_dir(sid), out / sid),
        }
    report["wall_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def verify_showcase(
    run_dir: Path,
    scenes: list[str] | None = None,
    out: Path = Path("out/verify_showcase"),
    timeout: float = 1800.0,
    replay: int = 5,
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
        echo(f"  {label}: {rep['wall_seconds']} s")

    cpu_vs_cpu = {
        sid: compare_dirs(out / "cpu_1" / sid, out / "cpu_2" / sid)
        for sid in scenes
        if (out / "cpu_1" / sid / layout.RENDER_MANIFEST).exists()
        and (out / "cpu_2" / sid / layout.RENDER_MANIFEST).exists()
    }
    gpu_vs_cpu = {
        sid: compare_dirs(out / "gpu" / sid, out / "cpu_1" / sid)
        for sid in scenes
        if (out / "gpu" / sid / layout.RENDER_MANIFEST).exists()
        and (out / "cpu_1" / sid / layout.RENDER_MANIFEST).exists()
    }
    rows = []
    for sid in scenes:
        g, c1 = passes["gpu"]["scenes"].get(sid, {}), passes["cpu_1"]["scenes"].get(sid, {})
        c2 = passes["cpu_2"]["scenes"].get(sid, {})
        each = [p for p in (g, c1, c2) if p]
        for cam, cg in g.get("cameras", {}).items():
            cc1 = c1.get("cameras", {}).get(cam, {})
            cc2 = c2.get("cameras", {}).get(cam, {})
            cc = cpu_vs_cpu.get(sid, {}).get("cameras", {}).get(cam, {})
            gc = gpu_vs_cpu.get(sid, {}).get("cameras", {}).get(cam, {})
            rows.append(
                {
                    "scene": sid,
                    "camera": cam,
                    "n_tasks": g.get("n_tasks"),
                    "scene_file_identical": all(p.get("scene_identical") for p in each),
                    "settled_identical": all(p.get("settled_identical") for p in each),
                    "masks_depth_exact_vs_frozen": all(
                        e.get("index_equal") and e.get("depth_equal") and e.get("amodal_equal")
                        for e in (cg, cc1, cc2)
                    ),
                    "answers_identical": all(p.get("answers_identical") for p in each),
                    "g7_hashes_match": all(p.get("task_hashes_match") for p in each),
                    "ssim_alone_gpu_vs_frozen": cg.get("beauty_ssim"),
                    "ssim_alone_cpu_vs_frozen": min(
                        cc1.get("beauty_ssim", 0), cc2.get("beauty_ssim", 0)
                    ),
                    "ssim_gpu_vs_cpu_raw": gc.get("raw", {}).get("ssim"),
                    "cpu_vs_cpu_png_bytes_identical": cc.get("beauty_png_sha256_identical"),
                    "cpu_vs_cpu_beauty": cc.get("beauty"),
                    "cpu_vs_cpu_raw": cc.get("raw"),
                    "cpu_vs_cpu_masks_depth_identical": bool(
                        cc.get("index_equal") and cc.get("depth_equal") and cc.get("amodal_equal")
                    ),
                }
            )

    replay_report = None
    if replay > 0:
        echo(f"verify-showcase: replaying the frozen run's first batch ({replay} scenes, GPU)")
        replay_report = replay_batch(run_dir, out / REPLAY_DIR, replay, timeout)
        echo(f"  replay: {replay_report['wall_seconds']} s")

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
        "cpu_vs_cpu": cpu_vs_cpu,
        "replay": replay_report,
        "exact_all": bool(rows)
        and all(
            r["scene_file_identical"]
            and r["settled_identical"]
            and r["masks_depth_exact_vs_frozen"]
            and r["answers_identical"]
            and r["g7_hashes_match"]
            for r in rows
        ),
        "cpu_png_bytes_identical": sum(bool(r["cpu_vs_cpu_png_bytes_identical"]) for r in rows),
        "images": len(rows),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "showcase.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "verify_showcase.md").write_text(markdown(result))
    echo(markdown(result))
    return result


def _yn(v) -> str:
    return "yes" if v else ("n/a" if v is None else "NO")


def _f(v, digits: int = 6) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def markdown(r: dict) -> str:
    lines = [
        "| Scene | Camera | Tasks | Scene file | Settled | Masks + depth | Answers | G7 hashes "
        "| SSIM alone GPU vs frozen | SSIM alone CPU vs frozen | CPU vs CPU PNG | CPU vs CPU "
        "max diff (pixels differing) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in r["rows"]:
        cc = row.get("cpu_vs_cpu_beauty") or {}
        lines.append(
            f"| {row['scene']} | {row['camera']} | {row['n_tasks']} "
            f"| {_yn(row['scene_file_identical'])} | {_yn(row['settled_identical'])} "
            f"| {_yn(row['masks_depth_exact_vs_frozen'])} | {_yn(row['answers_identical'])} "
            f"| {_yn(row['g7_hashes_match'])} | {_f(row['ssim_alone_gpu_vs_frozen'])} "
            f"| {_f(row['ssim_alone_cpu_vs_frozen'])} "
            f"| {'identical' if row['cpu_vs_cpu_png_bytes_identical'] else 'differs'} "
            f"| {cc.get('max_diff', 'n/a')} ({100 * cc.get('pixels_differing', 0):.4f}%) |"
        )
    rep = r.get("replay")
    if rep:
        lines += [
            "",
            "| Batch replay (GPU) | Camera | SSIM vs frozen | Max diff | Pixels differing "
            "| Masks + depth | Settled |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for sid, s in rep["scenes"].items():
            if not s.get("ok"):
                lines.append(f"| {sid} | render failed | | | | | |")
                continue
            for cam, c in s["cameras"].items():
                b = c["beauty"]
                exact = c["index_equal"] and c["depth_equal"] and c["amodal_equal"]
                lines.append(
                    f"| {sid} | {cam} | {_f(b['ssim'])} | {b['max_diff']} "
                    f"| {100 * b['pixels_differing']:.2f}% | {_yn(exact)} "
                    f"| {_yn(s['settled_identical'])} |"
                )
    lines += ["", "| Pass | Wall time (s) | Render time per scene (s) |", "| --- | --- | --- |"]
    for label, t in r["timings"].items():
        per = ", ".join(f"{s} {v}" for s, v in t["render_seconds"].items())
        lines.append(f"| {label} | {t['wall_seconds']} | {per} |")
    if rep:
        per = ", ".join(f"{s} {v.get('render_seconds')}" for s, v in rep["scenes"].items())
        lines.append(f"| batch replay | {rep['wall_seconds']} | {per} |")
    return "\n".join(lines) + "\n"
