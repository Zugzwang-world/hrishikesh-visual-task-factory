"""Sampler v0: recipe + seed -> SceneSpec (as sampled, before the renderer's settle).

`sample_scene` is a pure function of (recipe, scene_id, seed). All randomness comes from one
`make_rng(seed)` stream, drawn in a fixed order. Every float is rounded to 6 digits before it is
checked or stored, so the constraints hold for exactly the values written to the file.

Order of draws in one layout attempt: camera, objects (count, colour-shape pairs, then size,
rotation and position for each object in id order), gripper, lighting. An object whose position
draws all fail (overlap or out of view), or a gripper that never lands in view, restarts the
whole attempt on the same stream; after `max_layout_attempts` the sampler raises.
"""

import math

import numpy as np

from vtf.core.geometry import (
    IDENTITY_QUAT,
    Pose,
    look_at_camera_quat,
    quat_from_axis_angle,
    round_vec,
    transform_points,
)
from vtf.core.projection import project
from vtf.core.rng import make_rng
from vtf.core.scene import (
    CameraSpec,
    Gripper,
    Lighting,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    build_gripper,
)
from vtf.core.vocab import linear_rgb
from vtf.scene.recipe import Recipe

GENERATOR_VERSION = "vtf.scene.sampler_v0 0.1.0"
Z_AXIS = (0.0, 0.0, 1.0)
VIEW_CHECKED_GRIPPER_PARTS = ("finger_left", "finger_right", "palm")


class LayoutError(RuntimeError):
    """No valid layout within the recipe's attempt budget."""


def _r(value: float) -> float:
    return round(float(value), 6) + 0.0


def _uniform(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    return _r(rng.uniform(bounds[0], bounds[1]))


def _yaw_quat(yaw_deg: float) -> tuple[float, float, float, float]:
    return round_vec(quat_from_axis_angle(Z_AXIS, math.radians(yaw_deg)))  # type: ignore[return-value]


# Geometry helpers (also used by the tests) ---------------------------------------------------


def footprint_radius(shape: str, dimensions: tuple[float, float, float]) -> float:
    """Radius of the smallest circle around the object's footprint on the table.

    Boxes: half the footprint diagonal (holds for any yaw). Cylinders, cones, spheres: half the
    diameter.
    """
    dx, dy, _ = dimensions
    return math.hypot(dx, dy) / 2 if shape == "box" else dx / 2


def box_corners(pose: Pose, dimensions: tuple[float, float, float]) -> np.ndarray:
    """(8, 3) world corners of the local bounding box."""
    half = np.asarray(dimensions, dtype=float) / 2
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    return transform_points(pose.matrix(), signs * half)


def in_view(camera: CameraSpec, points_world: np.ndarray, margin_px: float) -> bool:
    """True if every point is in front of the camera and projects inside the image margin."""
    uv, depth = project(camera, points_world)
    width, height = camera.resolution
    return bool(
        np.all(depth > camera.clip_start)
        and np.all((uv[:, 0] >= margin_px) & (uv[:, 0] <= width - margin_px))
        and np.all((uv[:, 1] >= margin_px) & (uv[:, 1] <= height - margin_px))
    )


def gripper_view_points(gripper: Gripper) -> np.ndarray:
    """Corners of the gripper parts that must be in view (fingers and palm)."""
    return np.concatenate(
        [
            box_corners(part.pose, part.dimensions)
            for part in gripper.parts
            if part.name in VIEW_CHECKED_GRIPPER_PARTS
        ]
    )


# Sampling steps ------------------------------------------------------------------------------


def _sample_camera(rng: np.random.Generator, recipe: Recipe) -> CameraSpec:
    c = recipe.camera
    jitter = c.target_jitter_m
    target = (_r(rng.uniform(-jitter, jitter)), _r(rng.uniform(-jitter, jitter)),
              recipe.table.height)  # fmt: skip
    distance = _uniform(rng, c.distance_m)
    elevation = math.radians(_uniform(rng, c.elevation_deg))
    azimuth = math.radians(_uniform(rng, c.azimuth_deg))
    horizontal = distance * math.cos(elevation)
    eye = round_vec(
        (
            target[0] + horizontal * math.sin(azimuth),
            target[1] - horizontal * math.cos(azimuth),
            target[2] + distance * math.sin(elevation),
        )
    )
    return CameraSpec(
        id=c.id,
        rig=c.rig,
        pose=Pose(position=eye, rotation=round_vec(look_at_camera_quat(eye, target))),
        focal_mm=_uniform(rng, c.focal_mm),
        sensor_width_mm=c.sensor_width_mm,
        resolution=c.resolution,
        clip_start=c.clip_start,
        clip_end=c.clip_end,
    )


def _sample_shape(
    rng: np.random.Generator, recipe: Recipe, shape: str
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Dimensions and rotation for one primitive."""
    size = recipe.objects.size_m
    if shape == "box":
        dims = (_uniform(rng, size), _uniform(rng, size), _uniform(rng, size))
        return dims, _yaw_quat(_uniform(rng, recipe.objects.box_yaw_deg))
    if shape in ("cylinder", "cone"):
        diameter = _uniform(rng, size)
        return (diameter, diameter, _uniform(rng, size)), IDENTITY_QUAT
    if shape == "sphere":
        diameter = _uniform(rng, size)
        return (diameter, diameter, diameter), IDENTITY_QUAT
    raise ValueError(f"unknown primitive shape {shape!r}")


def _sample_objects(
    rng: np.random.Generator, recipe: Recipe, camera: CameraSpec
) -> list[SceneObject] | None:
    ob, pl = recipe.objects, recipe.placement
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    top = recipe.table.height
    pairs = [(colour, shape) for colour in ob.colours for shape in ob.shapes]
    n = int(rng.integers(ob.count[0], ob.count[1] + 1))
    chosen = rng.choice(len(pairs), size=n, replace=False)

    objects: list[SceneObject] = []
    circles: list[tuple[float, float, float]] = []
    for i, pair_index in enumerate(chosen):
        colour, shape = pairs[int(pair_index)]
        dims, rotation = _sample_shape(rng, recipe, shape)
        radius = footprint_radius(shape, dims)
        lim_x = half_x - pl.table_margin_m - radius
        lim_y = half_y - pl.table_margin_m - radius
        if lim_x < 0 or lim_y < 0:
            raise ValueError(f"a {shape} of {dims} m does not fit on the table with the margin")
        z = _r(top + dims[2] / 2 + ob.drop_m)
        for _ in range(pl.tries_per_object):
            x, y = _r(rng.uniform(-lim_x, lim_x)), _r(rng.uniform(-lim_y, lim_y))
            if any(math.hypot(x - cx, y - cy) < radius + cr + pl.gap_m for cx, cy, cr in circles):
                continue
            pose = Pose(position=(x, y, z), rotation=rotation)
            if not in_view(camera, box_corners(pose, dims), recipe.camera.view_margin_px):
                continue
            break
        else:
            return None
        circles.append((x, y, radius))
        objects.append(
            SceneObject(
                id=f"obj_{i:02d}",
                source="primitive",
                shape=shape,
                category=shape,
                color_name=colour,
                rgb=linear_rgb(colour),
                roughness=ob.roughness,
                metallic=ob.metallic,
                dimensions=dims,
                pose=pose,
                pass_index=i + 1,
            )
        )
    return objects


def _sample_gripper(
    rng: np.random.Generator, recipe: Recipe, camera: CameraSpec, objects: list[SceneObject]
) -> Gripper | None:
    g = recipe.gripper
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    lim_x, lim_y = half_x - g.xy_margin_m, half_y - g.xy_margin_m
    top = recipe.table.height
    for _ in range(g.tries):
        x, y = _r(rng.uniform(-lim_x, lim_x)), _r(rng.uniform(-lim_y, lim_y))
        tip_z = _r(top + _uniform(rng, g.tip_height_m))
        yaw = _uniform(rng, g.yaw_deg)
        gripper = build_gripper(Pose(position=(x, y, tip_z), rotation=_yaw_quat(yaw)))
        if not in_view(camera, gripper_view_points(gripper), recipe.camera.view_margin_px):
            continue
        # The gripper is not simulated: its fingertips (its lowest point) must clear the resting
        # top of the tallest object, wherever that object stands.
        tallest = max(o.dimensions[2] for o in objects)
        if tip_z - (top + tallest) < g.min_clearance_m:
            continue
        return gripper
    return None


def sample_scene(recipe: Recipe, scene_id: str, seed: int) -> SceneSpec:
    """Sample one scene. Same (recipe, scene_id, seed) -> the same SceneSpec, field for field."""
    rng = make_rng(seed)
    for _ in range(recipe.max_layout_attempts):
        camera = _sample_camera(rng, recipe)
        objects = _sample_objects(rng, recipe, camera)
        if objects is None:
            continue
        gripper = _sample_gripper(rng, recipe, camera, objects)
        if gripper is None:
            continue
        light = recipe.lighting
        return SceneSpec(
            meta=SceneMeta(
                scene_id=scene_id,
                generator_version=GENERATOR_VERSION,
                seed=seed,
                recipe_hash=recipe.recipe_hash,
                settled=False,
            ),
            table=Table(
                size_xy=recipe.table.size_xy,
                height=recipe.table.height,
                thickness=recipe.table.thickness,
                material_id=recipe.table.material_id,
            ),
            objects=objects,
            gripper=gripper,
            zones=[],
            cameras=[camera],
            lighting=Lighting(
                hdri_id=light.hdri_id,
                strength=light.strength,
                rotation_deg=_uniform(rng, light.rotation_deg),
            ),
            render=recipe.render.model_copy(),
            physics=recipe.physics.model_copy(),
        )
    raise LayoutError(
        f"{scene_id} (seed {seed}): no valid layout in {recipe.max_layout_attempts} attempts"
    )
