"""Rigid-body settle with Bullet, following the expert's Blender 5.3 measurements. Runs in Blender.

The table is a passive box; objects are active with primitive collision shapes (box, sphere,
cylinder, cone; convex hulls are 10x slower and let spheres roll) and mass = density x volume.
The gripper is not simulated. Every frame is stepped in order (never jump frames), the poses
are copied, the rigid-body world is removed (objects snap back to spawn) and the settled poses
are set again, rounded to a micrometre, so the render is a pure function of scene.settled.json.
Bullet is bit-identical run to run on one machine.
"""

import math
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from vtf_blender.build import BuiltScene
from vtf_blender.files import r6

COLLISION_SHAPE = {"box": "BOX", "sphere": "SPHERE", "cylinder": "CYLINDER", "cone": "CONE"}
VOLUME_FACTOR = {"box": 1.0, "sphere": math.pi / 6, "cylinder": math.pi / 4, "cone": math.pi / 12}
MOVING_M = 1e-5  # translation per frame between the last two frames, metres
MOVING_RAD = 1e-4  # rotation per frame between the last two frames, radians


def rounded_pose(matrix: Matrix) -> tuple[list[float], list[float]]:
    """Position and w >= 0 quaternion of a rigid matrix, rounded to 6 digits."""
    loc, rot, _ = matrix.decompose()
    q = rot.normalized()
    if q.w < 0:
        q = -q
    return [r6(v) for v in loc], [r6(v) for v in (q.w, q.x, q.y, q.z)]


def lowest_z(ob: bpy.types.Object, position, rotation) -> float:
    mesh = ob.data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    rot = np.array(Quaternion(rotation).normalized().to_matrix())
    return float((co @ rot.T)[:, 2].min() + position[2])


def settle(built: BuiltScene, spec: dict) -> tuple[dict[str, tuple[list, list]], dict]:
    """Drop the objects onto the table; return settled poses by object id and a SettleReport."""
    scene = built.scene
    phys = spec["physics"]
    frames = int(phys["settle_frames"])
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

    margin = float(phys["collision_margin"])
    for ob in [built.table] + [ob for _, ob in built.objects]:  # table first, then pass index
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
    for obj, ob in built.objects:
        rb = ob.rigid_body
        rb.type = "ACTIVE"
        rb.collision_shape = COLLISION_SHAPE[obj["shape"]]
        dx, dy, dz = (float(v) for v in obj["dimensions"])
        rb.mass = float(phys["density_kg_m3"]) * VOLUME_FACTOR[obj["shape"]] * dx * dy * dz
        rb.linear_damping = float(phys["linear_damping"])
        rb.angular_damping = float(phys["angular_damping"])
        rb.use_deactivation = bool(phys["use_deactivation"])

    previous: dict[str, Matrix] = {}
    for f in range(1, frames + 1):
        scene.frame_set(f)
        if f == frames - 1:
            previous = {obj["id"]: ob.matrix_world.copy() for obj, ob in built.objects}
    final = {obj["id"]: ob.matrix_world.copy() for obj, ob in built.objects}

    with bpy.context.temp_override(scene=scene):
        bpy.ops.rigidbody.world_remove()
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
        gap[oid] = r6(lowest_z(ob, position, rotation) - top)
        x, y, z = position
        if abs(x) > half_x or abs(y) > half_y or z < top:
            off_table.append(oid)
        if oid in previous:
            a, b = previous[oid], final[oid]
            step = (b.translation - a.translation).length
            turn = a.to_quaternion().rotation_difference(b.to_quaternion()).angle
            if step > MOVING_M or min(turn, 2 * math.pi - turn) > MOVING_RAD:
                moving.append(oid)
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
