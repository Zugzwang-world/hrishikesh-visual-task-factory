"""`vtf generate`: a recipe and a run seed -> run.json and one scene.json per scene.

Scene i gets the seed derive_seed(run_seed, i), so any scene can be rebuilt alone from run.json
(`regenerate_scene_json`, used by gate G7).
"""

from pathlib import Path

from vtf.core.io import model_json, write_model
from vtf.core.layout import RunLayout, scene_id_for
from vtf.core.rng import derive_seed
from vtf.core.scene import RunInfo, SceneSpec
from vtf.scene.recipe import load_recipe
from vtf.scene.sampler import GENERATOR_VERSION, sample_scene


def scene_json_bytes(scene: SceneSpec) -> bytes:
    """The exact bytes `generate_run` writes for a scene.json."""
    return model_json(scene).encode("utf-8")


def generate_run(recipe: Path, n: int, seed: int, out: Path) -> list[Path]:
    """Sample n scenes into <out>/scenes/<scene_id>/scene.json and write <out>/run.json."""
    rec = load_recipe(recipe)
    layout = RunLayout(out)
    seeds: dict[str, int] = {}
    paths: list[Path] = []
    counts: list[int] = []
    for i in range(n):
        scene_id = scene_id_for(i)
        scene_seed = derive_seed(seed, i)
        scene = sample_scene(rec, scene_id, scene_seed)
        paths.append(write_model(layout.scene_file(scene_id), scene))
        seeds[scene_id] = scene_seed
        counts.append(len(scene.objects))
    write_model(
        layout.run_file,
        RunInfo(
            recipe=str(recipe),
            recipe_hash=rec.recipe_hash,
            run_seed=seed,
            n=n,
            generator_version=GENERATOR_VERSION,
            scenes=seeds,
        ),
    )
    stale = sorted(set(layout.scene_ids()) - set(seeds))
    objects = f"{min(counts)}-{max(counts)} objects, {sum(counts)} in all" if counts else "empty"
    print(
        f"vtf generate: {n} scenes ({objects}) from {rec.name} "
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
    return scene_json_bytes(sample_scene(rec, scene_id, run_info.scenes[scene_id]))
