"""vtf quickstart and vtf verify-showcase pieces that need no Blender."""

import shutil
from pathlib import Path

from vtf.core.scene import SceneSpec
from vtf.quickstart import REPO_ROOT, required_assets
from vtf.scene.recipe import load_recipe
from vtf.validate.showcase import compare_dirs, markdown

RUN_EXAMPLE = REPO_ROOT / "spec" / "examples" / "run_example"
QUICK = REPO_ROOT / "recipes" / "quick.yaml"


def test_quick_recipe_loads_and_lists_its_assets():
    recipe = load_recipe(QUICK)
    models, hdris = required_assets(QUICK)
    assert hdris == ["studio_small_09"]
    assert len(models) == 13  # Poly Haven models only; the 4 procedural ones need no fetch
    assert set(models) < set(recipe.objects.assets.ids)
    assert "wooden_bowl_01" in models and "wicker_basket_01" in models  # containment


def test_quick_recipe_generates_scenes(tmp_path):
    from vtf.scene.generate import generate_run

    generate_run(recipe=QUICK, n=2, seed=1, out=tmp_path)
    first = (tmp_path / "scenes" / "scene_0000" / "scene.json").read_bytes()
    SceneSpec.model_validate_json(first)
    generate_run(recipe=QUICK, n=2, seed=1, out=tmp_path / "again")
    assert (tmp_path / "again" / "scenes" / "scene_0000" / "scene.json").read_bytes() == first


def test_compare_dirs_identical_copy(tmp_path):
    src = RUN_EXAMPLE / "scenes" / "scene_0000" / "render"
    shutil.copytree(src, tmp_path / "a")
    shutil.copytree(src, tmp_path / "b")
    cmp = compare_dirs(tmp_path / "a", tmp_path / "b")
    assert cmp["settled_identical"]
    for cam in cmp["cameras"].values():
        assert cam["beauty_png_sha256_identical"] and cam["beauty_pixels_identical"]
        assert cam["index_equal"] and cam["depth_equal"] and cam["amodal_equal"]


def test_showcase_markdown_table():
    row = {
        "scene": "scene_0004",
        "camera": "front",
        "n_tasks": 5,
        "scene_file_identical": True,
        "settled_identical": True,
        "masks_depth_exact_vs_frozen": True,
        "answers_identical": True,
        "g7_hashes_match": True,
        "ssim_gpu_vs_frozen_gpu": 0.9995,
        "gpu_identical_to_frozen": False,
        "ssim_cpu_vs_frozen_gpu": 0.98,
        "cpu_vs_cpu_png_bytes_identical": True,
    }
    md = markdown({"rows": [row], "timings": {"cpu_1": {"wall_seconds": 1, "render_seconds": {}}}})
    assert "| scene_0004 | front | 5 | yes | yes | yes | yes | yes | 0.999500" in md
    assert "identical |" in md
    assert Path(QUICK).is_file()
