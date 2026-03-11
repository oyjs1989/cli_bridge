# Data Model: Split Driver Config — Backend + Transport

**Branch**: `002-split-driver-config` | **Date**: 2026-03-11

## Entity: DriverConfig (updated)

The central configuration object that determines which AI backend runs and how the gateway communicates with it.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | `"iflow" \| "claude"` | `"iflow"` | Which AI tool processes messages |
| `transport` | `"cli" \| "stdio" \| "acp"` | `"stdio"` | How the gateway communicates with the backend |
| `max_turns` | `int` | `40` | Maximum conversation turns (all backends) |
| `timeout` | `int` | `180` | Request timeout in seconds (all backends) |
| `workspace` | `str` | `""` | Working directory path (empty = `~/.cli-bridge/workspace`) |
| `iflow` | `IFlowBackendConfig \| None` | `None` | iflow-specific settings; auto-populated when `backend="iflow"` |
| `claude` | `ClaudeBackendConfig \| None` | `None` | Claude-specific settings; auto-populated when `backend="claude"` |

### Derived Property: `mode` (backward compat)

```
(backend="iflow",  transport="cli")   → mode = "cli"
(backend="iflow",  transport="stdio") → mode = "stdio"
(backend="iflow",  transport="acp")   → mode = "acp"
(backend="claude", transport="cli")   → mode = "claude"
(backend="claude", transport="stdio") → mode = "claude"
```

### Validation Rules

1. **Legacy migration** (before validation): If input dict has `mode` key but no `backend`/`transport` keys, convert:
   - `mode="cli"` → `backend="iflow"`, `transport="cli"`
   - `mode="stdio"` → `backend="iflow"`, `transport="stdio"`
   - `mode="acp"` → `backend="iflow"`, `transport="acp"`
   - `mode="claude"` → `backend="claude"`, `transport="cli"`

2. **Backend config auto-population** (after validation):
   - `backend="iflow"` and `iflow is None` → set `iflow = IFlowBackendConfig()`
   - `backend="claude"` and `claude is None` → set `claude = ClaudeBackendConfig()`

3. **Unsupported combination** (after validation):
   - `backend="claude"` and `transport="acp"` → raise `ValueError: "claude + acp not yet supported"`

### Valid Combination Matrix

| | `transport=cli` | `transport=stdio` | `transport=acp` |
|--|--|--|--|
| `backend=iflow` | ✓ existing | ✓ existing | ✓ existing |
| `backend=claude` | ✓ existing (was "claude" mode) | ✓ **new** | ✗ not yet supported |

---

## Entity: IFlowAdapter (updated)

The adapter for communicating with iflow. Transport is now called `transport` (was `mode`).

### Fields (internal)

| Field | Type | Description |
|-------|------|-------------|
| `_transport` | `"cli" \| "stdio" \| "acp"` | Communication strategy with iflow (renamed from `_mode`) |

### Properties

| Property | Returns | Description |
|----------|---------|-------------|
| `transport` | `str` | Active transport: `"cli"`, `"stdio"`, or `"acp"` |
| `mode` | `str` | Deprecated alias → returns `self.transport` |
| `inline_agents` | `bool` | `True` only when `transport="cli"` (AGENTS.md injected per-message) |

---

## Entity: ClaudeAdapter (updated)

The adapter for the claude CLI backend using `claude-agent-sdk`. Currently always uses subprocess-per-message (equivalent to `transport="cli"`).

### Properties (added)

| Property | Returns | Description |
|----------|---------|-------------|
| `transport` | `"cli"` | Always returns `"cli"` — this adapter is the cli-transport variant |
| `inline_agents` | `bool` | Always `False` — Claude sessions have their own context |

---

## Entity: ClaudeStdioAdapter (new)

A new adapter for the `claude+stdio` combination. Manages a single persistent Claude process, reusing it across messages.

### Fields (internal)

| Field | Type | Description |
|-------|------|-------------|
| `_process` | `asyncio.Process \| None` | The long-running claude subprocess |
| `session_mappings` | `SessionMappingManager` | Maps channel:chat_id to claude session IDs |

### Properties

| Property | Returns | Description |
|----------|---------|-------------|
| `transport` | `"stdio"` | Always `"stdio"` |
| `inline_agents` | `bool` | `False` — context injected via session |

### State Transitions

```
Not started → connect() → Running (process alive)
Running → chat() → processes message, returns response
Running → stop() → Stopped (process terminated)
Running → process dies → reconnect on next chat()
```

---

## Entity: BaseAdapter (updated)

Abstract base class for all adapters. Gains one new property.

### New Property

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `inline_agents` | `bool` | `True` | Whether AGENTS.md content should be injected into each message inline. Overridden to `False` by long-lived adapters (StdioACPAdapter, ACPAdapter, ClaudeAdapter, ClaudeStdioAdapter). |

---

## Config File Format: v3

### New format (with `backend` + `transport`)

```json
{
  "driver": {
    "backend": "iflow",
    "transport": "stdio",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "~/.cli-bridge/workspace",
    "iflow": {
      "iflow_path": "iflow",
      "model": "minimax-m2.5"
    }
  }
}
```

### Legacy format (v2, still accepted via in-memory migration)

```json
{
  "driver": {
    "mode": "stdio",
    "iflow": { ... }
  }
}
```

### Migration mapping (in-memory only, no disk rewrite)

| Legacy `mode` | Derived `backend` | Derived `transport` |
|---------------|-------------------|---------------------|
| `"cli"` | `"iflow"` | `"cli"` |
| `"stdio"` | `"iflow"` | `"stdio"` |
| `"acp"` | `"iflow"` | `"acp"` |
| `"claude"` | `"claude"` | `"cli"` |
