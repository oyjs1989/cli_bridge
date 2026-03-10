# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run gateway in foreground (debug mode)
uv run cli-bridge gateway run

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_e2e_loop_flow.py::test_e2e_non_streaming_text_flow -v

# Lint
uv run ruff check .

# Format
uv run ruff format .
```

Tests use `asyncio_mode = "auto"` — no explicit `@pytest.mark.asyncio` loop setup needed when that decorator is present.

## Architecture

cli-bridge is a **multi-channel AI gateway** that bridges chat platforms (Telegram, Discord, Slack, Feishu, DingTalk, QQ, WhatsApp, Email, Mochat) to the `iflow` CLI AI agent.

### Message Flow

```
Channel → bus.publish_inbound() → AgentLoop → adapter.chat/chat_stream() → iflow CLI
                                                      ↓
Channel ← ChannelManager._listen_outbound() ← bus.publish_outbound()
```

1. Each `BaseChannel` subclass receives platform messages and calls `_handle_message()`, which checks the `allow_from` whitelist and puts an `InboundMessage` onto the `MessageBus`.
2. `AgentLoop.run()` consumes inbound messages and dispatches `_process_message()` as async tasks. Per-user `asyncio.Lock` prevents concurrent processing for the same `channel:chat_id`.
3. The loop calls `IFlowAdapter.chat()` or `chat_stream()`, which routes to the selected backend mode.
4. Responses are published as `OutboundMessage` to the bus. `ChannelManager._listen_outbound()` routes them to the correct channel's `send()`.

### Engine / Adapter Modes

`IFlowAdapter` (`engine/adapter.py`) supports three backend modes configured via `driver.mode`:

| Mode | Implementation | Notes |
|------|----------------|-------|
| `stdio` (default/recommended) | `StdioACPAdapter` — spawns `iflow --experimental-acp --stream` and communicates over stdin/stdout using the ACP protocol | Fastest, streaming, single process |
| `acp` | `ACPAdapter` — WebSocket to a running iflow ACP server | Remote-capable, streaming |
| `cli` | Subprocess per request, captures stdout | Simple, no streaming |

Both `stdio` and `acp` support `chat_stream()` with an `on_chunk` callback for real-time delivery.

### Streaming

`AgentLoop._process_with_streaming()` handles channels in `STREAMING_CHANNELS = {"telegram", "discord", "slack", "dingtalk", "qq", "feishu"}`. Chunks are buffered (random 10–25 char threshold) and flushed as `OutboundMessage` with `metadata["_streaming"] = True`. A final `metadata["_streaming_end"] = True` message signals completion. DingTalk and QQ have special handling (AI Card and newline-split batching respectively).

### Channel Registration

Channels self-register using the `@register_channel("name")` decorator (`channels/manager.py`). The `ChannelManager` reads `Config.get_enabled_channels()` and instantiates only enabled channels. To add a new channel: create `channels/myplatform.py`, extend `BaseChannel`, apply `@register_channel("myplatform")`, and add the config model to `config/schema.py`.

### Configuration

- File: `~/.cli-bridge/config.json`
- Schema: Pydantic `Config` in `config/schema.py` (with `BaseSettings`).
- Env var override: `CLI_BRIDGE_` prefix, `__` as nested delimiter (e.g. `CLI_BRIDGE_DRIVER__MODEL=kimi-k2.5`).
- Session mappings: `~/.cli-bridge/session_mappings.json` — maps `{channel}:{chat_id}` → iflow session ID.

### Bootstrap / Workspace

The `AgentLoop` checks for `workspace/BOOTSTRAP.md` before each message. If present, its content is injected into the message as a system prompt directing iflow to set up its identity, then delete the file. After bootstrap, `workspace/AGENTS.md` is injected in CLI mode for per-message context. The workspace path defaults to `~/.cli-bridge/workspace/` and is where iflow runs (and stores its sessions under `~/.iflow/projects/{hash}/`).

### Key Files

| Path | Role |
|------|------|
| `cli_bridge/engine/loop.py` | `AgentLoop` — core message processing, streaming, per-user locks |
| `cli_bridge/engine/adapter.py` | `IFlowAdapter` — mode router + session mapping |
| `cli_bridge/engine/stdio_acp.py` | `StdioACPAdapter` — ACP protocol over stdio |
| `cli_bridge/bus/queue.py` | `MessageBus` — two asyncio queues (inbound/outbound) |
| `cli_bridge/bus/events.py` | `InboundMessage` / `OutboundMessage` dataclasses |
| `cli_bridge/channels/base.py` | `BaseChannel` ABC — `start`, `stop`, `send`, `is_allowed` |
| `cli_bridge/channels/manager.py` | `ChannelManager` + `@register_channel` decorator |
| `cli_bridge/config/schema.py` | All Pydantic config models |
| `cli_bridge/cli/commands.py` | Typer CLI entry point |
| `cli_bridge/web/server.py` | FastAPI web console |
