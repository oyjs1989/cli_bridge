# cli-bridge

[![PyPI version](https://img.shields.io/pypi/v/cli-bridge.svg)](https://pypi.org/project/cli-bridge/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A self-hosted multi-channel AI gateway that bridges chat platforms to Claude or iflow.

---

## Features

- **9 chat platform integrations** — Telegram, Discord, Slack, Feishu, DingTalk, QQ, WhatsApp, Email, Mochat
- **2 AI backends** — Claude CLI (via `claude-agent-sdk`) and iflow CLI
- **Streaming responses** — real-time buffered streaming with edit-in-place for supported channels
- **MCP server proxy** — aggregate multiple MCP servers and share them across sessions
- **Cron job scheduler** — schedule periodic AI prompts or tasks
- **Web console** — built-in web UI for monitoring and management (port 8787)
- **Session persistence** — per-user session mapping survives gateway restarts
- **Workspace context** — inject persistent instructions into every message via `AGENTS.md`

---

## Prerequisites

- Python 3.10 or later
- [uv](https://github.com/astral-sh/uv) package manager
- **Claude backend:** [Claude CLI](https://github.com/anthropics/claude-code) installed and authenticated
- **iflow backend:** iflow CLI installed
- Bot tokens / API credentials for the chat platforms you want to use

---

## Installation

```bash
git clone https://github.com/oyjs1989/cli_bridge.git
cd cli_bridge
uv sync
```

---

## Quick Start

```bash
# 1. Initialize config and workspace
uv run cli-bridge onboard

# 2. Edit the config file and add your tokens
#    (see Configuration section below)
nano ~/.cli-bridge/config.json

# 3. Run in foreground (development / debug)
uv run cli-bridge gateway run

# 4. Or run in background (production)
uv run cli-bridge gateway start

# 5. Check status
uv run cli-bridge status
```

---

## Configuration

The config file lives at `~/.cli-bridge/config.json`. Run `cli-bridge onboard` to
create it with defaults, then edit it to suit your setup.

### Driver (AI backend)

**Claude backend (recommended):**

```json
{
  "driver": {
    "backend": "claude",
    "transport": "cli",
    "claude": {
      "model": "claude-opus-4-6",
      "permission_mode": "bypassPermissions"
    }
  }
}
```

**iflow backend:**

```json
{
  "driver": {
    "backend": "iflow",
    "transport": "stdio"
  }
}
```

`transport` options for iflow: `cli` (subprocess per message), `stdio` (long-running process), `acp` (WebSocket server).

### Channel Examples

Enable channels by adding them inside the top-level `"channels"` object in your config.

**Telegram:**

```json
"telegram": {
  "enabled": true,
  "token": "YOUR_BOT_TOKEN"
}
```

**Discord:**

```json
"discord": {
  "enabled": true,
  "token": "YOUR_BOT_TOKEN",
  "allow_from": ["YOUR_USER_ID"]
}
```

**Slack:**

```json
"slack": {
  "enabled": true,
  "bot_token": "xoxb-...",
  "app_token": "xapp-..."
}
```

**Feishu (Lark):**

```json
"feishu": {
  "enabled": true,
  "app_id": "cli_xxx",
  "app_secret": "YOUR_APP_SECRET",
  "encrypt_key": "YOUR_ENCRYPT_KEY",
  "verification_token": "YOUR_VERIFICATION_TOKEN"
}
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `gateway start` | Start gateway in background |
| `gateway run` | Start gateway in foreground (debug) |
| `gateway stop` | Stop the background gateway |
| `gateway restart` | Restart the background gateway |
| `status` | Show gateway and channel status |
| `model <name>` | Switch the AI model |
| `thinking on\|off` | Toggle extended thinking mode |
| `sessions` | Manage session mappings |
| `cron list\|add\|remove` | Manage scheduled tasks |
| `console` | Start the web UI (port 8787) |
| `mcp-sync` | Sync MCP servers from iflow |
| `onboard` | Initialize config and workspace |

Run `cli-bridge --help` or `cli-bridge <command> --help` for full option details.

---

## Workspace / Context Files

Place Markdown files in `~/.cli-bridge/workspace/` to inject context into the AI:

| File | Behavior |
|------|----------|
| `AGENTS.md` | Injected into **every** message as persistent system context |
| `BOOTSTRAP.md` | Injected once on **first run**, then cleared |
| `HEARTBEAT.md` | Template used for periodic heartbeat prompts (if cron is configured) |

Example `AGENTS.md`:

```markdown
You are a helpful assistant. Always reply in the same language the user writes in.
Keep responses concise unless asked for detail.
```

---

## MCP Server Support

cli-bridge includes an MCP proxy that aggregates multiple MCP servers and exposes them
to the AI backend in a single session.

Configure MCP servers in `~/.cli-bridge/config/.mcp_proxy_config.json`, then run:

```bash
uv run cli-bridge mcp-sync
```

This syncs the MCP server list from your iflow installation into the proxy config.

---

## Troubleshooting

**Gateway won't start**

Run `cli-bridge status` to see error details. Check logs in `~/.cli-bridge/` for stack
traces. Make sure the required CLI tool (`claude` or `iflow`) is on your `PATH`.

**Bot not responding to messages**

Verify the bot token is correct and the bot is active in the platform. For Discord and
some other channels, check the `allow_from` list — messages from unlisted user IDs are
silently ignored.

**Session lost after restart**

Session mappings are stored in `~/.cli-bridge/session_mappings.json`. If you need to
force a clean slate:

```bash
uv run cli-bridge sessions --clear
```

**Slow responses with iflow backend**

Switch to the `stdio` transport to keep iflow running between messages instead of
spawning a new process per message:

```json
"driver": { "backend": "iflow", "transport": "stdio" }
```

**Web console not loading**

Make sure port 8787 is free, then start the console separately:

```bash
uv run cli-bridge console
```

---

## License

MIT — see [LICENSE](LICENSE) for details.
