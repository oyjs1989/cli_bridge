# Quickstart: Split Driver Config

**Branch**: `002-split-driver-config` | **Date**: 2026-03-11

## For Existing Users — Nothing to Do

Your existing `~/.cli-bridge/config.json` with `driver.mode` continues to work without changes. The gateway migrates it automatically at startup.

## For New Configurations

Use `backend` + `transport` instead of `mode`:

```json
{
  "driver": {
    "backend": "iflow",
    "transport": "stdio"
  }
}
```

## Migration Reference

| Old config | New equivalent |
|------------|----------------|
| `"mode": "cli"` | `"backend": "iflow", "transport": "cli"` |
| `"mode": "stdio"` | `"backend": "iflow", "transport": "stdio"` |
| `"mode": "acp"` | `"backend": "iflow", "transport": "acp"` |
| `"mode": "claude"` | `"backend": "claude", "transport": "cli"` |

## New: Claude + Stdio

To run Claude Code as a persistent long-lived process:

```json
{
  "driver": {
    "backend": "claude",
    "transport": "stdio",
    "claude": {
      "claude_path": "claude",
      "model": "claude-opus-4-6",
      "permission_mode": "bypassPermissions"
    }
  }
}
```

## Reading Config Fields in Code

```python
config = load_config()

# New way (preferred)
backend = config.driver.backend      # "iflow" or "claude"
transport = config.driver.transport  # "cli", "stdio", or "acp"

# Old way (still works via property)
mode = config.driver.mode            # returns derived legacy string
```

## Status Command

After upgrading, `cli-bridge status` shows:

```
配置信息:
  Backend:    iflow
  Transport:  stdio
  Model:      minimax-m2.5
```
