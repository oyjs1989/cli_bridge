"""Claude Code SDK Adapter.

Wraps claude-agent-sdk-python's query() function to provide the same interface
as StdioACPAdapter. Communicates with the claude binary via JSONL over stdio.

Session management mirrors IFlowAdapter: channel:chat_id → claude session_id
stored in ~/.cli-bridge/session_mappings.json.
"""

from __future__ import annotations

import asyncio
import inspect
import json as _json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import query
from claude_agent_sdk._errors import CLIConnectionError as _CLIConnectionError
from claude_agent_sdk._errors import CLIJSONDecodeError as _CLIJSONDecodeError
from claude_agent_sdk._errors import ProcessError as _ProcessError
from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport as _BaseCLITransport,
)
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from loguru import logger

from cli_bridge.engine.adapter import SessionMappingManager
from cli_bridge.engine.base_adapter import BaseAdapter


class _FixedSubprocessCLITransport(_BaseCLITransport):
    """Extends SubprocessCLITransport to skip non-JSON lines like 'init done'.

    Claude outputs 'init done\\n' (plain text) to stdout before the first JSON
    message. The parent class accumulates this into the JSON buffer, causing
    subsequent control_response JSON to be unparseable (since the buffer becomes
    'init done{...}' which is not valid JSON). This subclass resets the buffer
    whenever it detects a non-JSON prefix, allowing the protocol to work.
    """

    async def _read_messages_impl(self):  # type: ignore[override]
        if not self._process or not self._stdout_stream:
            raise _CLIConnectionError("Not connected")

        json_buffer = ""

        try:
            async for line in self._stdout_stream:
                line_str = line.strip()
                if not line_str:
                    continue

                for json_line in line_str.split("\n"):
                    json_line = json_line.strip()
                    if not json_line:
                        continue

                    # Reset buffer if it contains non-JSON garbage (e.g. "init done")
                    if json_buffer and json_buffer[0] not in "{[":
                        json_buffer = ""

                    json_buffer += json_line

                    if len(json_buffer) > self._max_buffer_size:
                        buf_len = len(json_buffer)
                        json_buffer = ""
                        raise _CLIJSONDecodeError(
                            f"JSON message exceeded maximum buffer size of {self._max_buffer_size} bytes",
                            ValueError(f"Buffer size {buf_len} exceeds limit {self._max_buffer_size}"),
                        )

                    try:
                        data = _json.loads(json_buffer)
                        json_buffer = ""
                        yield data
                    except _json.JSONDecodeError:
                        continue

        except anyio.ClosedResourceError:
            pass
        except GeneratorExit:
            pass

        try:
            returncode = await self._process.wait()
        except Exception:
            returncode = -1

        if returncode is not None and returncode != 0:
            self._exit_error = _ProcessError(
                f"Command failed with exit code {returncode}",
                exit_code=returncode,
                stderr="Check stderr output for details",
            )
            raise self._exit_error


class ClaudeAdapterError(Exception):
    """Claude adapter error."""


async def _call_callback(cb: Callable, *args: Any) -> None:
    """Call a callback, awaiting it if it is a coroutine function."""
    result = cb(*args)
    if inspect.isawaitable(result):
        await result


class ClaudeAdapter(BaseAdapter):
    """Claude Code SDK adapter.

    Uses claude-agent-sdk-python to communicate with claude binary.
    Mirrors StdioACPAdapter interface for use with AgentLoop.

    Session resume: session_id returned in ResultMessage is stored via
    SessionMappingManager and passed as ClaudeAgentOptions(resume=...) on
    subsequent calls.
    """

    @property
    def mode(self) -> str:
        return "claude"

    def __init__(
        self,
        claude_path: str = "claude",
        model: str = "claude-opus-4-6",
        workspace: Path | None = None,
        permission_mode: str = "bypassPermissions",
        system_prompt: str = "",
        max_turns: int = 40,
        timeout: int = 300,
    ):
        self.claude_path = claude_path
        self.model = model
        self.permission_mode = permission_mode
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.timeout = timeout

        if workspace:
            self.workspace = Path(workspace).resolve()
        else:
            self.workspace = Path.home() / ".cli-bridge" / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.session_mappings = SessionMappingManager()
        logger.info(
            f"ClaudeAdapter: model={model}, workspace={self.workspace}, "
            f"permission_mode={permission_mode}"
        )

    def _build_options(self, channel: str, chat_id: str) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for a given session."""
        session_id = self.session_mappings.get_session_id(channel, chat_id)

        # system_prompt handling:
        # - empty string → use preset (keeps Claude's default coding assistant prompt)
        # - non-empty → use as append-system-prompt
        if self.system_prompt:
            system_prompt_opt: Any = {
                "type": "preset",
                "preset": "claude_code",
                "append": self.system_prompt,
            }
        else:
            system_prompt_opt = {"type": "preset", "preset": "claude_code"}

        return ClaudeAgentOptions(
            cli_path=self.claude_path,
            model=self.model,
            cwd=str(self.workspace),
            permission_mode=self.permission_mode,  # type: ignore[arg-type]
            system_prompt=system_prompt_opt,
            max_turns=self.max_turns if self.max_turns > 0 else None,
            resume=session_id,
            env={"NODE_TLS_REJECT_UNAUTHORIZED": "0", "CLAUDECODE": ""},
        )

    async def _run(
        self,
        message: str,
        channel: str,
        chat_id: str,
        timeout: int | None = None,
        on_chunk: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_event: Callable | None = None,
    ) -> str:
        """Core implementation: run query() and dispatch callbacks."""
        options = self._build_options(channel, chat_id)
        effective_timeout = timeout or self.timeout
        result_text = ""

        transport = _FixedSubprocessCLITransport(prompt=message, options=options)

        async def _consume() -> None:
            nonlocal result_text
            async for msg in query(prompt=message, options=options, transport=transport):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            if on_chunk:
                                await _call_callback(on_chunk, channel, chat_id, block.text)
                        elif isinstance(block, ToolUseBlock):
                            if block.name == "TodoWrite":
                                # Convert TodoWrite to plan event (mirrors acp-claude-code)
                                entries = block.input.get("todos", [])
                                if on_event:
                                    await _call_callback(
                                        on_event, {"type": "plan", "entries": entries}
                                    )
                            else:
                                if on_tool_call:
                                    await _call_callback(on_tool_call, channel, chat_id, block.name)

                elif isinstance(msg, ResultMessage):
                    result_text = msg.result or ""
                    if msg.session_id:
                        self.session_mappings.set_session_id(channel, chat_id, msg.session_id)
                        logger.debug(f"Session saved: {channel}:{chat_id} → {msg.session_id}")

        await asyncio.wait_for(_consume(), timeout=effective_timeout)
        return result_text

    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,  # ignored — uses self.model
        timeout: int | None = None,
    ) -> str:
        """Send message and return full response."""
        logger.info(f"Claude chat: {channel}:{chat_id}")
        return await self._run(message, channel, chat_id, timeout=timeout)

    async def chat_stream(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,  # ignored — uses self.model
        timeout: int | None = None,
        on_chunk: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_event: Callable | None = None,
    ) -> str:
        """Send message with streaming callbacks. Returns full result text."""
        logger.info(f"Claude chat_stream: {channel}:{chat_id}")
        return await self._run(
            message,
            channel,
            chat_id,
            timeout=timeout,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
            on_event=on_event,
        )

    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Clear session and start fresh conversation."""
        self.clear_session(channel, chat_id)
        return await self.chat(message, channel, chat_id, model, timeout)

    def clear_session(self, channel: str, chat_id: str) -> bool:
        """Remove session mapping. Returns True if a session existed."""
        return self.session_mappings.clear_session(channel, chat_id)

    async def health_check(self) -> bool:
        """Check if claude binary is available."""
        try:
            process = await asyncio.create_subprocess_exec(
                self.claude_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.wait(), timeout=10)
            return process.returncode == 0
        except Exception:
            return False

    async def close(self) -> None:
        """No persistent process — nothing to clean up."""
