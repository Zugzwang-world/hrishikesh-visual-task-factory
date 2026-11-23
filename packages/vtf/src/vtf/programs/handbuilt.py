"""Hand-built settled v1-like scenes for the question programs (no sampler, no Blender).

Real assets from assets/manifest.yaml (containers with contents, stacks on flat tops, look-alikes
with size words, procedural scale references), reach zones, a grid table, and front, overhead and
gripper cameras. Objects are placed at rest by ray casting down onto their support's mesh, so the
resting and inside rules hold exactly as after a settle.

    uv run python -m vtf.programs.handbuilt out/questions_demo   # writes <id>.settled.json
"""

import math
import sys
from pathlib import Path

import numpy as np

from vtf.core.assets import asset_local_mesh, load_asset_manifest
from vtf.core.geometry import Pose, look_at_camera_quat, quat_from_axis_angle, round_vec
from vtf.core.io import write_model
from vtf.core.scene import (
    CameraSpec,
    Lighting,
    Placement,
    QuestionMix,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    Zone,
    build_gripper,
)
from vtf.core.vocab import linear_rgb
from vtf.programs.geometry import obj_geom

TABLE_H = 0.75
RESOLUTION = (640, 480)
GRIPPER_CAM_OFFSET = (0.0, 0.035, 0.06)  # tip frame: beside the palm, looking down the approach


class SceneBuilder:
    def __init__(self, scene_id: str, seed: int, grid_cell_m: float | None = None) -> None:
        self.scene_id, self.seed, self.grid = scene_id, seed, grid_cell_m
        self.objects: list[SceneObject] = []
        self.zones: list[Zone] = []
        self.manifest = load_asset_manifest()

    def _surface(self, parent_id: str | None, x: float, y: float) -> float:
        if parent_id is None:
            return TABLE_H
        g = obj_geom(next(o for o in self.objects if o.id == parent_id))
        z = g.surface_below(x, y, g.top_z + 0.01)
        if z is None:
            raise ValueError(f"nothing of {parent_id} under ({x}, {y})")
        return z

    def add(self, asset_id: str, x: float, y: float, *, on: str | None = None, inside=False,
            yaw_deg: float = 0.0, color: str | None = None, size_word: str | None = None) -> str:
        """Place an asset at rest; with `on`, (x, y) is an offset from the parent's centre."""
        entry = self.manifest.get(asset_id)
        if on is not None:
            parent = next(o for o in self.objects if o.id == on)
            x, y = parent.pose.position[0] + x, parent.pose.position[1] + y
        rot = quat_from_axis_angle((0.0, 0.0, 1.0), math.radians(yaw_deg))
        local = asset_local_mesh(entry)
        local.apply_transform(Pose(position=(0.0, 0.0, 0.0), rotation=rot).matrix())
        low = float(np.asarray(local.vertices)[:, 2].min())
        z = self._surface(on, x, y) - low
        n = len(self.objects)
        colour = color or entry.color_name
        obj = SceneObject(
            id=f"obj_{n:02d}",
            source="asset",
            shape=asset_id,
            category=entry.category,
            color_name=colour,
            size_word=size_word,  # type: ignore[arg-type]
            rgb=linear_rgb(colour) if color else entry.rgb,
            dimensions=entry.size_m,
            pose=Pose(position=round_vec((x, y, z)), rotation=round_vec(rot)),  # type: ignore[arg-type]
            pass_index=n + 1,
            placement=Placement(kind="in" if inside else ("on" if on else "table"), parent_id=on),
        )
        self.objects.append(obj)
        return obj.id

    def zone(self, zone_id: str, shape: str, center, color: str, radius=None, size=None,
             yaw_deg: float = 0.0) -> None:
        self.zones.append(Zone(id=zone_id, shape=shape, center_xy=center, radius=radius,  # type: ignore[arg-type]
                               size_xy=size, yaw_deg=yaw_deg, color_name=color,
                               rgb=linear_rgb(color)))  # fmt: skip

    def build(self, tip_xy, tip_height: float = 0.17, tip_yaw_deg: float = 0.0,
              front_eye=(0.0, -0.72, 1.30)) -> SceneSpec:
        tip_rot = quat_from_axis_angle((0.0, 0.0, 1.0), math.radians(tip_yaw_deg))
        tip = Pose(position=round_vec((tip_xy[0], tip_xy[1], TABLE_H + tip_height)),
                   rotation=round_vec(tip_rot))  # type: ignore[arg-type]
        cam_pos = tip.matrix() @ np.array([*GRIPPER_CAM_OFFSET, 1.0])
        target = (0.0, 0.05, TABLE_H)
        cameras = [
            CameraSpec(id="front", rig="front", focal_mm=28.0, resolution=RESOLUTION,
                       pose=Pose(position=front_eye,
                                 rotation=round_vec(look_at_camera_quat(front_eye, target)))),
            CameraSpec(id="overhead", rig="overhead", focal_mm=24.0, resolution=RESOLUTION,
                       pose=Pose(position=(0.0, 0.0, TABLE_H + 1.0))),
            CameraSpec(id="gripper", rig="gripper", focal_mm=16.0, resolution=RESOLUTION,
                       pose=Pose(position=round_vec(cam_pos[:3]), rotation=tip.rotation)),
        ]  # fmt: skip
        return SceneSpec(
            meta=SceneMeta(scene_id=self.scene_id, generator_version="handbuilt 0.1.0",
                           seed=self.seed, recipe_hash="0" * 64, settled=True,
                           difficulty={"handbuilt": True}),
            table=Table(size_xy=(1.0, 0.7), height=TABLE_H, grid_cell_m=self.grid,
                        material_id="cm_grid" if self.grid else "wood_simple"),
            objects=self.objects,
            gripper=build_gripper(tip),
            zones=self.zones,
            cameras=cameras,
            lighting=Lighting(hdri_id="studio_small_09"),
            questions=QuestionMix(max_per_camera=6),
        )  # fmt: skip


def kitchen() -> SceneSpec:
    """Grid table; a bowl holding an apple and a lime; a cube stacked on a cheese box; a green
    circular reach zone; the gripper above the cube."""
    b = SceneBuilder("hand_kitchen", 101, grid_cell_m=0.05)
    bowl = b.add("wooden_bowl_01", -0.25, 0.10)
    b.add("food_apple_01", -0.03, 0.0, on=bowl, inside=True)
    b.add("food_lime_01", 0.05, 0.0, on=bowl, inside=True)
    box = b.add("CheeseBox_01", 0.15, 0.14)
    b.add("proc_box_5cm", 0.0, 0.0, on=box, color="red")
    b.add("lemon", 0.05, -0.12)
    b.add("bleach_bottle", 0.33, -0.06)
    b.add("multi_cleaner_bottle", -0.02, 0.22)
    b.add("baseball_01", -0.14, -0.16)
    b.add("proc_cylinder_6x10cm", -0.38, -0.16, color="blue")
    b.zone("zone_0", "circle", (0.09, -0.10), "green", radius=0.12)
    return b.build(tip_xy=(0.15, 0.14 - 0.035), tip_height=0.16)


def workshop() -> SceneSpec:
    """No grid but a visible procedural cube; a basket with tools; two grey wrenches told apart
    by size; a jug hiding a can from the front; a blue rectangular reach zone."""
    b = SceneBuilder("hand_workshop", 202)
    basket = b.add("wicker_basket_01", -0.24, 0.12)
    b.add("screwdriver", -0.05, 0.0, on=basket, inside=True, yaw_deg=90)
    b.add("pliers", 0.06, 0.02, on=basket, inside=True)
    b.add("combination_wrench", 0.30, 0.10, size_word="large")
    b.add("adjustable_wrench", 0.18, -0.20, yaw_deg=90, size_word="small")
    b.add("jug_01", 0.02, -0.12)
    b.add("leather_cleaner_can", 0.03, 0.06)
    tin = b.add("cleaner_tin_01", -0.30, -0.18)
    b.add("proc_box_5cm", 0.0, 0.0, on=tin, color="green")
    b.add("vintage_stapler", 0.36, -0.14, yaw_deg=90)
    b.add("proc_box_5cm", -0.12, -0.22, color="yellow")
    b.zone("zone_0", "rect", (0.28, -0.05), "blue", size=(0.26, 0.40), yaw_deg=10)
    return b.build(tip_xy=(-0.30, -0.18 - 0.035), tip_height=0.24)


def pantry() -> SceneSpec:
    """Grid table; look-alike rocks with size words; two lemons (only countable); a plate with a
    croissant; an empty bowl; a block on a rusted can; the gripper above the plate."""
    b = SceneBuilder("hand_pantry", 303, grid_cell_m=0.05)
    plate = b.add("carved_wooden_plate", 0.20, 0.12)
    b.add("croissant", 0.0, 0.0, on=plate, inside=True, yaw_deg=20)
    b.add("moon_rock_03", -0.30, 0.15, size_word="large")
    b.add("moon_rock_05", -0.02, -0.16, size_word="small")
    b.add("lemon", -0.16, -0.05)
    b.add("lemon", 0.08, -0.08)
    b.add("wooden_bowl_02", -0.12, 0.20)
    can = b.add("can_rusted", 0.36, -0.12)
    b.add("proc_box_10x10x4cm", 0.0, 0.0, on=can, color="orange")
    b.add("food_avocado_01", -0.36, -0.16)
    b.zone("zone_0", "circle", (-0.20, -0.08), "yellow", radius=0.11)
    return b.build(tip_xy=(0.20, 0.12 - 0.035), tip_height=0.14, tip_yaw_deg=15)


def tabletop() -> SceneSpec:
    """Procedural references stacked and scattered, a tray with fruit, a duck and a vase; front
    camera lower and to the side for a different parallax."""
    b = SceneBuilder("hand_tabletop", 404, grid_cell_m=0.04)
    block = b.add("proc_box_10x10x4cm", -0.05, 0.02, color="white")
    b.add("proc_box_5cm", 0.0, 0.0, on=block, color="purple")
    tray = b.add("brass_pot_02", 0.25, 0.12)
    b.add("food_lychee_01", -0.03, 0.02, on=tray, inside=True)
    b.add("food_kiwi_01", 0.05, -0.03, on=tray, inside=True)
    b.add("rubber_duck_toy", -0.28, 0.12, yaw_deg=-30)
    b.add("brass_vase_03", -0.30, -0.15)
    b.add("proc_sphere_5cm", 0.10, -0.18, color="pink")
    b.add("proc_cylinder_6x10cm", 0.32, -0.14, color="red")
    b.add("medical_tape", -0.08, -0.20)
    b.zone("zone_0", "circle", (0.0, -0.16), "orange", radius=0.13)
    return b.build(tip_xy=(-0.05, 0.02 - 0.035), tip_height=0.15, front_eye=(0.25, -0.70, 1.22))


SCENES = {"kitchen": kitchen, "workshop": workshop, "pantry": pantry, "tabletop": tabletop}


def build_all() -> list[SceneSpec]:
    return [make() for make in SCENES.values()]


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out/questions_demo")
    for scene in build_all():
        path = write_model(out / f"{scene.meta.scene_id}.settled.json", scene)
        print(path)


if __name__ == "__main__":
    main()
