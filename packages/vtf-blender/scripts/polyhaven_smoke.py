"""Poly Haven proof: load a cached glTF model and an HDRI, render one frame, save a PNG.

Fetch the assets first (they land in assets/cache/, which is gitignored):

    uv run python assets/fetch_polyhaven.py wooden_bowl_02 --kind model
    uv run python assets/fetch_polyhaven.py studio_small_09 --kind hdri

Then render (arguments after "--" are optional):

    BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
    $BLENDER -b -P packages/vtf-blender/scripts/polyhaven_smoke.py -- --device GPU

Writes out/smoke/polyhaven.png and out/smoke/polyhaven.json (device, timing, measured size).
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages" / "vtf-blender" / "src"))
from vtf_blender.device import configure_device  # noqa: E402

CACHE = REPO_ROOT / "assets" / "cache"


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="polyhaven_smoke.py")
    parser.add_argument("--model", default="wooden_bowl_02")
    parser.add_argument("--hdri", default="studio_small_09")
    parser.add_argument("--device", choices=["GPU", "CPU"], default="GPU")
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "smoke")
    return parser.parse_args(argv)


def cached_file(asset_id: str) -> Path:
    meta_path = CACHE / asset_id / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"{asset_id} is not cached; run assets/fetch_polyhaven.py first")
    return CACHE / asset_id / json.loads(meta_path.read_text())["file"]


def world_bbox(objects: list) -> tuple[Vector, Vector]:
    corners = [o.matrix_world @ Vector(c) for o in objects if o.type == "MESH" for c in o.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo, hi


def import_model(path: Path) -> tuple[list, Vector]:
    """Import a glTF, stand it on z=0 centred on the origin; return its objects and size (m)."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    objects = [o for o in bpy.data.objects if o not in before]
    bpy.context.view_layer.update()
    lo, hi = world_bbox(objects)
    shift = Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
    for root in (o for o in objects if o.parent is None):
        root.location += shift
    bpy.context.view_layer.update()
    for i, obj in enumerate(objects, start=2):
        obj.pass_index = i
    return objects, hi - lo


def set_hdri(path: Path, strength: float = 1.0) -> None:
    world = bpy.data.worlds.new("hdri")
    if getattr(world, "node_tree", None) is None and hasattr(world, "use_nodes"):
        world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    background = nodes.get("Background") or nodes.new("ShaderNodeBackground")
    output = nodes.get("World Output") or nodes.new("ShaderNodeOutputWorld")
    env = nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(path))
    links.new(env.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Strength"].default_value = strength
    bpy.context.scene.world = world


def add_table() -> None:
    bpy.ops.mesh.primitive_plane_add(size=1.2, location=(0, 0, 0))
    table = bpy.context.active_object
    table.name = "table"
    table.pass_index = 1
    mat = bpy.data.materials.new("table")
    if getattr(mat, "node_tree", None) is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.42, 0.44, 0.46, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.55
    table.data.materials.append(mat)


def add_camera(target: Vector, distance: float, elevation_deg: float, azimuth_deg: float) -> None:
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    offset = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = 50
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = target + offset * distance
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    add_table()
    objects, size = import_model(cached_file(args.model))
    set_hdri(cached_file(args.hdri))
    add_camera(Vector((0, 0, size.z * 0.45)), distance=0.42, elevation_deg=38, azimuth_deg=25)

    device = configure_device(args.device)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.seed = 0
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 800, 600
    scene.render.resolution_percentage = 100
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    settings.color_mode = "RGB"
    scene.render.filepath = str(out / "polyhaven.png")

    start = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    seconds = time.perf_counter() - start

    textures = sorted(
        {f"{img.name} {img.size[0]}x{img.size[1]}" for img in bpy.data.images if img.size[0]}
    )
    report = {
        "blender_version": bpy.app.version_string,
        "device": device,
        "samples": args.samples,
        "denoiser": scene.cycles.denoiser,
        "resolution": [800, 600],
        "render_seconds": round(seconds, 3),
        "model": args.model,
        "model_objects": [o.name for o in objects],
        "model_size_mm": [round(v * 1000, 1) for v in size],
        "hdri": args.hdri,
        "images_loaded": textures,
        "output": "out/smoke/polyhaven.png",
    }
    (out / "polyhaven.json").write_text(json.dumps(report, indent=2) + "\n")
    print("POLYHAVEN_REPORT " + json.dumps(report))


if __name__ == "__main__":
    main()
