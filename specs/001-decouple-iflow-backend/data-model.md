# Data Model: Decouple iflow Backend

**Branch**: `001-decouple-iflow-backend` | **Date**: 2026-03-11

---

## Config Schema (New Structure)

### DriverConfig (Revised)

Top-level backend driver config. Shared fields only; backend-specific fields live in nested objects.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `mode` | `"cli" \| "stdio" \| "acp" \| "claude"` | `"stdio"` | Selects the active backend |
| `max_turns` | `int` | `40` | Max conversation turns (shared) |
| `timeout` | `int` | `180` | Request timeout in seconds (shared) |
| `workspace` | `str` | `""` | Working directory; empty = `~/.cli-bridge/workspace` (shared) |
| `iflow` | `IFlowBackendConfig \| None` | auto-populated | Required & auto-defaulted when mode ∈ {cli, stdio, acp} |
| `claude` | `ClaudeBackendConfig \| None` | auto-populated | Required & auto-defaulted when mode == "claude" |

**Validation rule**: `model_validator(mode="after")` auto-populates the relevant nested config with defaults if not provided. If mode is iflow but `iflow` is None, it becomes `IFlowBackendConfig()`. If mode is claude but `claude` is None, it becomes `ClaudeBackendConfig()`.

---

### IFlowBackendConfig (New)

iflow-specific settings, nested under `driver.iflow`.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `iflow_path` | `str` | `"iflow"` | Path to iflow binary |
| `model` | `str` | `"minimax-m2.5"` | Default model name |
| `yolo` | `bool` | `True` | Auto-approve mode |
| `thinking` | `bool` | `False` | Extended thinking mode |
| `extra_args` | `list[str]` | `[]` | Additional CLI args |
| `compression_trigger_tokens` | `int` | `60000` | Token threshold for session compression |
| `acp_host` | `str` | `"localhost"` | ACP server hostname (acp mode only) |
| `acp_port` | `int` | `8090` | ACP server port (acp mode only) |
| `disable_mcp` | `bool` | `False` | Disable MCP servers |
| `mcp_proxy_enabled` | `bool` | `True` | Enable MCP proxy aggregator |
| `mcp_proxy_port` | `int` | `8888` | MCP proxy server port |
| `mcp_proxy_auto_start` | `bool` | `True` | Auto-start proxy on gateway startup |
| `mcp_servers_auto_discover` | `bool` | `True` | Auto-discover enabled servers from proxy |
| `mcp_servers_max` | `int` | `10` | Max MCP servers per iflow instance |
| `mcp_servers_allowlist` | `list[str]` | `[]` | Allowed MCP server names (empty = all) |
| `mcp_servers_blocklist` | `list[str]` | `[]` | Blocked MCP server names |

---

### ClaudeBackendConfig (New)

Claude Code-specific settings, nested under `driver.claude`.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `claude_path` | `str` | `"claude"` | Path to claude binary |
| `model` | `str` | `"claude-opus-4-6"` | Model ID passed to `--model` |
| `system_prompt` | `str` | `""` | Static system prompt to append |
| `permission_mode` | `"default" \| "acceptEdits" \| "bypassPermissions"` | `"bypassPermissions"` | Tool execution permission mode |

---

## Config File Format Migration

### Legacy Format (v1 — old, triggers ConfigMigrationError)

```json
{
  "driver": {
    "mode": "stdio",
    "iflow_path": "iflow",
    "model": "minimax-m2.5",
    "yolo": true,
    "thinking": false,
    "max_turns": 40,
    "timeout": 180,
    "compression_trigger_tokens": 60000,
    "acp_port": 8090,
    "mcp_proxy_enabled": true,
    "mcp_proxy_port": 8888,
    "claude_path": "claude",
    "claude_model": "claude-opus-4-6",
    "claude_permission_mode": "bypassPermissions"
  }
}
```

### New Format — iflow mode

```json
{
  "driver": {
    "mode": "stdio",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "",
    "iflow": {
      "iflow_path": "iflow",
      "model": "minimax-m2.5",
      "yolo": true,
      "thinking": false,
      "compression_trigger_tokens": 60000,
      "mcp_proxy_enabled": true,
      "mcp_proxy_port": 8888
    }
  }
}
```

### New Format — claude mode

```json
{
  "driver": {
    "mode": "claude",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "",
    "claude": {
      "claude_path": "claude",
      "model": "claude-opus-4-6",
      "permission_mode": "bypassPermissions",
      "system_prompt": ""
    }
  }
}
```

---

## Backend Adapter Hierarchy

```
BaseAdapter (ABC)
├── IFlowAdapter           — delegates to mode-specific inner adapters
│   ├── [cli mode]         — spawns iflow subprocess per message
│   ├── StdioACPAdapter    — long-running iflow process via JSON-RPC
│   └── ACPAdapter         — WebSocket connection to iflow ACP server
└── ClaudeAdapter          — claude-agent-sdk subprocess
```

**Shared state across all adapters**:
- `session_mappings: SessionMappingManager` — persisted at `~/.cli-bridge/session_mappings.json`
- `workspace: Path` — resolved absolute path
- `mode: str` — string identifier for the active mode

---

## Config Migration Error

### ConfigMigrationError

Raised by the config loader when a legacy flat format is detected.

| Attribute | Value |
|-----------|-------|
| Type | `ConfigMigrationError(ValueError)` |
| Trigger | `driver` dict contains any of: `iflow_path`, `yolo`, `compression_trigger_tokens`, `claude_path`, `claude_model`, `mcp_proxy_enabled` at the root level |
| Message | Lists the detected legacy fields and references the migration guide path |
| Exit code | Non-zero (gateway refuses to start) |

---

## Workspace Init State Transitions

```
init_workspace(mode, workspace_path)
      │
      ├── [always] create workspace dir
      ├── [always] copy markdown templates (AGENTS.md, BOOTSTRAP.md, etc.)
      ├── [always] create memory/ and channel/ subdirs
      │
      └── [mode ∈ {cli, stdio, acp}] create .iflow/settings.json
          [mode == "claude"] SKIP .iflow/settings.json creation
```
