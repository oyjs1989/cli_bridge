"""Characterization tests for channel streaming behavior.

These tests lock in the current behavior of streaming metadata flags
before the StreamingMixin refactoring in Sub-Phase D. They must all
be GREEN on unmodified channel code.

Characterized behaviors:
- send() with _progress=True: no-op (returns immediately without sending)
- send() with _streaming_end=True: clears stream state, does not send duplicate
- send() with _streaming=True: routes to _handle_streaming_message
- send() with plain content: sends new message (no streaming state)
- BaseChannel.is_allowed: allow_from whitelist logic
"""
from __future__ import annotations

import pytest

from cli_bridge.bus.events import OutboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.channels.base import BaseChannel
from cli_bridge.channels.telegram import TelegramChannel
from cli_bridge.config.schema import TelegramConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_telegram(allow_from: list[str] | None = None) -> TelegramChannel:
    config = TelegramConfig(
        token="fake_token",
        enabled=True,
        allow_from=allow_from or [],
    )
    ch = TelegramChannel(config, MessageBus())
    # Don't connect; tests monkey-patch internals
    return ch


def outbound(chat_id: str = "12345", content: str = "hi", **meta) -> OutboundMessage:
    return OutboundMessage(
        channel="telegram",
        chat_id=chat_id,
        content=content,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# BaseChannel.is_allowed characterization
# ---------------------------------------------------------------------------

class _ConcreteChannel(BaseChannel):
    name = "test"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass


def make_base_channel(allow_from: list[str]) -> _ConcreteChannel:
    class _FakeConfig:
        pass
    cfg = _FakeConfig()
    cfg.allow_from = allow_from  # type: ignore[attr-defined]
    return _ConcreteChannel(cfg, MessageBus())


def test_is_allowed_empty_whitelist_permits_all():
    ch = make_base_channel([])
    assert ch.is_allowed("anyone") is True
    assert ch.is_allowed("12345") is True


def test_is_allowed_whitelist_blocks_unlisted():
    ch = make_base_channel(["alice", "bob"])
    assert ch.is_allowed("alice") is True
    assert ch.is_allowed("charlie") is False


def test_is_allowed_pipe_separated_sender():
    """Sender IDs containing '|' are split and each part checked."""
    ch = make_base_channel(["alice"])
    assert ch.is_allowed("alice|extra") is True
    assert ch.is_allowed("unknown|extra") is False


# ---------------------------------------------------------------------------
# TelegramChannel streaming state characterization
# ---------------------------------------------------------------------------

def test_telegram_initial_streaming_state():
    ch = make_telegram()
    assert ch._stream_messages == {}
    assert ch._stream_buffer == {}
    assert ch._last_stream_update == {}


async def test_telegram_send_progress_flag_is_noop(monkeypatch):
    """_progress=True messages are silently dropped — no network call."""
    ch = make_telegram()
    # app is None so any real send would raise; if it returns silently, we pass
    handle_calls = []
    monkeypatch.setattr(ch, "_handle_streaming_message", lambda *a: handle_calls.append(a))

    msg = outbound(_progress=True)
    await ch.send(msg)

    assert handle_calls == [], "_progress messages must not invoke _handle_streaming_message"


async def test_telegram_send_streaming_end_clears_state(monkeypatch):
    """_streaming_end=True clears stream_messages / buffer state."""
    ch = make_telegram()
    ch._app = object()  # mark bot as "running"
    chat_id_str = "12345"
    ch._stream_messages[chat_id_str] = 999
    ch._stream_buffer[chat_id_str] = "partial"

    # Patch flush so it doesn't hit the real bot
    flushed = []
    async def fake_flush(chat_id_int, key):
        flushed.append(key)
    monkeypatch.setattr(ch, "_flush_stream_buffer", fake_flush)
    monkeypatch.setattr(ch, "_stop_typing", lambda *a: None)

    msg = outbound(_streaming_end=True)
    await ch.send(msg)

    # Flush must have been called because buffer was non-empty
    assert chat_id_str in flushed
    # State must be cleared
    assert chat_id_str not in ch._stream_messages
    assert chat_id_str not in ch._stream_buffer


async def test_telegram_send_streaming_end_no_flush_when_empty(monkeypatch):
    """_streaming_end without buffered content does NOT call flush."""
    ch = make_telegram()
    ch._app = object()  # mark bot as "running"

    flushed = []
    async def fake_flush(chat_id_int, key):
        flushed.append(key)
    monkeypatch.setattr(ch, "_flush_stream_buffer", fake_flush)
    monkeypatch.setattr(ch, "_stop_typing", lambda *a: None)

    msg = outbound(_streaming_end=True)
    await ch.send(msg)

    assert flushed == [], "flush must not be called when buffer is empty"


async def test_telegram_send_streaming_routes_to_handler(monkeypatch):
    """_streaming=True routes to _handle_streaming_message."""
    ch = make_telegram()
    ch._app = object()  # mark bot as "running"
    handled = []

    async def fake_handle(chat_id, msg):
        handled.append((chat_id, msg))

    monkeypatch.setattr(ch, "_handle_streaming_message", fake_handle)

    msg = outbound(_streaming=True)
    await ch.send(msg)

    assert len(handled) == 1
    assert handled[0][0] == 12345  # int-cast of "12345"
    assert handled[0][1] is msg


# ---------------------------------------------------------------------------
# _markdown_to_telegram_html characterization
# ---------------------------------------------------------------------------

def test_markdown_bold_converts_to_html():
    from cli_bridge.channels.telegram import _markdown_to_telegram_html
    result = _markdown_to_telegram_html("**hello**")
    assert "<b>hello</b>" in result


def test_markdown_code_block_converts_to_pre():
    from cli_bridge.channels.telegram import _markdown_to_telegram_html
    result = _markdown_to_telegram_html("```python\nprint('hi')\n```")
    assert "<pre><code>" in result


def test_markdown_empty_string_returns_empty():
    from cli_bridge.channels.telegram import _markdown_to_telegram_html
    assert _markdown_to_telegram_html("") == ""


# ---------------------------------------------------------------------------
# _split_message characterization
# ---------------------------------------------------------------------------

def test_split_message_short_text_single_chunk():
    from cli_bridge.channels.telegram import _split_message
    chunks = _split_message("hello world")
    assert chunks == ["hello world"]


def test_split_message_long_text_splits():
    from cli_bridge.channels.telegram import _split_message
    long_text = "x" * 5000
    chunks = _split_message(long_text, max_len=4000)
    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
