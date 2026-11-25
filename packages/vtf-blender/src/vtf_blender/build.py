"""Build a Blender scene from a scene file (read as plain dicts). Runs inside Blender.

Conventions follow vtf.core.scene: metres, Z up, quaternions w, x, y, z; an object's pose is the
centre of its local bounding box and its dimensions are full extents; cylinder and cone axes run
along local z, with the cone's base at -dz/2. Pass indices: objects 1..999, table top 1000,
gripper 1001; floor, table legs and world are 0, because the scene file does not model them.

Asset objects (source "asset") follow the vtf.core.assets mesh convention: import the glTF, join
every mesh with its node transforms applied (Blender's importer turns glTF +Y up into +Z up), scale
by the manifest scale times the object's scale, and recentre on the bounding-box centre. The
asset keeps its own PBR materials. Imported meshes are cached for the life of the Blender process,
so several scenes built in one process import each asset once. Procedural assets build as
primitives in the object's rgb.

Zones are painted into the table's material (outlines inside the zone boundary), so they are part
of the table in the index pass and have no geometry or collision. The cm_grid table material
prints lines on multiples of `table.grid_cell_m` from the table centre, bolder every 10 cm.
"""

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

FLOOR_SIZE = 40.0
LEG_SIZE = 0.05
LEG_INSET = 0.06
SEGMENTS = 64
REPO_ROOT = Path(__file__).resolve().parents[4]
MANIFEST_JSON = REPO_ROOT / "assets" / "manifest.json"
TWIN_MAT_TEXTURE = REPO_ROOT / "docs" / "twins" / "mat_texture.png"

GRID_PAPER_SRGB = (226, 224, 216)
GRID_LINE_SRGB = (38, 40, 46)
TEN_CM = 0.1


@dataclass
class BuiltScene:
    scene: bpy.types.Scene
    table: bpy.types.Object
    # (scene-file object dict, Blender object), sorted by pass index
    objects: list[tuple[dict, bpy.types.Object]]
    cameras: dict[str, bpy.types.Object]
    world: bpy.types.World
    scenery: list[bpy.types.Object] = field(default_factory=list)  # floor, legs, gripper parts
    lights: list[bpy.types.Object] = field(default_factory=list)
    assets: dict[str, dict] = field(default_factory=dict)  # object id -> manifest entry

    def renderables(self) -> list[bpy.types.Object]:
        return [self.table, *self.scenery, *(ob for _, ob in self.objects)]


# ---------------------------------------------------------------- asset manifest


_MANIFEST: dict[str, dict] = {}


def load_manifest(path: Path | None = None) -> dict[str, dict]:
    """Asset id -> manifest entry, from assets/manifest.json (Blender's Python has no YAML).

    The JSON mirror is written from assets/manifest.yaml by `assets/export_manifest_json.py`;
    tests check that it is current. $VTF_ASSET_MANIFEST_JSON overrides the path.
    """
    p = Path(path or os.environ.get("VTF_ASSET_MANIFEST_JSON", MANIFEST_JSON))
    key = str(p)
    if key not in _MANIFEST:
        data = json.loads(p.read_text(encoding="utf-8"))
        _MANIFEST.clear()
        _MANIFEST[key] = {a["id"]: a for a in data["assets"]}
    return _MANIFEST[key]


def asset_entry(obj: dict, manifest: dict[str, dict]) -> dict | None:
    if obj.get("source", "primitive") == "primitive":
        return None
    if obj["shape"] not in manifest:
        raise ValueError(f"{obj['id']}: asset {obj['shape']!r} is not in the asset manifest")
    return manifest[obj["shape"]]


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


def mesh_bounds(mesh: bpy.types.Mesh) -> tuple[np.ndarray, np.ndarray]:
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return co.min(axis=0), co.max(axis=0)


class AssetLibrary:
    """Imported asset meshes, cached for the whole Blender process (fake users keep them alive
    across scene resets). Key: (asset id, total scale)."""

    meshes: dict[tuple[str, float], bpy.types.Mesh] = {}
    # (asset id) -> joined mesh at scale 1, from which scaled copies are made
    base: dict[str, bpy.types.Mesh] = {}

    @classmethod
    def valid(cls, mesh) -> bool:
        try:
            return mesh is not None and mesh.name in bpy.data.meshes
        except ReferenceError:
            return False

    @classmethod
    def import_base(cls, entry: dict, assets_dir: Path) -> bpy.types.Mesh:
        asset_id = entry["id"]
        if cls.valid(cls.base.get(asset_id)):
            return cls.base[asset_id]
        path = Path(assets_dir) / entry["file"]
        if not path.exists():
            raise FileNotFoundError(
                f"{asset_id} is not cached: {path} (uv run python assets/fetch_polyhaven.py "
                f"{asset_id} --kind model)"
            )
        scene = bpy.context.scene
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(path))
        new = [ob for ob in bpy.data.objects if ob not in before]
        meshes = [ob for ob in new if ob.type == "MESH"]
        if not meshes:
            raise ValueError(f"{asset_id}: the glTF has no meshes")
        # Bake every node transform into a copy of each mesh, then join with bmesh, keeping
        # each source mesh's materials (material indices offset per source).
        bm = bmesh.new()
        materials: list = []
        for ob in meshes:
            me = ob.data.copy()
            me.transform(ob.matrix_world)
            offset = len(materials)
            materials.extend(me.materials)
            if offset:
                for poly in me.polygons:
                    poly.material_index += offset
            bm.from_mesh(me)
            bpy.data.meshes.remove(me)
        joined = bpy.data.meshes.new(f"asset_{asset_id}")
        bm.to_mesh(joined)
        bm.free()
        for mat in materials:
            joined.materials.append(mat)
        joined.use_fake_user = True
        for ob in new:
            data = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if (
                data is not None
                and getattr(data, "users", 1) == 0
                and isinstance(data, bpy.types.Mesh)
            ):
                bpy.data.meshes.remove(data)
        for mat in materials:
            if mat is not None:
                mat.use_fake_user = True
        bpy.context.view_layer.update()
        del scene
        cls.base[asset_id] = joined
        return joined

    @classmethod
    def mesh(cls, entry: dict, scale: float, assets_dir: Path) -> bpy.types.Mesh:
        """The asset's mesh in its centred, Z-up local frame at manifest scale times `scale`."""
        total = round(float(entry.get("scale", 1.0)) * float(scale), 9)
        key = (entry["id"], total)
        if cls.valid(cls.meshes.get(key)):
            return cls.meshes[key]
        base = cls.import_base(entry, assets_dir)
        me = base.copy()
        me.name = f"asset_{entry['id']}_x{total:g}"
        me.transform(Matrix.Scale(total, 4))
        lo, hi = mesh_bounds(me)
        me.transform(Matrix.Translation(Vector(-(lo + hi) / 2.0)))
        me.use_fake_user = True
        cls.meshes[key] = me
        return me


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


def srgb_to_linear(v: float) -> float:
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear(srgb) -> tuple[float, float, float, float]:
    return (*(srgb_to_linear(v) for v in srgb), 1.0)


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


def wood_simple(table: dict | None = None):
    """Procedural wood: fine grain lines along the table's long (x) axis.

    Object coordinates are stretched along x, so noise turns into long streaks; a ramp maps the
    streaks between two browns. Returns the material and its base-colour output socket.
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


class _Nodes:
    """Small helper for building math node graphs."""

    def __init__(self, mat: bpy.types.Material) -> None:
        self.nodes = mat.node_tree.nodes
        self.links = mat.node_tree.links

    def _in(self, node, index, value) -> None:
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, node.inputs[index])
        else:
            node.inputs[index].default_value = value

    def math(self, op: str, a, b=0.0, c=None):
        node = self.nodes.new("ShaderNodeMath")
        node.operation = op
        self._in(node, 0, a)
        self._in(node, 1, b)
        if c is not None:
            self._in(node, 2, c)
        return node.outputs[0]

    def xyz(self):
        geo = self.nodes.new("ShaderNodeNewGeometry")
        sep = self.nodes.new("ShaderNodeSeparateXYZ")
        self.links.new(geo.outputs["Position"], sep.inputs[0])
        nsep = self.nodes.new("ShaderNodeSeparateXYZ")
        self.links.new(geo.outputs["Normal"], nsep.inputs[0])
        return sep.outputs["X"], sep.outputs["Y"], nsep.outputs["Z"]

    def mix(self, factor, a, b):
        """Colour mix: a where factor is 0, b where 1 (a and b are sockets or RGBA tuples)."""
        node = self.nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = "MIX"
        node.clamp_factor = True
        self.links.new(factor, node.inputs["Factor"])
        for sock_name, value in (("A", a), ("B", b)):
            sock = next(s for s in node.inputs if s.name == sock_name and s.type == "RGBA")
            if isinstance(value, bpy.types.NodeSocket):
                self.links.new(value, sock)
            else:
                sock.default_value = value
        return next(s for s in node.outputs if s.type == "RGBA")

    def band(self, d, width):
        """1 where -width <= d <= 0 (inside the boundary, within `width` of it), else 0."""
        inside = self.math("LESS_THAN", d, 1e-9)
        deep = self.math("LESS_THAN", d, -width)
        return self.math("SUBTRACT", inside, deep)


def grid_lines(n: _Nodes, x, y, cell: float):
    """Line masks (thin, bold) for a printed grid on multiples of `cell` from the origin."""
    thin_w = min(max(cell * 0.08, 0.0008), 0.002)
    bold_w = 2.5 * thin_w

    def line(coord, spacing, width):
        nearest = n.math("MULTIPLY", n.math("ROUND", n.math("DIVIDE", coord, spacing)), spacing)
        dist = n.math("ABSOLUTE", n.math("SUBTRACT", coord, nearest))
        return n.math("LESS_THAN", dist, width / 2)

    thin = n.math("MAXIMUM", line(x, cell, thin_w), line(y, cell, thin_w))
    ratio = TEN_CM / cell
    if abs(ratio - round(ratio)) < 1e-6 and round(ratio) > 1:
        bold = n.math("MAXIMUM", line(x, TEN_CM, bold_w), line(y, TEN_CM, bold_w))
    else:
        bold = None
    return thin, bold


def zone_mask(n: _Nodes, x, y, zone: dict):
    """1 on the zone's outline (inside the boundary, line_width_m wide)."""
    cx, cy = (float(v) for v in zone["center_xy"])
    w = float(zone.get("line_width_m", 0.012))
    dx = n.math("SUBTRACT", x, cx)
    dy = n.math("SUBTRACT", y, cy)
    if zone["shape"] == "circle":
        r = float(zone["radius"])
        dist = n.math("SQRT", n.math("ADD", n.math("MULTIPLY", dx, dx), n.math("MULTIPLY", dy, dy)))
        return n.band(n.math("SUBTRACT", dist, r), w)
    hx, hy = (float(v) / 2 for v in zone["size_xy"])
    yaw = math.radians(float(zone.get("yaw_deg", 0.0)))
    c, s = math.cos(yaw), math.sin(yaw)
    # Rotate into the zone frame: p' = R(-yaw) (p - c).
    u = n.math("ADD", n.math("MULTIPLY", dx, c), n.math("MULTIPLY", dy, s))
    v = n.math("SUBTRACT", n.math("MULTIPLY", dy, c), n.math("MULTIPLY", dx, s))
    du = n.math("SUBTRACT", n.math("ABSOLUTE", u), hx)
    dv = n.math("SUBTRACT", n.math("ABSOLUTE", v), hy)
    return n.band(n.math("MAXIMUM", du, dv), w)


def zone_rgb(zone: dict) -> tuple[float, float, float, float]:
    if zone.get("rgb"):
        return (*(float(v) for v in zone["rgb"]), 1.0)
    return _linear((40, 160, 60))  # default: green


def twin_mat_material(table: dict) -> bpy.types.Material:
    """The printed physical-twin placement mat (docs/twins/mat_texture.png, made from
    docs/twins/mat_A3.pdf by `vtf twin texture`) stretched over the table top, which is the mat's
    grid area (40 x 28 cm): matte paper, the image on the up-facing faces, plain paper on the
    sides. $VTF_TWIN_MAT_TEXTURE overrides the image path."""
    path = Path(os.environ.get("VTF_TWIN_MAT_TEXTURE", TWIN_MAT_TEXTURE))
    if not path.exists():
        raise FileNotFoundError(f"twin mat texture not found: {path} (uv run vtf twin texture)")
    sx, sy = (float(v) for v in table["size_xy"])
    mat, bsdf = _node_material("twin_mat")
    bsdf.inputs["Roughness"].default_value = 0.8
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")  # object x, y (metres) -> u, v in 0..1
    mapping.inputs["Scale"].default_value = (1.0 / sx, 1.0 / sy, 1.0)
    mapping.inputs["Location"].default_value = (0.5, 0.5, 0.0)
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(path), check_existing=True)
    tex.image.colorspace_settings.name = "sRGB"
    tex.interpolation = "Cubic"
    tex.extension = "EXTEND"
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    n = _Nodes(mat)
    _, _, nz = n.xyz()
    top = n.math("GREATER_THAN", nz, 0.5)
    colour = n.mix(top, (0.9, 0.9, 0.9, 1.0), tex.outputs["Color"])
    links.new(colour, bsdf.inputs["Base Color"])
    mat.diffuse_color = (0.9, 0.9, 0.9, 1.0)
    return mat


def table_material(table: dict, zones: list[dict]) -> bpy.types.Material:
    """The table-top material (wood_simple, cm_grid or twin_mat) with zone outlines painted on
    top.

    Positions are world coordinates; the table is centred on the world origin, so the grid's
    lines sit on multiples of the cell measured from the table centre. Only up-facing surfaces
    (the top) get the grid and zone paint. twin_mat is the printed placement mat image; it carries
    its own grid, so grid_cell_m draws nothing more on it.
    """
    material_id = table.get("material_id", "wood_simple")
    cell = table.get("grid_cell_m")
    if material_id == "twin_mat":
        if zones:
            raise ValueError("twin_mat tables carry no zones")
        return twin_mat_material(table)
    if material_id == "wood_simple":
        mat = wood_simple()
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        base = bsdf.inputs["Base Color"].links[0].from_socket
    elif material_id == "cm_grid":
        mat, bsdf = _node_material("cm_grid")
        bsdf.inputs["Roughness"].default_value = 0.6
        base = _linear(GRID_PAPER_SRGB)
        mat.diffuse_color = base
        cell = cell or 0.01
    else:
        raise ValueError(
            f"unknown table material_id {material_id!r}; have wood_simple, cm_grid, twin_mat"
        )
    n = _Nodes(mat)
    if not cell and not zones:
        return mat
    x, y, nz = n.xyz()
    top = n.math("GREATER_THAN", nz, 0.5)
    colour = base
    if cell:
        thin, bold = grid_lines(n, x, y, float(cell))
        line_rgb = _linear(GRID_LINE_SRGB)
        colour = n.mix(n.math("MULTIPLY", thin, top), colour, line_rgb)
        if bold is not None:
            colour = n.mix(n.math("MULTIPLY", bold, top), colour, line_rgb)
    for zone in zones:
        mask = n.math("MULTIPLY", zone_mask(n, x, y, zone), top)
        colour = n.mix(mask, colour, zone_rgb(zone))
    if isinstance(colour, bpy.types.NodeSocket):
        n.links.new(colour, bsdf.inputs["Base Color"])
    return mat


MATERIALS = {
    "metal_grey": lambda: principled("metal_grey", (0.62, 0.62, 0.64), 0.35, 1.0),
}


def named_material(material_id: str) -> bpy.types.Material:
    if material_id not in MATERIALS:
        raise ValueError(f"unknown material_id {material_id!r}; have {sorted(MATERIALS)}")
    return MATERIALS[material_id]()


# ---------------------------------------------------------------- world, lights, camera


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
    env.image = bpy.data.images.load(
        str(asset_file(assets_dir, lighting["hdri_id"])), check_existing=True
    )
    links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    links.new(env.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Strength"].default_value = float(lighting["strength"])
    return world


def light_direction(elevation_deg: float, azimuth_deg: float) -> Vector:
    """Unit vector from the table toward the light. Azimuth follows the camera sampler: 0 is the
    -y side of the table (where the front camera stands), positive turns toward +x."""
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    return Vector(
        (math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
    ).normalized()


def key_light(scene, key: dict, target_z: float) -> bpy.types.Object:
    """Sun (angle_deg wide), area (size_m square) or spot at distance_m, aimed at the table."""
    kind = key.get("kind", "sun")
    direction = light_direction(float(key["elevation_deg"]), float(key["azimuth_deg"]))
    data = bpy.data.lights.new("key_light", type=kind.upper())
    data.energy = float(key["strength"])
    data.color = tuple(float(v) for v in key.get("rgb", (1.0, 1.0, 1.0)))
    if kind == "sun":
        data.angle = math.radians(float(key.get("angle_deg", 2.0)))
    elif kind == "area":
        data.shape = "SQUARE"
        data.size = float(key.get("size_m", 0.3))
    else:
        data.spot_size = math.radians(45.0)
        data.spot_blend = 0.3
        data.shadow_soft_size = 0.02
    ob = bpy.data.objects.new("key_light", data)
    scene.collection.objects.link(ob)
    ob.rotation_mode = "QUATERNION"
    # Lights shine along local -Z: point local +Z back toward the light's direction.
    ob.rotation_quaternion = direction.to_track_quat("Z", "Y")
    distance = float(key.get("distance_m", 2.0))
    ob.location = Vector((0.0, 0.0, target_z)) + direction * distance
    return ob


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


_FRESH = {"done": False}


def reset_scene() -> bpy.types.Scene:
    """An empty scene. The first call loads factory settings; later calls in the same process
    delete everything except cached asset meshes (and their materials and images), so a scene
    builds the same whether it is the first or the tenth in a process."""
    if not _FRESH["done"]:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _FRESH["done"] = True
        AssetLibrary.meshes.clear()
        AssetLibrary.base.clear()
        return bpy.context.scene
    scene = bpy.context.scene
    if scene.rigidbody_world is not None:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.rigidbody.world_remove()
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    scene.world = None
    scene.camera = None
    scene.frame_start, scene.frame_end = 1, 250
    scene.frame_set(1)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    return scene


def build_scene(
    spec: dict, assets_dir: Path, manifest: dict[str, dict] | None = None
) -> BuiltScene:
    """Build table, floor, objects, gripper, cameras, key light and HDRI world from a scene dict."""
    scene = reset_scene()
    needs_manifest = any(o.get("source", "primitive") != "primitive" for o in spec["objects"])
    if manifest is None and needs_manifest:
        manifest = load_manifest()

    t = spec["table"]
    sx, sy = (float(v) for v in t["size_xy"])
    height, thick = float(t["height"]), float(t["thickness"])
    top_mat = table_material(t, spec.get("zones") or [])
    wood = wood_simple() if t.get("material_id", "wood_simple") != "wood_simple" else top_mat
    table = add_object(
        scene,
        "table",
        primitive_mesh("table", "box", (sx, sy, thick)),
        (0.0, 0.0, height - thick / 2),
        (1, 0, 0, 0),
        int(t["pass_index"]),
        top_mat,
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

    objects, assets = [], {}
    for obj in sorted(spec["objects"], key=lambda o: o["pass_index"]):
        entry = asset_entry(obj, manifest or {})
        pose = obj["pose"]
        rotation = pose.get("rotation", (1.0, 0.0, 0.0, 0.0))
        if entry is None or entry["source"] == "procedural":
            shape = obj["shape"] if entry is None else entry.get("shape") or "box"
            mat = principled(obj["id"], obj["rgb"], float(obj["roughness"]), float(obj["metallic"]))
            mesh = primitive_mesh(obj["id"], shape, obj["dimensions"])
            ob = add_object(
                scene, obj["id"], mesh, pose["position"], rotation, int(obj["pass_index"]), mat
            )
        else:
            mesh = AssetLibrary.mesh(entry, float(obj.get("scale", 1.0)), assets_dir)
            ob = add_object(
                scene, obj["id"], mesh, pose["position"], rotation, int(obj["pass_index"]), None
            )
        if entry is not None:
            assets[obj["id"]] = entry
        objects.append((obj, ob))

    lights = []
    key = spec["lighting"].get("key_light")
    if key:
        lights.append(key_light(scene, key, height))

    cameras = {cam["id"]: add_camera(scene, cam) for cam in spec["cameras"]}
    world = hdri_world(spec["lighting"], assets_dir)
    scene.world = world
    bpy.context.view_layer.update()
    return BuiltScene(
        scene=scene,
        table=table,
        objects=objects,
        cameras=cameras,
        world=world,
        scenery=scenery,
        lights=lights,
        assets=assets,
    )
