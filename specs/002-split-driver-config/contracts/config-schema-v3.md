# Config Schema Contract: Driver v3

**Branch**: `002-split-driver-config` | **Date**: 2026-03-11
**File**: `~/.cli-bridge/config.json` → `driver` block

This document defines the contract for the `driver` section of the cli-bridge config file after this feature lands.

---

## Accepted Formats

Both formats are accepted. New fields take precedence over legacy `mode`.

### Format A — New (v3, preferred for new installs)

```json
{
  "driver": {
    "backend": "<iflow|claude>",
    "transport": "<cli|stdio|acp>",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "/path/to/workspace",
    "iflow": { ... },
    "claude": { ... }
  }
}
```

### Format B — Legacy (v2, accepted via migration)

```json
{
  "driver": {
    "mode": "<cli|stdio|acp|claude>",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "/path/to/workspace",
    "iflow": { ... },
    "claude": { ... }
  }
}
```

---

## Field Definitions

### `driver.backend`

- **Type**: string enum
- **Values**: `"iflow"` | `"claude"`
- **Default**: `"iflow"`
- **Description**: Which AI tool processes messages. Determines which backend-specific config block is active.

### `driver.transport`

- **Type**: string enum
- **Values**: `"cli"` | `"stdio"` | `"acp"`
- **Default**: `"stdio"`
- **Description**: How the gateway communicates with the backend.
  - `cli` — spawns a new subprocess for each message
  - `stdio` — maintains a single long-running process; messages sent over stdin/stdout
  - `acp` — connects to an external WebSocket server running the backend

### `driver.mode` (legacy, deprecated)

- **Type**: string enum
- **Values**: `"cli"` | `"stdio"` | `"acp"` | `"claude"`
- **Default**: not applicable (legacy field)
- **Description**: Combined backend+transport selector from v2. Migrated automatically to `backend`+`transport` on load. Will be removed in a future version. New configs should use `backend` + `transport`.

---

## Valid Combinations

| `backend` | `transport` | Status | Notes |
|-----------|-------------|--------|-------|
| `iflow` | `cli` | ✓ Supported | Equivalent to v2 `mode: "cli"` |
| `iflow` | `stdio` | ✓ Supported | Equivalent to v2 `mode: "stdio"` (current default) |
| `iflow` | `acp` | ✓ Supported | Equivalent to v2 `mode: "acp"` |
| `claude` | `cli` | ✓ Supported | Equivalent to v2 `mode: "claude"` |
| `claude` | `stdio` | ✓ Supported | **New in v3** |
| `claude` | `acp` | ✗ Not supported | Returns validation error at startup |

---

## Example Configs

### iflow + stdio (default for new installs)

```json
{
  "driver": {
    "backend": "iflow",
    "transport": "stdio",
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

### claude + cli (equivalent to v2 `mode: "claude"`)

```json
{
  "driver": {
    "backend": "claude",
    "transport": "cli",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "",
    "claude": {
      "claude_path": "claude",
      "model": "claude-opus-4-6",
      "system_prompt": "",
      "permission_mode": "bypassPermissions"
    }
  }
}
```

### claude + stdio (new in v3)

```json
{
  "driver": {
    "backend": "claude",
    "transport": "stdio",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "",
    "claude": {
      "claude_path": "claude",
      "model": "claude-opus-4-6",
      "system_prompt": "",
      "permission_mode": "bypassPermissions"
    }
  }
}
```

---

## Validation Errors

| Condition | Error |
|-----------|-------|
| `backend="claude"` and `transport="acp"` | `"claude + acp is not yet supported"` |
| Unknown `backend` value | Pydantic `ValidationError`: literal constraint failed |
| Unknown `transport` value | Pydantic `ValidationError`: literal constraint failed |
| Legacy v1 flat fields (e.g., `iflow_path` at driver level) | `ConfigMigrationError` — upgrade required |

---

## Backward Compatibility Guarantee

All configs that worked in v2 (`mode: "cli"`, `mode: "stdio"`, `mode: "acp"`, `mode: "claude"`) continue to work in v3 without modification. No action required from existing operators.
