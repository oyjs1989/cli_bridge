"""Agent Loop - 核心消息处理循环。

BOOTSTRAP 引导机制：
- 每次处理消息前检查 workspace/BOOTSTRAP.md 是否存在
- 如果存在，将内容作为系统前缀注入到消息中
- AI 会自动执行引导流程
- 引导完成后 AI 会删除 BOOTSTRAP.md

流式输出支持：
- ACP 模式下支持实时流式输出到渠道
- 消息块会实时发送到支持流式的渠道（如 Telegram）

文件回传支持 (from feishu-iflow-bridge)：
- 使用 ResultAnalyzer 分析 iflow 输出
- 自动检测输出中生成的文件路径（图片/音频/视频/文档）
- 通过 OutboundMessage.media 字段将文件附加到响应中
- 支持文件回传的渠道（如飞书）会自动上传并发送这些文件
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from cli_bridge.bus import InboundMessage, MessageBus, OutboundMessage
from cli_bridge.engine._streaming import (
    STREAMING_CHANNELS,
    analyze_and_build_outbound,
    process_with_streaming,
)
from cli_bridge.engine.adapter import IFlowAdapter

# Streaming output buffer size range (characters) — kept here so tests can monkeypatch them
STREAM_BUFFER_MIN = 10
STREAM_BUFFER_MAX = 25

if TYPE_CHECKING:
    from cli_bridge.channels.manager import ChannelManager


class AgentLoop:
    """Agent 主循环 - 处理来自各渠道的消息。

    工作流程:
    1. 检查 BOOTSTRAP.md 是否存在（首次启动引导）
    2. 从消息总线获取入站消息
    3. 通过 SessionMappingManager 获取/创建会话 ID
    4. 调用 IFlowAdapter 发送消息到 iflow（支持流式）
    5. 使用 ResultAnalyzer 分析响应（检测文件、状态等）
    6. 将响应和检测到的文件发布到消息总线
    """

    def __init__(
        self,
        bus: MessageBus,
        adapter: IFlowAdapter,
        model: str = "kimi-k2.5",
        streaming: bool = True,
        channel_manager: ChannelManager | None = None,
        backend_name: str = "",
    ):
        self.bus = bus
        self.adapter = adapter
        self.model = model
        self.streaming = streaming
        self.workspace = adapter.workspace
        self.channel_manager = channel_manager

        self._running = False
        self._task: asyncio.Task | None = None

        # 流式消息缓冲区
        self._stream_buffers: dict[str, str] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}

        # P3: 每用户并发锁，确保同一用户的消息串行处理，避免会话状态混乱
        self._user_locks: dict[str, asyncio.Lock] = {}

        # Structured logger bound with backend name for consistent audit fields
        self._log = logger.bind(backend=backend_name) if backend_name else logger

        logger.info(
            f"AgentLoop initialized with model={model}, workspace={self.workspace}, streaming={streaming}"
        )

    def _get_bootstrap_content(self) -> tuple[str | None, bool]:
        """读取引导内容。

        Returns:
            tuple: (内容, 是否是 BOOTSTRAP)
            - 如果 BOOTSTRAP.md 存在，返回 (BOOTSTRAP内容, True)
            - 否则如果 AGENTS.md 存在，返回 (AGENTS内容, False)
            - 都不存在，返回 (None, False)
        """
        # long-lived adapters inject AGENTS via session system_prompt; cli adapters need inline injection
        inline_agents = getattr(self.adapter, "inline_agents", True)

        # 优先检查 BOOTSTRAP.md
        bootstrap_file = self.workspace / "BOOTSTRAP.md"
        if bootstrap_file.exists():
            try:
                content = bootstrap_file.read_text(encoding="utf-8")
                logger.info("BOOTSTRAP.md detected - will inject bootstrap instructions")
                return content, True
            except Exception as e:
                logger.error(f"Error reading BOOTSTRAP.md: {e}")

        # 否则注入 AGENTS.md
        if inline_agents:
            agents_file = self.workspace / "AGENTS.md"
            if agents_file.exists():
                try:
                    content = agents_file.read_text(encoding="utf-8")
                    logger.debug("AGENTS.md detected - will inject agents context")
                    return content, False
                except Exception as e:
                    logger.error(f"Error reading AGENTS.md: {e}")

        return None, False

    def _inject_bootstrap(
        self, message: str, bootstrap_content: str, is_bootstrap: bool = True
    ) -> str:
        """将引导内容注入到消息中。"""
        if is_bootstrap:
            return f"""[BOOTSTRAP - 首次启动引导 - 必须执行]
以下是首次启动引导文件，你必须按照其中的指示完成身份设置。
完成引导后，删除 workspace/BOOTSTRAP.md 文件,删除后只需要告诉用户已完成身份设置即可，无需告诉用户关于 BOOTSTRAP.md 文件的任何信息。

{bootstrap_content}
[/BOOTSTRAP]

用户消息: {message}"""
        else:
            return f"""[AGENTS - 工作空间指南]
以下是当前工作空间的行为指南，请严格遵循。

{bootstrap_content}
[/AGENTS]

SOUL.md - Who You Are（你的灵魂）定义了你是谁，你的性格、特点、行为准则等核心信息。
IDENTITY.md - Your Identity（你的身份）定义了你的具体身份信息，如名字、年龄、职业、兴趣爱好等。
USERY.md - User Identity（用户身份）定义了用户的具体身份信息，如名字、年龄、职业、兴趣爱好等。
TOOLS.md - Your Tools（你的工具）定义了你可以使用的工具列表，包括每个工具的名称、功能描述、使用方法等, 每次学会一个工具，你便要主动更新该文件。

用户消息: {message}"""

    def _build_channel_context(self, msg) -> str:
        """Build channel context for the agent."""
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        context = f"""[message_source]
channel: {msg.channel}
chat_id: {msg.chat_id}
session: {msg.channel}:{msg.chat_id}
time: {now}
[/message_source]"""

        return context

    async def run(self) -> None:
        """启动主循环。"""
        self._running = True
        logger.info("AgentLoop started, listening for inbound messages...")

        while self._running:
            try:
                msg = await self.bus.consume_inbound()
                # 异步处理消息
                asyncio.create_task(self._process_message(msg))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(0.1)

    def _get_user_lock(self, channel: str, chat_id: str) -> asyncio.Lock:
        """获取指定用户的处理锁（P3：防止同一用户并发处理）。"""
        key = f"{channel}:{chat_id}"
        if key not in self._user_locks:
            self._user_locks[key] = asyncio.Lock()
        return self._user_locks[key]

    async def _process_message(self, msg: InboundMessage) -> None:
        """处理单条消息（每用户串行）。"""
        lock = self._get_user_lock(msg.channel, msg.chat_id)
        async with lock:
            try:
                self._log.info(f"Processing: {msg.channel}:{msg.chat_id}")
                self._log.info(
                    "Inbound detail: channel={} chat_id={} sender={} msg_type={}",
                    msg.channel,
                    msg.chat_id,
                    msg.sender_id,
                    msg.metadata.get("msg_type", ""),
                )

                # 检查是否是新会话请求（如 /new 命令）
                if msg.content.strip().lower() in ["/new", "/start"]:
                    cleared = False
                    try:
                        cleared = self.adapter.clear_session(msg.channel, msg.chat_id)
                    except Exception as e:
                        logger.warning(
                            f"Failed to clear session for {msg.channel}:{msg.chat_id}: {e}"
                        )

                    logger.info(
                        "New chat requested: channel=%s chat_id=%s mode=%s cleared=%s",
                        msg.channel,
                        msg.chat_id,
                        getattr(self.adapter, "mode", "unknown"),
                        cleared,
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="✨ 已开始新对话，之前的上下文已清除。",
                        )
                    )
                    return

                # 准备消息内容
                message_content = msg.content

                # 注入渠道上下文
                channel_context = self._build_channel_context(msg)
                if channel_context:
                    message_content = channel_context + "\n\n" + message_content

                # 检查引导文件（优先 BOOTSTRAP.md，否则 AGENTS.md）
                bootstrap_content, is_bootstrap = self._get_bootstrap_content()

                # 如果有引导内容，注入到消息中
                if bootstrap_content:
                    message_content = self._inject_bootstrap(
                        message_content, bootstrap_content, is_bootstrap
                    )
                    mode = "BOOTSTRAP" if is_bootstrap else "AGENTS"
                    logger.info(f"Injected {mode} for {msg.channel}:{msg.chat_id}")

                # 检查是否支持流式输出
                supports_streaming = self.streaming and msg.channel in STREAMING_CHANNELS

                if supports_streaming:
                    # 流式模式
                    response = await self._process_with_streaming(msg, message_content)
                else:
                    # 非流式模式
                    response = await self.adapter.chat(
                        message=message_content,
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        model=self.model,
                    )

                # 发送最终响应（如果有内容且不是流式模式）
                if response and not supports_streaming:
                    # 🆕 使用 ResultAnalyzer 分析响应并提取文件
                    outbound = analyze_and_build_outbound(
                        response=response,
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        metadata={"reply_to_id": msg.metadata.get("message_id")},
                    )
                    await self.bus.publish_outbound(outbound)
                    self._log.info(f"Response sent to {msg.channel}:{msg.chat_id}")

            except Exception as e:
                self._log.exception(f"Error processing message for {msg.channel}:{msg.chat_id}")  # B6
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"❌ 处理消息时出错: {e}",
                    )
                )

    async def _process_with_streaming(
        self,
        msg: InboundMessage,
        message_content: str,
    ) -> str:
        """流式处理消息 — delegates to _streaming.process_with_streaming."""
        return await process_with_streaming(
            adapter=self.adapter,
            bus=self.bus,
            msg=msg,
            message_content=message_content,
            model=self.model,
            channel_manager=self.channel_manager,
            stream_buffers=self._stream_buffers,
            buffer_min=STREAM_BUFFER_MIN,
            buffer_max=STREAM_BUFFER_MAX,
        )

    async def process_direct(
        self,
        message: str,
        session_key: str | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: callable | None = None,
    ) -> str:
        """直接处理消息（CLI 模式 / Cron / Heartbeat）。"""
        # 检查引导文件（优先 BOOTSTRAP.md，否则 AGENTS.md）
        bootstrap_content, is_bootstrap = self._get_bootstrap_content()

        message_content = message
        if bootstrap_content:
            message_content = self._inject_bootstrap(message, bootstrap_content, is_bootstrap)
            mode = "BOOTSTRAP" if is_bootstrap else "AGENTS"
            logger.info(f"Injected {mode} for {channel}:{chat_id} (direct mode)")

        effective_channel = channel
        effective_chat_id = chat_id

        if session_key:
            parts = session_key.split(":", 1)
            if len(parts) == 2:
                effective_channel = parts[0]
                effective_chat_id = parts[1]

        return await self.adapter.chat(
            message=message_content,
            channel=effective_channel,
            chat_id=effective_chat_id,
            model=self.model,
        )

    async def start_background(self) -> None:
        """后台启动。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())
            logger.info("AgentLoop started in background")

    def stop(self) -> None:
        """停止。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("AgentLoop stopped")
