"""Render scene files: build, settle, render every camera (beauty, index, depth, amodal masks) and
export scene.glb. One scene, or a batch of scenes in one Blender process.

    BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
    $BLENDER -b --python-exit-code 1 -P packages/vtf-blender/scripts/render_scene.py -- \
        --scene <scene.json> --out <dir> [--device GPU|CPU] [--assets assets/cache] [--no-gltf]
    $BLENDER -b --python-exit-code 1 -P packages/vtf-blender/scripts/render_scene.py -- \
        --jobs <jobs.json> [--device GPU|CPU] [--assets assets/cache] [--no-gltf]

jobs.json is a list of {"scene": <scene.json>, "out": <dir>} (optional "gltf": false). In batch
mode every scene prints `VTF_SCENE_START <n>`, then `VTF_SCENE_DONE <n>` or `VTF_SCENE_FAILED
<n> <error>`; a failed scene leaves no manifest and the batch goes on. The bridge
(vtf.render.bridge) watches these markers to time each scene and to kill a stuck Blender.

Writes into each output folder (see vtf.core.render_manifest):

    scene.settled.json                    the input scene with settled poses, meta.settled = true
    <camera_id>/beauty.png                8-bit RGB, Cycles on --device
    <camera_id>/index.png                 16-bit, pixel = pass_index (1-sample CPU render)
    <camera_id>/depth.npz                 float32 planar depth in metres (key "depth"), 0.0 = no hit
    <camera_id>/amodal/<pass_index>.png   0/255, each object rendered alone
    scene.glb                             display export for the Task Explorer (not truth)
    render_manifest.json                  written last: its presence means success

The camera-quality dial is not applied here: the bridge applies it core side afterwards.
Runs in Blender's own Python: reads the scene as plain dicts and never imports vtf or pydantic.
"""

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages" / "vtf-blender" / "src"))
from vtf_blender.build import build_scene  # noqa: E402
from vtf_blender.device import configure_device  # noqa: E402
from vtf_blender.export import export_glb  # noqa: E402
from vtf_blender.files import write_json, write_png  # noqa: E402
from vtf_blender.passes import (  # noqa: E402
    DataPasses,
    camera_matrices,
    configure_common,
    render_beauty,
    set_camera,
)
from vtf_blender.settle import settle  # noqa: E402

MANIFEST = "render_manifest.json"
SETTLED = "scene.settled.json"
GLTF = "scene.glb"
MANIFEST_VERSION = "0.2.0"
FILTER_MARGIN_PX = 2.0  # a 1-sample pixel can see up to about 1 px beyond its square
MIN_BORDER_PX = 32.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="render_scene.py")
    parser.add_argument("--scene", help="Scene file (SceneSpec JSON).")
    parser.add_argument("--out", type=Path, help="Output folder.")
    parser.add_argument("--jobs", type=Path, help="Batch: JSON list of {scene, out} jobs.")
    parser.add_argument(
        "--device",
        type=str.upper,
        choices=["GPU", "CPU"],
        default=None,
        help="Beauty device (default: the scene's render.device).",
    )
    parser.add_argument("--assets", type=Path, default=REPO_ROOT / "assets" / "cache")
    parser.add_argument("--no-gltf", action="store_true", help="Skip the scene.glb export.")
    args = parser.parse_args(argv)
    if bool(args.jobs) == bool(args.scene or args.out):
        parser.error("give either --jobs, or --scene and --out")
    if args.scene and not args.out or args.out and not args.scene:
        parser.error("--scene and --out go together")
    return args


def settled_scene(spec: dict, poses: dict) -> dict:
    """The input scene, same key order, with settled poses and meta.settled = true."""
    settled = copy.deepcopy(spec)
    settled["meta"]["settled"] = True
    for obj in settled["objects"]:
        position, rotation = poses[obj["id"]]
        obj["pose"]["position"] = position
        obj["pose"]["rotation"] = rotation
    return settled


def r9(value: float) -> float:
    return round(float(value), 9) + 0.0


# ---------------------------------------------------------------- amodal masks


def projected_box(ob, k, w2c, width: int, height: int):
    """Pixel bounds (x0, y0, x1, y1) of the object's world bounding box, clipped to the image;
    "all" when a corner is at or behind the camera plane (no safe bound); None when the box
    misses the image."""
    m = np.asarray(w2c, dtype=np.float64)
    corners = np.array([list(ob.matrix_world @ Vector(c)) for c in ob.bound_box])
    cam = corners @ m[:3, :3].T + m[:3, 3]
    if (cam[:, 2] <= 1e-6).any():
        return "all"
    u = k[0][0] * cam[:, 0] / cam[:, 2] + k[0][2]
    v = k[1][1] * cam[:, 1] / cam[:, 2] + k[1][2]
    x0, x1 = u.min() - FILTER_MARGIN_PX, u.max() + FILTER_MARGIN_PX
    y0, y1 = v.min() - FILTER_MARGIN_PX, v.max() + FILTER_MARGIN_PX
    if x1 <= 0 or y1 <= 0 or x0 >= width or y0 >= height:
        return None
    (x0, x1), (y0, y1) = _span(x0, x1, width), _span(y0, y1, height)
    return x0, y0, x1, y1


def _span(lo: float, hi: float, size: int) -> tuple[float, float]:
    """Clip [lo, hi] to [0, size] and widen it to MIN_BORDER_PX (Blender refuses tiny borders)."""
    lo, hi = max(0.0, lo), min(float(size), hi)
    grow = max(0.0, min(MIN_BORDER_PX, size) - (hi - lo))
    lo, hi = lo - grow / 2, hi + grow / 2
    if lo < 0:
        lo, hi = 0.0, hi - lo
    if hi > size:
        lo, hi = lo - (hi - size), float(size)
    return max(0.0, lo), min(float(size), hi)


def amodal_masks(data: DataPasses, built, cam_id: str, k, w2c, resolution) -> dict:
    """pass_index -> bool mask of that object rendered alone.

    Exact and cheaper than one full-frame render per object: an object whose projected bounding
    box misses the image gets an empty mask without rendering, and the others render only inside
    their projected box (plus a filter margin) with Cycles' render border; pixels outside the
    border are background either way.
    """
    scene = built.scene
    width, height = (int(v) for v in resolution)
    everything = built.renderables()
    render = scene.render
    saved = (render.use_border, render.use_crop_to_border)
    masks = {}
    try:
        for _, ob in built.objects:
            box = projected_box(ob, k, w2c, width, height)
            if box is None:
                masks[ob.pass_index] = np.zeros((height, width), bool)
                continue
            if box == "all":
                render.use_border = False
            else:
                x0, y0, x1, y1 = box
                render.use_border = True
                render.use_crop_to_border = False
                # Blender's border is in 0..1 with y up from the bottom edge.
                render.border_min_x, render.border_max_x = x0 / width, x1 / width
                render.border_min_y, render.border_max_y = 1 - y1 / height, 1 - y0 / height
            for other in everything:
                other.hide_render = other is not ob
            index, _ = data._render(f"amodal_{cam_id}_{ob.pass_index}", depth=False)
            masks[ob.pass_index] = index == ob.pass_index
    finally:
        render.use_border, render.use_crop_to_border = saved
        for other in everything:
            other.hide_render = False
    return masks


# ---------------------------------------------------------------- one scene


def render_camera(built, spec, cam: dict, device: str, out: Path, tmp: Path) -> tuple[dict, dict]:
    scene = built.scene
    cam_id = cam["id"]
    cam_dir = out / cam_id
    if cam_dir.exists():
        shutil.rmtree(cam_dir)
    (cam_dir / "amodal").mkdir(parents=True)
    set_camera(scene, built.cameras[cam_id], cam["resolution"])
    k, w2c = camera_matrices(scene, built.cameras[cam_id])

    beauty_s = render_beauty(built, spec["render"], device, cam_dir / "beauty.png")

    start = time.perf_counter()
    data = DataPasses(built, tmp)
    try:
        index, depth = data.index_and_depth(cam_id)
        write_png(cam_dir / "index.png", index, "uint16")
        with open(cam_dir / "depth.npz", "wb") as f:
            np.savez_compressed(f, depth=np.ascontiguousarray(depth, dtype=np.float32))
        data_s = time.perf_counter() - start
        start = time.perf_counter()
        amodal = {}
        masks = amodal_masks(data, built, cam_id, k, w2c, cam["resolution"])
        for pass_index, mask in masks.items():
            rel = f"{cam_id}/amodal/{pass_index}.png"
            write_png(out / rel, np.where(mask, 255, 0).astype(np.uint8), "uint8")
            amodal[str(pass_index)] = rel
        amodal_s = time.perf_counter() - start
    finally:
        data.restore()

    outputs = {
        "camera_id": cam_id,
        "rig": cam.get("rig", "front"),
        "resolution": [int(v) for v in cam["resolution"]],
        "beauty": f"{cam_id}/beauty.png",
        "index": f"{cam_id}/index.png",
        "depth": f"{cam_id}/depth.npz",
        "depth_kind": "planar_z",
        "amodal": amodal,
        "K": [[r9(v) for v in row] for row in k],
        "world_to_camera": [[r9(v) for v in row] for row in w2c],
        "render_seconds": round(beauty_s, 3),
        "amodal_seconds": round(amodal_s, 3),
    }
    return outputs, {"render": beauty_s, "data_passes": data_s, "amodal": amodal_s}


def render_one(scene_path: Path, out: Path, device_arg, assets: Path, gltf: bool) -> dict:
    """Render one scene file into `out`; return the manifest dict (written last)."""
    t_start = time.perf_counter()
    raw = scene_path.read_bytes()
    spec = json.loads(raw)
    if spec["meta"].get("settled"):
        raise ValueError(
            "render the sampled scene.json, not a settled scene: settling twice would re-drop it"
        )
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).unlink(missing_ok=True)  # its presence means success
    (out / GLTF).unlink(missing_ok=True)
    tmp = out / ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)  # leftovers of a killed render
    tmp.mkdir()

    built = build_scene(spec, assets)
    timings = {"build": time.perf_counter() - t_start}

    poses, settle_report = settle(built, spec)
    timings["settle"] = settle_report["seconds"]
    settled = settled_scene(spec, poses)
    write_json(out / SETTLED, settled)

    configure_common(built.scene, spec["render"])
    device_info = configure_device(device_arg or spec["render"]["device"])
    device = device_info["used"]
    cameras = []
    for key in ("render", "data_passes", "amodal"):
        timings[key] = 0.0
    try:
        for cam in spec["cameras"]:
            outputs, cam_timings = render_camera(built, spec, cam, device, out, tmp)
            cameras.append(outputs)
            for key, seconds in cam_timings.items():
                timings[key] += seconds
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # never leave EXRs behind

    gltf_rel = None
    if gltf:
        try:  # display only: a failed export never fails the render
            info = export_glb(built, settled, out / GLTF)
            gltf_rel = GLTF
            timings["gltf"] = info["seconds"]
            print(f"VTF_GLTF {json.dumps({k: info[k] for k in ('bytes', 'triangles')})}")
        except Exception as exc:
            (out / GLTF).unlink(missing_ok=True)
            print(f"VTF_GLTF_WARNING {type(exc).__name__}: {exc}", flush=True)
    timings["total"] = time.perf_counter() - t_start

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "scene_id": spec["meta"]["scene_id"],
        "scene_file": os.path.relpath(scene_path.resolve(), out),  # relative, like all paths
        "scene_sha256": hashlib.sha256(raw).hexdigest(),
        "settled_scene_file": SETTLED,
        "renderer": "blender",
        "blender_version": bpy.app.version_string,
        "device": device,
        "data_passes_device": "CPU",
        "device_name": ", ".join(device_info.get("devices", [])),
        "samples": int(spec["render"]["samples"]),
        "seed": int(spec["render"]["seed"]),
        "denoise": bool(spec["render"]["denoise"]),
        "cameras": cameras,
        "gltf": gltf_rel,
        "settle": settle_report,
        "timings": {k: round(v, 3) for k, v in timings.items()},
    }
    write_json(out / MANIFEST, manifest)
    return manifest


# ---------------------------------------------------------------- entry points


def run_batch(jobs: list[dict], args) -> int:
    """Render every job; a failure is reported and the batch goes on. Returns the failure count."""
    failures = 0
    for n, job in enumerate(jobs):
        print(f"VTF_SCENE_START {n} {json.dumps(job)}", flush=True)
        out = Path(job["out"])
        try:
            if job.get("debug_sleep_s"):  # test hook: a deliberately slow scene
                time.sleep(float(job["debug_sleep_s"]))
            gltf = bool(job.get("gltf", True)) and not args.no_gltf
            manifest = render_one(Path(job["scene"]), out, args.device, args.assets, gltf)
            print(f"VTF_SCENE_DONE {n} {json.dumps(manifest['timings'])}", flush=True)
        except Exception as exc:
            failures += 1
            (out / MANIFEST).unlink(missing_ok=True)
            shutil.rmtree(out / ".tmp", ignore_errors=True)
            traceback.print_exc()
            sys.stdout.flush()
            message = f"{type(exc).__name__}: {exc}".replace("\n", " ")
            print(f"VTF_RENDER_ERROR {message}", flush=True)
            print(f"VTF_SCENE_FAILED {n} {message}", flush=True)
    print(f"VTF_BATCH_DONE {len(jobs) - failures} ok, {failures} failed", flush=True)
    return failures


def main() -> None:
    args = parse_args()
    if args.jobs:
        run_batch(json.loads(args.jobs.read_text(encoding="utf-8")), args)
        return
    manifest = render_one(Path(args.scene), args.out, args.device, args.assets, not args.no_gltf)
    print("VTF_RENDER_OK " + json.dumps({"out": str(args.out), "timings": manifest["timings"]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # One greppable line for the bridge; the traceback follows, and Blender exits non-zero
        # when started with --python-exit-code 1.
        print(f"VTF_RENDER_ERROR {type(exc).__name__}: {exc}", flush=True)
        raise
