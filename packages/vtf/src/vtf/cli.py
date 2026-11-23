"""vtf command-line interface.

Each command is a thin wrapper around one lane's module, imported lazily, so every lane edits its
own module and (at most) its own command function here:

    generate  -> vtf.scene.generate.generate_run          (spec and sampler lane)
    render    -> vtf.render.bridge.render_run             (render lane)
    validate  -> vtf.validate.pipeline.validate_run       (validation lane)
    run       -> vtf.validate.scale.scale_run             (validation lane)
    verify    -> vtf.validate.verify.verify_run           (validation lane)
    eval      -> vtf.eval.runner.eval_run                 (eval lane)
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
) -> None:
    """Resumable scale run: generate, then render, validate, twin and collect in batches."""
    _lane("run", "vtf.validate.scale", "scale_run")(
        recipe=recipe, n=n, seed=seed, out=out, batch=batch, twin_fraction=twin_fraction,
        twins=not no_twins, max_scenes=max_scenes, device=device,
    )  # fmt: skip


@app.command(name="eval")
def eval_(
    path: Path = typer.Argument(..., help="Validated run folder."),
    model: str = typer.Option("claude-sonnet-5-5", help="Model id."),
    limit: int = typer.Option(None, help="Evaluate at most this many tasks."),
    models: Path = typer.Option(None, help="Models config YAML (stage 4)."),
) -> None:
    """Run a model on the tasks, score its answers and print accuracy per family."""
    _lane("eval", "vtf.eval.runner", "eval_run")(
        run_dir=path, model=model, limit=limit, models_config=models
    )


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
