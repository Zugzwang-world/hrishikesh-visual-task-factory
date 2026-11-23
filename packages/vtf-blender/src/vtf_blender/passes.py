"""Cycles renders: beauty on the requested device; object index, depth and amodal masks from
1-sample CPU renders, which are exact on re-render and equal the GPU index. Runs in Blender.

The data passes keep the beauty render's resolution, pixel filter and seed. (Whether their one
sample per pixel lands exactly where the beauty's first sample does is unverified; the index and
amodal passes share one sampling pattern, which is what the visibility gate needs.) Amodal masks
render each object alone (every other object, table, gripper, legs and floor hidden; no world),
so the visible pixels of an object in index.png are always a subset of its amodal mask.
"""

import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix

from vtf_blender.build import BuiltScene
from vtf_blender.files import read_index_and_depth, read_png_rgb, write_png

BOUNCE_ATTRS = (
    "max_bounces",
    "diffuse_bounces",
    "glossy_bounces",
    "transmission_bounces",
    "volume_bounces",
    "transparent_max_bounces",
)


def configure_common(scene: bpy.types.Scene, render: dict) -> None:
    """Settings shared by every render: Cycles, fixed seed, no stamps, fixed colour management."""
    scene.render.engine = "CYCLES"
    c = scene.cycles
    c.seed = int(render["seed"])
    c.use_animated_seed = False
    c.use_adaptive_sampling = False
    scene.render.use_persistent_data = False
    scene.render.film_transparent = False
    scene.render.use_motion_blur = False
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0
    # No date, time or host metadata in output files: identical pixels, identical bytes.
    for attr in dir(scene.render):
        if attr.startswith("use_stamp") and isinstance(getattr(scene.render, attr), bool):
            setattr(scene.render, attr, False)
    view = scene.view_settings
    view.view_transform = "AgX"
    view.look = "None"
    view.exposure = 0.0
    view.gamma = 1.0


def set_camera(scene: bpy.types.Scene, cam_ob: bpy.types.Object, resolution) -> None:
    scene.camera = cam_ob
    scene.render.resolution_x, scene.render.resolution_y = (int(v) for v in resolution)
    bpy.context.view_layer.update()


def camera_matrices(scene: bpy.types.Scene, cam_ob: bpy.types.Object) -> tuple[list, list]:
    """K (OpenCV, pixel origin top-left) and the 4x4 world -> OpenCV camera transform."""
    r = scene.render
    s = r.resolution_percentage / 100
    width, height = round(r.resolution_x * s), round(r.resolution_y * s)
    d = cam_ob.data
    assert d.type == "PERSP", d.type
    assert d.sensor_fit == "HORIZONTAL", d.sensor_fit
    assert d.shift_x == 0 and d.shift_y == 0, (d.shift_x, d.shift_y)
    assert r.pixel_aspect_x == r.pixel_aspect_y, (r.pixel_aspect_x, r.pixel_aspect_y)
    fx = d.lens / d.sensor_width * width
    k = [[fx, 0.0, width / 2], [0.0, fx, height / 2], [0.0, 0.0, 1.0]]
    w2c = Matrix.Diagonal((1.0, -1.0, -1.0, 1.0)) @ cam_ob.matrix_world.normalized().inverted()
    return k, [list(row) for row in w2c]


def _image_settings(scene: bpy.types.Scene, kind: str) -> None:
    settings = scene.render.image_settings
    if kind == "png":
        if hasattr(settings, "media_type"):
            settings.media_type = "IMAGE"
        settings.file_format = "PNG"
        settings.color_mode = "RGB"
        settings.color_depth = "8"
    else:
        if hasattr(settings, "media_type"):  # Blender 5.x: pick the multilayer media type first
            settings.media_type = "MULTI_LAYER_IMAGE"
        settings.file_format = "OPEN_EXR_MULTILAYER"
        settings.color_depth = "32"
        settings.exr_codec = "ZIP"


# Beauty speed settings (P12, measured on the M4 at 1024x768, hero_kitchen front camera):
# 64 samples took 8.1 s with Blender's defaults and 6.4 s with these; 32 samples took 3.6 s and
# looked the same after denoising. Adaptive sampling stops converged pixels early; a tabletop
# needs few bounces and no caustics; OIDN runs on the GPU with the fast prefilter.
BEAUTY_SETTINGS = {
    "use_adaptive_sampling": True,
    "adaptive_threshold": 0.02,
    "adaptive_min_samples": 0,
    "max_bounces": 6,
    "diffuse_bounces": 3,
    "glossy_bounces": 3,
    "transmission_bounces": 4,
    "transparent_max_bounces": 4,
    "volume_bounces": 0,
    "caustics_reflective": False,
    "caustics_refractive": False,
    "blur_glossy": 1.0,
    "denoiser": "OPENIMAGEDENOISE",
    "denoising_input_passes": "RGB_ALBEDO_NORMAL",
    "denoising_prefilter": "FAST",
    "denoising_quality": "BALANCED",
}


def render_beauty(built: BuiltScene, render: dict, device: str, path: Path) -> float:
    """Full render on `device` (GPU or CPU) to an 8-bit RGB PNG without text chunks."""
    scene = built.scene
    c = scene.cycles
    c.device = device
    c.samples = int(render["samples"])
    c.use_denoising = bool(render["denoise"])
    for attr, value in BEAUTY_SETTINGS.items():
        if hasattr(c, attr):
            setattr(c, attr, value)
    if hasattr(c, "denoising_use_gpu"):
        c.denoising_use_gpu = device == "GPU"
    scene.world = built.world
    view_layer = scene.view_layers[0]
    view_layer.use_pass_z = False
    view_layer.use_pass_object_index = False
    for ob in built.renderables():
        ob.hide_render = False
    start = time.perf_counter()
    bpy.ops.render.render()
    _image_settings(scene, "png")
    bpy.data.images["Render Result"].save_render(filepath=str(path), scene=scene)
    # Cycles writes render-time text chunks into the PNG; re-encode the pixels without them.
    write_png(path, read_png_rgb(path), "uint8")
    return time.perf_counter() - start


class DataPasses:
    """1-sample CPU renders with no bounces and no world: index, depth and amodal masks."""

    def __init__(self, built: BuiltScene, tmp_dir: Path) -> None:
        self.built = built
        self.tmp_dir = tmp_dir
        scene = built.scene
        c = scene.cycles
        keep = (*BOUNCE_ATTRS, "samples", "use_denoising", "device", "use_adaptive_sampling")
        self.saved = {a: getattr(c, a) for a in keep}
        c.device = "CPU"
        c.samples = 1
        c.use_denoising = False
        c.use_adaptive_sampling = False
        for attr in BOUNCE_ATTRS:
            setattr(c, attr, 0)
        scene.world = None
        _image_settings(scene, "exr")

    def _render(self, name: str, depth: bool) -> tuple[np.ndarray, np.ndarray | None]:
        scene = self.built.scene
        view_layer = scene.view_layers[0]
        view_layer.use_pass_z = depth
        view_layer.use_pass_object_index = True
        path = self.tmp_dir / f"{name}.exr"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        index, z = read_index_and_depth(path)
        path.unlink()
        return index, z

    def index_and_depth(self, camera_id: str) -> tuple[np.ndarray, np.ndarray]:
        for ob in self.built.renderables():
            ob.hide_render = False
        index, depth = self._render(f"data_{camera_id}", depth=True)
        assert depth is not None
        return index, depth

    def amodal(self, camera_id: str) -> dict[int, np.ndarray]:
        """pass_index -> bool mask of that object rendered alone."""
        masks = {}
        everything = self.built.renderables()
        for _, ob in self.built.objects:
            for other in everything:
                other.hide_render = other is not ob
            index, _ = self._render(f"amodal_{camera_id}_{ob.pass_index}", depth=False)
            masks[ob.pass_index] = index == ob.pass_index
        for other in everything:
            other.hide_render = False
        return masks

    def restore(self) -> None:
        scene = self.built.scene
        for attr, value in self.saved.items():
            setattr(scene.cycles, attr, value)
        scene.world = self.built.world
