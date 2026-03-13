"""Characterization tests for cli/commands.py.

Locks current CLI behavior before the Phase A refactoring split.
Do NOT modify these tests unless behavior intentionally changes.
"""
from typer.testing import CliRunner

from cli_bridge.cli.commands import app

runner = CliRunner()


def test_characterize_gateway_help_exit_code():
    """Characterization: 'gateway --help' exits 0."""
    result = runner.invoke(app, ["gateway", "--help"])
    assert result.exit_code == 0


def test_characterize_gateway_help_shows_start():
    """Characterization: 'gateway --help' output contains 'start' subcommand."""
    result = runner.invoke(app, ["gateway", "--help"])
    assert "start" in result.output


def test_characterize_gateway_help_shows_stop():
    """Characterization: 'gateway --help' output contains 'stop' subcommand."""
    result = runner.invoke(app, ["gateway", "--help"])
    assert "stop" in result.output


def test_characterize_model_help_exit_code():
    """Characterization: 'model --help' exits 0."""
    result = runner.invoke(app, ["model", "--help"])
    assert result.exit_code == 0


def test_characterize_thinking_help_exit_code():
    """Characterization: 'thinking --help' exits 0."""
    result = runner.invoke(app, ["thinking", "--help"])
    assert result.exit_code == 0


def test_characterize_status_help_exit_code():
    """Characterization: 'status --help' exits 0."""
    result = runner.invoke(app, ["status", "--help"])
    assert result.exit_code == 0


def test_characterize_iflow_help_exit_code():
    """Characterization: 'iflow --help' exits 0."""
    result = runner.invoke(app, ["iflow", "--help"])
    assert result.exit_code == 0


def test_characterize_app_importable():
    """Characterization: 'app' is importable from cli_bridge.cli.commands."""
    from cli_bridge.cli.commands import app as imported_app
    assert imported_app is not None


def test_characterize_top_level_help():
    """Characterization: top-level --help shows key subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "gateway" in result.output
    assert "status" in result.output
