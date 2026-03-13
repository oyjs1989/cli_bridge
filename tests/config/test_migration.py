"""Tests for config schema loading with backend + transport fields."""

import json

import pytest
from pydantic import ValidationError

from cli_bridge.config.loader import load_config
from cli_bridge.config.schema import Config, DriverConfig

# ── New backend + transport format ───────────────────────────────────────────

def test_new_backend_transport_round_trip(tmp_path):
    """Config with backend/transport fields loads correctly."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "backend": "iflow",
            "transport": "stdio",
            "iflow": {"iflow_path": "iflow", "model": "minimax-m2.5"},
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.backend == "iflow"
    assert config.driver.transport == "stdio"
    assert config.driver.iflow is not None
    assert config.driver.iflow.model == "minimax-m2.5"
    assert config.driver.claude is None


def test_new_claude_stdio_combination():
    """backend='claude', transport='stdio' is valid."""
    d = DriverConfig(backend="claude", transport="stdio")
    assert d.backend == "claude"
    assert d.transport == "stdio"


def test_claude_acp_combination_raises_validation_error():
    """backend='claude' + transport='acp' is not supported."""
    with pytest.raises((ValidationError, ValueError)):
        DriverConfig(backend="claude", transport="acp")


def test_iflow_cli_combination():
    """backend='iflow', transport='cli' is valid."""
    d = DriverConfig(backend="iflow", transport="cli")
    assert d.backend == "iflow"
    assert d.transport == "cli"
    assert d.iflow is not None


def test_iflow_stdio_combination():
    """backend='iflow', transport='stdio' is valid."""
    d = DriverConfig(backend="iflow", transport="stdio")
    assert d.backend == "iflow"
    assert d.transport == "stdio"


def test_iflow_acp_combination():
    """backend='iflow', transport='acp' is valid."""
    d = DriverConfig(backend="iflow", transport="acp")
    assert d.backend == "iflow"
    assert d.transport == "acp"


def test_claude_cli_combination():
    """backend='claude', transport='cli' is valid."""
    d = DriverConfig(backend="claude", transport="cli")
    assert d.backend == "claude"
    assert d.transport == "cli"


# ── Config file loading ───────────────────────────────────────────────────────

def test_claude_config_loads_correctly(tmp_path):
    """Claude config loads successfully; driver.iflow is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "backend": "claude",
            "transport": "cli",
            "claude": {
                "claude_path": "claude",
                "model": "claude-opus-4-6",
                "permission_mode": "bypassPermissions",
            }
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.backend == "claude"
    assert config.driver.transport == "cli"
    assert config.driver.claude is not None
    assert config.driver.claude.model == "claude-opus-4-6"
    assert config.driver.iflow is None


def test_claude_auto_populates_defaults(tmp_path):
    """When claude block absent in claude backend, defaults are auto-populated."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {"backend": "claude", "transport": "cli"}
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.backend == "claude"
    assert config.driver.claude is not None
    assert config.driver.claude.claude_path == "claude"
    assert config.driver.iflow is None


def test_iflow_config_loads_without_claude_block(tmp_path):
    """iflow config loads; driver.claude is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "backend": "iflow",
            "transport": "stdio",
            "iflow": {
                "iflow_path": "iflow",
                "model": "minimax-m2.5",
            }
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.backend == "iflow"
    assert config.driver.transport == "stdio"
    assert config.driver.iflow is not None
    assert config.driver.iflow.model == "minimax-m2.5"
    assert config.driver.claude is None


# ── get_model() dispatch ──────────────────────────────────────────────────────

def test_get_model_returns_iflow_model():
    config = Config(driver=DriverConfig(backend="iflow", transport="stdio"))
    assert config.get_model() == "minimax-m2.5"


def test_get_model_returns_claude_model():
    config = Config(driver=DriverConfig(backend="claude", transport="cli"))
    assert config.get_model() == "claude-opus-4-6"


# ── per-backend separation ────────────────────────────────────────────────────

def test_iflow_config_has_no_claude_block(tmp_path):
    """iflow config loads with driver.claude is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "backend": "iflow",
            "transport": "cli",
            "iflow": {"iflow_path": "iflow", "model": "minimax-m2.5"}
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.backend == "iflow"
    assert config.driver.transport == "cli"
    assert config.driver.iflow is not None
    assert config.driver.claude is None


def test_claude_config_has_no_iflow_block(tmp_path):
    """claude config loads with driver.iflow is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "backend": "claude",
            "transport": "cli",
            "claude": {"claude_path": "claude", "model": "claude-opus-4-6"}
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.backend == "claude"
    assert config.driver.claude is not None
    assert config.driver.iflow is None


def test_acp_transport_populates_iflow_block():
    """acp transport auto-populates driver.iflow with defaults."""
    config = Config(driver=DriverConfig(backend="iflow", transport="acp"))
    assert config.driver.backend == "iflow"
    assert config.driver.transport == "acp"
    assert config.driver.iflow is not None
    assert config.driver.iflow.acp_port == 8090
    assert config.driver.claude is None


# ── _create_default_config format ────────────────────────────────────────────

def test_default_config_iflow_writes_iflow_block(tmp_path):
    """_create_default_config writes iflow block using backend/transport format."""
    from cli_bridge.config.loader import _create_default_config

    cfg_file = tmp_path / "config.json"
    _create_default_config(cfg_file, backend="iflow", transport="stdio")

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "iflow" in data["driver"]
    assert "claude" not in data["driver"]
    assert data["driver"]["backend"] == "iflow"
    assert data["driver"]["transport"] == "stdio"
    assert "mode" not in data["driver"]


def test_default_config_claude_writes_claude_block(tmp_path):
    """_create_default_config writes claude block using backend/transport format."""
    from cli_bridge.config.loader import _create_default_config

    cfg_file = tmp_path / "config.json"
    _create_default_config(cfg_file, backend="claude", transport="cli")

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "claude" in data["driver"]
    assert "iflow" not in data["driver"]
    assert data["driver"]["backend"] == "claude"
    assert data["driver"]["transport"] == "cli"
    assert "mode" not in data["driver"]
