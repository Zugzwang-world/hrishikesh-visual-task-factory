"""Ray caster: analytic primitives stay exact, meshes agree with them, assets are traced."""

import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from trimesh.ray.ray_pyembree import RayMeshIntersector
from trimesh.ray.ray_triangle import RayMeshIntersector as PlainIntersector

from vtf.core.assets import asset_cache_dir, load_asset_manifest, object_mesh
from vtf.core.geometry import Pose, quat_from_axis_angle
from vtf.core.projection import pixel_rays
from vtf.core.raycast import HITTERS, _local_rays, hit_box, hit_mesh, hit_sphere, raycast
from vtf.core.rng import derive_seed
from vtf.core.scene import MAX_OBJECT_PASS_INDEX, SceneSpec
from vtf.scene.recipe import load_recipe
from vtf.scene.sampler_v1 import sample_scene_v1

REPO = Path(__file__).resolve().parents[1]


def _cached(asset_id: str) -> bool:
    entry = load_asset_manifest().get(asset_id)
    return bool(entry.file) and (asset_cache_dir() / entry.file).is_file()


def _brute_force(scene: SceneSpec, camera_id: str) -> tuple[np.ndarray, np.ndarray]:
    """Every primitive over every pixel (no windows): the reference for the windowed caster."""
    cam = scene.camera(camera_id)
    origin, dirs = pixel_rays(cam)
    t = scene.table
    targets = [(t.pass_index, "box", (*t.size_xy, t.thickness),
                Pose(position=(0.0, 0.0, t.height - t.thickness / 2)))]  # fmt: skip
    targets += [(scene.gripper.pass_index, p.shape, p.dimensions, p.pose)
                for p in scene.gripper.parts]  # fmt: skip
    targets += [(o.pass_index, o.shape, o.dimensions, o.pose) for o in scene.objects]
    best = np.full(dirs.shape[:2], np.inf)
    index = np.zeros(dirs.shape[:2], dtype=np.uint16)
    for pass_index, shape, dims, pose in targets:
        o, d = _local_rays(pose, origin, dirs)
        hit = HITTERS[shape](dims, np.broadcast_to(o, d.shape), d)
        closer = hit < best
        best = np.where(closer, hit, best)
        index = np.where(closer, pass_index, index).astype(np.uint16)
    return index, np.where(np.isfinite(best), best, 0.0).astype(np.float32)


def test_windowed_primitives_match_brute_force() -> None:
    rec = load_recipe(REPO / "recipes" / "s2_nearest_v1.yaml")
    scene = sample_scene_v1(rec, "scene_0000", derive_seed(2, 0))
    for cam in scene.cameras:  # front, overhead and the gripper (parts straddle its plane)
        small = cam.model_copy(update={"resolution": (256, 192)})
        s = scene.model_copy(update={"cameras": [small]})
        rc = raycast(s, cam.id)
        index, depth = _brute_force(s, cam.id)
        assert np.array_equal(rc.index, index), cam.id
        assert np.array_equal(rc.depth, depth), cam.id


def test_mesh_hits_agree_with_analytic_shapes() -> None:
    rng = np.random.default_rng(0)
    origin = np.array([0.31, -0.42, 0.55])
    targets = rng.uniform(-0.05, 0.05, size=(4000, 3))
    dirs = (targets - origin) * rng.uniform(0.5, 2.0, size=(4000, 1))  # unnormalised
    box_dims = (0.06, 0.04, 0.08)
    box = trimesh.creation.box(extents=box_dims)
    t_mesh = hit_mesh(RayMeshIntersector(box), origin, dirs)
    t_exact = hit_box(box_dims, np.broadcast_to(origin, dirs.shape), dirs)
    hit = np.isfinite(t_exact)
    # Rays grazing an edge may go either way in float32 Embree; everything else agrees.
    assert (np.isfinite(t_mesh) == hit).mean() > 0.998
    both = hit & np.isfinite(t_mesh)
    assert np.abs(t_mesh[both] - t_exact[both]).max() < 1e-7
    sphere = trimesh.creation.icosphere(subdivisions=6, radius=0.04)
    t_mesh = hit_mesh(RayMeshIntersector(sphere), origin, dirs)
    t_exact = hit_sphere((0.08, 0.08, 0.08), np.broadcast_to(origin, dirs.shape), dirs)
    both = np.isfinite(t_exact) & np.isfinite(t_mesh)
    assert both.sum() > 1000
    assert np.abs(t_mesh[both] - t_exact[both]).max() < 2e-4  # tessellation, not the caster


@pytest.mark.skipif(not _cached("wooden_bowl_02"), reason="asset not cached")
def test_asset_object_is_traced_on_its_mesh() -> None:
    rec = load_recipe(REPO / "recipes" / "v1.yaml")
    scene = sample_scene_v1(rec, "scene_0000", derive_seed(3, 0))
    bowl = scene.objects[0].model_copy(
        update={
            "source": "asset",
            "shape": "wooden_bowl_02",
            "category": "bowl",
            "dimensions": load_asset_manifest().get("wooden_bowl_02").size_m,
            "pose": Pose(position=(0.0, 0.0, scene.table.height + 0.0731 / 2),
                         rotation=quat_from_axis_angle((0, 0, 1), 0.4)),  # fmt: skip
            "placement": {"kind": "table"},
        }
    )
    scene = scene.model_copy(update={"objects": [bowl]})
    cam = scene.camera("front").model_copy(update={"resolution": (320, 240)})
    scene = scene.model_copy(update={"cameras": [cam]})
    rc = raycast(scene, "front")
    mask = rc.amodal[bowl.pass_index]
    assert mask.sum() > 200 and (rc.index == bowl.pass_index).sum() > 200
    # Depths agree with trimesh's own (non-Embree) ray-triangle test on the world mesh.
    origin, dirs = pixel_rays(cam)
    rows, cols = np.nonzero(rc.index == bowl.pass_index)
    pick = np.linspace(0, len(rows) - 1, 60).astype(int)
    d = dirs[rows[pick], cols[pick]]
    mesh = object_mesh(bowl)
    loc, ray, _ = PlainIntersector(mesh).intersects_location(
        np.broadcast_to(origin, d.shape), d, multiple_hits=False
    )
    t_ref = np.full(len(d), np.inf)
    t_ref[ray] = ((loc - origin) * d[ray]).sum(-1) / (d[ray] * d[ray]).sum(-1)
    assert np.abs(rc.depth[rows[pick], cols[pick]] - t_ref).max() < 1e-5


def test_v1_scene_with_assets_fast_and_complete() -> None:
    rec = load_recipe(REPO / "recipes" / "v1.yaml")
    data = rec.model_dump(mode="json")
    data["objects"]["clutter"] = {"high": {"count": [25, 25], "weight": 1.0}}
    rec = type(rec).model_validate(data)
    scene = sample_scene_v1(rec, "scene_0000", derive_seed(4, 0))
    assert len(scene.objects) == 25
    if any(not _cached(o.shape) for o in scene.objects if not o.shape.startswith("proc_")):
        pytest.skip("assets not cached")
    raycast(scene, "overhead")  # warm the mesh caches
    for cam in scene.cameras:
        start = time.perf_counter()
        rc = raycast(scene, cam.id)
        seconds = time.perf_counter() - start
        assert seconds < 3.0, (cam.id, seconds)  # brief 05 target, 1024x768 and 25 objects
        assert set(rc.amodal) == {o.pass_index for o in scene.objects}
        assert all(k <= MAX_OBJECT_PASS_INDEX for k in rc.amodal)
        if cam.id != "gripper":  # every object is in the front and overhead views
            assert all(rc.amodal[o.pass_index].any() for o in scene.objects), cam.id
