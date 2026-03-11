from pathlib import Path

import pytest
from loguru import logger

from cli_bridge.bus.events import InboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.engine.loop import AgentLoop


class _FakeSessionMappings:
    def __init__(self):
        self.cleared: list[tuple[str, str]] = []

    def clear_session(self, channel: str, chat_id: str) -> bool:
        self.cleared.append((channel, chat_id))
        return True


class _FakeAdapter:
    def __init__(self):
        self.workspace = Path("/tmp")
        self.mode = "cli"
        self.session_mappings = _FakeSessionMappings()
        self._stream_chunks: list[str] = []
        self._stream_response = ""

    def clear_session(self, channel: str, chat_id: str) -> bool:
        return self.session_mappings.clear_session(channel, chat_id)

    async def chat(self, message: str, channel: str, chat_id: str, model: str):
        return "ok"

    async def chat_stream(self, message: str, channel: str, chat_id: str, model: str, on_chunk=None, on_tool_call=None, on_event=None, **kwargs):
        for chunk in self._stream_chunks:
            await on_chunk(channel, chat_id, chunk)
        return self._stream_response


@pytest.mark.asyncio
async def test_observability_new_chat_log_emitted():
    bus = MessageBus()
    adapter = _FakeAdapter()
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    records: list[str] = []
    handler_id = logger.add(records.append, format="{message}")
    try:
        msg = InboundMessage(
            channel="telegram",
            sender_id="u1",
            chat_id="c1",
            content="/new",
            metadata={"message_id": "m1"},
        )
        await loop._process_message(msg)
    finally:
        logger.remove(handler_id)

    assert any("New chat requested:" in rec for rec in records)
    # 至少要有结构化模板（当前实现使用 loguru + %s 模板占位）
    assert any("channel=%s" in rec and "chat_id=%s" in rec and "mode=%s" in rec and "cleared=%s" in rec for rec in records)


@pytest.mark.asyncio
async def test_observability_empty_stream_warning_emitted():
    bus = MessageBus()
    adapter = _FakeAdapter()
    adapter._stream_chunks = []
    adapter._stream_response = ""
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    records: list[str] = []
    handler_id = logger.add(records.append, format="{message}")
    try:
        msg = InboundMessage(
            channel="telegram",
            sender_id="u1",
            chat_id="c1",
            content="hello",
            metadata={"message_id": "m2"},
        )
        await loop._process_message(msg)
    finally:
        logger.remove(handler_id)

    assert any("Streaming produced empty output" in rec for rec in records)
