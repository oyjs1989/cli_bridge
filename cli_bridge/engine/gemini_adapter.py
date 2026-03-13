"""Gemini CLI Backend Adapter.

Communicates with the Gemini CLI via ACP (Agent Client Protocol):
  gemini --experimental-acp [--model ...] [--yolo] [--sandbox]

Uses the official `agent-client-protocol` Python SDK (imports as `acp`).
Each channel:chat_id pair gets its own ACP session for the lifetime of the
running Gemini process. Sessions are lost on process restart.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from cli_bridge.config.loader import DEFAULT_TIMEOUT
from cli_bridge.engine.base_adapter import BaseAdapter


class GeminiACPError(Exception):
    """Gemini ACP adapter error."""


class _GeminiClient:
    """ACP Client implementation for GeminiACPAdapter.

    Receives streaming session_update events from the Gemini CLI and
    dispatches them to per-session callbacks registered by chat_stream().
    """

    def __init__(self) -> None:
        self._session_cbs: dict[str, dict[str, Any]] = {}

    def register(
        self,
        session_id: str,
        on_chunk: Callable | None,
        on_tool_call: Callable | None,
    ) -> None:
        self._session_cbs[session_id] = {
            "on_chunk": on_chunk,
            "on_tool_call": on_tool_call,
            "text": [],
        }

    def unregister(self, session_id: str) -> str:
        cb = self._session_cbs.pop(session_id, {})
        return "".join(cb.get("text", []))

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        """Called by the ACP SDK for each streaming event."""
        from acp.schema import AgentMessageChunk, TextContentBlock, ToolCallStart

        cb = self._session_cbs.get(session_id)
        if cb is None:
            return

        if isinstance(update, AgentMessageChunk):
            for block in update.content:
                if isinstance(block, TextContentBlock) and block.text:
                    cb["text"].append(block.text)
                    if cb["on_chunk"]:
                        await cb["on_chunk"](block.text)

        elif isinstance(update, ToolCallStart):
            if cb["on_tool_call"] and update.title:
                await cb["on_tool_call"](update.title)

    async def request_permission(
        self, options: list, session_id: str, tool_call: Any, **kwargs: Any
    ) -> Any:
        """Auto-approve all permission requests (yolo mode)."""
        from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

        for opt in options:
            if getattr(opt, "kind", None) in {"allow_once", "allow_always"}:
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
                )
        if options:
            opt = options[0]
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # Minimal no-op implementations for optional file system / terminal methods
    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs: Any):
        from acp.schema import WriteTextFileResponse
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return WriteTextFileResponse()

    async def read_text_file(self, path: str, session_id: str, **kwargs: Any):
        from acp.schema import ReadTextFileResponse
        return ReadTextFileResponse(content=Path(path).read_text())

    async def create_terminal(self, command: str, session_id: str, **kwargs: Any):
        from acp.schema import CreateTerminalResponse
        return CreateTerminalResponse(terminal_id="term-1")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any):
        from acp.schema import TerminalOutputResponse
        return TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any):
        from acp.schema import ReleaseTerminalResponse
        return ReleaseTerminalResponse()

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any):
        from acp.schema import WaitForTerminalExitResponse
        return WaitForTerminalExitResponse()

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any):
        from acp.schema import KillTerminalCommandResponse
        return KillTerminalCommandResponse()


class GeminiACPAdapter(BaseAdapter):
    """Gemini CLI backend adapter using Agent Client Protocol (ACP).

    Spawns `gemini --experimental-acp` as a long-lived subprocess and
    communicates via the official `acp` Python SDK. Each channel:chat_id
    pair gets its own ACP session.

    Sessions persist for the lifetime of the Gemini process; they are lost
    on process restart.
    """

    @property
    def inline_agents(self) -> bool:
        """Persistent session carries context; no inline injection needed."""
        return False

    def __init__(
        self,
        gemini_path: str = "gemini",
        model: str = "gemini-2.5-pro",
        workspace: Path | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        api_key: str = "",
        google_api_key: str = "",
        yolo: bool = True,
        sandbox: bool = False,
    ) -> None:
        self.gemini_path = gemini_path
        self.model = model
        self.workspace = workspace or Path.cwd()
        self.timeout = timeout
        self.api_key = api_key
        self.google_api_key = google_api_key
        self.yolo = yolo
        self.sandbox = sandbox

        self._proc: asyncio.subprocess.Process | None = None
        self._conn = None        # acp.core.ClientSideConnection
        self._client_impl = _GeminiClient()
        self._session_map: dict[str, str] = {}   # channel:chat_id → acp session_id
        self._session_lock = asyncio.Lock()
        self._started = False

    # ── Public helpers ────────────────────────────────────────────────────────

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.api_key:
            env["GEMINI_API_KEY"] = self.api_key
        if self.google_api_key:
            env["GOOGLE_API_KEY"] = self.google_api_key
        return env

    def _build_cmd(self) -> list[str]:
        cmd = [self.gemini_path, "--experimental-acp"]
        if self.model:
            cmd += ["--model", self.model]
        if self.yolo:
            cmd.append("--yolo")
        if self.sandbox:
            cmd.append("--sandbox")
        return cmd

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def _start(self) -> None:
        """Spawn gemini process and initialize ACP connection."""
        if self._started:
            return

        from acp import PROTOCOL_VERSION, connect_to_agent
        from acp.schema import ClientCapabilities, FileSystemCapability

        cmd = self._build_cmd()
        env = self._build_env()

        logger.info(f"GeminiACPAdapter: starting {' '.join(cmd)}")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
            env=env,
        )

        if self._proc.stdin is None or self._proc.stdout is None:
            raise GeminiACPError("Gemini process did not expose stdio pipes")

        # Build the acp Client subclass dynamically to avoid import-time issues
        import acp

        class _BoundClient(acp.Client):
            def __init__(inner_self) -> None:
                pass

            async def session_update(inner_self, session_id, update, **kwargs):
                await self._client_impl.session_update(session_id, update, **kwargs)

            async def request_permission(inner_self, options, session_id, tool_call, **kwargs):
                return await self._client_impl.request_permission(options, session_id, tool_call, **kwargs)

            async def write_text_file(inner_self, content, path, session_id, **kwargs):
                return await self._client_impl.write_text_file(content, path, session_id, **kwargs)

            async def read_text_file(inner_self, path, session_id, **kwargs):
                return await self._client_impl.read_text_file(path, session_id, **kwargs)

            async def create_terminal(inner_self, command, session_id, **kwargs):
                return await self._client_impl.create_terminal(command, session_id, **kwargs)

            async def terminal_output(inner_self, session_id, terminal_id, **kwargs):
                return await self._client_impl.terminal_output(session_id, terminal_id, **kwargs)

            async def release_terminal(inner_self, session_id, terminal_id, **kwargs):
                return await self._client_impl.release_terminal(session_id, terminal_id, **kwargs)

            async def wait_for_terminal_exit(inner_self, session_id, terminal_id, **kwargs):
                return await self._client_impl.wait_for_terminal_exit(session_id, terminal_id, **kwargs)

            async def kill_terminal(inner_self, session_id, terminal_id, **kwargs):
                return await self._client_impl.kill_terminal(session_id, terminal_id, **kwargs)

        self._conn = connect_to_agent(_BoundClient(), self._proc.stdin, self._proc.stdout)

        await self._conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(
                fs=FileSystemCapability(readTextFile=True, writeTextFile=True),
                terminal=True,
            ),
        )
        self._started = True
        logger.info(f"GeminiACPAdapter: connected (pid={self._proc.pid})")

    async def close(self) -> None:
        """Terminate the Gemini subprocess and clean up."""
        import contextlib
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
            self._proc = None
        self._started = False
        logger.info("GeminiACPAdapter: closed")

    async def health_check(self) -> bool:
        if not self._started or self._proc is None:
            return False
        return self._proc.returncode is None

    # ── Session management ────────────────────────────────────────────────────

    def _session_key(self, channel: str, chat_id: str) -> str:
        return f"{channel}:{chat_id}"

    async def _get_or_create_session(self, channel: str, chat_id: str) -> str:
        key = self._session_key(channel, chat_id)
        if key in self._session_map:
            return self._session_map[key]

        async with self._session_lock:
            if key in self._session_map:
                return self._session_map[key]

            await self._start()
            session = await self._conn.new_session(
                cwd=str(self.workspace), mcp_servers=[]
            )
            self._session_map[key] = session.session_id
            logger.debug(f"GeminiACPAdapter: new session {channel}:{chat_id} → {session.session_id}")
            return session.session_id

    def clear_session(self, channel: str, chat_id: str) -> bool:
        key = self._session_key(channel, chat_id)
        if key in self._session_map:
            del self._session_map[key]
            return True
        return False

    # ── Core chat ─────────────────────────────────────────────────────────────

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
        from acp import text_block

        await self._start()
        session_id = await self._get_or_create_session(channel, chat_id)
        effective_timeout = timeout or self.timeout

        self._client_impl.register(session_id, on_chunk, on_tool_call)
        try:
            await asyncio.wait_for(
                self._conn.prompt(
                    session_id=session_id,
                    prompt=[text_block(message)],
                ),
                timeout=effective_timeout,
            )
        finally:
            result_text = self._client_impl.unregister(session_id)

        return result_text

    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        return await self.chat_stream(
            message, channel, chat_id, model=model, timeout=timeout
        )

    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        # Drop the old ACP session; next call creates a fresh one
        self.clear_session(channel, chat_id)
        return await self.chat(message, channel, chat_id, model=model, timeout=timeout)
