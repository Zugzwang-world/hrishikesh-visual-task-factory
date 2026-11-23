"""Sampler v1: determinism, recipe sliders, constraints and contract validity."""

import itertools
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import yaml

from vtf.core.assets import load_asset_manifest
from vtf.core.io import read_model
from vtf.core.layout import RunLayout
from vtf.core.projection import project
from vtf.core.rng import derive_seed
from vtf.core.scene import QuestionMix, RunInfo, SceneSpec
from vtf.core.vocab import linear_rgb
from vtf.scene import sampler_v1
from vtf.scene.generate import generate_run, regenerate_scene_json
from vtf.scene.recipe import Recipe, RecipeV1, load_recipe
from vtf.scene.sampler import box_corners
from vtf.scene.sampler_v1 import (
    GENERATOR_VERSION,
    point_rect_distance,
    rects_overlap,
    sample_scene_v1,
    zone_contains,
    zone_signed_distance,
)

REPO = Path(__file__).resolve().parents[1]
V1 = REPO / "recipes" / "v1.yaml"
S2N = REPO / "recipes" / "s2_nearest_v1.yaml"
EPS = 1e-6


@pytest.fixture(scope="module")
def recipe() -> RecipeV1:
    rec = load_recipe(V1)
    assert isinstance(rec, RecipeV1)
    return rec


@pytest.fixture(scope="module")
def scenes(recipe: RecipeV1) -> list[SceneSpec]:
    return [sample_scene_v1(recipe, f"scene_{i:04d}", derive_seed(1, i)) for i in range(30)]


def _files(root: Path) -> dict[str, bytes]:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def _variant(recipe: RecipeV1, **changes) -> RecipeV1:
    """A copy of the recipe with dotted-path changes, e.g. {'stacks.count': [2, 2]}."""
    data = recipe.model_dump(mode="json")
    for path, value in changes.items():
        node = data
        *head, last = path.split("__")
        for key in head:
            node = node[key]
        node[last] = value
    return RecipeV1.model_validate(data)


def _sample(recipe: RecipeV1, n: int = 12, run_seed: int = 5) -> list[SceneSpec]:
    return [sample_scene_v1(recipe, f"scene_{i:04d}", derive_seed(run_seed, i)) for i in range(n)]


# Geometry helpers written independently of the sampler ----------------------------------------


def _vertical_axis(obj) -> int:
    rot = obj.pose.rotation_matrix()
    return int(np.argmax(np.abs(rot[2, :])))


def _height(obj) -> float:
    return obj.dimensions[_vertical_axis(obj)]


def _footprint(obj) -> tuple[float, float, float, float, float]:
    rot = obj.pose.rotation_matrix()
    a, b = (i for i in range(3) if i != _vertical_axis(obj))
    yaw = math.atan2(rot[1, a], rot[0, a])
    return (*obj.pose.position[:2], obj.dimensions[a] / 2, obj.dimensions[b] / 2, yaw)


def _rest_top(scene: SceneSpec, obj) -> float:
    top = scene.table.height
    p = obj.placement
    if p.kind == "table":
        return top + _height(obj)
    parent = scene.object(p.parent_id)
    if p.kind == "on":
        return _rest_top(scene, parent) + _height(obj)
    info = load_asset_manifest().get(parent.shape).container
    return top + info.floor_z_m * parent.scale + _height(obj)


def _in_image(cam, points: np.ndarray, margin: float) -> bool:
    uv, depth = project(cam, points)
    w, h = cam.resolution
    return bool(
        (depth > 0).all()
        and (uv[:, 0] >= margin).all() and (uv[:, 0] <= w - margin).all()
        and (uv[:, 1] >= margin).all() and (uv[:, 1] <= h - margin).all()
    )  # fmt: skip


def _floats(value: object) -> list[float]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [f for v in value.values() for f in _floats(v)]
    if isinstance(value, list):
        return [f for v in value for f in _floats(v)]
    return []


# Determinism ------------------------------------------------------------------------------------


def test_same_seed_gives_byte_identical_files(tmp_path: Path) -> None:
    generate_run(recipe=V1, n=5, seed=3, out=tmp_path / "a")
    generate_run(recipe=V1, n=5, seed=3, out=tmp_path / "b")
    a, b = _files(tmp_path / "a"), _files(tmp_path / "b")
    assert len(a) == 6 and a == b
    generate_run(recipe=V1, n=5, seed=4, out=tmp_path / "c")
    c = _files(tmp_path / "c")
    assert all(a[k] != c[k] for k in a if k.startswith("scenes/"))


def test_regenerate_v1_scene_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)
    generate_run(recipe=Path("recipes/v1.yaml"), n=3, seed=9, out=tmp_path)
    layout = RunLayout(tmp_path)
    run_info = read_model(layout.run_file, RunInfo)
    assert run_info.generator_version == GENERATOR_VERSION
    for scene_id in run_info.scenes:
        assert regenerate_scene_json(run_info, scene_id) == layout.scene_file(scene_id).read_bytes()


def test_v0_recipe_still_loads_as_v0() -> None:
    assert type(load_recipe(REPO / "recipes" / "first_scene.yaml")) is Recipe


# Contract and constraints ---------------------------------------------------------------------


def test_scenes_validate_and_round_trip(scenes: list[SceneSpec], recipe: RecipeV1) -> None:
    for scene in scenes:
        again = SceneSpec.model_validate_json(scene.model_dump_json())
        assert again == scene
        assert scene.meta.generator_version == GENERATOR_VERSION
        assert scene.meta.recipe_hash == recipe.recipe_hash and not scene.meta.settled
        assert scene.questions == recipe.questions
        assert all(round(f, 6) == f for f in _floats(scene.model_dump(mode="json")))


def test_objects_names_and_assets(scenes: list[SceneSpec], recipe: RecipeV1) -> None:
    manifest = load_asset_manifest()
    for scene in scenes:
        d = scene.meta.difficulty
        lo, hi = recipe.objects.clutter[d["clutter"]].count
        assert lo <= len(scene.objects) == d["n_objects"] <= hi
        assert [o.pass_index for o in scene.objects] == list(range(1, len(scene.objects) + 1))
        for o in scene.objects:
            entry = manifest.get(o.shape)
            assert o.source == "asset" and o.category == entry.category
            if entry.color_name is None:  # procedural reference: a recipe colour
                assert o.color_name in recipe.objects.colours and o.rgb == linear_rgb(o.color_name)
            else:
                assert o.color_name == entry.color_name and o.rgb == entry.rgb
            expected = tuple(round(v * o.scale, 6) for v in entry.size_m)
            assert o.dimensions == pytest.approx(expected, abs=EPS)
            if "standing on end" in entry.notes:  # laid on its side
                assert _vertical_axis(o) != 2, o.shape
        # Unique names except a duplicate group (same full name) and size pairs (size words).
        keys = Counter((o.category, o.color_name) for o in scene.objects)
        full = Counter(o.name for o in scene.objects)
        dup_names = [n for n, c in full.items() if c > 1]
        assert len(dup_names) <= 1
        assert sum(full[n] for n in dup_names) == d["n_duplicates"]
        for (cat, col), count in keys.items():
            if count > 1:
                group = [o for o in scene.objects if (o.category, o.color_name) == (cat, col)]
                sizes = {o.size_word for o in group}
                assert sizes == {"small", "large"} or len({o.name for o in group}) == 1
        # No colourless object shares a category with another object of other colour.
        for a, b in itertools.combinations(scene.objects, 2):
            if a.category == b.category and a.name != b.name and a.size_word is None:
                assert a.color_name is not None and b.color_name is not None


def test_placement_rules(scenes: list[SceneSpec], recipe: RecipeV1) -> None:
    manifest = load_asset_manifest()
    top, drop = recipe.table.height, recipe.objects.drop_m
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    gap = recipe.placement.gap_m
    for scene in scenes:
        objs = scene.objects
        for o in objs:
            entry = manifest.get(o.shape)
            x, y, z = o.pose.position
            h = _height(o)
            p = o.placement
            if p.kind == "table":
                expect = top + h / 2 + (0.0 if entry.passive else drop)
                assert z == pytest.approx(expect, abs=EPS)
                for cx, cy in box_corners(o.pose, o.dimensions)[:, :2]:
                    assert abs(cx) <= half_x - recipe.placement.table_margin_m + EPS
                    assert abs(cy) <= half_y - recipe.placement.table_margin_m + EPS
            elif p.kind == "on":
                base = scene.object(p.parent_id)
                assert manifest.get(base.shape).can_support and entry.stackable
                base_top = base.pose.position[2] + _height(base) / 2
                assert z == pytest.approx(base_top + h / 2 + drop, abs=EPS)
                assert point_rect_distance(x, y, _footprint(base)) == 0.0  # centre over support
            else:
                box = scene.object(p.parent_id)
                info = manifest.get(box.shape).container
                assert info is not None and entry.can_go_in_container
                local = (np.array([x, y, z]) - box.pose.position) @ box.pose.rotation_matrix()
                if info.shape == "circle":
                    assert math.hypot(local[0], local[1]) <= info.inner_radius_m
                else:
                    assert abs(local[0]) <= info.inner_size_xy[0] / 2
                    assert abs(local[1]) <= info.inner_size_xy[1] / 2
                bottom = box.pose.position[2] - box.dimensions[2] / 2
                assert z - h / 2 == pytest.approx(bottom + info.floor_z_m + drop, abs=EPS)
        flat = [o for o in objs if o.placement.kind != "in"]
        for a, b in itertools.combinations(flat, 2):
            if b.id in (a.placement.parent_id,) or a.id in (b.placement.parent_id,):
                continue
            assert not rects_overlap(_footprint(a), _footprint(b), gap - 1e-4), (a.id, b.id)


def test_low_gripper(scenes: list[SceneSpec], recipe: RecipeV1) -> None:
    g = recipe.gripper
    for scene in scenes:
        tip = scene.gripper.tip.position
        near = [
            o for o in scene.objects
            if point_rect_distance(tip[0], tip[1], _footprint(o)) <= g.neighbourhood_m
        ]  # fmt: skip
        base = max([_rest_top(scene, o) for o in near], default=scene.table.height)
        clearance = tip[2] - base
        assert g.clearance_m[0] - EPS <= clearance <= g.clearance_m[1] + EPS
        assert scene.meta.difficulty["gripper_clearance_cm"] == pytest.approx(clearance * 100,
                                                                             abs=0.01)  # fmt: skip
        # Touching nothing: any object whose footprint meets a gripper part's footprint
        # spawns entirely below the fingertips.
        parts = [
            (*part.pose.position[:2], part.dimensions[0] / 2, part.dimensions[1] / 2,
             math.atan2(part.pose.rotation_matrix()[1, 0], part.pose.rotation_matrix()[0, 0]))
            for part in scene.gripper.parts
        ]  # fmt: skip
        for o in scene.objects:
            if any(rects_overlap(_footprint(o), r) for r in parts):
                assert o.pose.position[2] + _height(o) / 2 < tip[2], o.id


def test_three_cameras(scenes: list[SceneSpec], recipe: RecipeV1) -> None:
    cr = recipe.cameras
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    table = np.array([(sx * half_x, sy * half_y, recipe.table.height)
                      for sx in (-1, 1) for sy in (-1, 1)])  # fmt: skip
    for scene in scenes:
        cams = {c.id: c for c in scene.cameras}
        assert list(cams) == ["front", "overhead", "gripper"]
        assert all(c.rig == c.id and c.resolution == cr.resolution for c in cams.values())
        assert _in_image(cams["overhead"], table, cr.view_margin_px)  # whole table
        down = -cams["overhead"].pose.rotation_matrix()[:, 2]
        assert down[2] < -math.cos(math.radians(cr.overhead.tilt_deg[1] + 0.1))
        for o in scene.objects:
            for rig in ("front", "overhead"):
                assert _in_image(cams[rig], box_corners(o.pose, o.dimensions), cr.view_margin_px)
        grip = cams["gripper"]
        assert grip.pose.rotation == scene.gripper.tip.rotation
        hfov = math.degrees(2 * math.atan(grip.sensor_width_mm / 2 / grip.focal_mm))
        assert cr.gripper.hfov_deg[0] - 0.01 <= hfov <= cr.gripper.hfov_deg[1] + 0.01
        offset = np.subtract(grip.pose.position, scene.gripper.tip.position)
        assert 0.05 < np.linalg.norm(offset) < 0.09  # beside the palm, above the fingertips
        for c in cams.values():
            assert c.quality.level == scene.meta.difficulty[f"quality_{c.id}"]


def test_zones_lighting_grid(scenes: list[SceneSpec], recipe: RecipeV1) -> None:
    zr = recipe.zones
    for scene in scenes:
        colours = {o.color_name for o in scene.objects}
        assert len(scene.zones) == scene.meta.difficulty["n_zones"] <= zr.count[1]
        for zone in scene.zones:
            assert zone.color_name in zr.colours and zone.color_name not in colours
            assert zone.rgb == linear_rgb(zone.color_name)
            inside = [zone_contains(zone, *o.pose.position[:2]) for o in scene.objects]
            assert zr.min_inside <= sum(inside) <= len(inside) - zr.min_outside
        light = scene.lighting
        assert light.condition in recipe.lighting.conditions
        assert light.condition == scene.meta.difficulty["lighting"]
        assert light.key_light is not None and light.key_light.kind == "sun"
        grid = scene.meta.difficulty["grid"]
        assert (scene.table.material_id == "cm_grid") == grid
        assert scene.table.grid_cell_m == (0.01 if grid else None)


def test_v1_recipe_covers_every_control(scenes: list[SceneSpec]) -> None:
    diffs = [s.meta.difficulty for s in scenes]
    assert {d["clutter"] for d in diffs} == {"low", "medium", "high"}
    for key in ("n_stacks", "n_contained", "n_zones", "n_size_pairs", "n_colour_pairs",
                "n_duplicates"):  # fmt: skip
        assert any(d[key] for d in diffs), key
    assert any(d["grid"] for d in diffs) and not all(d["grid"] for d in diffs)
    assert len({d["lighting"] for d in diffs}) >= 3


# Recipe sliders -----------------------------------------------------------------------------


def test_clutter_slider(recipe: RecipeV1) -> None:
    high = _variant(recipe, objects__clutter={"high": {"count": [17, 25], "weight": 1.0}})
    low = _variant(recipe, objects__clutter={"low": {"count": [3, 5], "weight": 1.0}})
    assert all(17 <= len(s.objects) <= 25 for s in _sample(high, 8))
    assert all(3 <= len(s.objects) <= 5 for s in _sample(low, 8))


def test_feature_sliders_off(recipe: RecipeV1) -> None:
    off = _variant(
        recipe,
        stacks__count=[0, 0],
        containers__count=[0, 0],
        zones__count=[0, 0],
        lookalikes__size_pairs=[0, 0],
        lookalikes__colour_pairs=[0, 0],
        duplicates__probability=0.0,
        table__grid_fraction=0.0,
    )
    for s in _sample(off):
        d = s.meta.difficulty
        assert d["n_stacks"] == d["n_contained"] == d["n_containers"] == d["n_zones"] == 0
        assert d["n_lookalike_pairs"] == d["n_duplicates"] == 0 and not d["grid"]
        assert all(o.placement.kind == "table" and o.size_word is None for o in s.objects)
        assert len({o.name for o in s.objects}) == len(s.objects)
        assert not s.zones and s.table.material_id == "wood_simple"


def test_feature_sliders_on(recipe: RecipeV1) -> None:
    on = _variant(
        recipe,
        objects__clutter={"high": {"count": [18, 22], "weight": 1.0}},
        stacks__count=[2, 2],
        containers__count=[1, 1],
        containers__items=[1, 2],
        zones__count=[2, 2],
        lookalikes__size_pairs=[1, 1],
        lookalikes__colour_pairs=[1, 1],
        duplicates__probability=1.0,
        table__grid_fraction=1.0,
    )
    scenes = _sample(on)
    diffs = [s.meta.difficulty for s in scenes]
    assert sum(d["n_stacks"] == 2 for d in diffs) >= 0.8 * len(diffs)
    assert sum(d["n_contained"] >= 1 for d in diffs) >= 0.8 * len(diffs)
    assert sum(d["n_zones"] == 2 for d in diffs) >= 0.8 * len(diffs)
    assert all(d["grid"] for d in diffs)
    for s, d in zip(scenes, diffs, strict=True):
        if d["n_size_pairs"]:
            small = [o for o in s.objects if o.size_word == "small"]
            large = [o for o in s.objects if o.size_word == "large"]
            assert len(small) == len(large) == 1
            (a,), (b,) = small, large
            assert (a.shape, a.color_name) == (b.shape, b.color_name)
            assert max(a.dimensions) < max(b.dimensions)
            ratio = max(b.dimensions) / max(a.dimensions)
            assert 1.25 < ratio < 1.5
        if d["n_colour_pairs"]:
            pairs = [(a, b) for a, b in itertools.combinations(s.objects, 2)
                     if a.color_name == b.color_name and a.category != b.category]  # fmt: skip
            assert pairs
        if d["n_duplicates"]:
            assert max(Counter(o.name for o in s.objects).values()) >= 2
    assert sum(d["n_size_pairs"] == 1 for d in diffs) >= 0.8 * len(diffs)
    assert sum(d["n_colour_pairs"] == 1 for d in diffs) >= 0.8 * len(diffs)
    assert sum(d["n_duplicates"] >= 2 for d in diffs) >= 0.8 * len(diffs)


def test_quality_and_lighting_sliders(recipe: RecipeV1) -> None:
    cond = recipe.lighting.conditions["dim"].model_dump(mode="json")
    v = _variant(
        recipe,
        cameras__quality__weights={"cctv": 1.0},
        cameras__quality__per_rig={"gripper": {"mild": 1.0}},
        lighting__conditions={"dim": cond},
    )
    for s in _sample(v, 5):
        levels = {c.id: c.quality for c in s.cameras}
        assert levels["front"].level == levels["overhead"].level == "cctv"
        assert levels["front"].cctv and levels["front"].jpeg_quality == 40
        assert levels["gripper"].level == "mild" and levels["gripper"].noise_sigma > 0
        assert s.lighting.condition == "dim" and s.lighting.strength <= 0.25


def test_asset_pool_filters(recipe: RecipeV1, tmp_path: Path) -> None:
    fruit = _variant(recipe, objects__assets__categories=["apple", "lemon", "lime", "kiwi",
                                                          "bowl", "cube"])  # fmt: skip
    for s in _sample(fruit, 6):
        assert {o.category for o in s.objects} <= {"apple", "lemon", "lime", "kiwi", "bowl",
                                                   "cube"}  # fmt: skip
    # A recipe can pin its own manifest; procedural-only needs no asset cache.
    manifest = load_asset_manifest()
    procs = [a.model_dump(mode="json") for a in manifest.assets if a.source == "procedural"]
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"manifest_version": "0.1.0", "assets": procs}))
    pinned = _variant(recipe, asset_manifest=str(path))
    for s in _sample(pinned, 4):
        assert all(o.shape.startswith("proc_") for o in s.objects)


# s2_nearest_v1 -------------------------------------------------------------------------------


def test_s2_nearest_recipe() -> None:
    rec = load_recipe(S2N)
    assert isinstance(rec, RecipeV1)
    for s in _sample(rec, 10):
        assert 5 <= len(s.objects) <= 10
        assert all(o.source == "primitive" and o.category == o.shape for o in s.objects)
        assert {o.shape for o in s.objects} <= {"box", "cylinder", "sphere", "cone"}
        assert len({(o.color_name, o.shape) for o in s.objects}) == len(s.objects)
        assert s.questions == QuestionMix(families=["nearest_to_gripper"], max_per_camera=5)
        assert [c.id for c in s.cameras] == ["front", "overhead", "gripper"]
        assert s.lighting.key_light is not None and not s.zones
        assert 0.03 - EPS <= s.meta.difficulty["gripper_clearance_cm"] / 100 <= 0.12 + EPS


# Geometry helpers -----------------------------------------------------------------------------


def test_rect_and_zone_helpers() -> None:
    a = (0.0, 0.0, 0.05, 0.02, 0.0)
    assert rects_overlap(a, (0.09, 0.0, 0.05, 0.02, 0.0))
    assert not rects_overlap(a, (0.11, 0.0, 0.05, 0.02, 0.0))
    assert rects_overlap(a, (0.11, 0.0, 0.05, 0.02, 0.0), gap=0.02)
    assert not rects_overlap(a, (0.0, 0.08, 0.05, 0.02, math.pi / 2))  # rotated, just apart
    assert rects_overlap(a, (0.0, 0.06, 0.05, 0.02, math.pi / 4))
    assert point_rect_distance(0.08, 0.0, a) == pytest.approx(0.03)
    assert point_rect_distance(0.0, 0.0, a) == 0.0
    zone = sampler_v1.Zone(id="z", shape="rect", center_xy=(0.1, 0.0), size_xy=(0.2, 0.1),
                           yaw_deg=90.0)  # fmt: skip
    assert zone_contains(zone, 0.1, 0.09) and not zone_contains(zone, 0.18, 0.0)
    assert zone_signed_distance(zone, 0.1, 0.0) == pytest.approx(-0.05)
    circle = sampler_v1.Zone(id="c", shape="circle", center_xy=(0.0, 0.0), radius=0.1)
    assert zone_signed_distance(circle, 0.3, 0.0) == pytest.approx(0.2)
