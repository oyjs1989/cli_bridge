"""ACP (Agent Communication Protocol) Connector for iflow CLI.

ACPClient has been extracted to _acp_client.py.
This module re-exports all protocol types for backward compatibility
and defines the ACPAdapter (session-management wrapper).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from loguru import logger

from cli_bridge.config.loader import DEFAULT_TIMEOUT

# Re-export all protocol types so existing importers keep working
from cli_bridge.engine._acp_client import (  # noqa: F401
    ACPClient,
    ACPConnectionError,
    ACPError,
    ACPResponse,
    ACPTimeoutError,
    AgentMessageChunk,
    ContentBlock,
    SessionUpdate,
    StopReason,
    TextContent,
    ToolCall,
)
from cli_bridge.engine._acp_protocol import (
    clip_text as _clip_text_fn,
)
from cli_bridge.engine._acp_protocol import (
    estimate_tokens as _estimate_tokens_fn,
)
from cli_bridge.engine._acp_protocol import (
    get_session_key as _get_session_key_fn,
)
from cli_bridge.engine._acp_protocol import (
    inject_history_before_user_message as _inject_history_fn,
)
from cli_bridge.engine._acp_protocol import (
    is_context_overflow_error as _is_context_overflow_error_fn,
)


class ACPAdapter:
    """
    ACP 适配器 - 封装 ACPClient 提供与 IFlowAdapter 兼容的接口。

    管理会话映射，提供 chat 和 new_chat 方法。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8090,
        timeout: int = DEFAULT_TIMEOUT,
        workspace: Path | None = None,
        default_model: str = "glm-5",
        thinking: bool = False,
    ):
        """
        初始化 ACP 适配器。

        Args:
            host: ACP 服务器主机地址
            port: ACP 服务器端口
            timeout: 请求超时时间（秒）
            workspace: 工作目录路径
            default_model: 默认模型
            thinking: 是否启用思考模式
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.workspace = workspace
        self.default_model = default_model
        self.thinking = thinking

        self._client: ACPClient | None = None
        self._session_map: dict[str, str] = {}  # channel:chat_id -> session_id
        self._session_map_file = Path.home() / ".cli-bridge" / "session_mappings.json"
        self._session_lock = asyncio.Lock()  # 防止并发创建 session
        self._load_session_map()

        logger.info(f"ACPAdapter: host={host}, port={port}, workspace={workspace}")

    def _load_session_map(self) -> None:
        """加载持久化的 session 映射。"""
        if self._session_map_file.exists():
            try:
                with open(self._session_map_file, encoding="utf-8") as f:
                    self._session_map = json.load(f)
                logger.debug(f"Loaded {len(self._session_map)} session mappings")
            except json.JSONDecodeError:
                logger.warning("Invalid session mapping file, starting fresh")
                self._session_map = {}

    def _save_session_map(self) -> None:
        """保存 session 映射到文件。"""
        self._session_map_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._session_map_file, "w", encoding="utf-8") as f:
            json.dump(self._session_map, f, indent=2, ensure_ascii=False)

    def _find_session_file(self, session_id: str) -> Path | None:
        """查找 session 对话历史文件。

        Args:
            session_id: session ID

        Returns:
            session 文件路径，如果不存在返回 None
        """
        # ACP sessions 存放在 ~/.iflow/acp/sessions/
        sessions_dir = Path.home() / ".iflow" / "acp" / "sessions"

        if not sessions_dir.exists():
            return None

        # 文件名格式: {session_id}.json
        session_file = sessions_dir / f"{session_id}.json"
        if session_file.exists():
            return session_file

        return None

    def _extract_conversation_history(
        self,
        session_id: str,
        max_turns: int = 20,
        token_budget: int = 3000,
    ) -> str | None:
        """提取 session 对话历史并格式化成提示词。

        Args:
            session_id: session ID
            max_turns: 最大提取的对话轮次

        Returns:
            格式化后的对话历史，如果无法提取返回 None
        """
        import datetime

        session_file = self._find_session_file(session_id)
        if not session_file:
            logger.debug(f"Session file not found for: {session_id[:16]}...")
            return None

        try:
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)

            chat_history = data.get("chatHistory", [])
            if not chat_history:
                return None

            all_conversations: list[tuple[str, str, str]] = []
            for chat in chat_history:
                role = chat.get("role")
                parts = chat.get("parts", [])

                # 提取文本内容
                full_text = ""
                for part in parts:
                    if isinstance(part, dict):
                        text = part.get("text", "")
                        if text:
                            full_text += text + "\n"

                if not full_text.strip():
                    continue

                # 对于用户消息，提取"用户消息:"之后的实际内容
                if role == "user":
                    # 尝试提取用户消息后的实际内容
                    if "用户消息:" in full_text:
                        # 取用户消息:之后的内容
                        idx = full_text.find("用户消息:") + len("用户消息:")
                        content = full_text[idx:].strip()
                    else:
                        # 如果没有"用户消息:"，跳过系统提示
                        continue

                    # 跳过太短或太长的内容
                    if len(content) < 2 or len(content) > 2000:
                        continue

                    # 获取时间戳
                    timestamp = chat.get("timestamp") or data.get("createdAt", "")
                    time_str = ""
                    if timestamp:
                        try:
                            ts = timestamp.replace("Z", "+00:00")
                            dt = datetime.datetime.fromisoformat(ts.replace("+00:00", ""))
                            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, AttributeError):
                            pass
                    all_conversations.append(("user", time_str, content))

                elif role == "model":
                    # 对于 model 响应，过滤掉系统提示
                    content = full_text.strip()

                    # 过滤掉太长的内容
                    if len(content) > 3000:
                        content = content[:3000] + "..."

                    # 跳过包含系统提示的内容
                    if "<system-reminder>" in content or "[AGENTS - 工作空间指南]" in content:
                        continue

                    if len(content) > 10:
                        all_conversations.append(("assistant", "", content))

            if not all_conversations:
                return None

            history = self._build_budgeted_history_context(
                all_conversations,
                token_budget=token_budget,
                recent_turns=max_turns,
            )
            logger.info(f"Extracted {len(all_conversations)} conversation turns from session {session_id[:16]}...")
            return history

        except Exception as e:
            logger.warning(f"Failed to extract conversation history: {e}")
            return None

    async def connect(self) -> None:
        """连接到 ACP 服务器并进行认证。"""
        if self._client is None:
            self._client = ACPClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout,
                workspace=self.workspace,
            )

        await self._client.connect()
        await self._client.initialize()

        # 进行认证 (使用 iflow 方法，需要设置 IFLOW_API_KEY 环境变量)
        authenticated = await self._client.authenticate("iflow")
        if not authenticated:
            logger.warning("ACP authentication failed, some features may not work")

    async def disconnect(self) -> None:
        """断开连接。"""
        if self._client:
            await self._client.disconnect()
            self._client = None

    def _get_session_key(self, channel: str, chat_id: str) -> str:
        return _get_session_key_fn(channel, chat_id)

    @staticmethod
    def _inject_history_before_user_message(message: str, history_context: str) -> str:
        return _inject_history_fn(message, history_context)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return _estimate_tokens_fn(text)

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        return _clip_text_fn(text, max_chars)

    def _build_budgeted_history_context(
        self,
        conversations: list[tuple[str, str, str]],
        token_budget: int = 3000,
        recent_turns: int = 20,
    ) -> str:
        if not conversations:
            return "<history_context>\n</history_context>"

        def fmt(role: str, time_str: str, content: str, max_chars: int) -> str:
            body = self._clip_text(content, max_chars)
            if role == "user":
                return f"{time_str}\n用户：{body}".strip()
            return f"我：{body}"

        recent = conversations[-recent_turns:] if recent_turns > 0 else []
        older = conversations[:-len(recent)] if recent else conversations[:]

        summary_lines: list[str] = []
        if older:
            summary_lines.append("更早对话摘要：")
            for role, time_str, content in older[-40:]:
                prefix = "用户" if role == "user" else "我"
                item = self._clip_text(content, 120)
                if time_str:
                    summary_lines.append(f"- {time_str} {prefix}: {item}")
                else:
                    summary_lines.append(f"- {prefix}: {item}")

        blocks = [fmt(role, time_str, content, 1600) for role, time_str, content in recent]
        summary_block = "\n".join(summary_lines).strip()

        def build_text(cur_summary: str, cur_blocks: list[str]) -> str:
            parts: list[str] = []
            if cur_summary:
                parts.append(cur_summary)
            parts.extend([b for b in cur_blocks if b.strip()])
            return "<history_context>\n" + "\n\n".join(parts).strip() + "\n</history_context>"

        text = build_text(summary_block, blocks)

        while self._estimate_tokens(text) > token_budget and len(blocks) > 4:
            blocks.pop(0)
            text = build_text(summary_block, blocks)

        if self._estimate_tokens(text) > token_budget and summary_block:
            compact_summary_lines = []
            for line in summary_lines[:24]:
                compact_summary_lines.append(self._clip_text(line, 90))
            summary_block = "\n".join(compact_summary_lines)
            text = build_text(summary_block, blocks)

        while self._estimate_tokens(text) > token_budget and len(text) > 200:
            text = text[: max(200, int(len(text) * 0.9))].rstrip() + "\n</history_context>"

        return text

    @staticmethod
    def _is_context_overflow_error(error_text: str) -> bool:
        return _is_context_overflow_error_fn(error_text)

    async def _get_or_create_session(
        self,
        channel: str,
        chat_id: str,
        model: str | None = None,
    ) -> str:
        """
        获取或创建会话。

        Args:
            channel: 渠道名称
            chat_id: 聊天 ID
            model: 模型名称

        Returns:
            会话 ID
        """
        key = self._get_session_key(channel, chat_id)

        # 如果 session 已存在，直接返回（不尝试 load，因为 ACP session 在服务端保持）
        if key in self._session_map:
            logger.debug(f"Reusing existing session: {key} -> {self._session_map[key][:16]}...")
            return self._session_map[key]

        return await self._create_new_session(key, model)

    async def _create_new_session(
        self,
        key: str,
        model: str | None = None,
    ) -> str:
        """创建新会话。"""
        async with self._session_lock:
            # 再次检查，可能在等待锁时其他协程已创建
            if key in self._session_map:
                logger.debug(f"Session created by another request: {key} -> {self._session_map[key][:16]}...")
                return self._session_map[key]

            # 创建新会话
            if not self._client:
                raise ACPConnectionError("ACP client not connected")

            session_id = await self._client.create_session(
                workspace=self.workspace,
                model=model or self.default_model,
                approval_mode="yolo",
            )

            self._session_map[key] = session_id
            self._save_session_map()
            logger.info(f"ACP session mapped: {key} -> {session_id[:16]}...")

            return session_id

    async def _invalidate_session(self, key: str) -> str | None:
        """使 session 失效并清除映射。

        Returns:
            旧的 session_id，如果不存在返回 None
        """
        old_session = self._session_map.pop(key, None)
        if old_session:
            self._save_session_map()
            logger.info(f"Session invalidated: {key} -> {old_session[:16]}...")
        return old_session

    async def _get_or_create_session_with_retry(
        self,
        channel: str,
        chat_id: str,
        model: str | None = None,
    ) -> str:
        """获取或创建 session，如果失效则自动重建。"""
        key = self._get_session_key(channel, chat_id)

        if key in self._session_map:
            return self._session_map[key]

        return await self._create_new_session(key, model)

    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """
        发送消息并获取响应。

        Args:
            message: 用户消息
            channel: 渠道名称
            chat_id: 聊天 ID
            model: 模型名称
            timeout: 超时时间

        Returns:
            响应文本
        """
        if not self._client:
            raise ACPConnectionError("ACP client not connected")

        key = self._get_session_key(channel, chat_id)
        session_id = await self._get_or_create_session(channel, chat_id, model)

        response = await self._client.prompt(
            session_id=session_id,
            message=message,
            timeout=timeout or self.timeout,
        )

        # 如果 session 失效，自动重建并重试
        if response.error and "Invalid request" in response.error:
            logger.warning(f"Session invalid, recreating: {key}")
            # 获取旧的 session_id 并提取对话历史
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(old_session_id) or ""

            # 创建新 session 并注入历史
            session_id = await self._create_new_session(key, model)

            # 如果有历史，注入到消息中（放在"用户消息:"之前）
            if history_context:
                message = self._inject_history_before_user_message(message, history_context)
                logger.info("Injected conversation history before user message")

            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
            )

        if response.error and self._is_context_overflow_error(response.error):
            logger.warning(f"Context overflow suspected, recreating with compact history: {key}")
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(
                    old_session_id,
                    max_turns=4,
                    token_budget=1200,
                ) or ""

            session_id = await self._create_new_session(key, model)
            retry_message = message
            if history_context:
                retry_message = self._inject_history_before_user_message(retry_message, history_context)
                logger.info("Injected compact conversation history before user message")

            response = await self._client.prompt(
                session_id=session_id,
                message=retry_message,
                timeout=timeout or self.timeout,
            )

        if response.error:
            raise ACPError(f"Chat error: {response.error}")

        # 如果启用思考模式，包含思考过程
        if self.thinking and response.thought:
            return f"[Thinking]\n{response.thought}\n\n[Response]\n{response.content}"

        return response.content

    async def chat_stream(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
        on_chunk: Callable[[AgentMessageChunk], Coroutine] | None = None,
        on_tool_call: Callable[[ToolCall], Coroutine] | None = None,
        on_event: Callable[[dict[str, Any]], Coroutine] | None = None,
    ) -> str:
        """
        发送消息并流式获取响应。

        Args:
            message: 用户消息
            channel: 渠道名称
            chat_id: 聊天 ID
            model: 模型名称
            timeout: 超时时间
            on_chunk: 消息块回调（支持异步）
            on_tool_call: 工具调用回调（支持异步）

        Returns:
            最终响应文本
        """
        if not self._client:
            raise ACPConnectionError("ACP client not connected")

        key = self._get_session_key(channel, chat_id)
        session_id = await self._get_or_create_session(channel, chat_id, model)

        # 收集完整响应
        content_parts: list[str] = []

        async def handle_chunk(chunk: AgentMessageChunk):
            """处理消息块。"""
            if not chunk.is_thought and chunk.text:
                content_parts.append(chunk.text)
            if on_chunk:
                result = on_chunk(chunk)
                if asyncio.iscoroutine(result):
                    await result

        async def handle_tool_call(tool_call: ToolCall):
            """处理工具调用。"""
            if on_tool_call:
                result = on_tool_call(tool_call)
                if asyncio.iscoroutine(result):
                    await result

        async def handle_event(event: dict[str, Any]):
            if on_event:
                result = on_event(event)
                if asyncio.iscoroutine(result):
                    await result

        response = await self._client.prompt(
            session_id=session_id,
            message=message,
            timeout=timeout or self.timeout,
            on_chunk=handle_chunk,
            on_tool_call=handle_tool_call,
            on_event=handle_event,
        )

        # 如果 session 失效，自动重建并重试
        if response.error and "Invalid request" in response.error:
            logger.warning(f"Session invalid (stream), recreating: {key}")
            # 获取旧的 session_id 并提取对话历史
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(old_session_id) or ""

            # 创建新 session 并注入历史
            session_id = await self._create_new_session(key, model)

            # 如果有历史，注入到消息中（放在"用户消息:"之前）
            if history_context:
                message = self._inject_history_before_user_message(message, history_context)
                logger.info("Injected conversation history before user message (stream)")

            content_parts.clear()
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
                on_chunk=handle_chunk,
                on_tool_call=handle_tool_call,
                on_event=handle_event,
            )

        stream_content = "".join(content_parts) or response.content

        should_recover = False
        if response.error and self._is_context_overflow_error(response.error):
            should_recover = True
            logger.warning(f"Context overflow suspected (stream), recreating with compact history: {key}")
        elif not (stream_content or "").strip():
            should_recover = True
            logger.warning(f"Empty stream response detected, recreating with compact history: {key}")

        if should_recover:
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(
                    old_session_id,
                    max_turns=4,
                    token_budget=1200,
                ) or ""

            session_id = await self._create_new_session(key, model)
            retry_message = message
            if history_context:
                retry_message = self._inject_history_before_user_message(retry_message, history_context)
                logger.info("Injected compact conversation history before user message (stream)")

            content_parts.clear()
            response = await self._client.prompt(
                session_id=session_id,
                message=retry_message,
                timeout=timeout or self.timeout,
                on_chunk=handle_chunk,
                on_tool_call=handle_tool_call,
                on_event=handle_event,
            )
            stream_content = "".join(content_parts) or response.content

        if response.error:
            raise ACPError(f"Chat error: {response.error}")

        # 返回收集的完整响应
        return stream_content

    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """
        开始新对话。

        清除已有会话映射，创建新会话。
        """
        key = self._get_session_key(channel, chat_id)
        if key in self._session_map:
            del self._session_map[key]

        return await self.chat(message, channel, chat_id, model, timeout)

    async def health_check(self) -> bool:
        """检查连接状态。"""
        if self._client is None:
            return False
        return await self._client.is_connected()

    def clear_session(self, channel: str, chat_id: str) -> bool:
        """清除会话映射。"""
        key = self._get_session_key(channel, chat_id)
        if key in self._session_map:
            del self._session_map[key]
            return True
        return False

    def list_sessions(self) -> dict[str, str]:
        """列出所有会话映射。"""
        return self._session_map.copy()
