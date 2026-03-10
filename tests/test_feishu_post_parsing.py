import json
from types import SimpleNamespace

import pytest

from cli_bridge.bus.queue import MessageBus
from cli_bridge.channels.feishu import FeishuChannel, _extract_post_parts
from cli_bridge.config.schema import FeishuConfig


def test_extract_post_parts_with_nested_resources():
    content = {
        "zh_cn": {
            "title": "日报",
            "content": [[
                {"tag": "text", "text": "今天情况如下"},
                {"tag": "img", "image_key": "img_123", "alt": "截图"},
                {"tag": "a", "text": "文档", "href": "https://example.com"},
                {"tag": "file", "file_key": "file_456", "file_name": "report.xlsx"},
                {"tag": "table", "elements": [[{"tag": "text", "text": "表格内容"}]]},
            ]]
        }
    }

    text_parts, resources = _extract_post_parts(content)

    assert "日报" in text_parts
    assert "今天情况如下" in text_parts
    assert "截图" in text_parts
    assert "文档" in text_parts
    assert "link: https://example.com" in text_parts
    assert "report.xlsx" in text_parts
    assert "[table]" in text_parts
    assert {"type": "image", "image_key": "img_123"} in resources
    assert {"type": "file", "file_key": "file_456"} in resources


@pytest.mark.asyncio
async def test_feishu_post_message_collects_media(monkeypatch):
    bus = MessageBus()
    ch = FeishuChannel(FeishuConfig(app_id="x", app_secret="y", enabled=True), bus)
    ch._client = object()

    accepted = []

    async def fake_add_reaction(*_args, **_kwargs):
        return None

    async def fake_download(msg_type, content_json, message_id=None):
        if msg_type == "image":
            return "/tmp/post-image.jpg", "[image: post-image.jpg]"
        if msg_type == "file":
            return "/tmp/report.xlsx", "[file: report.xlsx]"
        return None, f"[{msg_type}: download failed]"

    async def fake_publish(msg):
        accepted.append(msg)

    monkeypatch.setattr(ch, "_add_reaction", fake_add_reaction)
    monkeypatch.setattr(ch, "_download_and_save_media", fake_download)
    monkeypatch.setattr(bus, "publish_inbound", fake_publish)

    message = SimpleNamespace(
        message_id="om_1",
        chat_id="oc_1",
        chat_type="group",
        message_type="post",
        content=json.dumps(
            {
                "zh_cn": {
                    "title": "日报",
                    "content": [[
                        {"tag": "text", "text": "今天情况如下"},
                        {"tag": "img", "image_key": "img_123", "alt": "截图"},
                        {"tag": "file", "file_key": "file_456", "file_name": "report.xlsx"},
                    ]]
                }
            },
            ensure_ascii=False,
        ),
    )
    sender = SimpleNamespace(sender_type="user", sender_id=SimpleNamespace(open_id="ou_1"))
    data = SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))

    await ch._on_message(data)

    assert len(accepted) == 1
    payload = accepted[0]
    assert payload.chat_id == "oc_1"
    assert payload.sender_id == "ou_1"
    assert "日报" in payload.content
    assert "今天情况如下" in payload.content
    assert "[image: post-image.jpg]" in payload.content
    assert "[file: report.xlsx]" in payload.content
    assert payload.media == ["/tmp/post-image.jpg", "/tmp/report.xlsx"]
