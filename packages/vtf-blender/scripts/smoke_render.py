"""Smoke render: prove Blender renders headless on the GPU and writes the passes the gates need.

Builds a ground plane and three primitives, renders them with Cycles (Metal GPU by default,
64 samples, 640x480, fixed seed) and writes into out/smoke/:

    beauty.png            the rendered image
    passes.exr            multilayer EXR: Combined, Depth (float, metres) and IndexOB (object index)
    object_index.png      raw object index per pixel (0 = background, 1 = plane, 2-4 = primitives)
    object_index_vis.png  the same, coloured for viewing
    depth.png             16-bit depth in millimetres (0 = background)
    depth_vis.png         depth normalised for viewing (near = bright)
    preview.png           beauty | object index | depth, side by side
    render.json           device, timing, versions, pixel counts per object index

Run with Blender in background mode (arguments after "--" are optional):

    BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
    $BLENDER -b -P packages/vtf-blender/scripts/smoke_render.py -- --device GPU --out out/smoke
"""

import argparse
import json
import sys
import time
from pathlib import Path

import bpy
import numpy as np
import OpenImageIO as oiio
from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages" / "vtf-blender" / "src"))
from vtf_blender.device import configure_device  # noqa: E402

WIDTH, HEIGHT = 640, 480
SEED = 0

# name, primitive, location, size argument, base colour, pass index
OBJECTS = [
    ("cube", "cube", (-0.9, 0.3, 0.4), 0.8, (0.80, 0.15, 0.10, 1.0), 2),
    ("sphere", "sphere", (0.5, -0.6, 0.45), 0.45, (0.10, 0.35, 0.80, 1.0), 3),
    ("cylinder", "cylinder", (0.7, 0.9, 0.6), 0.35, (0.15, 0.65, 0.25, 1.0), 4),
]
PALETTE = np.array(
    [[0, 0, 0], [90, 90, 90], [220, 60, 50], [50, 110, 230], [60, 190, 90]], dtype=np.uint8
)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="smoke_render.py")
    parser.add_argument("--device", choices=["GPU", "CPU"], default="GPU")
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "smoke")
    return parser.parse_args(argv)


def make_material(name: str, color: tuple, roughness: float) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    if getattr(mat, "node_tree", None) is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    if mat.node_tree is not None:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = color
            bsdf.inputs["Roughness"].default_value = roughness
    return mat


def build_scene() -> bpy.types.Scene:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    bpy.ops.mesh.primitive_plane_add(size=12, location=(0, 0, 0))
    plane = bpy.context.active_object
    plane.name = "ground"
    plane.pass_index = 1
    plane.data.materials.append(make_material("ground", (0.6, 0.58, 0.55, 1.0), 0.8))

    for name, kind, loc, size, color, index in OBJECTS:
        if kind == "cube":
            bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
        elif kind == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(
                radius=size, location=loc, segments=64, ring_count=32
            )
            bpy.ops.object.shade_smooth()
        else:
            bpy.ops.mesh.primitive_cylinder_add(
                radius=size, depth=2 * loc[2], location=loc, vertices=64
            )
        obj = bpy.context.active_object
        obj.name = name
        obj.pass_index = index
        obj.data.materials.append(make_material(name, color, 0.35))

    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = 35
    cam = bpy.data.objects.new("camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (4.2, -4.6, 3.4)
    direction = Vector((0.0, 0.0, 0.35)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    sun_data = bpy.data.lights.new("sun", type="SUN")
    sun_data.energy = 3.5
    sun_data.angle = 0.1
    sun = bpy.data.objects.new("sun", sun_data)
    sun.rotation_euler = (0.7, 0.2, 0.9)
    scene.collection.objects.link(sun)

    world = bpy.data.worlds.new("world")
    world.color = (0.25, 0.27, 0.30)
    if getattr(world, "node_tree", None) is None and hasattr(world, "use_nodes"):
        world.use_nodes = True
    if world.node_tree is not None:
        bg = world.node_tree.nodes.get("Background")
        if bg is not None:
            bg.inputs["Color"].default_value = (0.25, 0.27, 0.30, 1.0)
            bg.inputs["Strength"].default_value = 0.6
    scene.world = world
    return scene


def configure_render(scene: bpy.types.Scene, samples: int, exr_path: Path) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.cycles.seed = SEED
    scene.cycles.use_animated_seed = False
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100

    # No date, time or host metadata in output files: identical pixels, identical bytes.
    for attr in dir(scene.render):
        if attr.startswith("use_stamp") and isinstance(getattr(scene.render, attr), bool):
            setattr(scene.render, attr, False)

    view_layer = scene.view_layers[0]
    view_layer.use_pass_z = True
    view_layer.use_pass_object_index = True

    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):  # Blender 5.x: pick the multilayer media type first
        settings.media_type = "MULTI_LAYER_IMAGE"
    settings.file_format = "OPEN_EXR_MULTILAYER"
    settings.color_depth = "32"
    settings.exr_codec = "ZIP"
    scene.render.filepath = str(exr_path)


def save_beauty_png(scene: bpy.types.Scene, path: Path) -> None:
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    settings.color_mode = "RGB"
    settings.color_depth = "8"
    bpy.data.images["Render Result"].save_render(filepath=str(path), scene=scene)
    # Cycles writes render-time text chunks into the PNG; re-encode the pixels without them.
    write_png(path, read_png_rgb(path), "uint8")


def read_exr_channels(path: Path) -> dict[str, np.ndarray]:
    """Return every channel of every subimage of a multilayer EXR as float32 HxW arrays.

    Blender 5.x writes multilayer EXR as multi-part: one subimage per pass.
    """
    channels: dict[str, np.ndarray] = {}
    nsubimages = oiio.ImageBuf(str(path)).nsubimages
    if nsubimages == 0:
        raise RuntimeError(f"Cannot open {path}: {oiio.geterror()}")
    for subimage in range(nsubimages):
        buf = oiio.ImageBuf(str(path), subimage, 0)
        pixels = buf.get_pixels(oiio.FLOAT)
        for i, name in enumerate(buf.spec().channelnames):
            channels[name] = np.asarray(pixels[:, :, i], dtype=np.float32)
    return channels


def find_channel(channels: dict[str, np.ndarray], *suffixes: str) -> np.ndarray:
    for name, data in channels.items():
        if name.endswith(suffixes):
            return data
    raise KeyError(f"No channel ending in {suffixes!r}; have {sorted(channels)}")


def write_png(path: Path, pixels: np.ndarray, fmt: str) -> None:
    if pixels.ndim == 2:
        pixels = pixels[:, :, None]
    h, w, c = pixels.shape
    out = oiio.ImageOutput.create(str(path))
    out.open(str(path), oiio.ImageSpec(w, h, c, fmt))
    out.write_image(np.ascontiguousarray(pixels))
    out.close()


def read_png_rgb(path: Path) -> np.ndarray:
    inp = oiio.ImageInput.open(str(path))
    spec = inp.spec()
    pixels = np.asarray(inp.read_image(0, 3, "uint8")).reshape(spec.height, spec.width, 3)
    inp.close()
    return pixels


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    scene = build_scene()
    device = configure_device(args.device)
    exr_path = out / "passes.exr"
    configure_render(scene, args.samples, exr_path)

    start = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    render_seconds = time.perf_counter() - start

    save_beauty_png(scene, out / "beauty.png")

    channels = read_exr_channels(exr_path)
    # Blender 5.x names the pass "Object Index"; older releases used "IndexOB".
    index = np.rint(find_channel(channels, "Object Index.X", "IndexOB.X")).astype(np.uint8)
    depth = find_channel(channels, "Depth.Z")
    hit = depth < 1e9

    write_png(out / "object_index.png", index, "uint8")
    index_vis = PALETTE[np.clip(index, 0, len(PALETTE) - 1)]
    write_png(out / "object_index_vis.png", index_vis, "uint8")

    depth_mm = np.where(hit, np.clip(depth * 1000.0, 0, 65535), 0).astype(np.uint16)
    write_png(out / "depth.png", depth_mm, "uint16")
    near, far = float(depth[hit].min()), float(depth[hit].max())
    depth_norm = np.where(hit, 1.0 - (depth - near) / max(far - near, 1e-6), 0.0)
    depth_vis = (np.clip(depth_norm, 0, 1) * 255).astype(np.uint8)
    write_png(out / "depth_vis.png", np.repeat(depth_vis[:, :, None], 3, axis=2), "uint8")

    beauty = read_png_rgb(out / "beauty.png")
    gutter = np.full((HEIGHT, 8, 3), 255, dtype=np.uint8)
    preview = np.concatenate(
        [beauty, gutter, index_vis, gutter, np.repeat(depth_vis[:, :, None], 3, axis=2)], axis=1
    )
    write_png(out / "preview.png", preview, "uint8")

    ids, counts = np.unique(index, return_counts=True)
    names = {0: "background", 1: "ground"} | {o[5]: o[0] for o in OBJECTS}
    report = {
        "blender_version": bpy.app.version_string,
        "blender_python": sys.version.split()[0],
        "device": device,
        "samples": args.samples,
        "resolution": [WIDTH, HEIGHT],
        "seed": SEED,
        "render_seconds": round(render_seconds, 3),
        "exr_channels": sorted(channels),
        "object_index_pixels": {
            names.get(int(i), str(int(i))): int(c) for i, c in zip(ids, counts, strict=True)
        },
        "depth_range_m": [round(near, 4), round(far, 4)],
        "files": sorted(p.name for p in out.iterdir()),
    }
    (out / "render.json").write_text(json.dumps(report, indent=2) + "\n")
    print("SMOKE_RENDER_REPORT " + json.dumps(report))


if __name__ == "__main__":
    main()
