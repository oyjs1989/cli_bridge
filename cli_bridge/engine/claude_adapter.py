"""Claude Code SDK Adapter.

Wraps claude-agent-sdk-python's query() function to provide the same interface
as StdioACPAdapter. Communicates with the claude binary via JSONL over stdio.

Session management mirrors IFlowAdapter: channel:chat_id → claude session_id
stored in ~/.cli-bridge/session_mappings.json.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import query
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from loguru import logger

from cli_bridge.engine.adapter import SessionMappingManager


class ClaudeAdapterError(Exception):
    """Claude adapter error."""


async def _call_callback(cb: Callable, *args: Any) -> None:
    """Call a callback, awaiting it if it is a coroutine function."""
    result = cb(*args)
    if inspect.isawaitable(result):
        await result


class ClaudeAdapter:
    """Claude Code SDK adapter.

    Uses claude-agent-sdk-python to communicate with claude binary.
    Mirrors StdioACPAdapter interface for use with AgentLoop.

    Session resume: session_id returned in ResultMessage is stored via
    SessionMappingManager and passed as ClaudeAgentOptions(resume=...) on
    subsequent calls.
    """

    mode: Literal["claude"] = "claude"

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

        async def _consume() -> None:
            nonlocal result_text
            async for msg in query(prompt=message, options=options):
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
