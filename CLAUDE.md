# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (using uv with .venv)
uv sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_e2e_loop_flow.py

# Run a specific test
uv run pytest tests/test_e2e_loop_flow.py::test_new_chat_command -v

# Lint and format check
uv run ruff check cli_bridge tests
uv run ruff format --check cli_bridge tests

# Fix lint issues
uv run ruff check --fix cli_bridge tests

# Run the gateway (foreground/debug)
uv run cli-bridge gateway run

# Run the gateway (background)
uv run cli-bridge gateway start
```

Config file lives at `~/.cli-bridge/config.json`. First run auto-creates defaults.

## Architecture

**cli-bridge** is a multi-channel AI gateway: it bridges chat platforms (Telegram, Discord, Slack, Feishu, DingTalk, QQ, WhatsApp, Email, Mochat) to an AI backend (Claude CLI or iflow CLI).

### Message Flow

```
Chat Platform → Channel → MessageBus (inbound queue) → AgentLoop
                                                              ↓
Chat Platform ← Channel ← MessageBus (outbound queue) ← IFlowAdapter / ClaudeAdapter
```

1. **Channels** (`cli_bridge/channels/`) each implement `BaseChannel` with `start()`, `stop()`, and `send()`. They receive platform-specific webhooks/events and publish `InboundMessage` to the bus, then subscribe to outbound messages.

2. **MessageBus** (`cli_bridge/bus/queue.py`) is a simple asyncio two-queue broker — one for inbound (channels → agent) and one for outbound (agent → channels). Also integrates with `ChannelRecorder` for session recording.

3. **AgentLoop** (`cli_bridge/engine/loop.py`) is the core processing loop. Per-user asyncio locks prevent concurrent processing for the same user. It:
   - Checks for `workspace/BOOTSTRAP.md` (first-run instructions) or `workspace/AGENTS.md` (persistent context) to inject into messages
   - Supports streaming (buffered chunks, 10–25 chars) for Telegram, Discord, Slack, DingTalk, QQ, Feishu
   - Uses `ResultAnalyzer` to detect generated files (images, audio, video, docs) in responses for media callback

4. **Adapters** (`cli_bridge/engine/`) route messages to the AI backend based on `driver.mode`:
   - `cli` — spawns `iflow` subprocess per message (default legacy mode)
   - `stdio` — long-running `iflow --experimental-acp` process over stdin/stdout; `StdioACPAdapter`
   - `acp` — connects to a running iflow ACP WebSocket server; `ACPAdapter`
   - `claude` — uses `claude-agent-sdk` to drive the `claude` CLI; `ClaudeAdapter`

5. **Session mapping** (`SessionMappingManager` in `engine/adapter.py`) maps `channel:chat_id` keys to backend session IDs, persisted in `~/.cli-bridge/session_mappings.json`.

### Key Modules

| Path | Purpose |
|------|---------|
| `cli_bridge/cli/commands.py` | Typer CLI — `gateway start/run/stop`, `model`, `thinking`, `status`, `iflow` passthrough |
| `cli_bridge/config/schema.py` | Pydantic config schema; `DriverConfig` controls backend mode and MCP proxy settings |
| `cli_bridge/config/loader.py` | Loads/saves `~/.cli-bridge/config.json`; `DEFAULT_TIMEOUT = 180` |
| `cli_bridge/engine/claude_adapter.py` | Claude CLI adapter using `claude-agent-sdk`; patches SDK transport to skip non-JSON `init done` stdout lines |
| `cli_bridge/channels/manager.py` | Instantiates and manages all enabled channels |
| `cli_bridge/cron/` | Cron-style scheduled task runner |
| `cli_bridge/heartbeat/` | Periodic heartbeat messages to channels |
| `cli_bridge/mcp_proxy.py` | MCP server proxy that aggregates multiple MCP servers for sharing across sessions |

### Driver Modes

The `driver.mode` config field is central. The default in new installs is `stdio`. The `claude` mode bypasses iflow entirely and uses the Claude CLI directly via `claude-agent-sdk`. When adding features that touch the adapter layer, all four modes must be considered.

### Streaming Protocol

Channels receive outbound messages with `metadata._progress = True` and `metadata._streaming = True` for intermediate streaming updates, and `metadata._streaming_end = True` for the final termination signal. Channels implement edit-in-place (e.g. Telegram message edits) based on these flags.

### Testing Patterns

Tests use `FakeAdapter` patterns to stub out actual CLI execution. The `asyncio_mode = "auto"` pytest setting means all `async def test_*` functions run automatically. Tests in `tests/engine/` cover individual adapter unit behavior.

## Active Technologies
- Python 3.10+ (targets 3.10, 3.11, 3.12) + Pydantic v2 (config), Typer (CLI), FastAPI + Uvicorn (Web UI), `claude-agent-sdk` (Claude backend), asyncio (concurrency), loguru (logging) (001-decouple-iflow-backend)
- JSON config file at `~/.cli-bridge/config.json`; session mappings at `~/.cli-bridge/session_mappings.json`; workspace markdown files at configurable path (001-decouple-iflow-backend)
- Python 3.10+ (targets 3.10, 3.11, 3.12) + Pydantic v2 (config/validation), Typer (CLI), loguru (logging), claude-agent-sdk (Claude backend) (002-split-driver-config)
- Python 3.10, 3.11, 3.12 + Pydantic v2 (config), Typer (CLI), FastAPI + Uvicorn (Web UI), loguru (logging), claude-agent-sdk v0.1.48 (Claude backend), asyncio (concurrency), aiohttp (MCP proxy HTTP layer) (003-post-decouple-followups)
- `~/.cli-bridge/config.json` (gateway config), `~/.cli-bridge/config/.mcp_proxy_config.json` (MCP server defs), `~/.cli-bridge/session_mappings.json` (session map), `~/.cli-bridge/workspace/channel/` (ChannelRecorder), `~/.cli-bridge/sessions/*.json` (ACP session files for iflow) (003-post-decouple-followups)
- Python 3.10–3.12 (venv uses 3.11) + Typer, Pydantic v2, FastAPI/Uvicorn, claude-agent-sdk v0.1.48, loguru, asyncio, aiohttp, pytest + pytest-asyncio (004-tdd-refactor-skill)
- JSON files at `~/.cli-bridge/`; no relational DB (004-tdd-refactor-skill)
- Python 3.10, 3.11, 3.12 + Pydantic v2 (config), Typer (CLI), FastAPI + Uvicorn (Web UI), asyncio, loguru, claude-agent-sdk v0.1.48, aiohttp (MCP proxy) (005-incremental-refactor)
- `~/.cli-bridge/config.json`, `~/.cli-bridge/session_mappings.json`, `~/.cli-bridge/workspace/` (channel recorder) (005-incremental-refactor)

## Recent Changes
- 001-decouple-iflow-backend: Added Python 3.10+ (targets 3.10, 3.11, 3.12) + Pydantic v2 (config), Typer (CLI), FastAPI + Uvicorn (Web UI), `claude-agent-sdk` (Claude backend), asyncio (concurrency), loguru (logging)
