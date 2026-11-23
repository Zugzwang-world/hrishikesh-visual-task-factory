"""Smoke tests: the package imports, the CLI runs, and the core stays free of bpy."""

import subprocess
import sys

from typer.testing import CliRunner

import vtf
from vtf.cli import app

COMMANDS = ["generate", "render", "validate", "eval", "report", "verify", "export"]
STUBS = ["report", "export"]


def test_version() -> None:
    assert vtf.__version__


def test_cli_help() -> None:
    runner = CliRunner()
    for command in COMMANDS:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output


def test_cli_stubs() -> None:
    runner = CliRunner()
    for command in STUBS:
        result = runner.invoke(app, [command])
        assert result.exit_code == 0, result.output
        assert "not yet implemented" in result.output


def test_core_never_imports_bpy() -> None:
    code = "import sys, vtf, vtf.cli, vtf.api, vtf.core; assert 'bpy' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
