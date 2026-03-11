"""Tests for MCPProxyConfig schema and Config.mcp_proxy field."""

import json

from cli_bridge.config.schema import Config, MCPProxyConfig

# ── MCPProxyConfig defaults ───────────────────────────────────────────────────


def test_mcp_proxy_config_defaults():
    """MCPProxyConfig has sensible opt-in defaults."""
    cfg = MCPProxyConfig()
    assert cfg.enabled is False
    assert cfg.port == 8888
    assert cfg.auto_start is True
    assert cfg.servers_auto_discover is True
    assert cfg.servers_max == 10
    assert cfg.servers_allowlist == []
    assert cfg.servers_blocklist == []


def test_mcp_proxy_config_extra_fields_ignored():
    """MCPProxyConfig ignores extra fields (backward compat)."""
    cfg = MCPProxyConfig(enabled=True, unknown_future_field="noop")
    assert cfg.enabled is True
    assert not hasattr(cfg, "unknown_future_field")


def test_mcp_proxy_config_allowlist_and_blocklist():
    """Allowlist and blocklist can be set."""
    cfg = MCPProxyConfig(
        enabled=True,
        servers_allowlist=["filesystem", "memory"],
        servers_blocklist=["dangerous"],
    )
    assert "filesystem" in cfg.servers_allowlist
    assert "dangerous" in cfg.servers_blocklist


# ── Config.mcp_proxy field ────────────────────────────────────────────────────


def test_config_has_mcp_proxy_field():
    """Config class has mcp_proxy field with MCPProxyConfig default."""
    cfg = Config()
    assert hasattr(cfg, "mcp_proxy")
    assert isinstance(cfg.mcp_proxy, MCPProxyConfig)


def test_config_mcp_proxy_disabled_by_default():
    """Config.mcp_proxy.enabled is False by default."""
    cfg = Config()
    assert cfg.mcp_proxy.enabled is False


def test_config_mcp_proxy_loads_from_json(tmp_path):
    """Config.mcp_proxy can be set from config JSON."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"mcp_proxy": {"enabled": True, "port": 9000}}),
        encoding="utf-8",
    )
    from cli_bridge.config.loader import load_config

    cfg = load_config(config_path=config_file)
    assert cfg.mcp_proxy.enabled is True
    assert cfg.mcp_proxy.port == 9000


# ── Backward compat: iflow-specific MCP fields still work ────────────────────


def test_iflow_mcp_proxy_fields_still_load(tmp_path):
    """Existing driver.iflow.mcp_proxy_* fields still load correctly."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "driver": {
                    "backend": "iflow",
                    "iflow": {
                        "mcp_proxy_enabled": True,
                        "mcp_proxy_port": 8888,
                        "mcp_proxy_auto_start": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    from cli_bridge.config.loader import load_config

    cfg = load_config(config_path=config_file)
    assert cfg.driver.iflow is not None
    assert cfg.driver.iflow.mcp_proxy_enabled is True
    assert cfg.driver.iflow.mcp_proxy_port == 8888
    assert cfg.driver.iflow.mcp_proxy_auto_start is False


def test_both_mcp_configs_can_coexist(tmp_path):
    """Both mcp_proxy (unified) and driver.iflow.mcp_proxy_enabled can exist simultaneously."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "mcp_proxy": {"enabled": True},
                "driver": {
                    "backend": "iflow",
                    "iflow": {"mcp_proxy_enabled": True},
                },
            }
        ),
        encoding="utf-8",
    )
    from cli_bridge.config.loader import load_config

    # Should load without error; conflict warning is emitted at gateway startup
    cfg = load_config(config_path=config_file)
    assert cfg.mcp_proxy.enabled is True
    assert cfg.driver.iflow.mcp_proxy_enabled is True


def test_mcp_proxy_only_config_loads(tmp_path):
    """Config with only mcp_proxy section (no iflow block) loads correctly."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "mcp_proxy": {"enabled": True},
                "driver": {"backend": "claude"},
            }
        ),
        encoding="utf-8",
    )
    from cli_bridge.config.loader import load_config

    cfg = load_config(config_path=config_file)
    assert cfg.mcp_proxy.enabled is True
    assert cfg.driver.backend == "claude"
    assert cfg.driver.iflow is None
