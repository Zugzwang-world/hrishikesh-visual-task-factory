"""Render one scene file: build, settle, render beauty, index, depth and amodal masks.

    BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
    $BLENDER -b --python-exit-code 1 -P packages/vtf-blender/scripts/render_scene.py -- \
        --scene <scene.json> --out <dir> [--device GPU|CPU] [--assets assets/cache]

Writes into <dir> (see vtf.core.render_manifest):

    scene.settled.json                    the input scene with settled poses, meta.settled = true
    <camera_id>/beauty.png                8-bit RGB, Cycles on --device
    <camera_id>/index.png                 16-bit, pixel = pass_index (1-sample CPU render)
    <camera_id>/depth.npy                 float32 planar depth in metres, 0.0 = no hit
    <camera_id>/amodal/<pass_index>.png   0/255, each object rendered alone
    render_manifest.json                  written last: its presence means success

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
from pathlib import Path

import bpy
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages" / "vtf-blender" / "src"))
from vtf_blender.build import build_scene  # noqa: E402
from vtf_blender.device import configure_device  # noqa: E402
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
MANIFEST_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="render_scene.py")
    parser.add_argument("--scene", required=True, help="Scene file (SceneSpec JSON).")
    parser.add_argument("--out", required=True, type=Path, help="Output folder.")
    parser.add_argument(
        "--device",
        type=str.upper,
        choices=["GPU", "CPU"],
        default=None,
        help="Beauty device (default: the scene's render.device).",
    )
    parser.add_argument("--assets", type=Path, default=REPO_ROOT / "assets" / "cache")
    return parser.parse_args(argv)


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
        np.save(cam_dir / "depth.npy", depth)
        data_s = time.perf_counter() - start
        start = time.perf_counter()
        amodal = {}
        for pass_index, mask in data.amodal(cam_id).items():
            rel = f"{cam_id}/amodal/{pass_index}.png"
            write_png(out / rel, np.where(mask, 255, 0).astype(np.uint8), "uint8")
            amodal[str(pass_index)] = rel
        amodal_s = time.perf_counter() - start
    finally:
        data.restore()

    outputs = {
        "camera_id": cam_id,
        "resolution": [int(v) for v in cam["resolution"]],
        "beauty": f"{cam_id}/beauty.png",
        "index": f"{cam_id}/index.png",
        "depth": f"{cam_id}/depth.npy",
        "depth_kind": "planar_z",
        "amodal": amodal,
        "K": [[r9(v) for v in row] for row in k],
        "world_to_camera": [[r9(v) for v in row] for row in w2c],
        "render_seconds": round(beauty_s, 3),
        "amodal_seconds": round(amodal_s, 3),
    }
    return outputs, {"render": beauty_s, "data_passes": data_s, "amodal": amodal_s}


def main() -> None:
    t_start = time.perf_counter()
    args = parse_args()
    scene_path = Path(args.scene)
    raw = scene_path.read_bytes()
    spec = json.loads(raw)
    if spec["meta"].get("settled"):
        raise ValueError(
            "render the sampled scene.json, not a settled scene: settling twice would re-drop it"
        )
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).unlink(missing_ok=True)  # its presence means success
    tmp = out / ".tmp"
    tmp.mkdir(exist_ok=True)

    built = build_scene(spec, args.assets)
    timings = {"build": time.perf_counter() - t_start}

    poses, settle_report = settle(built, spec)
    timings["settle"] = settle_report["seconds"]
    write_json(out / SETTLED, settled_scene(spec, poses))

    configure_common(built.scene, spec["render"])
    device_info = configure_device(args.device or spec["render"]["device"])
    device = device_info["used"]
    cameras = []
    for key in ("render", "data_passes", "amodal"):
        timings[key] = 0.0
    for cam in spec["cameras"]:
        outputs, cam_timings = render_camera(built, spec, cam, device, out, tmp)
        cameras.append(outputs)
        for key, seconds in cam_timings.items():
            timings[key] += seconds
    shutil.rmtree(tmp, ignore_errors=True)
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
        "settle": settle_report,
        "timings": {k: round(v, 3) for k, v in timings.items()},
    }
    write_json(out / MANIFEST, manifest)
    print("VTF_RENDER_OK " + json.dumps({"out": str(out), "timings": manifest["timings"]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # One greppable line for the bridge; the traceback follows, and Blender exits non-zero
        # when started with --python-exit-code 1.
        print(f"VTF_RENDER_ERROR {type(exc).__name__}: {exc}", flush=True)
        raise
