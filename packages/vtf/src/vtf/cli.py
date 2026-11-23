"""vtf command-line interface.

Each command is a thin wrapper around one lane's module, imported lazily, so every lane edits its
own module and (at most) its own command function here:

    generate  -> vtf.scene.generate.generate_run          (spec and sampler lane)
    render    -> vtf.render.bridge.render_run             (render lane)
    validate  -> vtf.validate.pipeline.validate_run       (coordinator, P07)
    verify    -> vtf.validate.verify.verify_run           (coordinator, P07)
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
def validate(path: Path = typer.Argument(..., help="Rendered run folder.")) -> None:
    """Compile questions, run gates G1, G3 and G7, and package passing tasks."""
    _lane("validate", "vtf.validate.pipeline", "validate_run")(run_dir=path)


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
    path: Path = typer.Argument(None, help="Run folder."),
    format: str = typer.Option("hf", help="Export format: hf, sft, rl, explorer."),
    out: Path = typer.Option(None, help="Output folder (explorer: e.g. web/public/data)."),
    scenes: int = typer.Option(6, help="explorer: number of scenes in the sample."),
    max_width: int = typer.Option(1024, help="explorer: camera images at most this wide (px)."),
    max_mb: float = typer.Option(25.0, help="explorer: size budget for the whole sample (MB)."),
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
        )
        return
    _stub("export")


@app.command()
def twin(
    action: str = typer.Argument(..., help="mat, shopping or layouts."),
    path: Path = typer.Argument(None, help="Run folder (layouts only), e.g. out/twins."),
    out: Path = typer.Option(
        Path("docs/twins"), help="Output folder, or the PDF path for layouts."
    ),
    limit: int = typer.Option(None, help="Layouts: at most this many scenes."),
) -> None:
    """Physical twins: placement mat PDFs, shopping list, printable layout sheets."""
    _lane("twin", "vtf.twin.commands", "run_twin")(action=action, path=path, out=out, limit=limit)


if __name__ == "__main__":
    app()
