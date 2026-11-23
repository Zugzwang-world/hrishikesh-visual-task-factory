"""Sampler v0: determinism, recipe constraints, camera view and ray-cast visibility."""

import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest

from vtf.core.geometry import IDENTITY_QUAT
from vtf.core.io import read_model, read_scene
from vtf.core.layout import RunLayout
from vtf.core.projection import project
from vtf.core.raycast import raycast
from vtf.core.scene import Physics, RunInfo, SceneSpec
from vtf.core.vocab import linear_rgb
from vtf.scene.generate import generate_run, regenerate_scene_json
from vtf.scene.recipe import Recipe, load_recipe
from vtf.scene.sampler import (
    GENERATOR_VERSION,
    LayoutError,
    box_corners,
    footprint_radius,
    sample_scene,
)

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "recipes" / "first_scene.yaml"
EPS = 1e-6


@pytest.fixture(scope="module")
def recipe() -> Recipe:
    return load_recipe(RECIPE)


@pytest.fixture(scope="module")
def scenes(tmp_path_factory: pytest.TempPathFactory) -> list[SceneSpec]:
    out = tmp_path_factory.mktemp("s2")
    paths = generate_run(recipe=RECIPE, n=20, seed=1, out=out)
    return [read_scene(p) for p in paths]


def _files(root: Path) -> dict[str, bytes]:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def test_same_seed_gives_byte_identical_files(tmp_path: Path) -> None:
    generate_run(recipe=RECIPE, n=6, seed=3, out=tmp_path / "a")
    generate_run(recipe=RECIPE, n=6, seed=3, out=tmp_path / "b")
    a, b = _files(tmp_path / "a"), _files(tmp_path / "b")
    assert len(a) == 7  # run.json + 6 scene.json
    assert a == b


def test_different_seeds_differ(tmp_path: Path) -> None:
    generate_run(recipe=RECIPE, n=3, seed=3, out=tmp_path / "a")
    generate_run(recipe=RECIPE, n=3, seed=4, out=tmp_path / "b")
    a, b = _files(tmp_path / "a"), _files(tmp_path / "b")
    for name in a:
        if name.startswith("scenes/"):
            assert a[name] != b[name], name
    scene_bytes = [v for k, v in a.items() if k.startswith("scenes/")]
    assert len(set(scene_bytes)) == len(scene_bytes)


def test_regenerate_scene_json_matches_written_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO)  # run.json stores the recipe path as given, relative to the cwd
    generate_run(recipe=Path("recipes/first_scene.yaml"), n=3, seed=5, out=tmp_path)
    layout = RunLayout(tmp_path)
    run_info = read_model(layout.run_file, RunInfo)
    assert run_info.recipe == "recipes/first_scene.yaml"
    for scene_id in run_info.scenes:
        assert regenerate_scene_json(run_info, scene_id) == layout.scene_file(scene_id).read_bytes()


def _floats(value: object) -> list[float]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [f for v in value.values() for f in _floats(v)]
    if isinstance(value, list):
        return [f for v in value for f in _floats(v)]
    return []


def _in_image(scene: SceneSpec, points: np.ndarray, margin: float) -> bool:
    cam = scene.cameras[0]
    uv, depth = project(cam, points)
    width, height = cam.resolution
    return bool(
        (depth > 0).all()
        and (uv[:, 0] >= margin).all() and (uv[:, 0] <= width - margin).all()
        and (uv[:, 1] >= margin).all() and (uv[:, 1] <= height - margin).all()
    )  # fmt: skip


def test_scenes_meet_recipe_constraints(scenes: list[SceneSpec], recipe: Recipe) -> None:
    ob, pl, gr, cr = recipe.objects, recipe.placement, recipe.gripper, recipe.camera
    top = recipe.table.height
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    lo, hi = ob.size_m
    counts = set()
    for scene in scenes:
        meta = scene.meta
        assert not meta.settled
        assert meta.generator_version == GENERATOR_VERSION
        assert meta.recipe_hash == recipe.recipe_hash
        assert scene.physics == Physics() and scene.render == recipe.render
        assert scene.table.size_xy == recipe.table.size_xy and scene.table.height == top

        objs = scene.objects
        counts.add(len(objs))
        assert ob.count[0] <= len(objs) <= ob.count[1]
        assert [o.id for o in objs] == [f"obj_{i:02d}" for i in range(len(objs))]
        assert [o.pass_index for o in objs] == list(range(1, len(objs) + 1))
        assert len({o.name for o in objs}) == len(objs)
        for o in objs:
            assert o.source == "primitive" and o.category == o.shape
            assert o.color_name in ob.colours and o.shape in ob.shapes
            assert o.rgb == linear_rgb(o.color_name)
            dx, dy, dz = o.dimensions
            assert all(lo - EPS <= d <= hi + EPS for d in o.dimensions)
            if o.shape == "box":
                w, x, y, z = o.pose.rotation
                assert x == 0 and y == 0 and w >= 0  # yaw only
            else:
                assert o.pose.rotation == IDENTITY_QUAT  # upright, unrotated
                assert dx == dy
                if o.shape == "sphere":
                    assert dz == dx
            px, py, pz = o.pose.position
            assert pz == pytest.approx(top + dz / 2 + ob.drop_m, abs=EPS)
            r = footprint_radius(o.shape, o.dimensions)
            assert abs(px) + r <= half_x - pl.table_margin_m + EPS
            assert abs(py) + r <= half_y - pl.table_margin_m + EPS
            assert _in_image(scene, box_corners(o.pose, o.dimensions), cr.view_margin_px), o.name
        for a, b in itertools.combinations(objs, 2):
            gap = (
                math.dist(a.pose.position[:2], b.pose.position[:2])
                - footprint_radius(a.shape, a.dimensions)
                - footprint_radius(b.shape, b.dimensions)
            )
            assert gap >= pl.gap_m - EPS, (a.name, b.name)

        grip = scene.gripper
        tip_height = grip.tip.position[2] - top
        assert gr.tip_height_m[0] - EPS <= tip_height <= gr.tip_height_m[1] + EPS
        assert tip_height - max(o.dimensions[2] for o in objs) >= gr.min_clearance_m - EPS
        assert abs(grip.tip.position[0]) <= half_x - gr.xy_margin_m + EPS
        assert abs(grip.tip.position[1]) <= half_y - gr.xy_margin_m + EPS
        for part in grip.parts:
            if part.name != "arm":
                assert _in_image(scene, box_corners(part.pose, part.dimensions),
                                 cr.view_margin_px), part.name  # fmt: skip

        (cam,) = scene.cameras
        assert cam.id == cr.id and cam.resolution == cr.resolution
        assert cr.focal_mm[0] <= cam.focal_mm <= cr.focal_mm[1]
        offset = np.subtract(cam.pose.position, (0.0, 0.0, top))
        assert 0.85 < np.linalg.norm(offset) < 0.95  # about 0.9 m from the table-top centre
        elevation = math.degrees(math.asin(offset[2] / np.linalg.norm(offset)))
        assert 36 < elevation < 44  # tilted down about 40 degrees
        assert scene.lighting.hdri_id == recipe.lighting.hdri_id

        floats = _floats(scene.model_dump(mode="json"))
        assert all(round(f, 6) == f for f in floats), "every float is rounded to 6 digits"
    assert len(counts) > 2, "object counts vary across scenes"


def test_objects_are_visible_in_a_low_res_raycast(scenes: list[SceneSpec]) -> None:
    all_visible = 0
    for scene in scenes:
        cam = scene.cameras[0].model_copy(update={"resolution": (256, 192)})
        rc = raycast(scene.model_copy(update={"cameras": [cam]}), cam.id)
        for o in scene.objects:
            assert rc.amodal[o.pass_index].any(), f"{scene.meta.scene_id}: {o.name} out of view"
        visible = [(rc.index == o.pass_index).sum() > 0 for o in scene.objects]
        all_visible += all(visible)
    assert all_visible >= 0.8 * len(scenes)


def test_impossible_recipe_raises(recipe: Recipe) -> None:
    data = recipe.model_dump(mode="json")
    data["table"]["size_xy"] = [0.3, 0.3]
    data["objects"]["count"] = [10, 10]
    data["objects"]["size_m"] = [0.04, 0.05]
    data["placement"]["tries_per_object"] = 20
    data["max_layout_attempts"] = 2
    with pytest.raises(LayoutError):
        sample_scene(Recipe.model_validate(data), "scene_0000", 0)


def test_recipe_rejects_reversed_ranges(recipe: Recipe) -> None:
    data = recipe.model_dump(mode="json")
    data["objects"]["size_m"] = [0.10, 0.04]
    with pytest.raises(ValueError, match="above high"):
        Recipe.model_validate(data)


def test_recipe_hash_covers_defaults(recipe: Recipe) -> None:
    data = json.loads(json.dumps(recipe.model_dump(mode="json")))
    assert Recipe.model_validate(data).recipe_hash == recipe.recipe_hash
    data["physics"]["friction"] = 0.9
    assert Recipe.model_validate(data).recipe_hash != recipe.recipe_hash
