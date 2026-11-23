"""`vtf generate`: a recipe and a run seed -> run.json and one scene.json per scene.

Scene i gets the seed derive_seed(run_seed, i), so any scene can be rebuilt alone from run.json
(`regenerate_scene_json`, used by gate G7). A `sampler: v1` recipe runs sampler v1; any other
recipe runs sampler v0, unchanged.
"""

from collections import Counter
from pathlib import Path

from vtf.core.io import model_json, write_model
from vtf.core.layout import RunLayout, scene_id_for
from vtf.core.rng import derive_seed
from vtf.core.scene import RunInfo, SceneSpec
from vtf.scene import sampler, sampler_v1
from vtf.scene.recipe import AnyRecipe, RecipeV1, load_recipe

GENERATOR_VERSION = sampler.GENERATOR_VERSION  # v0, kept for existing imports


def generator_version(recipe: AnyRecipe) -> str:
    return sampler_v1.GENERATOR_VERSION if isinstance(recipe, RecipeV1) else GENERATOR_VERSION


def sample_any(recipe: AnyRecipe, scene_id: str, seed: int) -> SceneSpec:
    if isinstance(recipe, RecipeV1):
        return sampler_v1.sample_scene_v1(recipe, scene_id, seed)
    return sampler.sample_scene(recipe, scene_id, seed)


def scene_json_bytes(scene: SceneSpec) -> bytes:
    """The exact bytes `generate_run` writes for a scene.json."""
    return model_json(scene).encode("utf-8")


def _summary(scenes: list[SceneSpec]) -> str:
    counts = [len(s.objects) for s in scenes]
    text = f"{min(counts)}-{max(counts)} objects, {sum(counts)} in all" if counts else "empty"
    diffs = [s.meta.difficulty for s in scenes if s.meta.difficulty]
    if diffs:
        total = Counter()
        for d in diffs:
            for key in ("n_stacks", "n_contained", "n_zones", "n_lookalike_pairs",
                        "n_duplicates"):  # fmt: skip
                total[key] += int(d.get(key, 0))
        grid = sum(bool(d.get("grid")) for d in diffs)
        clutter = Counter(str(d.get("clutter")) for d in diffs)
        text += (
            f"; clutter {dict(sorted(clutter.items()))}; stacks {total['n_stacks']}, "
            f"contained {total['n_contained']}, zones {total['n_zones']}, look-alike pairs "
            f"{total['n_lookalike_pairs']}, duplicates {total['n_duplicates']}, grid {grid}"
        )
    return text


def generate_run(recipe: Path, n: int, seed: int, out: Path) -> list[Path]:
    """Sample n scenes into <out>/scenes/<scene_id>/scene.json and write <out>/run.json."""
    rec = load_recipe(recipe)
    layout = RunLayout(out)
    seeds: dict[str, int] = {}
    paths: list[Path] = []
    scenes: list[SceneSpec] = []
    for i in range(n):
        scene_id = scene_id_for(i)
        scene_seed = derive_seed(seed, i)
        scene = sample_any(rec, scene_id, scene_seed)
        paths.append(write_model(layout.scene_file(scene_id), scene))
        seeds[scene_id] = scene_seed
        scenes.append(scene)
    write_model(
        layout.run_file,
        RunInfo(
            recipe=str(recipe),
            recipe_hash=rec.recipe_hash,
            run_seed=seed,
            n=n,
            generator_version=generator_version(rec),
            scenes=seeds,
        ),
    )
    stale = sorted(set(layout.scene_ids()) - set(seeds))
    print(
        f"vtf generate: {n} scenes ({_summary(scenes)}) from {rec.name} "
        f"[recipe {rec.recipe_hash[:8]}, seed {seed}] -> {layout.root}"
        + (f"; warning: {len(stale)} older scene folders left in place" if stale else "")
    )
    return paths


def regenerate_scene_json(run_info: RunInfo, scene_id: str) -> bytes:
    """Re-sample one scene from run.json and return the bytes `generate_run` wrote for it.

    The recipe is loaded from `run_info.recipe` relative to the current working directory. If the
    recipe has changed since the run, the result differs (its recipe_hash at least), which is
    what gate G7 should see.
    """
    rec = load_recipe(Path(run_info.recipe))
    return scene_json_bytes(sample_any(rec, scene_id, run_info.scenes[scene_id]))
