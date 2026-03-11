# Config Migration Guide: v1 → v2

cli-bridge v2 moves all backend-specific settings into nested sub-objects under
`driver`. If you upgrade from v1 and your `~/.cli-bridge/config.json` still uses
the old flat layout, the gateway will refuse to start with:

```
ConfigMigrationError: Legacy config format detected (v1).
Found legacy fields: acp_port, iflow_path, yolo
Please migrate your config to v2 format.
Migration guide: ~/.cli-bridge/docs/config-migration-v2.md
```

---

## Mode: `stdio` / `cli` / `acp` (iflow backend)

### v1 (flat — no longer accepted)

```json
{
  "driver": {
    "mode": "stdio",
    "iflow_path": "iflow",
    "model": "minimax-m2.5",
    "yolo": true,
    "thinking": false,
    "extra_args": [],
    "compression_trigger_tokens": 60000,
    "acp_host": "localhost",
    "acp_port": 8090,
    "disable_mcp": false,
    "mcp_proxy_enabled": true,
    "mcp_proxy_port": 8888,
    "mcp_proxy_auto_start": true,
    "mcp_servers_auto_discover": true,
    "mcp_servers_max": 10,
    "mcp_servers_allowlist": [],
    "mcp_servers_blocklist": []
  }
}
```

### v2 (nested — required)

```json
{
  "driver": {
    "mode": "stdio",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "~/.cli-bridge/workspace",
    "iflow": {
      "iflow_path": "iflow",
      "model": "minimax-m2.5",
      "yolo": true,
      "thinking": false,
      "extra_args": [],
      "compression_trigger_tokens": 60000,
      "acp_host": "localhost",
      "acp_port": 8090,
      "disable_mcp": false,
      "mcp_proxy_enabled": true,
      "mcp_proxy_port": 8888,
      "mcp_proxy_auto_start": true,
      "mcp_servers_auto_discover": true,
      "mcp_servers_max": 10,
      "mcp_servers_allowlist": [],
      "mcp_servers_blocklist": []
    }
  }
}
```

**Changes**: all iflow-specific fields moved under `"iflow": { ... }`. The shared
fields `mode`, `max_turns`, `timeout`, `workspace` stay at the `driver` root.

---

## Mode: `claude` (Claude Code backend)

### v1 (flat — no longer accepted)

```json
{
  "driver": {
    "mode": "claude",
    "claude_path": "claude",
    "claude_model": "claude-opus-4-6"
  }
}
```

### v2 (nested — required)

```json
{
  "driver": {
    "mode": "claude",
    "max_turns": 40,
    "timeout": 180,
    "workspace": "~/.cli-bridge/workspace",
    "claude": {
      "claude_path": "claude",
      "model": "claude-opus-4-6",
      "system_prompt": "",
      "permission_mode": "bypassPermissions"
    }
  }
}
```

**Changes**: `claude_path` and `claude_model` (renamed to `model`) moved under
`"claude": { ... }`. No `iflow` block required.

---

## Manual Migration Steps

1. Open `~/.cli-bridge/config.json` in a text editor.
2. Identify the `driver.mode` value.
3. Move all backend-specific fields into the appropriate nested block:
   - iflow modes → `"driver": { "iflow": { <fields here> } }`
   - claude mode  → `"driver": { "claude": { <fields here> } }`
4. Remove the now-empty flat fields from the `driver` root.
5. Restart the gateway: `cli-bridge gateway run`

**Tip**: Delete `~/.cli-bridge/config.json` entirely and let the gateway
regenerate a fresh v2 default. Then re-apply your customizations.
