"""Characterization tests for cli_bridge.engine.loop.AgentLoop.

These tests lock the observable behavior of AgentLoop BEFORE any refactoring.
They must pass GREEN on unmodified source code.

Invariants captured:
- process_message() publishes an OutboundMessage for non-streaming channels
- In non-streaming mode, adapter.chat() is called (not chat_stream)
- In streaming mode, intermediate messages have _progress=True, _streaming=True
- The final streaming message has _streaming_end=True
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cli_bridge.bus.events import InboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.engine.loop import AgentLoop


class FakeAdapter:
    """Minimal stub adapter for loop characterization."""

    def __init__(self):
        self.workspace = Path("/tmp")
        self.inline_agents = True
        self.chat_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self._stream_chunks: list[str] = []
        self._stream_response: str = ""

    def clear_session(self, channel: str, chat_id: str) -> bool:
        return True

    async def chat(
        self, message: str, channel: str, chat_id: str, model: str, **kwargs
    ) -> str:
        self.chat_calls.append({"channel": channel, "chat_id": chat_id, "model": model})
        return "OK:non-stream"

    async def chat_stream(
        self,
        message: str,
        channel: str,
        chat_id: str,
        model: str,
        on_chunk=None,
        on_tool_call=None,
        on_event=None,
        **kwargs,
    ) -> str:
        self.stream_calls.append({"channel": channel, "chat_id": chat_id, "model": model})
        for chunk in self._stream_chunks:
            if on_chunk:
                await on_chunk(channel, chat_id, chunk)
        return self._stream_response


def _drain_outbound(bus: MessageBus) -> list:
    """Drain all pending outbound messages from the bus (non-blocking)."""
    messages = []
    while not bus._outbound.empty():
        try:
            messages.append(bus._outbound.get_nowait())
        except asyncio.QueueEmpty:
            break
    return messages


@pytest.mark.asyncio
async def test_process_message_publishes_outbound_for_non_streaming_channel():
    """AgentLoop._process_message() publishes an OutboundMessage for non-streaming channel."""
    bus = MessageBus()
    adapter = FakeAdapter()
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=False)

    msg = InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="chat1",
        content="hello",
        metadata={"message_id": "m1"},
    )

    await loop._process_message(msg)

    out = await bus.consume_outbound()
    assert out.channel == "feishu"
    assert out.chat_id == "chat1"
    assert "OK:non-stream" in out.content
    assert out.metadata.get("reply_to_id") == "m1"


@pytest.mark.asyncio
async def test_non_streaming_uses_chat_not_stream():
    """In non-streaming mode, AgentLoop calls adapter.chat() (not chat_stream)."""
    bus = MessageBus()
    adapter = FakeAdapter()
    loop = AgentLoop(bus=bus, adapter=adapter, model="test-model", streaming=False)

    msg = InboundMessage(
        channel="email",
        sender_id="user2",
        chat_id="chat2",
        content="ping",
        metadata={},
    )

    await loop._process_message(msg)

    assert len(adapter.chat_calls) == 1, "adapter.chat must be called once in non-streaming mode"
    assert adapter.chat_calls[0]["channel"] == "email"
    assert adapter.chat_calls[0]["model"] == "test-model"
    assert len(adapter.stream_calls) == 0, "adapter.chat_stream must NOT be called in non-streaming mode"


@pytest.mark.asyncio
async def test_streaming_intermediate_messages_have_progress_flags():
    """In streaming mode, intermediate messages have _progress=True and _streaming=True."""
    bus = MessageBus()
    adapter = FakeAdapter()
    # telegram is in STREAMING_CHANNELS; streaming=True activates stream path
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    # 30 chars exceeds STREAM_BUFFER_MAX (25), guaranteeing a flush
    adapter._stream_chunks = ["A" * 30]
    adapter._stream_response = "final response"

    msg = InboundMessage(
        channel="telegram",
        sender_id="user3",
        chat_id="chat3",
        content="stream me",
        metadata={"message_id": "m3"},
    )

    await loop._process_message(msg)

    published = _drain_outbound(bus)
    assert published, "Expected outbound messages after streaming"

    progress_msgs = [
        m for m in published
        if m.metadata.get("_progress") and m.metadata.get("_streaming")
    ]
    assert progress_msgs, (
        "At least one intermediate message must have _progress=True and _streaming=True"
    )


@pytest.mark.asyncio
async def test_streaming_final_message_has_streaming_end_flag():
    """After streaming completes, a message with _streaming_end=True is published."""
    bus = MessageBus()
    adapter = FakeAdapter()
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    adapter._stream_chunks = []
    adapter._stream_response = "final text"

    msg = InboundMessage(
        channel="telegram",
        sender_id="user4",
        chat_id="chat4",
        content="end test",
        metadata={"message_id": "m4"},
    )

    await loop._process_message(msg)

    published = _drain_outbound(bus)
    assert published, "Expected outbound messages after streaming"

    end_msgs = [m for m in published if m.metadata.get("_streaming_end")]
    assert end_msgs, "Final streaming message must have _streaming_end=True"
