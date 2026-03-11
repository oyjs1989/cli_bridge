"""Tests for config migration detection and v2 schema loading."""

import json

import pytest
from pydantic import ValidationError

from cli_bridge.config.loader import ConfigMigrationError, load_config
from cli_bridge.config.schema import Config, DriverConfig

# ── ConfigMigrationError detection ───────────────────────────────────────────

def test_v1_flat_iflow_path_raises_migration_error(tmp_path):
    """v1 config with flat iflow_path in driver raises ConfigMigrationError."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "stdio",
            "iflow_path": "/usr/local/bin/iflow",
        }
    }), encoding="utf-8")

    with pytest.raises(ConfigMigrationError) as exc_info:
        load_config(cfg_file, auto_create=False)

    assert "Legacy config format detected" in str(exc_info.value)
    assert "iflow_path" in str(exc_info.value)


def test_v1_flat_claude_model_raises_migration_error(tmp_path):
    """v1 config with flat claude_model in driver raises ConfigMigrationError."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "claude",
            "claude_model": "claude-opus-4-6",
            "claude_path": "claude",
        }
    }), encoding="utf-8")

    with pytest.raises(ConfigMigrationError) as exc_info:
        load_config(cfg_file, auto_create=False)

    assert "claude_model" in str(exc_info.value)


def test_v1_multiple_legacy_fields_listed_in_error(tmp_path):
    """Error message lists all detected legacy fields."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "stdio",
            "iflow_path": "iflow",
            "yolo": True,
            "acp_port": 8090,
        }
    }), encoding="utf-8")

    with pytest.raises(ConfigMigrationError) as exc_info:
        load_config(cfg_file, auto_create=False)

    msg = str(exc_info.value)
    assert "iflow_path" in msg
    assert "yolo" in msg
    assert "acp_port" in msg


# ── v2 claude-mode config loading ────────────────────────────────────────────

def test_v2_claude_mode_config_loads_without_iflow(tmp_path):
    """v2 claude-mode config loads successfully; driver.iflow is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "claude",
            "claude": {
                "claude_path": "claude",
                "model": "claude-opus-4-6",
                "permission_mode": "bypassPermissions",
            }
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.mode == "claude"
    assert config.driver.claude is not None
    assert config.driver.claude.model == "claude-opus-4-6"
    assert config.driver.iflow is None


def test_v2_claude_mode_auto_populates_defaults(tmp_path):
    """When claude block absent in claude mode, defaults are auto-populated."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {"mode": "claude"}
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.mode == "claude"
    assert config.driver.claude is not None
    assert config.driver.claude.claude_path == "claude"
    assert config.driver.iflow is None


def test_v2_iflow_mode_config_loads_without_claude(tmp_path):
    """v2 iflow stdio-mode config loads; driver.claude is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "stdio",
            "iflow": {
                "iflow_path": "iflow",
                "model": "minimax-m2.5",
            }
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.mode == "stdio"
    assert config.driver.iflow is not None
    assert config.driver.iflow.model == "minimax-m2.5"
    assert config.driver.claude is None


# ── get_model() dispatch ─────────────────────────────────────────────────────

def test_get_model_returns_iflow_model_in_stdio_mode():
    config = Config(driver=DriverConfig(mode="stdio"))
    assert config.get_model() == "minimax-m2.5"


def test_get_model_returns_claude_model_in_claude_mode():
    config = Config(driver=DriverConfig(mode="claude"))
    assert config.get_model() == "claude-opus-4-6"


# ── per-backend separation ────────────────────────────────────────────────────

def test_iflow_only_v2_config_has_no_claude_block(tmp_path):
    """iflow-only v2 config loads with driver.claude is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "cli",
            "iflow": {"iflow_path": "iflow", "model": "minimax-m2.5"}
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.mode == "cli"
    assert config.driver.iflow is not None
    assert config.driver.claude is None


def test_claude_only_v2_config_has_no_iflow_block(tmp_path):
    """claude-only v2 config loads with driver.iflow is None."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "driver": {
            "mode": "claude",
            "claude": {"claude_path": "claude", "model": "claude-opus-4-6"}
        }
    }), encoding="utf-8")

    config = load_config(cfg_file, auto_create=False)

    assert config.driver.mode == "claude"
    assert config.driver.claude is not None
    assert config.driver.iflow is None


def test_invalid_mode_raises_validation_error():
    """An invalid mode value raises Pydantic ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        DriverConfig(mode="unknown_backend")

    assert "mode" in str(exc_info.value).lower() or "literal" in str(exc_info.value).lower()


def test_acp_mode_populates_iflow_block():
    """acp mode auto-populates driver.iflow with defaults."""
    config = Config(driver=DriverConfig(mode="acp"))
    assert config.driver.iflow is not None
    assert config.driver.iflow.acp_port == 8090
    assert config.driver.claude is None


# ── _create_default_config mode branching ────────────────────────────────────

def test_default_config_stdio_mode_writes_iflow_block(tmp_path):
    """_create_default_config writes iflow block for stdio mode."""
    from cli_bridge.config.loader import _create_default_config

    cfg_file = tmp_path / "config.json"
    _create_default_config(cfg_file, mode="stdio")

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "iflow" in data["driver"]
    assert "claude" not in data["driver"]
    assert data["driver"]["mode"] == "stdio"


def test_default_config_claude_mode_writes_claude_block(tmp_path):
    """_create_default_config writes claude block (no iflow) for claude mode."""
    from cli_bridge.config.loader import _create_default_config

    cfg_file = tmp_path / "config.json"
    _create_default_config(cfg_file, mode="claude")

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "claude" in data["driver"]
    assert "iflow" not in data["driver"]
    assert data["driver"]["mode"] == "claude"
