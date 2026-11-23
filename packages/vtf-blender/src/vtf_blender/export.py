"""Export the settled scene as scene.glb for the Task Explorer. Display only, never truth. Runs in
Blender after the renders, so it may change the built scene freely (the next scene starts from
factory settings).

What goes in: the table, its legs, the gripper, every scene object and the cameras (named by
camera id). The floor and any other scenery larger than FLOOR_SKIP_M are left out. The table top
gets a thin textured plane painted from the scene file with the printed grid (table.grid_cell_m)
and the zone outlines, so the viewer shows them without procedural shaders; renderer objects
named *zone* or *grid* are left out so they are not drawn twice.

Size: every material becomes a plain Principled BSDF (base colour from its image texture, else its
viewport colour), textures shrink to TEXTURE_PX, heavy meshes get a Decimate modifier from a
triangle budget, and the budget halves until the file is under target_bytes (1 MB; disk is
tight at scale). A file still over max_bytes (3 MB) at the smallest budget is not kept. glTF is
Y-up: world z becomes glTF y and world y becomes glTF -z (the exporter's standard conversion).
"""

import os
import time
from pathlib import Path

import bpy
import numpy as np

from vtf_blender.build import BuiltScene

TARGET_BYTES = 1_000_000
MAX_BYTES = 3_000_000
TRIANGLE_BUDGETS = (40_000, 20_000, 10_000, 5_000)
MIN_TRIANGLES = 64  # never decimate an object below this
TEXTURE_PX = 256
TOP_TEXTURE_PX = 1024
FLOOR_SKIP_M = 5.0
DECAL_LIFT_M = 0.0005
GRID_LINE_M = 0.0015
WOOD_SRGB = (0.55, 0.38, 0.24)
GRID_SRGB = (0.12, 0.12, 0.12)


def _linear_to_srgb(c: float) -> float:
    c = min(max(float(c), 0.0), 1.0)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


# ---------------------------------------------------------------- objects


def _export_objects(built: BuiltScene) -> list[bpy.types.Object]:
    keep = []
    for ob in built.renderables():
        if ob.type != "MESH":
            continue
        name = ob.name.lower()
        if "zone" in name or "grid" in name or max(ob.dimensions) > FLOOR_SKIP_M:
            continue
        keep.append(ob)
    return keep


def _triangles(ob: bpy.types.Object) -> int:
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


# ---------------------------------------------------------------- materials


def _principled(mat: bpy.types.Material):
    if mat is None or getattr(mat, "node_tree", None) is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _upstream_image(socket, depth: int = 4):
    """The first image texture feeding this socket, following links up to `depth` nodes."""
    frontier = [link.from_node for link in socket.links]
    for _ in range(depth):
        nxt = []
        for node in frontier:
            if node.type == "TEX_IMAGE" and node.image is not None:
                return node.image
            for inp in node.inputs:
                nxt.extend(link.from_node for link in inp.links)
        frontier = nxt
    return None


def _shrink(image: bpy.types.Image, shrunk: set) -> None:
    if image.name in shrunk:
        return
    shrunk.add(image.name)
    w, h = image.size
    if max(w, h) > TEXTURE_PX:
        s = TEXTURE_PX / max(w, h)
        image.scale(max(1, round(w * s)), max(1, round(h * s)))


def _display_material(mat, cache: dict, shrunk: set) -> bpy.types.Material:
    key = mat.name if mat is not None else None
    if key in cache:
        return cache[key]
    new = bpy.data.materials.new(f"glb_{key or 'default'}")
    if getattr(new, "node_tree", None) is None and hasattr(new, "use_nodes"):
        new.use_nodes = True
    bsdf = _principled(new)
    src = _principled(mat)
    image = _upstream_image(src.inputs["Base Color"]) if src is not None else None
    if image is not None:
        _shrink(image, shrunk)
        tex = new.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        new.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        base = src.inputs["Base Color"] if src is not None else None
        if base is not None and not base.is_linked:
            color = tuple(base.default_value)
        elif mat is not None:
            color = tuple(mat.diffuse_color)
        else:
            color = (0.6, 0.6, 0.6, 1.0)
        bsdf.inputs["Base Color"].default_value = color
    for name, fallback in (("Roughness", 0.5), ("Metallic", 0.0)):
        value = fallback
        if src is not None and not src.inputs[name].is_linked:
            value = float(src.inputs[name].default_value)
        bsdf.inputs[name].default_value = value
    cache[key] = new
    return new


def _flatten_materials(objects: list[bpy.types.Object]) -> None:
    cache: dict = {}
    shrunk: set = set()
    for ob in objects:
        if not ob.material_slots:
            ob.data.materials.append(_display_material(None, cache, shrunk))
            continue
        for slot in ob.material_slots:
            slot.material = _display_material(slot.material, cache, shrunk)


# ---------------------------------------------------------------- table top


def _paint_table_top(spec: dict) -> np.ndarray | None:
    """(H, W, 3) sRGB float image of the table top seen from above (row 0 = +y edge), or None
    when there is no grid and no zone."""
    table = spec["table"]
    zones = spec.get("zones") or []
    cell = table.get("grid_cell_m")
    if not cell and not zones:
        return None
    sx, sy = (float(v) for v in table["size_xy"])
    w = TOP_TEXTURE_PX
    h = max(1, round(w * sy / sx))
    px = sx / w
    xs = (np.arange(w) + 0.5) * sx / w - sx / 2
    ys = sy / 2 - (np.arange(h) + 0.5) * sy / h
    xx, yy = np.meshgrid(xs, ys)
    img = np.empty((h, w, 3), np.float32)
    img[:] = WOOD_SRGB
    if cell:
        half = max(GRID_LINE_M, px) / 2
        cell = float(cell)
        on_x = np.abs(xx - np.round(xx / cell) * cell) <= half
        on_y = np.abs(yy - np.round(yy / cell) * cell) <= half
        img[on_x | on_y] = GRID_SRGB
    for zone in zones:
        rgb = zone.get("rgb") or (0.9, 0.1, 0.1)
        color = tuple(_linear_to_srgb(c) for c in rgb)
        line = max(float(zone.get("line_width_m", 0.012)), px)
        cx, cy = (float(v) for v in zone["center_xy"])
        dx, dy = xx - cx, yy - cy
        if zone["shape"] == "circle":
            r = float(zone["radius"])
            dist = np.hypot(dx, dy)
            ring = (dist <= r) & (dist >= r - line)
        else:
            yaw = np.radians(float(zone.get("yaw_deg", 0.0)))
            lx = np.cos(yaw) * dx + np.sin(yaw) * dy
            ly = -np.sin(yaw) * dx + np.cos(yaw) * dy
            hx, hy = (float(v) / 2 for v in zone["size_xy"])
            inside = (np.abs(lx) <= hx) & (np.abs(ly) <= hy)
            ring = inside & ((np.abs(lx) >= hx - line) | (np.abs(ly) >= hy - line))
        img[ring] = color
    return img


def _add_table_top(built: BuiltScene, spec: dict) -> bpy.types.Object | None:
    pixels = _paint_table_top(spec)
    if pixels is None:
        return None
    h, w, _ = pixels.shape
    image = bpy.data.images.new("table_top", w, h, alpha=False)
    rgba = np.concatenate([pixels, np.ones((h, w, 1), np.float32)], axis=2)
    image.pixels.foreach_set(np.ascontiguousarray(rgba[::-1]).ravel())  # Blender rows: bottom up
    image.pack()

    sx, sy = (float(v) for v in spec["table"]["size_xy"])
    z = float(spec["table"]["height"]) + DECAL_LIFT_M
    mesh = bpy.data.meshes.new("table_top")
    verts = [(-sx / 2, -sy / 2, z), (sx / 2, -sy / 2, z), (sx / 2, sy / 2, z), (-sx / 2, sy / 2, z)]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, (u, v) in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1)), strict=True):
        loop.uv = (u, v)
    mat = bpy.data.materials.new("glb_table_top")
    if getattr(mat, "node_tree", None) is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = _principled(mat)
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    mesh.materials.append(mat)
    ob = bpy.data.objects.new("table_top", mesh)
    built.scene.collection.objects.link(ob)
    return ob


# ---------------------------------------------------------------- export


def _decimate(objects: list[bpy.types.Object], budget: int) -> int:
    """Set Decimate modifiers so the meshes total about `budget` triangles; return the total."""
    counts = {ob.name: _triangles(ob) for ob in objects}
    cap = max(MIN_TRIANGLES, budget // max(1, len(objects)))
    total = 0
    for ob in objects:
        tris = counts[ob.name]
        mod = ob.modifiers.get("glb_decimate")
        if tris > cap:
            if mod is None:
                mod = ob.modifiers.new("glb_decimate", "DECIMATE")
            mod.ratio = cap / tris
            total += cap
        else:
            if mod is not None:
                ob.modifiers.remove(mod)
            total += tris
    return total


def export_glb(
    built: BuiltScene,
    spec: dict,
    path: Path,
    target_bytes: int = TARGET_BYTES,
    max_bytes: int = MAX_BYTES,
) -> dict:
    """Write path (GLB) from the built, settled scene; return bytes, triangles and seconds."""
    start = time.perf_counter()
    path = Path(path)
    path.unlink(missing_ok=True)
    objects = _export_objects(built)
    _flatten_materials(objects)
    top = _add_table_top(built, spec)
    meshes = [*objects, *([top] if top else [])]
    cameras = list(built.cameras.values())
    for ob in built.scene.objects:
        ob.select_set(False)
    for ob in (*meshes, *cameras):
        ob.select_set(True)
    size, triangles = 0, 0
    for budget in TRIANGLE_BUDGETS:
        triangles = _decimate(objects, budget)
        bpy.ops.export_scene.gltf(
            filepath=str(path),
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_cameras=True,
            export_lights=False,
            export_animations=False,
            export_skins=False,
            export_morph=False,
            export_extras=False,
            export_yup=True,
            export_texcoords=True,
            export_normals=True,
            export_tangents=False,
            export_materials="EXPORT",
            export_image_format="JPEG",
            export_jpeg_quality=80,
        )
        size = os.path.getsize(path)
        if size <= target_bytes:
            break
    if size > max_bytes:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"scene.glb is {size} bytes at the smallest budget (> {max_bytes})")
    return {
        "bytes": size,
        "triangles": int(triangles),
        "seconds": time.perf_counter() - start,
    }
