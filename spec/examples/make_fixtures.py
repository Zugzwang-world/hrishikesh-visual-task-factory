"""Write the contract fixtures in spec/examples/ without Blender.

    uv run python spec/examples/make_fixtures.py

- scene_example.json: a hand-made v0 scene (7 primitives, gripper, front camera), as sampled.
- run_example/: a complete tiny run folder in the layout of vtf.core.layout. The "render" comes
  from the analytic ray caster (vtf.core.raycast), so index, depth and amodal masks are exact.
  The camera is 320x240 to keep the repo light; recipes use 1024x768. The tasks are hand-made
  with fixture gate evidence, so the eval lane has something to run on before validate exists.
"""

import math
import shutil
from pathlib import Path

import numpy as np

from vtf import __version__ as VTF_VERSION
from vtf.core import layout
from vtf.core.geometry import Pose, look_at_camera_quat, quat_from_axis_angle
from vtf.core.hashing import sha256_file, sha256_json, short_id
from vtf.core.io import (
    write_depth,
    write_index_png,
    write_jsonl,
    write_mask_png,
    write_model,
    write_rgb_png,
)
from vtf.core.program import ProgramRef
from vtf.core.projection import intrinsics, pixel_rays, to_camera, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.render_manifest import CameraOutputs, RenderManifest, SettleReport
from vtf.core.scene import (
    SPEC_VERSION,
    CameraSpec,
    Lighting,
    RenderSettings,
    RunInfo,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    build_gripper,
)
from vtf.core.task import GateResult, ManifestRow, TaskPackage, Versions
from vtf.core.vocab import linear_rgb, object_name

HERE = Path(__file__).resolve().parent
TABLE_TOP = 0.75
DROP = 0.01
GENERATOR = "fixture/hand-made 0.1.0"

# id, colour, shape, dimensions (m), x, y, yaw (deg)
OBJECTS = [
    ("obj_00", "red", "box", (0.06, 0.06, 0.06), -0.22, 0.10, 20),
    ("obj_01", "blue", "cylinder", (0.05, 0.05, 0.08), 0.05, 0.12, 0),
    ("obj_02", "green", "sphere", (0.06, 0.06, 0.06), 0.20, -0.05, 0),
    ("obj_03", "yellow", "cone", (0.06, 0.06, 0.08), -0.05, -0.12, 0),
    ("obj_04", "red", "cylinder", (0.05, 0.05, 0.05), 0.27, 0.20, 0),
    ("obj_05", "white", "cylinder", (0.07, 0.07, 0.04), -0.30, -0.15, 0),
    ("obj_06", "orange", "sphere", (0.045, 0.045, 0.045), 0.20, 0.02, 0),
]


def make_scene(settled: bool) -> SceneSpec:
    objects = []
    for i, (oid, colour, shape, dims, x, y, yaw) in enumerate(OBJECTS):
        z = TABLE_TOP + dims[2] / 2 + (0.0 if settled else DROP)
        objects.append(
            SceneObject(
                id=oid,
                shape=shape,
                category=shape,
                color_name=colour,
                rgb=linear_rgb(colour),
                dimensions=dims,
                pose=Pose(
                    position=(x, y, round(z, 6)),
                    rotation=tuple(
                        round(v, 6) + 0.0
                        for v in quat_from_axis_angle((0, 0, 1), math.radians(yaw))
                    ),
                ),
                pass_index=i + 1,
            )
        )
    elev = math.radians(40)
    target = (0.0, 0.0, TABLE_TOP)
    eye = (0.0, -0.9 * math.cos(elev), TABLE_TOP + 0.9 * math.sin(elev))
    camera = CameraSpec(
        id="front",
        rig="front",
        pose=Pose(
            position=tuple(round(v, 6) for v in eye),
            rotation=tuple(round(v, 6) for v in look_at_camera_quat(eye, target)),
        ),
        focal_mm=28.0,
        sensor_width_mm=36.0,
        resolution=(320, 240),
    )
    return SceneSpec(
        meta=SceneMeta(
            scene_id="scene_0000",
            generator_version=GENERATOR,
            seed=0,
            recipe_hash="0" * 64,
            settled=settled,
        ),
        table=Table(size_xy=(1.0, 0.7), height=TABLE_TOP),
        objects=objects,
        gripper=build_gripper(Pose(position=(0.02, 0.0, TABLE_TOP + 0.18))),
        cameras=[camera],
        lighting=Lighting(hdri_id="studio_small_09"),
        render=RenderSettings(samples=128, seed=0, device="GPU", denoise=True),
    )


def shade(scene: SceneSpec, depth: np.ndarray, colour: np.ndarray) -> np.ndarray:
    """Simple Lambert shading from depth-derived normals, then linear -> sRGB 8-bit."""
    origin, dirs = pixel_rays(scene.cameras[0])
    pts = origin + dirs * depth[..., None]
    du = np.gradient(pts, axis=1)
    dv = np.gradient(pts, axis=0)
    n = np.cross(dv, du)
    n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-12
    light = np.array([0.3, -0.5, 0.8])
    light /= np.linalg.norm(light)
    lambert = np.clip((n * light).sum(-1), 0.0, 1.0)
    lit = colour * (0.35 + 0.65 * lambert[..., None])
    lit = np.where((depth > 0)[..., None], lit, 0.18)
    srgb = np.where(lit <= 0.0031308, lit * 12.92, 1.055 * np.power(lit, 1 / 2.4) - 0.055)
    return (np.clip(srgb, 0, 1) * 255 + 0.5).astype(np.uint8)


def main() -> None:
    sampled = make_scene(settled=False)
    settled = make_scene(settled=True)
    write_model(HERE / "scene_example.json", sampled)

    run = HERE / "run_example"
    if run.exists():
        shutil.rmtree(run)
    lay = layout.RunLayout(run)
    sid = sampled.meta.scene_id
    write_model(
        lay.run_file,
        RunInfo(
            recipe="(hand-made fixture)",
            recipe_hash=sampled.meta.recipe_hash,
            run_seed=0,
            n=1,
            generator_version=GENERATOR,
            scenes={sid: sampled.meta.seed},
        ),
    )
    write_model(lay.scene_file(sid), sampled)

    rdir = lay.render_dir(sid)
    write_model(lay.settled_scene(sid), settled)
    cam = settled.cameras[0]
    rc = raycast(settled, cam.id)
    cdir = rdir / cam.id
    write_rgb_png(cdir / layout.BEAUTY, shade(settled, rc.depth, rc.colour))
    write_index_png(cdir / layout.INDEX, rc.index)
    write_depth(cdir / layout.DEPTH, rc.depth)
    amodal = {}
    for pass_index, mask in sorted(rc.amodal.items()):
        rel = f"{cam.id}/{layout.AMODAL_DIR}/{pass_index}.png"
        write_mask_png(rdir / rel, mask)
        amodal[str(pass_index)] = rel
    manifest = RenderManifest(
        scene_id=sid,
        scene_file=str(lay.scene_file(sid).relative_to(run)),
        scene_sha256=sha256_file(lay.scene_file(sid)),
        renderer="fixture",
        blender_version="none (vtf.core.raycast)",
        device="CPU",
        samples=1,
        seed=0,
        denoise=False,
        cameras=[
            CameraOutputs(
                camera_id=cam.id,
                resolution=cam.resolution,
                beauty=f"{cam.id}/{layout.BEAUTY}",
                index=f"{cam.id}/{layout.INDEX}",
                depth=f"{cam.id}/{layout.DEPTH}",
                amodal=amodal,
                K=tuple(tuple(round(float(v), 6) for v in row) for row in intrinsics(cam)),
                world_to_camera=tuple(
                    tuple(round(float(v), 6) + 0.0 for v in row) for row in world_to_camera(cam)
                ),
                render_seconds=0.0,
                amodal_seconds=0.0,
            )
        ],
        settle=SettleReport(
            frames=0,
            substeps_per_frame=0,
            seconds=0.0,
            displacement_m={o.id: DROP for o in settled.objects},
            max_displacement_m=DROP,
        ),
        timings={"build": 0.0, "settle": 0.0, "render": 0.0, "amodal": 0.0, "total": 0.0},
    )
    write_model(lay.render_manifest(sid), manifest)

    # Hand-made tasks so the eval lane can run before validate exists.
    scene_hash = sha256_json(settled)
    depth_of = {o.id: float(to_camera(cam, [o.pose.position])[0, 2]) for o in settled.objects}
    a, b = settled.object("obj_03"), settled.object("obj_01")
    closer = a if depth_of[a.id] < depth_of[b.id] else b
    reds = [o for o in settled.objects if o.color_name == "red"]
    specs = [
        ("count", "count_all.p1", "How many objects are on the table?",
         ProgramRef(name="count_all"), len(settled.objects), "int",
         [o.id for o in settled.objects]),
        ("count", "count_color.p1", "How many red objects are there?",
         ProgramRef(name="count_color", params={"color": "red"}), len(reds), "int",
         [o.id for o in reds]),
        ("front_to_back", "front_to_back.p1",
         f"Which is closer to the camera, the {a.name} or the {b.name}?",
         ProgramRef(name="front_to_back", params={"a": a.id, "b": b.id}),
         object_name(closer.color_name, closer.category), "object", [a.id, b.id]),
    ]  # fmt: skip
    versions = Versions(
        vtf=VTF_VERSION, spec=SPEC_VERSION, generator=GENERATOR, programs="fixture",
        renderer="fixture",
    )  # fmt: skip
    rows = []
    for family, template_id, question, program, answer, answer_type, evidence in specs:
        core = {
            "scene_hash": scene_hash,
            "camera_id": cam.id,
            "template_id": template_id,
            "program": program.model_dump(mode="json"),
            "answer": answer,
            "versions": versions.model_dump(mode="json"),
        }
        task_id = short_id("t", sha256_json(core))
        task = TaskPackage(
            task_id=task_id,
            scene_id=sid,
            scene_hash=scene_hash,
            camera_id=cam.id,
            question=question,
            family=family,
            template_id=template_id,
            program=program,
            answer=answer,
            answer_type=answer_type,
            answer_text=str(answer),
            evidence_object_ids=evidence,
            gates={
                g: GateResult(passed=True, evidence={"fixture": True}) for g in ("G1", "G3", "G7")
            },
            difficulty={"n_objects": len(settled.objects)},
            seed=settled.meta.seed,
            versions=versions,
        )
        tdir = lay.task_dir(task_id)
        write_model(tdir / layout.TASK_FILE, task)
        shutil.copyfile(cdir / layout.BEAUTY, tdir / layout.TASK_IMAGE)
        rows.append(
            ManifestRow(
                task_id=task_id,
                scene_id=sid,
                camera_id=cam.id,
                family=family,
                answer_type=answer_type,
                path=f"{layout.TASKS_DIR}/{task_id}/{layout.TASK_FILE}",
            )
        )
    write_jsonl(lay.task_manifest, rows)
    print(f"wrote {HERE / 'scene_example.json'} and {run}")


if __name__ == "__main__":
    main()
