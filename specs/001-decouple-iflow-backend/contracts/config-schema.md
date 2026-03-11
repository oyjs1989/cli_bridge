# Contract: Config Schema v2

**Type**: Pydantic schema + JSON file format
**Location**: `cli_bridge/config/schema.py`
**Date**: 2026-03-11

---

## Breaking Change Notice

Config format v2 is **not backward compatible** with v1. The gateway refuses to start with a legacy config and emits a `ConfigMigrationError` with migration instructions.

---

## Schema Structure

```
Config
└── driver: DriverConfig
    ├── mode: "cli" | "stdio" | "acp" | "claude"   (discriminator)
    ├── max_turns: int                               (shared)
    ├── timeout: int                                 (shared)
    ├── workspace: str                               (shared)
    ├── iflow: IFlowBackendConfig | None            (populated when mode ∈ {cli,stdio,acp})
    └── claude: ClaudeBackendConfig | None          (populated when mode == "claude")
```

---

## Migration Detection Rules

The config loader (`cli_bridge/config/loader.py`) raises `ConfigMigrationError` when the raw `driver` dict contains **any** of these legacy root-level keys:

```
iflow_path, yolo, compression_trigger_tokens,
claude_path, claude_model, mcp_proxy_enabled,
acp_port, acp_host, extra_args, disable_mcp
```

**Error message format**:
```
ConfigMigrationError: Legacy config format detected (v1).
Found legacy fields: iflow_path, yolo, mcp_proxy_enabled
Please migrate your config to v2 format.
Migration guide: ~/.cli-bridge/docs/config-migration-v2.md
```

---

## Accessor Patterns

**Before (v1 — do not use)**:
```python
config.driver.iflow_path        # direct flat access
config.driver.claude_path       # direct flat access
config.driver.mcp_proxy_enabled # direct flat access
```

**After (v2)**:
```python
# iflow modes
config.driver.iflow.iflow_path
config.driver.iflow.mcp_proxy_enabled

# claude mode
config.driver.claude.claude_path
config.driver.claude.model

# shared
config.driver.timeout
config.driver.max_turns
config.driver.workspace
```

---

## Helper Methods on Config (retained)

| Method | Returns | Notes |
|--------|---------|-------|
| `get_workspace() -> str` | Absolute workspace path | Falls back to `~/.cli-bridge/workspace` |
| `get_timeout() -> int` | Timeout in seconds | Falls back to `DEFAULT_TIMEOUT` (180) |
| `get_model() -> str` | Active backend model name | Reads from `driver.iflow.model` or `driver.claude.model` based on mode |
| `get_enabled_channels() -> list[str]` | Channel names | Unchanged |
