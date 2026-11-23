"""vtf command-line interface. Every command is a stub until stage 2."""

from pathlib import Path

import typer

app = typer.Typer(
    help="Visual Task Factory: generate, render, validate and evaluate 3D robot-workcell tasks.",
    no_args_is_help=True,
)


def _stub(command: str) -> None:
    typer.echo(f"vtf {command}: not yet implemented")


@app.command()
def generate(
    recipe: Path = typer.Option(None, help="Recipe YAML."),
    out: Path = typer.Option(None, help="Output directory."),
) -> None:
    """Sample scene files and questions from a recipe and a seed."""
    _stub("generate")


@app.command()
def render(path: Path = typer.Argument(None, help="Generated task directory.")) -> None:
    """Render scenes headless in Blender, with mask and depth passes."""
    _stub("render")


@app.command()
def validate(path: Path = typer.Argument(None, help="Rendered task directory.")) -> None:
    """Run validation gates G1-G8 and write gate reports."""
    _stub("validate")


@app.command(name="eval")
def eval_(
    path: Path = typer.Argument(None, help="Validated task directory."),
    models: Path = typer.Option(None, help="Models config YAML."),
) -> None:
    """Run models on the tasks and score their answers."""
    _stub("eval")


@app.command()
def report(path: Path = typer.Argument(None, help="Evaluated task directory.")) -> None:
    """Build the findings report and model risk cards."""
    _stub("report")


@app.command()
def verify(
    path: Path = typer.Argument(None, help="Task directory."),
    sample: int = typer.Option(20, help="Number of tasks to rebuild from their seeds."),
) -> None:
    """Rebuild sampled tasks from their seeds and compare hashes."""
    _stub("verify")


@app.command()
def export(
    path: Path = typer.Argument(None, help="Task directory."),
    format: str = typer.Option("hf", help="Export format: hf, sft, rl, gltf."),
) -> None:
    """Export tasks for Hugging Face, SFT, RL or the Task Explorer."""
    _stub("export")


if __name__ == "__main__":
    app()
