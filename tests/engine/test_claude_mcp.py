"""Tests for ClaudeAdapter._build_mcp_servers() MCP integration."""

import json
from pathlib import Path
from unittest.mock import patch

from cli_bridge.config.schema import MCPProxyConfig
from cli_bridge.engine.claude_adapter import ClaudeAdapter

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_adapter(tmp_path: Path, mcp_proxy_config: MCPProxyConfig | None = None) -> ClaudeAdapter:
    """Create a ClaudeAdapter with optional mcp_proxy_config."""
    return ClaudeAdapter(workspace=tmp_path, mcp_proxy_config=mcp_proxy_config)


def _make_mcp_json(tmp_path: Path, servers: dict) -> Path:
    """Write a .mcp_proxy_config.json file and return its path."""
    p = tmp_path / ".mcp_proxy_config.json"
    p.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return p


# ── _build_mcp_servers() when disabled ───────────────────────────────────────


def test_build_mcp_servers_returns_none_when_no_config(tmp_path):
    """_build_mcp_servers() returns None when mcp_proxy_config is not passed."""
    adapter = _make_adapter(tmp_path, mcp_proxy_config=None)
    assert adapter._build_mcp_servers() is None


def test_build_mcp_servers_returns_none_when_disabled(tmp_path):
    """_build_mcp_servers() returns None when enabled=False."""
    cfg = MCPProxyConfig(enabled=False)
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    assert adapter._build_mcp_servers() is None


def test_build_mcp_servers_returns_none_when_config_file_missing(tmp_path):
    """_build_mcp_servers() returns None when .mcp_proxy_config.json is absent."""
    cfg = MCPProxyConfig(enabled=True)
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=None):
        result = adapter._build_mcp_servers()
    assert result is None


# ── _build_mcp_servers() filtering ───────────────────────────────────────────


def test_build_mcp_servers_filters_disabled_entries(tmp_path):
    """_build_mcp_servers() skips entries with disabled=True."""
    cfg = MCPProxyConfig(enabled=True)
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {
            "active": {"command": "npx", "args": ["-y", "active-server"], "type": "stdio"},
            "inactive": {"command": "npx", "args": ["-y", "inactive-server"], "type": "stdio", "disabled": True},
        },
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        result = adapter._build_mcp_servers()
    assert result is not None
    assert "active" in result
    assert "inactive" not in result


def test_build_mcp_servers_filters_non_stdio_entries(tmp_path):
    """_build_mcp_servers() skips entries with type != stdio."""
    cfg = MCPProxyConfig(enabled=True)
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {
            "stdio_server": {"command": "npx", "args": [], "type": "stdio"},
            "http_server": {"command": "npx", "args": [], "type": "http", "url": "http://localhost:8080"},
        },
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        result = adapter._build_mcp_servers()
    assert result is not None
    assert "stdio_server" in result
    assert "http_server" not in result


def test_build_mcp_servers_applies_blocklist(tmp_path):
    """_build_mcp_servers() skips entries in the blocklist."""
    cfg = MCPProxyConfig(enabled=True, servers_blocklist=["dangerous"])
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {
            "safe": {"command": "npx", "args": [], "type": "stdio"},
            "dangerous": {"command": "npx", "args": [], "type": "stdio"},
        },
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        result = adapter._build_mcp_servers()
    assert result is not None
    assert "safe" in result
    assert "dangerous" not in result


def test_build_mcp_servers_applies_allowlist(tmp_path):
    """_build_mcp_servers() only includes entries in the allowlist."""
    cfg = MCPProxyConfig(enabled=True, servers_allowlist=["filesystem"])
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {
            "filesystem": {"command": "npx", "args": ["-y", "@mcp/filesystem"], "type": "stdio"},
            "memory": {"command": "npx", "args": ["-y", "@mcp/memory"], "type": "stdio"},
        },
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        result = adapter._build_mcp_servers()
    assert result is not None
    assert "filesystem" in result
    assert "memory" not in result


def test_build_mcp_servers_respects_max_limit(tmp_path):
    """_build_mcp_servers() stops after servers_max entries."""
    cfg = MCPProxyConfig(enabled=True, servers_max=2)
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {
            "s1": {"command": "npx", "args": [], "type": "stdio"},
            "s2": {"command": "npx", "args": [], "type": "stdio"},
            "s3": {"command": "npx", "args": [], "type": "stdio"},
        },
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        result = adapter._build_mcp_servers()
    assert result is not None
    assert len(result) == 2


def test_build_mcp_servers_returns_none_for_empty_servers(tmp_path):
    """_build_mcp_servers() returns None when all servers are filtered out."""
    cfg = MCPProxyConfig(enabled=True, servers_blocklist=["s1"])
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {"s1": {"command": "npx", "args": [], "type": "stdio"}},
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        result = adapter._build_mcp_servers()
    assert result is None


# ── _build_options() integration ─────────────────────────────────────────────


def test_build_options_includes_mcp_servers_when_enabled(tmp_path):
    """_build_options() passes mcp_servers to ClaudeAgentOptions when enabled."""
    cfg = MCPProxyConfig(enabled=True)
    adapter = _make_adapter(tmp_path, mcp_proxy_config=cfg)
    config_path = _make_mcp_json(
        tmp_path,
        {"filesystem": {"command": "npx", "args": ["-y", "@mcp/fs"], "type": "stdio"}},
    )
    with patch("cli_bridge.engine.claude_adapter._resolve_mcp_proxy_config_file", return_value=config_path):
        options = adapter._build_options("telegram", "123")
    assert options.mcp_servers is not None
    assert "filesystem" in options.mcp_servers


def test_build_options_empty_mcp_servers_when_disabled(tmp_path):
    """_build_options() passes empty mcp_servers dict when MCP is disabled."""
    adapter = _make_adapter(tmp_path, mcp_proxy_config=None)
    options = adapter._build_options("telegram", "123")
    # mcp_servers should be {} or unset (not None to avoid SDK issues)
    assert not options.mcp_servers
