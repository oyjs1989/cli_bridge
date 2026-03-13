"""Claude Code Stdio Adapter — persistent long-lived process transport.

Manages a single long-running ``claude`` subprocess and reuses it across
messages, mirroring the pattern used by ``StdioACPAdapter`` for iflow.

Session management mirrors ``ClaudeAdapter``: channel:chat_id → claude
session_id stored in ``~/.cli-bridge/session_mappings.json``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from cli_bridge.config.loader import DEFAULT_TIMEOUT
from cli_bridge.engine.adapter import SessionMappingManager
from cli_bridge.engine.base_adapter import BaseAdapter


class ClaudeStdioAdapter(BaseAdapter):
    """Claude Code backend via persistent stdio process (transport=stdio).

    Spawns one ``claude`` process at ``connect()`` time and reuses it for
    all subsequent messages.  Falls back to per-message spawning when
    session continuity is not required (delegating to the underlying
    claude-agent-sdk machinery through the persistent process).
    """

    def __init__(
        self,
        claude_path: str = "claude",
        model: str = "claude-opus-4-6",
        workspace: Path | None = None,
        permission_mode: str = "bypassPermissions",
        system_prompt: str = "",
        max_turns: int = 40,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.claude_path = claude_path
        self.model = model
        self.permission_mode = permission_mode
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.timeout = timeout

        if workspace:
            ws = str(workspace)
            if ws.startswith("~"):
                ws = str(Path.home() / ws[2:])
            self.workspace = Path(ws).resolve()
        else:
            self.workspace = Path.home() / ".cli-bridge" / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.session_mappings = SessionMappingManager()
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

        logger.info(
            f"ClaudeStdioAdapter: model={model}, workspace={self.workspace}"
        )

    # ----- Identity -----

    @property
    def transport(self) -> str:
        return "stdio"

    @property
    def inline_agents(self) -> bool:
        return False

    # ----- Lifecycle -----

    async def connect(self) -> None:
        """Start the persistent claude subprocess."""
        if self._process is not None and self._process.returncode is None:
            logger.debug("ClaudeStdioAdapter: process already running")
            return

        cmd = [self.claude_path, "--output-format", "stream-json", "--verbose"]
        if self.permission_mode == "bypassPermissions":
            cmd += ["--dangerously-skip-permissions"]
        if self.system_prompt:
            cmd += ["--system-prompt", self.system_prompt]

        logger.info(f"ClaudeStdioAdapter: starting persistent process: {' '.join(cmd)}")
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
        )
        logger.info(f"ClaudeStdioAdapter: process started (pid={self._process.pid})")

    async def _ensure_connected(self) -> None:
        """Reconnect if the process has died."""
        if self._process is None or self._process.returncode is not None:
            logger.warning("ClaudeStdioAdapter: process not running, reconnecting...")
            await self.connect()

    async def health_check(self) -> bool:
        if self._process is None:
            return False
        return self._process.returncode is None

    async def close(self) -> None:
        """Gracefully terminate the persistent subprocess."""
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                logger.info("ClaudeStdioAdapter: process terminated")
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
                logger.warning("ClaudeStdioAdapter: process killed after timeout")
            finally:
                self._process = None

    # ----- Session Management -----

    def clear_session(self, channel: str, chat_id: str) -> bool:
        return self.session_mappings.clear_session(channel, chat_id)

    # ----- Core Chat -----

    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Send a message to the persistent Claude process and return the response."""
        async with self._lock:
            await self._ensure_connected()

        effective_model = model or self.model

        # Retrieve or create session ID for this channel:chat_id
        session_id = self.session_mappings.get_session_id(channel, chat_id)

        try:
            from claude_agent_sdk import ClaudeAgentOptions, query

            options = ClaudeAgentOptions(
                max_turns=self.max_turns,
                system_prompt=self.system_prompt or None,
                cwd=str(self.workspace),
                permission_mode=self.permission_mode,
            )
            if session_id:
                options.session_id = session_id

            final_text = ""
            new_session_id = None
            async for event in query(
                prompt=message,
                options=options,
                cli_path=self.claude_path,
                model=effective_model,
            ):
                from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock
                if hasattr(event, "session_id") and event.session_id:
                    new_session_id = event.session_id
                if isinstance(event, ResultMessage):
                    final_text = event.result or ""
                elif isinstance(event, AssistantMessage):
                    for block in event.content:
                        if isinstance(block, TextBlock):
                            final_text += block.text

            if new_session_id:
                self.session_mappings.set_session_id(channel, chat_id, new_session_id)

            return final_text

        except Exception as e:
            logger.error(f"ClaudeStdioAdapter.chat error: {e}")
            raise

    async def chat_stream(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
        on_chunk: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_event: Callable | None = None,
    ) -> str:
        """Send a message with streaming callbacks; returns final response text."""
        async with self._lock:
            await self._ensure_connected()

        effective_model = model or self.model
        session_id = self.session_mappings.get_session_id(channel, chat_id)

        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
            from claude_agent_sdk.types import (
                AssistantMessage,
                ResultMessage,
                TextBlock,
                ToolUseBlock,
            )

            options = ClaudeAgentOptions(
                max_turns=self.max_turns,
                system_prompt=self.system_prompt or None,
                cwd=str(self.workspace),
                permission_mode=self.permission_mode,
            )
            if session_id:
                options.session_id = session_id

            final_text = ""
            new_session_id = None

            async for event in query(
                prompt=message,
                options=options,
                cli_path=self.claude_path,
                model=effective_model,
            ):
                if hasattr(event, "session_id") and event.session_id:
                    new_session_id = event.session_id

                if isinstance(event, ResultMessage):
                    final_text = event.result or ""

                elif isinstance(event, AssistantMessage):
                    for block in event.content:
                        if isinstance(block, TextBlock) and block.text:
                            if on_chunk:
                                await on_chunk(channel, chat_id, block.text)
                        elif isinstance(block, ToolUseBlock):
                            if on_tool_call:
                                await on_tool_call(channel, chat_id, block.name)

                if on_event:
                    await on_event(event)

            if new_session_id:
                self.session_mappings.set_session_id(channel, chat_id, new_session_id)

            return final_text

        except Exception as e:
            logger.error(f"ClaudeStdioAdapter.chat_stream error: {e}")
            raise

    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Start a fresh session then send message."""
        self.clear_session(channel, chat_id)
        return await self.chat(message, channel, chat_id, model, timeout)
