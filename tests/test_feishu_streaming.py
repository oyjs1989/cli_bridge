import json

import pytest

from cli_bridge.bus.events import OutboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.channels.feishu import FeishuChannel
from cli_bridge.config.schema import FeishuConfig


@pytest.fixture
def feishu_channel():
    ch = FeishuChannel(FeishuConfig(app_id="x", app_secret="y", enabled=True), MessageBus())
    ch._client = object()  # mark initialized
    return ch


@pytest.mark.asyncio
async def test_feishu_streaming_patch_success(feishu_channel, monkeypatch):
    ch = feishu_channel
    ch._streaming_message_ids["ou_test"] = "msg_existing"

    async def fake_remove(_):
        return None

    monkeypatch.setattr(ch, "_remove_typing_reaction", fake_remove)
    monkeypatch.setattr(ch, "_patch_message_sync", lambda message_id, content: message_id == "msg_existing")

    send_calls = []
    monkeypatch.setattr(
        ch,
        "_send_message_sync",
        lambda receive_id_type, receive_id, msg_type, content: send_calls.append((receive_id_type, receive_id, msg_type, content)),
    )

    await ch._handle_streaming_message(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="hello stream",
            metadata={"_streaming": True, "reply_to_id": "src1"},
        ),
        "open_id",
    )

    assert ch._streaming_last_content["ou_test"] == "hello stream"
    assert send_calls == []


@pytest.mark.asyncio
async def test_feishu_streaming_patch_fail_recreate_success(feishu_channel, monkeypatch):
    ch = feishu_channel
    ch._streaming_message_ids["ou_test"] = "msg_existing"

    async def fake_remove(_):
        return None

    monkeypatch.setattr(ch, "_remove_typing_reaction", fake_remove)
    monkeypatch.setattr(ch, "_patch_message_sync", lambda *_: False)

    send_calls = []

    def fake_send(receive_id_type, receive_id, msg_type, content):
        send_calls.append((receive_id_type, receive_id, msg_type, content))
        return "msg_new"

    monkeypatch.setattr(ch, "_send_message_sync", fake_send)

    await ch._handle_streaming_message(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="hello recreate",
            metadata={"_streaming": True, "reply_to_id": "src1"},
        ),
        "open_id",
    )

    assert ch._streaming_message_ids["ou_test"] == "msg_new"
    assert ch._streaming_last_content["ou_test"] == "hello recreate"
    assert len(send_calls) == 1
    assert send_calls[0][2] == "interactive"
    payload = json.loads(send_calls[0][3])
    assert payload["elements"] == [{"tag": "markdown", "content": "hello recreate"}]


@pytest.mark.asyncio
async def test_feishu_streaming_fallback_to_text_when_patch_and_create_fail(feishu_channel, monkeypatch):
    ch = feishu_channel
    ch._streaming_message_ids["ou_test"] = "msg_existing"

    async def fake_remove(_):
        return None

    monkeypatch.setattr(ch, "_remove_typing_reaction", fake_remove)
    monkeypatch.setattr(ch, "_patch_message_sync", lambda *_: False)

    send_calls = []

    def fake_send(receive_id_type, receive_id, msg_type, content):
        send_calls.append((receive_id_type, receive_id, msg_type, content))
        if msg_type == "interactive":
            return None
        if msg_type == "text":
            return "msg_text"
        return None

    monkeypatch.setattr(ch, "_send_message_sync", fake_send)

    await ch._handle_streaming_message(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="hello fallback",
            metadata={"_streaming": True, "reply_to_id": "src1"},
        ),
        "open_id",
    )

    assert ch._streaming_last_content["ou_test"] == "hello fallback"
    assert "ou_test" not in ch._streaming_message_ids
    assert [call[2] for call in send_calls] == ["interactive", "text"]
    text_payload = json.loads(send_calls[-1][3])
    assert text_payload == {"text": "hello fallback"}


@pytest.mark.asyncio
async def test_feishu_streaming_end_clears_state(feishu_channel, monkeypatch):
    ch = feishu_channel
    ch._streaming_message_ids["ou_test"] = "msg_existing"
    ch._streaming_last_content["ou_test"] = "partial"
    ch._typing_reaction_ids["src1"] = "reaction1"

    cleared = []

    async def fake_remove(source_message_id):
        cleared.append(source_message_id)
        ch._typing_reaction_ids.pop(source_message_id, None)

    monkeypatch.setattr(ch, "_remove_typing_reaction", fake_remove)

    await ch._handle_streaming_message(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="",
            metadata={"_streaming_end": True, "reply_to_id": "src1"},
        ),
        "open_id",
    )

    assert cleared == ["src1"]
    assert "ou_test" not in ch._streaming_message_ids
    assert "ou_test" not in ch._streaming_last_content
