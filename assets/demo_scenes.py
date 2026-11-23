"""Hand-made v1-style scene files for render-lane checks and hero renders (no sampler needed).

Three scenes on a 1.0 x 0.7 m table: Poly Haven assets, two-high stacks, open containers with
objects inside, a cm-grid or wooden table top, painted reach zones, a key light, a low gripper,
and front, overhead and gripper cameras (the gripper camera follows the sampler-v1 convention:
beside the palm's +y face at palm mid-height, looking along the tip's -Z).

    uv run python assets/demo_scenes.py out/demo     # writes out/demo/scenes/<id>/scene.json
"""

import math
import sys
from pathlib import Path

from vtf.core.assets import load_asset_manifest
from vtf.core.geometry import Pose, look_at_camera_quat, transform_points
from vtf.core.io import write_model
from vtf.core.scene import (
    CameraSpec,
    KeyLight,
    Lighting,
    Placement,
    RenderSettings,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    Zone,
    build_gripper,
)

TOP = 0.75
DROP = 0.005
RES = (1024, 768)
GREEN = (0.0262, 0.3515, 0.0452)
BLUE = (0.0152, 0.0802, 0.5776)
RGB = {
    "red": (0.5776, 0.013, 0.013),
    "blue": (0.0152, 0.0802, 0.5776),
    "yellow": (0.8308, 0.6105, 0.0123),
    "green": (0.0262, 0.3515, 0.0452),
    "orange": (0.8714, 0.1845, 0.0070),
}


def r6(v: float) -> float:
    return round(float(v), 6) + 0.0


def yaw_q(deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2
    return (r6(math.cos(h)), 0.0, 0.0, r6(math.sin(h)))


class Builder:
    def __init__(self) -> None:
        self.manifest = load_asset_manifest()
        self.objects: list[SceneObject] = []

    def add(self, asset_id, x, y, yaw=0.0, on=None, inside=None, colour=None, size_word=None):
        e = self.manifest.get(asset_id)
        dz = e.size_m[2]
        if on is not None:
            parent = self.objects[on]
            z = parent.pose.position[2] + parent.dimensions[2] / 2 + dz / 2 + DROP
            placement = Placement(kind="on", parent_id=parent.id)
        elif inside is not None:
            parent = self.objects[inside]
            info = self.manifest.get(parent.shape).container
            z = TOP + info.floor_z_m + dz / 2 + 0.012
            placement = Placement(kind="in", parent_id=parent.id)
        else:
            z = TOP + dz / 2 + (0.0 if e.passive else DROP)
            placement = Placement()
        procedural = e.source == "procedural"
        obj = SceneObject(
            id=f"obj_{len(self.objects) + 1:02d}",
            source="asset",
            shape=asset_id,
            category=e.category,
            color_name=colour if procedural else e.color_name,
            size_word=size_word,
            rgb=RGB[colour] if procedural else e.rgb,
            dimensions=tuple(e.size_m),
            pose=Pose(position=(r6(x), r6(y), r6(z)), rotation=yaw_q(yaw)),
            pass_index=len(self.objects) + 1,
            placement=placement,
        )
        self.objects.append(obj)
        return len(self.objects) - 1


def cameras(gripper, front_az=0.0) -> list[CameraSpec]:
    target = (0.0, 0.0, TOP)
    dist, el, az = 0.95, math.radians(42.0), math.radians(front_az)
    h = dist * math.cos(el)
    eye = (r6(h * math.sin(az)), r6(-h * math.cos(az)), r6(TOP + dist * math.sin(el)))
    front = CameraSpec(
        id="front",
        rig="front",
        pose=Pose(position=eye, rotation=tuple(r6(v) for v in look_at_camera_quat(eye, target))),
        focal_mm=28.0,
        resolution=RES,
    )
    overhead = CameraSpec(
        id="overhead",
        rig="overhead",
        pose=Pose(position=(0.0, 0.0, TOP + 0.78), rotation=(1.0, 0.0, 0.0, 0.0)),
        focal_mm=24.0,
        resolution=RES,
    )
    offset = (
        0.0,
        gripper.palm_size[1] / 2 + 0.01,
        gripper.finger_length + gripper.palm_size[2] / 2,
    )
    centre = transform_points(gripper.tip.matrix(), [offset])[0]
    hfov = 80.0
    grip = CameraSpec(
        id="gripper",
        rig="gripper",
        pose=Pose(position=tuple(r6(v) for v in centre), rotation=gripper.tip.rotation),
        focal_mm=r6(18.0 / math.tan(math.radians(hfov) / 2)),
        resolution=RES,
    )
    return [front, overhead, grip]


def scene(scene_id, b: Builder, table: Table, zones, tip_xyz, tip_yaw, light, hdri_rot, seed):
    gripper = build_gripper(Pose(position=tip_xyz, rotation=yaw_q(tip_yaw)))
    return SceneSpec(
        meta=SceneMeta(
            scene_id=scene_id,
            generator_version="assets/demo_scenes.py 0.1.0 (hand-made)",
            seed=seed,
            recipe_hash="0" * 64,
        ),
        table=table,
        objects=b.objects,
        gripper=gripper,
        zones=zones,
        cameras=cameras(gripper),
        lighting=Lighting(
            hdri_id="studio_small_09", strength=0.5, rotation_deg=hdri_rot, key_light=light
        ),
        render=RenderSettings(samples=64, seed=0, device="GPU", denoise=True),
    )


def scene_kitchen() -> SceneSpec:
    b = Builder()
    bowl = b.add("wooden_bowl_02", -0.27, 0.13, yaw=20)
    b.add("food_lime_01", -0.245, 0.13, inside=bowl)
    b.add("food_lychee_01", -0.30, 0.125, inside=bowl)
    block = b.add("proc_box_10x10x4cm", 0.17, 0.16, yaw=15, colour="blue")
    b.add("proc_box_5cm", 0.17, 0.16, yaw=40, on=block, colour="red")
    b.add("food_apple_01", 0.03, -0.04, yaw=60)
    b.add("bleach_bottle", 0.35, -0.02, yaw=-25)
    b.add("leather_cleaner_can", -0.04, 0.21)
    b.add("screwdriver", -0.2, -0.16, yaw=35)
    b.add("pliers", 0.2, -0.2, yaw=-20)
    tin = b.add("cleaner_tin_01", -0.4, -0.06, yaw=10)
    b.add("medical_tape", -0.4, -0.06, on=tin)
    zones = [Zone(id="zone_reach", shape="rect", center_xy=(0.12, -0.12), size_xy=(0.34, 0.24),
                  yaw_deg=8.0, color_name="green", rgb=GREEN, line_width_m=0.012)]  # fmt: skip
    light = KeyLight(kind="sun", elevation_deg=52, azimuth_deg=-40, strength=3.0, angle_deg=2.0,
                     rgb=(1.0, 0.96, 0.9))  # fmt: skip
    table = Table(size_xy=(1.0, 0.7), height=TOP, material_id="cm_grid", grid_cell_m=0.01)
    return scene("hero_kitchen", b, table, zones, (0.03, -0.04, TOP + 0.16), 30, light, 40, 1)


def scene_workshop() -> SceneSpec:
    b = Builder()
    basket = b.add("wicker_basket_01", -0.24, 0.12, yaw=-8)
    b.add("baseball_01", -0.3, 0.12, inside=basket)
    b.add("moon_rock_05", -0.18, 0.13, inside=basket)
    box = b.add("CheeseBox_01", 0.2, 0.19, yaw=4)
    b.add("lemon", 0.2, 0.19, yaw=80, on=box)
    b.add("cross_pein_hammer", 0.02, -0.16, yaw=70)
    b.add("combination_wrench", 0.33, -0.1, yaw=-30)
    b.add("jug_01", -0.33, -0.17, yaw=150)
    b.add("rubber_duck_toy", 0.4, 0.15, yaw=-120)
    b.add("food_avocado_01", 0.0, 0.09, yaw=40)
    b.add("tongue_groove_pliers", 0.17, -0.02, yaw=100)
    zones = [Zone(id="zone_reach", shape="circle", center_xy=(0.14, -0.1), radius=0.16,
                  color_name="blue", rgb=BLUE, line_width_m=0.012)]  # fmt: skip
    light = KeyLight(kind="sun", elevation_deg=48, azimuth_deg=35, strength=3.2, angle_deg=2.0,
                     rgb=(1.0, 0.95, 0.88))  # fmt: skip
    table = Table(size_xy=(1.0, 0.7), height=TOP, material_id="wood_simple")
    return scene("hero_workshop", b, table, zones, (0.17, -0.02, TOP + 0.09), -20, light, 200, 2)


def scene_containers() -> SceneSpec:
    b = Builder()
    pot = b.add("brass_pot_02", -0.25, 0.11, yaw=10)
    b.add("food_lychee_01", -0.28, 0.1, inside=pot)
    b.add("proc_sphere_5cm", -0.21, 0.13, inside=pot, colour="yellow")
    plate = b.add("carved_wooden_plate", 0.22, 0.13)
    b.add("food_kiwi_01", 0.19, 0.12, yaw=30, inside=plate)
    b.add("yellow_onion", 0.27, 0.15, inside=plate)
    b.add("wooden_bowl_01", -0.12, -0.13)
    cyl = b.add("proc_cylinder_6x10cm", 0.12, -0.16, colour="green")
    b.add("proc_box_5cm", 0.12, -0.16, yaw=20, on=cyl, colour="orange")
    b.add("multi_cleaner_bottle", 0.38, -0.14, yaw=200)
    b.add("food_pomegranate_01", 0.0, 0.12)
    zones = [
        Zone(id="zone_a", shape="circle", center_xy=(0.22, 0.13), radius=0.17, color_name="green",
             rgb=GREEN, line_width_m=0.01),
        Zone(id="zone_b", shape="rect", center_xy=(-0.05, -0.14), size_xy=(0.5, 0.2),
             yaw_deg=-5.0, color_name="blue", rgb=BLUE, line_width_m=0.01),
    ]  # fmt: skip
    light = KeyLight(kind="sun", elevation_deg=60, azimuth_deg=-10, strength=3.0, angle_deg=2.5)
    table = Table(size_xy=(1.0, 0.7), height=TOP, material_id="cm_grid", grid_cell_m=0.05)
    return scene("test_containers", b, table, zones, (0.12, -0.16, TOP + 0.24), 0, light, 120, 3)


SCENES = (scene_kitchen, scene_workshop, scene_containers)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out/demo")
    for make in SCENES:
        spec = make()
        path = out / "scenes" / spec.meta.scene_id / "scene.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_model(path, spec)
        print(path)
