"""vtf command-line interface.

Each command is a thin wrapper around one lane's module, imported lazily, so every lane edits its
own module and (at most) its own command function here:

    generate  -> vtf.scene.generate.generate_run          (spec and sampler lane)
    render    -> vtf.render.bridge.render_run             (render lane)
    validate  -> vtf.validate.pipeline.validate_run       (validation lane)
    run       -> vtf.validate.scale.scale_run             (validation lane)
    verify    -> vtf.validate.verify.verify_run           (validation lane)
    eval      -> vtf.eval.runner.eval_run                 (eval lane)
    balance   -> vtf.validate.balance.balance_run         (report and audit lane, G5)
    baseline  -> vtf.validate.gates.g5_vision_necessity.blind_baseline
    audit     -> vtf.package.audit.make_audit / score_audit   (P16)
    dataset-report -> vtf.validate.report.dataset_report  (P17)
"""

import importlib
from collections.abc import Callable
from pathlib import Path

import typer

app = typer.Typer(
    help="Visual Task Factory: generate, render, validate and evaluate 3D robot-workcell tasks.",
    no_args_is_help=True,
)


DEVICE_HELP = (
    "Render device. gpu (default) is fast; images then match re-renders at SSIM >= 0.999. "
    "cpu gives byte-identical images, e.g. for vtf verify on stage."
)


def _stub(command: str) -> None:
    typer.echo(f"vtf {command}: not yet implemented")


def _lane(command: str, module: str, func: str) -> Callable:
    try:
        return getattr(importlib.import_module(module), func)
    except (ImportError, AttributeError) as exc:
        typer.echo(f"vtf {command}: not yet implemented ({module}.{func}: {exc})")
        raise typer.Exit(1) from exc


@app.command()
def generate(
    recipe: Path = typer.Option(..., help="Recipe YAML, e.g. recipes/first_scene.yaml."),
    n: int = typer.Option(20, help="Number of scenes."),
    seed: int = typer.Option(1, help="Run seed; same seed gives byte-identical scene files."),
    out: Path = typer.Option(..., help="Run folder, e.g. out/s2."),
) -> None:
    """Sample scene files from a recipe and a seed into <out>/scenes/."""
    _lane("generate", "vtf.scene.generate", "generate_run")(recipe=recipe, n=n, seed=seed, out=out)


@app.command()
def render(
    path: Path = typer.Argument(..., help="Run folder written by vtf generate."),
    device: str = typer.Option("gpu", help=DEVICE_HELP),
    blender: str = typer.Option(
        None, help="Blender executable (default: $VTF_BLENDER or the app)."
    ),
    limit: int = typer.Option(None, help="Render at most this many scenes."),
    force: bool = typer.Option(False, help="Re-render scenes that already have a manifest."),
    batch_size: int = typer.Option(5, help="Scenes per Blender process."),
    timeout: float = typer.Option(300, help="Seconds per scene before Blender is killed."),
    gltf: bool = typer.Option(True, help="Export scene.glb for the Task Explorer."),
    keep_raw: bool = typer.Option(
        True, help="Keep beauty_raw.png (the image before the camera dial); off saves disk."
    ),
) -> None:
    """Render scenes headless in Blender: every camera's beauty, object index, depth and amodal
    masks, the camera-quality dial, and scene.glb."""
    _lane("render", "vtf.render.bridge", "render_run")(
        run_dir=path,
        device=device,
        blender=blender,
        limit=limit,
        force=force,
        batch_size=batch_size,
        timeout_per_scene=timeout,
        gltf=gltf,
        keep_raw=keep_raw,
    )


@app.command()
def validate(
    path: Path = typer.Argument(..., help="Rendered run folder."),
    twins: bool = typer.Option(
        True, "--twins/--no-twins", help="G6: render counterfactual twins for a sample of tasks."
    ),
    twin_fraction: float = typer.Option(0.2, help="G6 sample per family x rig (0.2 = 20%)."),
    device: str = typer.Option("gpu", help=DEVICE_HELP),
    force: bool = typer.Option(False, help="Re-validate scenes that are already validated."),
    collect_only: bool = typer.Option(
        False, help="Only rebuild manifest.jsonl, rejected.jsonl and validate_report.json."
    ),
) -> None:
    """Validate rendered scenes not yet validated (gates G1-G4, G7; G6 twins on a sample),
    package passing tasks, then collect the run's manifest, rejection log and report."""
    if collect_only:
        _lane("validate", "vtf.validate.pipeline", "collect")(run_dir=path)
        return
    _lane("validate", "vtf.validate.pipeline", "validate_run")(
        run_dir=path, force=force, twins=twins, twin_fraction=twin_fraction, device=device
    )


@app.command(name="run")
def run_(
    recipe: Path = typer.Option(..., help="Recipe YAML."),
    n: int = typer.Option(..., help="Number of scenes."),
    seed: int = typer.Option(1, help="Run seed."),
    out: Path = typer.Option(..., help="Run folder."),
    batch: int = typer.Option(25, help="Scenes per batch (render, validate, twins, collect)."),
    twin_fraction: float = typer.Option(0.2, help="G6 sample of each batch's new tasks."),
    no_twins: bool = typer.Option(False, "--no-twins", help="Skip G6 twins."),
    max_scenes: int = typer.Option(None, help="Process only the first N scenes of the run."),
    device: str = typer.Option("gpu", help=DEVICE_HELP),
    min_free_gb: float = typer.Option(20.0, help="Stop cleanly below this much free disk (GB)."),
    keep_beauty_raw: bool = typer.Option(False, help="Keep beauty_raw.png copies."),
) -> None:
    """Resumable scale run: generate, then render, validate, twin and collect in batches."""
    _lane("run", "vtf.validate.scale", "scale_run")(
        recipe=recipe, n=n, seed=seed, out=out, batch=batch, twin_fraction=twin_fraction,
        twins=not no_twins, max_scenes=max_scenes, device=device, min_free_gb=min_free_gb,
        keep_beauty_raw=keep_beauty_raw,
    )  # fmt: skip


@app.command(name="eval")
def eval_(
    path: Path = typer.Argument(..., help="Validated run folder."),
    model: str = typer.Option("claude-sonnet-5-5", help="Model id."),
    limit: int = typer.Option(None, help="Evaluate at most this many tasks."),
    models: Path = typer.Option(None, help="Models config YAML (stage 4)."),
    sample: int = typer.Option(
        None, help="Seeded stratified sample of this many tasks (equal share per stratum)."
    ),
    stratify: str = typer.Option(
        "family,rig", help="Strata for --sample: family, rig, camera_id, shortcut_conflict."
    ),
    seed: int = typer.Option(0, help="Seed for --sample."),
    blind: bool = typer.Option(
        False, help="Question only, no image (G5 baseline); results go to eval/<model>__blind/."
    ),
) -> None:
    """Run a model on the tasks, score its answers and print accuracy per family."""
    _lane("eval", "vtf.eval.runner", "eval_run")(
        run_dir=path,
        model=model,
        limit=limit,
        models_config=models,
        sample=sample,
        stratify=tuple(s.strip() for s in stratify.split(",") if s.strip()),
        seed=seed,
        blind=blind,
    )


@app.command()
def balance(
    path: Path = typer.Argument(..., help="Validated run folder (after vtf validate)."),
    seed: int = typer.Option(0, help="Seed for the rejection sampling."),
) -> None:
    """G5: rebalance answer distributions per family; write the prior-only baseline."""
    _lane("balance", "vtf.validate.balance", "balance_run")(run_dir=path, seed=seed)


@app.command()
def baseline(
    path: Path = typer.Argument(..., help="Balanced run folder (after vtf balance)."),
    model: str = typer.Option("claude-sonnet-5-5", help="Model asked without the image."),
    per_family: int = typer.Option(15, help="Tasks per family, stratified by rig."),
    seed: int = typer.Option(0, help="Sampling seed."),
    models: Path = typer.Option(None, help="Models config YAML."),
) -> None:
    """G5: question-only baseline (no image) per family, compared with the prior."""
    _lane("baseline", "vtf.validate.gates.g5_vision_necessity", "blind_baseline")(
        run_dir=path, model=model, per_family=per_family, seed=seed, models_config=models
    )


audit_app = typer.Typer(help="P16 blind audit: a labelling page for the founder, and its scorer.")
app.add_typer(audit_app, name="audit")


@audit_app.command("make")
def audit_make(
    path: Path = typer.Argument(..., help="Validated run folder."),
    n: int = typer.Option(150, help="Tasks in the audit (stratified family x rig x conflict)."),
    seed: int = typer.Option(0, help="Sampling seed."),
    out: Path = typer.Option(None, help="Output folder (default <run>/audit)."),
    png: bool = typer.Option(False, help="Copy PNG images as they are (default: JPEG q90)."),
) -> None:
    """Write <run>/audit/index.html: one task at a time, answers kept in the browser."""
    _lane("audit make", "vtf.package.audit", "make_audit")(
        run_dir=path, n=n, seed=seed, out_dir=out, image_format="png" if png else "jpg"
    )


@audit_app.command("score")
def audit_score(
    path: Path = typer.Argument(..., help="The run the audit was drawn from."),
    answers: Path = typer.Argument(..., help="Answers JSON exported from the audit page."),
    audit_dir: Path = typer.Option(None, help="Audit folder (default <run>/audit)."),
) -> None:
    """Score the founder's answers with the eval scorer: accuracy per family and the misses."""
    _lane("audit score", "vtf.package.audit", "score_audit")(
        run_dir=path, answers_path=answers, audit_dir=audit_dir
    )


@audit_app.command("refresh")
def audit_refresh(
    path: Path = typer.Argument(..., help="Validated run with an audit draw in <run>/audit."),
    exclude_scenes: Path = typer.Option(
        ..., help="JSON list of scene ids the auditor has seen with answers (twins: source)."
    ),
    prefer: Path = typer.Option(
        None, help="JSON with a task_ids list (e.g. the sweep's task_set.json): take these first."
    ),
    seed: int = typer.Option(1, help="Replacement seed."),
) -> None:
    """Swap every audit task from an already-seen scene for a fresh one of the same stratum;
    rewrites <run>/audit/index.html and writes sample_v2.json (sample.json stays as drawn)."""
    import json as _json

    scenes = _json.loads(exclude_scenes.read_text())
    prefer_ids = _json.loads(prefer.read_text())["task_ids"] if prefer else None
    _lane("audit refresh", "vtf.package.audit", "refresh_audit")(
        run_dir=path, exclude_scenes=scenes, prefer_ids=prefer_ids, seed=seed
    )


@audit_app.command("make-s2")
def audit_make_s2(
    run: Path = typer.Option(Path("out/s2"), help="The stage-2 run (read only)."),
    out: Path = typer.Option(Path("out/audit_s2"), help="Output folder."),
) -> None:
    """Audit page for every stage-2 nearest-to-gripper task (out/s2 -> out/audit_s2)."""
    _lane("audit make-s2", "vtf.package.audit", "make_audit")(
        run_dir=run,
        n=None,
        out_dir=out,
        families=["nearest_to_gripper"],
        title="Blind audit: stage 2, nearest to gripper",
    )


@app.command(name="dataset-report")
def dataset_report(
    path: Path = typer.Argument(..., help="Validated (and ideally balanced, evaluated) run."),
    out: Path = typer.Option(None, help="Output folder (default <run>/report)."),
) -> None:
    """P17: write <run>/report/index.html, the static dataset report for judges."""
    _lane("dataset-report", "vtf.validate.report", "dataset_report")(run_dir=path, out_dir=out)


@app.command()
def report(
    path: Path = typer.Argument(..., help="Evaluated run folder."),
    model: str = typer.Option("claude-sonnet-5-5", help="Model whose answers to show."),
    out: Path = typer.Option(
        None, help="Contact sheet PNG (default <run>/report/contact_sheet.png)."
    ),
    max_tasks: int = typer.Option(24, help="Tasks on the sheet."),
) -> None:
    """Contact sheet of tasks with true and model answers (findings report: stage 4)."""
    from vtf.package.contact_sheet import make_contact_sheet

    make_contact_sheet(
        path, model, out or Path(path) / "report" / "contact_sheet.png", max_tasks=max_tasks
    )


@app.command()
def verify(
    path: Path = typer.Argument(..., help="Validated run folder."),
    sample: int = typer.Option(5, help="Number of scenes to rebuild from their seeds."),
    device: str = typer.Option("gpu", help=DEVICE_HELP),
) -> None:
    """Rebuild sampled scenes from their seeds and compare G7 hashes and image SSIM."""
    _lane("verify", "vtf.validate.verify", "verify_run")(run_dir=path, sample=sample, device=device)


@app.command()
def export(
    path: Path = typer.Argument(None, help="Run folder."),
    format: str = typer.Option("hf", help="Export format: hf, sft, rl, explorer."),
    out: Path = typer.Option(None, help="Output folder (explorer: e.g. web/public/data)."),
    scenes: int = typer.Option(6, help="explorer: number of scenes in the sample."),
    max_width: int = typer.Option(1024, help="explorer: camera images at most this wide (px)."),
    max_mb: float = typer.Option(40.0, help="explorer: size budget for the whole sample (MB)."),
    results: list[Path] = typer.Option(
        None,
        "--results",
        help="explorer: a result folder (results.jsonl + summary.json), labelled by its name; "
        "repeat for each model. Default: <run>/eval/*.",
    ),
    gallery: Path = typer.Option(
        None, help='explorer: JSON with a "gallery" list (docs/reports/v1/findings.json).'
    ),
    compress_glb: bool = typer.Option(
        True, help="explorer: meshopt + WebP glTF via web/scripts/compress-glb.mjs (needs Node)."
    ),
) -> None:
    """Export tasks for Hugging Face, SFT, RL or the Task Explorer."""
    if format == "explorer":
        if path is None:
            raise typer.BadParameter("give the run folder, e.g. out/s2")
        from vtf.core.layout import RunLayout
        from vtf.package.explorer import export_explorer

        export_explorer(
            path,
            out or RunLayout(path).explorer_dir,
            n_scenes=scenes,
            max_width=max_width,
            max_mb=max_mb,
            results_dirs=results or None,
            gallery=gallery,
            compress_glbs=compress_glb,
        )
        return
    _stub("export")


@app.command(name="verify-showcase")
def verify_showcase(
    path: Path = typer.Argument(Path("out/v1"), help="A validated (e.g. frozen) run; read only."),
    scenes: str = typer.Option(
        None, help="Comma-separated scene ids (default scene_0004,scene_0170,scene_0014)."
    ),
    out: Path = typer.Option(Path("out/verify_showcase"), help="Where the re-renders go."),
    timeout: float = typer.Option(1800, help="Seconds per scene before Blender is killed."),
    replay: int = typer.Option(
        5, help="Also replay the run's first N scenes in one Blender process, as rendered (0: no)."
    ),
) -> None:
    """Rule C on a frozen run: re-sample and re-render the scenes once on the GPU and twice on
    the CPU, and replay the first render batch; show what is exact and how close images come."""
    from vtf.validate.showcase import verify_showcase as run_showcase

    ids = [s.strip() for s in scenes.split(",") if s.strip()] if scenes else None
    run_showcase(path, scenes=ids, out=out, timeout=timeout, replay=replay)


@app.command()
def quickstart(
    recipe: Path = typer.Option(None, help="Recipe YAML (default recipes/quick.yaml)."),
    n: int = typer.Option(7, help="Scenes; each has 3 cameras, so 7 scenes = 21 images."),
    seed: int = typer.Option(1, help="Run seed; same seed gives byte-identical scene files."),
    out: Path = typer.Option(Path("out/quick"), help="Run folder (resumes if it exists)."),
    device: str = typer.Option("gpu", help=DEVICE_HELP),
    twins: bool = typer.Option(
        True, "--twins/--no-twins", help="G6: render counterfactual twins for a sample of tasks."
    ),
    model: str = typer.Option(
        None, help="Model for the 20-task eval (default claude-sonnet-5-5 if ANTHROPIC_API_KEY)."
    ),
    eval_sample: int = typer.Option(20, help="Tasks in the real-model eval (seeded sample)."),
) -> None:
    """One command, end to end: fetch the recipe's CC0 assets, generate, render, validate and
    package about 20 images, evaluate (fake-oracle always; Sonnet if a key is set), report."""
    from vtf.quickstart import DEFAULT_RECIPE
    from vtf.quickstart import quickstart as run_quickstart

    run_quickstart(
        recipe=recipe or DEFAULT_RECIPE, n=n, seed=seed, out=out, device=device, twins=twins,
        model=model, eval_sample=eval_sample,
    )  # fmt: skip


@app.command()
def twin(
    action: str = typer.Argument(
        ...,
        help="mat, shopping, layouts, texture, guide, digital, check, register or sim-to-real.",
    ),
    path: Path = typer.Argument(
        None,
        help="layouts/guide/digital: the layout run (out/twins); check: the digital-twin run; "
        "register: the photo inbox (twins/inbox); sim-to-real: the register output.",
    ),
    out: Path = typer.Option(
        None,
        help="Output (default docs/twins; layouts docs/twins/layouts.pdf; digital "
        "out/twins_digital; register out/twins_real).",
    ),
    limit: int = typer.Option(None, help="Layouts: at most this many scenes."),
    selection: Path = typer.Option(
        None, help="Layout pick (docs/twins/selection.json): only those sheets and items."
    ),
    twins: Path = typer.Option(
        None, help="register: the rendered digital twins (default out/twins_digital)."
    ),
    render: bool = typer.Option(
        True, help="register: render and validate the photos' synthetic siblings."
    ),
    device: str = typer.Option("gpu", help=DEVICE_HELP),
    model: list[str] = typer.Option(
        None, help="sim-to-real: model(s) to compare (default fake-oracle). Repeat the option."
    ),
    focal_35mm: float = typer.Option(
        None, help="register: the phone's 35 mm-equivalent focal length if its photos lack EXIF."
    ),
) -> None:
    """Physical twins: mat, shopping list, layout sheets, photo guide, digital twins, gate G8 on
    photos, and the sim-to-real comparison."""
    _lane("twin", "vtf.twin.commands", "run_twin")(
        action=action, path=path, out=out, limit=limit, selection=selection, twins=twins,
        render=render, device=device, models=model or None, focal_35mm=focal_35mm,
    )  # fmt: skip


if __name__ == "__main__":
    app()
