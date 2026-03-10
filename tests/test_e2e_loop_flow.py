import asyncio
from pathlib import Path

import pytest

from cli_bridge.bus.events import InboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.engine.loop import AgentLoop


class _FakeSessionMappings:
    def __init__(self):
        self.cleared: list[tuple[str, str]] = []

    def clear_session(self, channel: str, chat_id: str) -> bool:
        self.cleared.append((channel, chat_id))
        return True


class FakeAdapter:
    def __init__(self, mode: str = "cli"):
        self.workspace = Path("/tmp")
        self.mode = mode
        self.session_mappings = _FakeSessionMappings()
        self.chat_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self._stream_chunks: list[str] = []
        self._stream_response: str = ""

    async def chat(self, message: str, channel: str, chat_id: str, model: str):
        self.chat_calls.append(
            {
                "message": message,
                "channel": channel,
                "chat_id": chat_id,
                "model": model,
            }
        )
        return "OK:non-stream"

    async def chat_stream(self, message: str, channel: str, chat_id: str, model: str, on_chunk):
        self.stream_calls.append(
            {
                "message": message,
                "channel": channel,
                "chat_id": chat_id,
                "model": model,
            }
        )
        for chunk in self._stream_chunks:
            await on_chunk(channel, chat_id, chunk)
        return self._stream_response


@pytest.mark.asyncio
async def test_e2e_non_streaming_text_flow():
    bus = MessageBus()
    adapter = FakeAdapter(mode="cli")
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=False)

    msg = InboundMessage(
        channel="feishu",
        sender_id="ou_1",
        chat_id="oc_1",
        content="hello",
        metadata={"message_id": "m1", "msg_type": "text"},
    )

    await loop._process_message(msg)

    out = await bus.consume_outbound()
    assert out.channel == "feishu"
    assert out.chat_id == "oc_1"
    assert "OK:non-stream" in out.content
    assert out.metadata.get("reply_to_id") == "m1"
    assert adapter.chat_calls, "chat should be called in non-streaming mode"


@pytest.mark.asyncio
async def test_e2e_new_command_clears_session_and_ack():
    bus = MessageBus()
    adapter = FakeAdapter(mode="cli")
    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    msg = InboundMessage(
        channel="telegram",
        sender_id="u1",
        chat_id="c1",
        content="/new",
        metadata={"message_id": "m2"},
    )

    await loop._process_message(msg)

    out = await bus.consume_outbound()
    assert "已开始新对话" in out.content
    assert adapter.session_mappings.cleared == [("telegram", "c1")]


@pytest.mark.asyncio
async def test_e2e_streaming_flow_emits_progress_and_end(monkeypatch):
    bus = MessageBus()
    adapter = FakeAdapter(mode="cli")
    adapter._stream_chunks = ["hello", " world"]
    adapter._stream_response = "hello world"

    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    # force flush on each chunk so progress messages are deterministic
    monkeypatch.setattr("cli_bridge.engine.loop.STREAM_BUFFER_MIN", 1)
    monkeypatch.setattr("cli_bridge.engine.loop.STREAM_BUFFER_MAX", 1)

    msg = InboundMessage(
        channel="telegram",
        sender_id="u1",
        chat_id="c1",
        content="stream me",
        metadata={"message_id": "m3"},
    )

    await loop._process_message(msg)

    outs = []
    while bus.outbound_size:
        outs.append(await bus.consume_outbound())

    assert adapter.stream_calls, "chat_stream should be called in streaming mode"
    assert any(o.metadata.get("_streaming") for o in outs), "should emit streaming progress/final"
    assert any(o.metadata.get("_streaming_end") for o in outs), "should emit streaming end marker"

    # 进度/结束消息应携带一致的 reply_to_id，便于全链路追踪
    tracked = [o for o in outs if o.metadata.get("_streaming") or o.metadata.get("_streaming_end")]
    assert tracked
    assert all(o.metadata.get("reply_to_id") == "m3" for o in tracked)


@pytest.mark.asyncio
async def test_e2e_streaming_empty_response_fallback():
    bus = MessageBus()
    adapter = FakeAdapter(mode="cli")
    adapter._stream_chunks = []
    adapter._stream_response = ""

    loop = AgentLoop(bus=bus, adapter=adapter, model="kimi-k2.5", streaming=True)

    msg = InboundMessage(
        channel="telegram",
        sender_id="u1",
        chat_id="c1",
        content="please reply",
        metadata={"message_id": "m4"},
    )

    await loop._process_message(msg)

    out = await bus.consume_outbound()
    assert "本轮未产出可见文本" in out.content
    assert out.metadata.get("reply_to_id") == "m4"
