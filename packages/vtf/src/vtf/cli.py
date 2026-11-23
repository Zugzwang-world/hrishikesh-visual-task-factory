"""vtf command-line interface.

Each command is a thin wrapper around one lane's module, imported lazily, so every lane edits its
own module and (at most) its own command function here:

    generate  -> vtf.scene.generate.generate_run          (spec and sampler lane)
    render    -> vtf.render.bridge.render_run             (render lane)
    validate  -> vtf.validate.pipeline.validate_run       (coordinator, P07)
    verify    -> vtf.validate.verify.verify_run           (coordinator, P07)
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
) -> None:
    """Render scenes headless in Blender: beauty, object index, depth, amodal masks."""
    _lane("render", "vtf.render.bridge", "render_run")(
        run_dir=path, device=device, blender=blender, limit=limit, force=force
    )


@app.command()
def validate(path: Path = typer.Argument(..., help="Rendered run folder.")) -> None:
    """Compile questions, run gates G1, G3 and G7, and package passing tasks."""
    _lane("validate", "vtf.validate.pipeline", "validate_run")(run_dir=path)


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
    path: Path = typer.Argument(None, help="Task directory."),
    format: str = typer.Option("hf", help="Export format: hf, sft, rl, gltf."),
) -> None:
    """Export tasks for Hugging Face, SFT, RL or the Task Explorer."""
    _stub("export")


if __name__ == "__main__":
    app()
