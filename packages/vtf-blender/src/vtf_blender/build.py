"""Build a Blender scene from a scene file (read as plain dicts). Runs inside Blender.

Conventions follow vtf.core.scene: metres, Z up, quaternions w, x, y, z; an object's pose is the
centre of its local bounding box and its dimensions are full extents; cylinder and cone axes run
along local z, with the cone's base at -dz/2. Pass indices: objects 1..999, table top 1000,
gripper 1001; floor, table legs and world are 0, because the scene file does not model them.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

FLOOR_SIZE = 40.0
LEG_SIZE = 0.05
LEG_INSET = 0.06
SEGMENTS = 64


@dataclass
class BuiltScene:
    scene: bpy.types.Scene
    table: bpy.types.Object
    # (scene-file object dict, Blender object), sorted by pass index
    objects: list[tuple[dict, bpy.types.Object]]
    cameras: dict[str, bpy.types.Object]
    world: bpy.types.World
    scenery: list[bpy.types.Object] = field(default_factory=list)  # floor, legs, gripper parts

    def renderables(self) -> list[bpy.types.Object]:
        return [self.table, *self.scenery, *(ob for _, ob in self.objects)]


# ---------------------------------------------------------------- meshes


def _smooth_curved_faces(bm: bmesh.types.BMesh, shape: str) -> None:
    """Smooth-shade curved surfaces; keep caps flat with a sharp edge at the rim."""
    if shape == "sphere":
        for face in bm.faces:
            face.smooth = True
        return
    if shape not in ("cylinder", "cone"):
        return
    bm.normal_update()
    for face in bm.faces:
        face.smooth = abs(face.normal.z) < 0.999
    for edge in bm.edges:
        flags = {f.smooth for f in edge.link_faces}
        edge.smooth = len(flags) == 1


def primitive_mesh(name: str, shape: str, dims) -> bpy.types.Mesh:
    """A mesh centred on its bounding box with full extents `dims`."""
    dx, dy, dz = (float(v) for v in dims)
    scale = Matrix.Diagonal((dx, dy, dz, 1.0))
    bm = bmesh.new()
    if shape == "box":
        bmesh.ops.create_cube(bm, size=1.0, matrix=scale)
    elif shape == "cylinder":
        bmesh.ops.create_cone(
            bm, cap_ends=True, segments=SEGMENTS, radius1=0.5, radius2=0.5, depth=1.0, matrix=scale
        )
    elif shape == "cone":  # radius1 is the base, at local z = -depth/2
        bmesh.ops.create_cone(
            bm, cap_ends=True, segments=SEGMENTS, radius1=0.5, radius2=0.0, depth=1.0, matrix=scale
        )
    elif shape == "sphere":
        bmesh.ops.create_uvsphere(
            bm, u_segments=SEGMENTS, v_segments=SEGMENTS // 2, radius=0.5, matrix=scale
        )
    else:
        bm.free()
        raise ValueError(f"unknown primitive shape {shape!r}")
    _smooth_curved_faces(bm, shape)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def add_object(scene, name: str, mesh, position, rotation, pass_index: int, material):
    ob = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(ob)
    ob.rotation_mode = "QUATERNION"
    ob.location = Vector(position)
    ob.rotation_quaternion = Quaternion(rotation)
    ob.pass_index = pass_index
    if material is not None:
        ob.data.materials.append(material)
    return ob


# ---------------------------------------------------------------- materials


def _node_material(name: str) -> tuple[bpy.types.Material, bpy.types.ShaderNodeBsdfPrincipled]:
    mat = bpy.data.materials.new(name)
    if getattr(mat, "node_tree", None) is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    return mat, bsdf


def principled(name: str, rgb, roughness: float, metallic: float = 0.0) -> bpy.types.Material:
    mat, bsdf = _node_material(name)
    color = (*(float(v) for v in rgb), 1.0)
    mat.diffuse_color = color
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def wood_simple() -> bpy.types.Material:
    """Procedural wood: fine grain lines along the table's long (x) axis.

    Object coordinates are stretched along x, so noise turns into long streaks; a ramp maps the
    streaks between two browns.
    """
    mat, bsdf = _node_material("wood_simple")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (0.6, 14.0, 1.0)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.0
    noise.inputs["Detail"].default_value = 10.0
    noise.inputs["Roughness"].default_value = 0.6
    noise.inputs["Distortion"].default_value = 0.6
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.3
    ramp.color_ramp.elements[0].color = (0.16, 0.072, 0.030, 1.0)
    ramp.color_ramp.elements[1].position = 0.7
    ramp.color_ramp.elements[1].color = (0.34, 0.170, 0.075, 1.0)
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.45
    mat.diffuse_color = (0.25, 0.12, 0.05, 1.0)
    return mat


MATERIALS = {
    "wood_simple": wood_simple,
    "metal_grey": lambda: principled("metal_grey", (0.62, 0.62, 0.64), 0.35, 1.0),
}


def named_material(material_id: str) -> bpy.types.Material:
    if material_id not in MATERIALS:
        raise ValueError(f"unknown material_id {material_id!r}; have {sorted(MATERIALS)}")
    return MATERIALS[material_id]()


# ---------------------------------------------------------------- world, camera


def asset_file(assets_dir: Path, asset_id: str) -> Path:
    meta_path = Path(assets_dir) / asset_id / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"{asset_id} is not cached in {assets_dir} (no meta.json)")
    return meta_path.parent / json.loads(meta_path.read_text())["file"]


def hdri_world(lighting: dict, assets_dir: Path) -> bpy.types.World:
    world = bpy.data.worlds.new("hdri")
    if getattr(world, "node_tree", None) is None and hasattr(world, "use_nodes"):
        world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    background = nodes.get("Background") or nodes.new("ShaderNodeBackground")
    output = nodes.get("World Output") or nodes.new("ShaderNodeOutputWorld")
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(lighting["rotation_deg"]))
    env = nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(asset_file(assets_dir, lighting["hdri_id"])))
    links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    links.new(env.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Strength"].default_value = float(lighting["strength"])
    return world


def add_camera(scene, cam: dict) -> bpy.types.Object:
    data = bpy.data.cameras.new(cam["id"])
    data.type = "PERSP"
    data.lens = float(cam["focal_mm"])
    data.sensor_width = float(cam["sensor_width_mm"])
    data.sensor_fit = "HORIZONTAL"
    data.shift_x = data.shift_y = 0.0
    data.clip_start = float(cam["clip_start"])
    data.clip_end = float(cam["clip_end"])
    ob = bpy.data.objects.new(cam["id"], data)
    scene.collection.objects.link(ob)
    ob.rotation_mode = "QUATERNION"
    ob.location = Vector(cam["pose"]["position"])
    ob.rotation_quaternion = Quaternion(cam["pose"]["rotation"])
    return ob


# ---------------------------------------------------------------- scene


def build_scene(spec: dict, assets_dir: Path) -> BuiltScene:
    """Build table, floor, objects, gripper, cameras and HDRI world from a scene-file dict."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    t = spec["table"]
    sx, sy = (float(v) for v in t["size_xy"])
    height, thick = float(t["height"]), float(t["thickness"])
    wood = named_material(t["material_id"])
    table = add_object(
        scene,
        "table",
        primitive_mesh("table", "box", (sx, sy, thick)),
        (0.0, 0.0, height - thick / 2),
        (1, 0, 0, 0),
        int(t["pass_index"]),
        wood,
    )
    scenery = []
    leg_h = height - thick
    for i, (kx, ky) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
        pos = (kx * (sx / 2 - LEG_INSET), ky * (sy / 2 - LEG_INSET), leg_h / 2)
        mesh = primitive_mesh(f"leg_{i}", "box", (LEG_SIZE, LEG_SIZE, leg_h))
        scenery.append(add_object(scene, f"leg_{i}", mesh, pos, (1, 0, 0, 0), 0, wood))
    floor_mat = principled("floor", (0.22, 0.22, 0.22), 0.85)
    floor = add_object(
        scene,
        "floor",
        primitive_mesh("floor", "box", (FLOOR_SIZE, FLOOR_SIZE, 0.01)),
        (0.0, 0.0, -0.005),
        (1, 0, 0, 0),
        0,
        floor_mat,
    )
    scenery.append(floor)

    g = spec["gripper"]
    metal = named_material(g["material_id"])
    for part in g["parts"]:
        mesh = primitive_mesh(f"gripper_{part['name']}", part["shape"], part["dimensions"])
        ob = add_object(
            scene,
            f"gripper_{part['name']}",
            mesh,
            part["pose"]["position"],
            part["pose"]["rotation"],
            int(g["pass_index"]),
            metal,
        )
        scenery.append(ob)

    objects = []
    for obj in sorted(spec["objects"], key=lambda o: o["pass_index"]):
        if obj.get("source", "primitive") != "primitive":
            raise ValueError(
                f"{obj['id']}: only primitive objects render in v0, not {obj['shape']}"
            )
        mat = principled(obj["id"], obj["rgb"], float(obj["roughness"]), float(obj["metallic"]))
        mesh = primitive_mesh(obj["id"], obj["shape"], obj["dimensions"])
        pose = obj["pose"]
        ob = add_object(
            scene,
            obj["id"],
            mesh,
            pose["position"],
            pose.get("rotation", (1.0, 0.0, 0.0, 0.0)),
            int(obj["pass_index"]),
            mat,
        )
        objects.append((obj, ob))

    cameras = {cam["id"]: add_camera(scene, cam) for cam in spec["cameras"]}
    world = hdri_world(spec["lighting"], assets_dir)
    scene.world = world
    bpy.context.view_layer.update()
    return BuiltScene(
        scene=scene, table=table, objects=objects, cameras=cameras, world=world, scenery=scenery
    )
