"""Rigid-body settle with Bullet, following the expert's Blender 5.3 measurements. Runs in Blender.

The table is a passive box. Primitive objects (and procedural assets) are active with primitive
collision shapes (box, sphere, cylinder, cone; convex hulls are 10x slower and let spheres roll)
and mass = density x volume. Poly Haven assets use the manifest's collision proxy: a convex hull
(or a bounding box) for movable objects, and a passive triangle mesh for open containers, which
never move: before the drop a passive object is lowered until its lowest point touches the table
top (the sampler may spawn it a little above). Objects placed on (stacks) or in (containers)
another object drop onto it like any other body. The gripper is not simulated.

Every frame is stepped in order (never jump frames), the poses are copied, the rigid-body world
is removed (objects snap back to spawn) and the settled poses are set again, rounded to a
micrometre, so the render is a pure function of scene.settled.json. Bullet is bit-identical run
to run on one machine.

support_gap_m in the settle report: for placement "table", the object's lowest point minus the
table top (as in v0.1). For placement "on" and "in", the smallest distance from the object's
mesh to any other body (its parent, another object or the table), so it is about 0 whenever the
object rests on something, whatever that is (a contained object may rest on another one).
"""

import math
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

from vtf_blender.build import BuiltScene
from vtf_blender.files import r6

COLLISION_SHAPE = {"box": "BOX", "sphere": "SPHERE", "cylinder": "CYLINDER", "cone": "CONE"}
ASSET_COLLISION = {"convex_hull": "CONVEX_HULL", "box": "BOX", "mesh": "MESH"}
VOLUME_FACTOR = {"box": 1.0, "sphere": math.pi / 6, "cylinder": math.pi / 4, "cone": math.pi / 12}
HULL_FILL = 0.5  # mass of a convex-hull asset: density x bounding-box volume x HULL_FILL
MOVING_M = 1e-5  # translation per frame between the last two frames, metres
MOVING_RAD = 1e-4  # rotation per frame between the last two frames, radians
GAP_SAMPLES = 1500  # most vertices per body pair used for the on/in support gap
FLAT_BASE_M = 0.004  # hull proxies: points within this of the bottom also project onto it
HULL_GRID_M = 0.0005  # hull input points are deduplicated on this grid
HULL_MAX_VERTS = 96  # hull proxies keep at most this many vertices
HULL_BASE_VERTS = 16  # of which this many outline the flat base
MESH_MAX_TRIANGLES = 1500  # container colliders are decimated to at most this many triangles

_PROXY_MESHES: dict[str, bpy.types.Mesh] = {}


def rounded_pose(matrix: Matrix) -> tuple[list[float], list[float]]:
    """Position and w >= 0 quaternion of a rigid matrix, rounded to 6 digits."""
    loc, rot, _ = matrix.decompose()
    q = rot.normalized()
    if q.w < 0:
        q = -q
    return [r6(v) for v in loc], [r6(v) for v in (q.w, q.x, q.y, q.z)]


def local_vertices(ob: bpy.types.Object) -> np.ndarray:
    mesh = ob.data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def world_vertices(ob: bpy.types.Object, position, rotation) -> np.ndarray:
    rot = np.array(Quaternion(rotation).normalized().to_matrix())
    return local_vertices(ob) @ rot.T + np.asarray(position, dtype=np.float64)


def lowest_z(ob: bpy.types.Object, position, rotation) -> float:
    return float(world_vertices(ob, position, rotation)[:, 2].min())


def body_kind(obj: dict, entry: dict | None) -> tuple[str, str, bool]:
    """(collision shape, mass rule, passive) for a scene object and its manifest entry."""
    if entry is None:
        return COLLISION_SHAPE[obj["shape"]], obj["shape"], False
    if entry["source"] == "procedural":
        shape = entry.get("shape") or "box"
        if shape == "sphere":  # a flat-based hull proxy, so the ball does not roll away
            return "CONVEX_HULL", shape, False
        return COLLISION_SHAPE[shape], shape, False
    collision = entry.get("collision", "convex_hull")
    if collision == "primitive":
        raise ValueError(f"{entry['id']}: collision primitive is only for procedural assets")
    return ASSET_COLLISION[collision], "hull", bool(entry.get("passive", False))


def _hull_points(points: np.ndarray) -> np.ndarray:
    """The vertices of the convex hull of `points` (bmesh), in a stable order."""
    bm = bmesh.new()
    for p in points:
        bm.verts.new(p)
    result = bmesh.ops.convex_hull(bm, input=list(bm.verts))
    unused = {
        v
        for v in result["geom_interior"] + result["geom_unused"]
        if isinstance(v, bmesh.types.BMVert)
    }
    out = np.array([tuple(v.co) for v in bm.verts if v not in unused], dtype=np.float64)
    bm.free()
    return out[np.lexsort(out.T[::-1])]


def _farthest_points(points: np.ndarray, n: int, seed_points=None) -> np.ndarray:
    """Deterministic farthest-point subset of `points` (starts from the lowest, then farthest)."""
    if len(points) <= n:
        return points
    if seed_points is not None and len(seed_points):
        dist = np.min(np.linalg.norm(points[:, None] - seed_points[None], axis=2), axis=1)
        chosen = []
    else:
        first = int(np.lexsort(points.T[::-1])[0])
        chosen = [first]
        dist = np.linalg.norm(points - points[first], axis=1)
    while len(chosen) < n:
        i = int(np.argmax(dist))
        chosen.append(i)
        dist = np.minimum(dist, np.linalg.norm(points - points[i], axis=1))
    return points[sorted(chosen)]


def hull_proxy_mesh(mesh: bpy.types.Mesh) -> bpy.types.Mesh:
    """Convex hull of the mesh with a flat base: every vertex within FLAT_BASE_M of the lowest
    point is also projected onto the lowest plane, so a round object rests on a small disc in
    its modelled orientation instead of rolling away (Bullet has no rolling resistance here). The
    base is at the mesh's lowest point, so the visual mesh still touches what it rests on."""
    cached = _PROXY_MESHES.get(mesh.name)
    try:
        if cached is not None and cached.name in bpy.data.meshes:
            return cached
    except ReferenceError:
        pass
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    zmin = co[:, 2].min()
    low = co[co[:, 2] < zmin + FLAT_BASE_M].copy()
    low[:, 2] = zmin
    pts = np.unique(np.round(np.vstack([co, low]) / HULL_GRID_M).astype(np.int64), axis=0)
    hull = _hull_points(pts.astype(np.float64) * HULL_GRID_M)
    if len(hull) > HULL_MAX_VERTS:
        # Bullet's convex-vs-triangle-mesh cost grows with hull vertices: keep the base disc's
        # outline and a farthest-point subset of the rest (exact vertices, so the reduced hull
        # lies inside the true one, by under a millimetre for fruit-sized objects).
        on_base = hull[:, 2] <= zmin + 1e-9
        base = _farthest_points(hull[on_base], HULL_BASE_VERTS)
        rest = _farthest_points(hull[~on_base], HULL_MAX_VERTS - len(base), seed_points=base)
        hull = _hull_points(np.vstack([base, rest]))
    bm = bmesh.new()
    for p in hull:
        bm.verts.new(p)
    bmesh.ops.convex_hull(bm, input=list(bm.verts))
    proxy = bpy.data.meshes.new(f"{mesh.name}_hull")
    bm.to_mesh(proxy)
    bm.free()
    proxy.use_fake_user = True
    _PROXY_MESHES[mesh.name] = proxy
    return proxy


def collider_mesh(mesh: bpy.types.Mesh) -> bpy.types.Mesh:
    """A container's triangle-mesh collider, decimated to at most MESH_MAX_TRIANGLES (collapse
    decimation keeps the shape within about a millimetre at these sizes; Bullet's convex-vs-mesh
    cost grows with the triangles under each contact)."""
    key = f"{mesh.name}_collider"
    cached = _PROXY_MESHES.get(key)
    try:
        if cached is not None and cached.name in bpy.data.meshes:
            return cached
    except ReferenceError:
        pass
    tris = sum(len(p.vertices) - 2 for p in mesh.polygons)
    if tris <= MESH_MAX_TRIANGLES:
        _PROXY_MESHES[key] = mesh
        return mesh
    tmp = bpy.data.objects.new("vtf_decimate_tmp", mesh)
    bpy.context.scene.collection.objects.link(tmp)
    mod = tmp.modifiers.new("decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = MESH_MAX_TRIANGLES / tris
    bpy.context.view_layer.update()
    evaluated = tmp.evaluated_get(bpy.context.evaluated_depsgraph_get())
    collider = bpy.data.meshes.new_from_object(evaluated)
    collider.name = key
    collider.materials.clear()
    collider.use_fake_user = True
    bpy.data.objects.remove(tmp, do_unlink=True)
    _PROXY_MESHES[key] = collider
    return collider


def _mass(rule: str, dims, density: float) -> float:
    dx, dy, dz = (float(v) for v in dims)
    factor = HULL_FILL if rule == "hull" else VOLUME_FACTOR[rule]
    return max(density * factor * dx * dy * dz, 1e-4)


def settle(built: BuiltScene, spec: dict) -> tuple[dict[str, tuple[list, list]], dict]:
    """Drop the objects onto the table; return settled poses by object id and a SettleReport."""
    scene = built.scene
    phys = spec["physics"]
    frames = int(phys["settle_frames"])
    top = float(spec["table"]["height"])
    start = time.perf_counter()

    scene.render.fps = int(phys["fps"])
    scene.render.fps_base = 1.0
    scene.frame_start, scene.frame_end = 1, frames
    with bpy.context.temp_override(scene=scene):
        bpy.ops.rigidbody.world_add()
    rbw = scene.rigidbody_world
    rbw.time_scale = 1.0
    rbw.substeps_per_frame = int(phys["substeps_per_frame"])
    rbw.solver_iterations = int(phys["solver_iterations"])
    rbw.use_split_impulse = bool(phys["use_split_impulse"])
    rbw.point_cache.frame_start = 1
    rbw.point_cache.frame_end = frames
    rbw.collection = bpy.data.collections.new("RigidBodyWorld")

    # Passive objects (open containers) never move: rest them on the table top first.
    kinds = {}
    for obj, ob in built.objects:
        kinds[obj["id"]] = body_kind(obj, built.assets.get(obj["id"]))
        if kinds[obj["id"]][2]:
            q = ob.rotation_quaternion
            drop = lowest_z(ob, ob.location, (q.w, q.x, q.y, q.z)) - top
            ob.location.z -= drop
    bpy.context.view_layer.update()

    # Convex-hull assets simulate a hidden flat-based hull proxy with the same transform; its
    # final matrix is copied to the visual object.
    bodies: dict[str, bpy.types.Object] = {}
    proxies = []
    for obj, ob in built.objects:
        shape, rule, _ = kinds[obj["id"]]
        proxy_mesh = None
        if shape == "CONVEX_HULL":
            proxy_mesh = hull_proxy_mesh(ob.data)
        elif shape == "MESH":
            proxy_mesh = collider_mesh(ob.data)
        if proxy_mesh is not None:
            proxy = bpy.data.objects.new(f"{obj['id']}_proxy", proxy_mesh)
            scene.collection.objects.link(proxy)
            proxy.rotation_mode = "QUATERNION"
            proxy.location = ob.location.copy()
            proxy.rotation_quaternion = ob.rotation_quaternion.copy()
            proxy.hide_render = True
            proxies.append(proxy)
            bodies[obj["id"]] = proxy
        else:
            bodies[obj["id"]] = ob
    bpy.context.view_layer.update()

    margin = float(phys["collision_margin"])
    order = [built.table] + [bodies[obj["id"]] for obj, _ in built.objects]
    for ob in order:  # table first, then pass index
        rbw.collection.objects.link(ob)
        with bpy.context.temp_override(
            object=ob, active_object=ob, selected_objects=[ob], scene=scene
        ):
            bpy.ops.rigidbody.object_add()
        rb = ob.rigid_body
        rb.use_margin = True
        rb.collision_margin = margin
        rb.friction = float(phys["friction"])
        rb.restitution = float(phys["restitution"])
    built.table.rigid_body.type = "PASSIVE"
    built.table.rigid_body.collision_shape = "BOX"
    for obj, _ in built.objects:
        shape, rule, passive = kinds[obj["id"]]
        rb = bodies[obj["id"]].rigid_body
        rb.type = "PASSIVE" if passive else "ACTIVE"
        rb.collision_shape = shape
        if shape in ("MESH", "CONVEX_HULL"):
            rb.mesh_source = "BASE"
        if passive:
            continue
        rb.mass = _mass(rule, obj["dimensions"], float(phys["density_kg_m3"]))
        rb.linear_damping = float(phys["linear_damping"])
        rb.angular_damping = float(phys["angular_damping"])
        rb.use_deactivation = bool(phys["use_deactivation"])

    previous: dict[str, Matrix] = {}
    for f in range(1, frames + 1):
        scene.frame_set(f)
        if f == frames - 1:
            previous = {oid: body.matrix_world.copy() for oid, body in bodies.items()}
    final = {oid: body.matrix_world.copy() for oid, body in bodies.items()}

    with bpy.context.temp_override(scene=scene):
        bpy.ops.rigidbody.world_remove()
    for proxy in proxies:
        bpy.data.objects.remove(proxy, do_unlink=True)
    scene.frame_set(1)

    poses: dict[str, tuple[list, list]] = {}
    for obj, ob in built.objects:
        position, rotation = rounded_pose(final[obj["id"]])
        poses[obj["id"]] = (position, rotation)
        ob.location = Vector(position)
        ob.rotation_quaternion = Quaternion(rotation)
    bpy.context.view_layer.update()
    seconds = time.perf_counter() - start
    return poses, settle_report(built, spec, poses, previous, final, seconds)


def _subsample(points: np.ndarray, n: int = GAP_SAMPLES) -> np.ndarray:
    if len(points) <= n:
        return points
    step = int(math.ceil(len(points) / n))
    return points[::step]


def _bvh(ob: bpy.types.Object, position, rotation) -> BVHTree:
    verts = world_vertices(ob, position, rotation)
    polys = [tuple(p.vertices) for p in ob.data.polygons]
    return BVHTree.FromPolygons([tuple(v) for v in verts], polys, all_triangles=False)


def _min_distance(points: np.ndarray, tree: BVHTree, limit: float) -> float:
    best = limit
    for p in points:
        hit = tree.find_nearest(Vector(p), best)
        if hit[0] is not None and hit[3] < best:
            best = hit[3]
    return best


def _near(points: np.ndarray, lo: np.ndarray, hi: np.ndarray, pad: float) -> np.ndarray:
    keep = np.all((points >= lo - pad) & (points <= hi + pad), axis=1)
    return points[keep]


def contact_gap(built: BuiltScene, spec: dict, oid: str, poses: dict, limit: float = 0.02) -> float:
    """Smallest distance (capped at `limit`) between object `oid` and any other body: the table
    top and every object whose bounding box comes within `limit`. Vertex-to-surface distances
    both ways, on vertices inside the other body's padded bounding box."""
    obs = {obj["id"]: ob for obj, ob in built.objects}
    ob = obs[oid]
    position, rotation = poses[oid]
    own_pts = world_vertices(ob, position, rotation)
    lo, hi = own_pts.min(axis=0), own_pts.max(axis=0)
    best = min(limit, max(float(lo[2]) - float(spec["table"]["height"]), 0.0))
    own = None
    for other_id, other in obs.items():
        if other_id == oid or best <= 1e-6:
            continue
        o_pos, o_rot = poses[other_id]
        o_pts = world_vertices(other, o_pos, o_rot)
        o_lo, o_hi = o_pts.min(axis=0), o_pts.max(axis=0)
        if np.any(o_lo > hi + best) or np.any(o_hi < lo - best):
            continue  # bounding boxes farther apart than the best gap so far
        if own is None:
            own = _bvh(ob, position, rotation)
        tree = _bvh(other, o_pos, o_rot)
        if own.overlap(tree):
            return 0.0
        mine = _subsample(_near(own_pts, o_lo, o_hi, best))
        best = min(best, _min_distance(mine, tree, best))
        theirs = _subsample(_near(o_pts, lo, hi, best))
        best = min(best, _min_distance(theirs, own, best))
    return 0.0 if best <= 1e-6 else best


def settle_report(built, spec, poses, previous, final, seconds) -> dict:
    t = spec["table"]
    half_x, half_y = (float(v) / 2 for v in t["size_xy"])
    top = float(t["height"])
    displacement, gap, off_table, moving = {}, {}, [], []
    for obj, ob in built.objects:
        oid = obj["id"]
        position, rotation = poses[oid]
        spawn = Vector(obj["pose"]["position"])
        displacement[oid] = r6((Vector(position) - spawn).length)
        if obj.get("placement", {}).get("kind", "table") == "table":
            gap[oid] = r6(lowest_z(ob, position, rotation) - top)
        else:
            gap[oid] = r6(contact_gap(built, spec, oid, poses))
        x, y, z = position
        if abs(x) > half_x or abs(y) > half_y or z < top:
            off_table.append(oid)
        if oid in previous:
            a, b = previous[oid], final[oid]
            step = (b.translation - a.translation).length
            turn = a.to_quaternion().rotation_difference(b.to_quaternion()).angle
            if step > MOVING_M or min(turn, 2 * math.pi - turn) > MOVING_RAD:
                moving.append(oid)
                print(f"VTF_SETTLE_MOVING {oid} step_m={step:.2e} turn_rad={turn:.2e}")
    phys = spec["physics"]
    return {
        "frames": int(phys["settle_frames"]),
        "substeps_per_frame": int(phys["substeps_per_frame"]),
        "seconds": round(seconds, 3),
        "displacement_m": displacement,
        "max_displacement_m": max(displacement.values(), default=0.0),
        "off_table": off_table,
        "moving_at_end": moving,
        "support_gap_m": gap,
    }
