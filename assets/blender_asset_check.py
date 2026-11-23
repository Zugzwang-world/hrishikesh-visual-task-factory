"""Load, settle and optionally render every manifest asset in one Blender process.

Runs in Blender. For each asset: build a one-object scene with vtf_blender.build (a cm-grid
table, the asset dropped 5 mm above the table in its modelled orientation, a key light), record
the built mesh's local bounding box (tests/test_assets.py compares it with vtf.core.assets),
settle it with vtf_blender.settle, and with --render save a thumbnail. Writes assets_check.json.

    BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
    $BLENDER -b --python-exit-code 1 -P assets/blender_asset_check.py -- --out out/asset_check \
        [--ids lemon food_apple_01] [--render] [--size 256] [--samples 32]
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "vtf-blender" / "src"))
from vtf_blender.build import build_scene, load_manifest, mesh_bounds  # noqa: E402
from vtf_blender.device import configure_device  # noqa: E402
from vtf_blender.passes import configure_common, render_beauty, set_camera  # noqa: E402
from vtf_blender.settle import settle  # noqa: E402

TABLE_TOP = 0.75
PHYSICS = {
    "settle_frames": 90,
    "fps": 24,
    "substeps_per_frame": 40,
    "solver_iterations": 20,
    "friction": 0.6,
    "restitution": 0.0,
    "linear_damping": 0.04,
    "angular_damping": 0.1,
    "collision_margin": 0.0005,
    "density_kg_m3": 600.0,
    "use_deactivation": True,
    "use_split_impulse": True,
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="blender_asset_check.py")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--ids", nargs="*", default=None)
    p.add_argument("--render", action="store_true")
    p.add_argument("--size", type=int, default=256)
    p.add_argument("--samples", type=int, default=32)
    p.add_argument("--device", default="GPU")
    p.add_argument("--assets", type=Path, default=REPO_ROOT / "assets" / "cache")
    return p.parse_args(argv)


def look_at(eye: Vector, target: Vector) -> list[float]:
    q = (target - eye).to_track_quat("-Z", "Y")
    return [q.w, q.x, q.y, q.z]


def one_asset_scene(entry: dict, size: int) -> dict:
    sx, sy, sz = (float(v) for v in entry["size_m"])
    centre = Vector((0.0, 0.0, TABLE_TOP + sz / 2 + 0.005))
    radius = 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)
    fov = 2 * math.atan(18.0 / 50.0)
    dist = radius / math.sin(fov / 2) * 1.05
    el, az = math.radians(32.0), math.radians(25.0)
    eye = centre + dist * Vector(
        (math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
    )
    procedural = entry["source"] == "procedural"
    obj = {
        "id": "obj_01",
        "source": "asset",
        "shape": entry["id"],
        "category": entry["category"],
        "color_name": entry.get("color_name"),
        "rgb": [0.55, 0.12, 0.08] if procedural else entry["rgb"],
        "roughness": 0.45,
        "metallic": 0.0,
        "dimensions": entry["size_m"],
        "scale": 1.0,
        "pose": {"position": list(centre), "rotation": [1.0, 0.0, 0.0, 0.0]},
        "pass_index": 1,
        "placement": {"kind": "table", "parent_id": None},
    }
    return {
        "meta": {"scene_id": f"asset_{entry['id']}", "settled": False},
        "table": {
            "size_xy": [1.0, 0.7],
            "height": TABLE_TOP,
            "thickness": 0.03,
            "material_id": "cm_grid",
            "grid_cell_m": 0.01,
            "pass_index": 1000,
        },
        "objects": [obj],
        "gripper": {"material_id": "metal_grey", "pass_index": 1001, "parts": []},
        "zones": [],
        "cameras": [
            {
                "id": "thumb",
                "rig": "front",
                "pose": {"position": list(eye), "rotation": look_at(eye, centre)},
                "focal_mm": 50.0,
                "sensor_width_mm": 36.0,
                "resolution": [size, size],
                "clip_start": 0.01,
                "clip_end": 20.0,
            }
        ],
        "lighting": {
            "hdri_id": "studio_small_09",
            "strength": 0.6,
            "rotation_deg": 0.0,
            "key_light": {
                "kind": "sun",
                "elevation_deg": 55.0,
                "azimuth_deg": -35.0,
                "strength": 3.0,
                "angle_deg": 3.0,
                "rgb": [1.0, 0.97, 0.92],
            },
        },
        "render": {"samples": 32, "seed": 0, "device": "GPU", "denoise": True},
        "physics": PHYSICS,
    }


def main() -> None:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    ids = args.ids or list(manifest)
    results = {}
    for asset_id in ids:
        entry = manifest[asset_id]
        spec = one_asset_scene(entry, args.size)
        start = time.perf_counter()
        built = build_scene(spec, args.assets, manifest)
        build_s = time.perf_counter() - start
        ob = built.objects[0][1]
        lo, hi = mesh_bounds(ob.data)
        poses, report = settle(built, spec)
        row = {
            "blender_size_m": [round(float(v), 6) for v in (hi - lo)],
            "blender_centre_m": [round(float(v), 6) for v in (hi + lo) / 2],
            "manifest_size_m": entry["size_m"],
            "triangles": sum(len(p.vertices) - 2 for p in ob.data.polygons),
            "build_seconds": round(build_s, 3),
            "settle": {
                "seconds": report["seconds"],
                "displacement_m": report["displacement_m"]["obj_01"],
                "moving": bool(report["moving_at_end"]),
                "off_table": bool(report["off_table"]),
                "support_gap_m": report["support_gap_m"]["obj_01"],
                "rotation": poses["obj_01"][1],
            },
        }
        if args.render:
            configure_common(built.scene, spec["render"])
            spec["render"]["samples"] = args.samples
            device = configure_device(args.device)["used"]
            set_camera(built.scene, built.cameras["thumb"], (args.size, args.size))
            path = args.out / f"{asset_id}.png"
            row["render_seconds"] = round(render_beauty(built, spec["render"], device, path), 3)
            row["thumbnail"] = path.name
        results[asset_id] = row
        print("VTF_ASSET", asset_id, json.dumps(row["settle"]), flush=True)
    (args.out / "assets_check.json").write_text(json.dumps(results, indent=1) + "\n")
    print("VTF_ASSET_CHECK_OK", len(results))


if __name__ == "__main__":
    main()
