"""`vtf quickstart`: the whole factory on recipes/quick.yaml with one command.

1. assets: fetch the Poly Haven models and the HDRI the recipe uses into assets/cache/ (only the
   missing ones; CC0, about 18 MB on a fresh clone);
2. generate, render, validate, twin and collect: the resumable scale run (vtf.validate.scale) on
   N scenes in one batch: scene files from the seed, Blender headless (beauty, object index,
   depth and amodal masks per camera, the camera-quality dial), gates G1-G4 and G7 on every
   question, G6 counterfactual twins on a sample, tasks/, manifest.jsonl, rejected.jsonl and
   validate_report.json;
3. evaluate: fake-oracle on every task (it returns the true answer, so the scorer must give
   100%), then claude-sonnet-5-5 on a seeded 20-task sample when ANTHROPIC_API_KEY is set
   (environment or .env); replies are cached in out/cache/, so a rerun costs nothing;
4. report: the static dataset report (<run>/report/index.html) and a contact sheet of tasks
   with true and model answers.

Every step is a pure function of the recipe, the seed and the code versions, so a rerun on the
same folder resumes and a fresh folder gives the same scene files, tasks and answers. Writes
<run>/quickstart.json with the counts and the time each step took.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RECIPE = Path("recipes/quick.yaml")  # relative, so run.json holds no machine path
DEFAULT_OUT = Path("out/quick")
REAL_MODEL = "claude-sonnet-5-5"
ORACLE = "fake-oracle"
SUMMARY_FILE = "quickstart.json"


def required_assets(recipe_path: Path) -> tuple[list[str], list[str]]:
    """(Poly Haven model ids, HDRI ids) the recipe can place, from the recipe and the manifest."""
    from vtf.core.assets import load_asset_manifest
    from vtf.scene.recipe import load_recipe

    recipe = load_recipe(recipe_path)
    lighting = recipe.lighting
    hdris = list(getattr(lighting, "hdri_ids", None) or [getattr(lighting, "hdri_id", "")])
    hdris = [h for h in hdris if h]
    objects = recipe.objects
    if not hasattr(objects, "assets") or objects.pool_weights.get("assets", 0.0) <= 0:
        return [], hdris
    pool = objects.assets
    models = [
        e.id
        for e in load_asset_manifest().assets
        if e.source == "polyhaven"
        and (pool.ids is None or e.id in pool.ids)
        and (pool.categories is None or e.category in pool.categories)
        and e.id not in pool.exclude_ids
        and pool.source_weights.get("polyhaven", 0.0) > 0
    ]
    return models, hdris


def missing_assets(models: list[str], hdris: list[str]) -> list[str]:
    from vtf.core.assets import asset_cache_dir, load_asset_manifest

    manifest = load_asset_manifest()
    cache = asset_cache_dir(manifest)
    missing = [m for m in models if not (cache / manifest.get(m).file).exists()]
    missing += [h for h in hdris if not (cache / h / "meta.json").exists()]
    return missing


def fetch_assets(models: list[str], hdris: list[str], echo: Callable[[str], Any]) -> int:
    """Fetch the missing ones with assets/fetch_manifest.py; returns how many were missing."""
    missing = missing_assets(models, hdris)
    if not missing:
        echo(f"assets: all {len(models)} models and {len(hdris)} HDRI(s) already cached")
        return 0
    echo(f"assets: fetching {len(missing)} CC0 asset(s) from Poly Haven: {', '.join(missing)}")
    script = REPO_ROOT / "assets" / "fetch_manifest.py"
    cmd = [sys.executable, str(script), "--ids", *models, "--hdri", *hdris]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    still = missing_assets(models, hdris)
    if still:
        raise SystemExit(f"vtf quickstart: could not fetch {', '.join(still)}")
    return len(missing)


def _api_key_set() -> bool:
    load_dotenv(find_dotenv(usecwd=True))
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def quickstart(
    recipe: Path = DEFAULT_RECIPE,
    n: int = 7,
    seed: int = 1,
    out: Path = DEFAULT_OUT,
    device: str = "gpu",
    twins: bool = True,
    twin_fraction: float = 0.1,
    model: str | None = None,
    eval_sample: int = 20,
    min_free_gb: float = 2.0,
    echo: Callable[[str], Any] = print,
) -> dict:
    from vtf.core.io import read_jsonl
    from vtf.core.layout import RunLayout
    from vtf.eval.runner import eval_run
    from vtf.render.bridge import find_blender
    from vtf.validate.scale import scale_run

    recipe, out = Path(recipe), Path(out)
    if not recipe.is_absolute() and not recipe.exists():
        recipe = REPO_ROOT / recipe  # run from outside the repo
    lay = RunLayout(out)
    steps: dict[str, float] = {}
    t_all = time.perf_counter()
    try:
        blender = find_blender()
    except FileNotFoundError as exc:
        raise SystemExit(f"vtf quickstart: {exc}. Install Blender 5.3 first.") from exc
    echo(f"vtf quickstart: {n} scenes from {recipe} with seed {seed} -> {out} (Blender {blender})")

    t = time.perf_counter()
    models, hdris = required_assets(recipe)
    fetched = fetch_assets(models, hdris, echo)
    steps["assets"] = time.perf_counter() - t

    t = time.perf_counter()
    report = scale_run(
        recipe=recipe, n=n, seed=seed, out=out, batch=n, twin_fraction=twin_fraction,
        twins=twins, device=device, min_free_gb=min_free_gb,
    )  # fmt: skip
    steps["generate_render_validate"] = time.perf_counter() - t
    rows = list(read_jsonl(lay.task_manifest)) if lay.task_manifest.exists() else []
    if not rows:
        raise SystemExit("vtf quickstart: no task passed the gates; see " + str(lay.logs_dir))

    evals: dict[str, dict] = {}
    t = time.perf_counter()
    oracle = eval_run(out, model=ORACLE, echo=echo)
    evals[ORACLE] = {"n": oracle.n, "accuracy": oracle.accuracy}
    real = model or (REAL_MODEL if _api_key_set() else None)
    if real and real != ORACLE:
        summary = eval_run(out, model=real, sample=eval_sample, stratify=("family",), echo=echo)
        evals[real] = {"n": summary.n, "accuracy": summary.accuracy}
    else:
        echo("eval: no ANTHROPIC_API_KEY, so only fake-oracle ran (add one to .env for Sonnet)")
    steps["eval"] = time.perf_counter() - t

    t = time.perf_counter()
    pages: dict[str, str | None] = {"dataset_report": None, "contact_sheet": None}
    try:
        from vtf.validate.report import dataset_report

        pages["dataset_report"] = str(dataset_report(out, echo=echo))
    except Exception as exc:  # the report is a view; never fail the run over it
        echo(f"report: dataset report skipped ({type(exc).__name__}: {exc})")
    try:
        from vtf.package.contact_sheet import make_contact_sheet

        sheet = lay.report_dir / "contact_sheet.png"
        make_contact_sheet(out, real or ORACLE, sheet, max_tasks=12, cols=4)
        pages["contact_sheet"] = str(sheet)
    except (Exception, SystemExit) as exc:
        echo(f"report: contact sheet skipped ({type(exc).__name__}: {exc})")
    steps["report"] = time.perf_counter() - t
    steps["total"] = time.perf_counter() - t_all

    scenes = len(lay.scene_ids())
    images = sum(1 for s in lay.scene_ids() for _ in (lay.render_dir(s)).glob("*/beauty.png"))
    result = {
        "recipe": str(recipe),
        "seed": seed,
        "run": str(out),
        "device": device,
        "assets_fetched": fetched,
        "scenes": scenes,
        "images": images,
        "tasks": len(rows) - report.get("twin_tasks", 0),
        "twin_tasks": report.get("twin_tasks", 0),
        "tasks_in_manifest": len(rows),
        "rejected": report.get("rejected", 0),
        "families": sorted({r["family"] for r in rows}),
        "gate_pass_rates": {g: s.get("pass_rate") for g, s in report.get("gates", {}).items()},
        "eval": evals,
        "pages": pages,
        "seconds": {k: round(v, 1) for k, v in steps.items()},
    }
    (out / SUMMARY_FILE).write_text(json.dumps(result, indent=2) + "\n")
    _print(result, echo)
    return result


def _print(r: dict, echo: Callable[[str], Any]) -> None:
    rates = ", ".join(f"{g} {v:.0%}" for g, v in r["gate_pass_rates"].items() if v is not None)
    evals = "; ".join(f"{m} {e['accuracy']:.0%} of {e['n']}" for m, e in r["eval"].items())
    s = r["seconds"]
    echo("")
    echo(f"vtf quickstart: done in {s['total'] / 60:.1f} min")
    echo(f"  {r['scenes']} scenes, {r['images']} images, {r['tasks']} tasks in "
         f"{len(r['families'])} families (+{r['twin_tasks']} twin tasks), "
         f"{r['rejected']} candidate questions rejected")  # fmt: skip
    echo(f"  gate pass rates: {rates}")
    echo(f"  eval: {evals}")
    echo(f"  steps (s): assets {s['assets']}, generate+render+validate "
         f"{s['generate_render_validate']}, eval {s['eval']}, report {s['report']}")  # fmt: skip
    run = r["run"]
    echo(f"  look at: {run}/report/index.html, {run}/report/contact_sheet.png, "
         f"{run}/tasks/<task_id>/task.json, {run}/rejected.jsonl")  # fmt: skip
    echo(f"  next: uv run vtf verify {run} --sample 2 --device cpu   (re-render, compare hashes)")
