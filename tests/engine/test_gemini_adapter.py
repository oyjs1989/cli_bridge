"""Tests for GeminiACPAdapter."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_bridge.engine.gemini_adapter import GeminiACPAdapter


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_adapter(tmp_path, **kwargs):
    return GeminiACPAdapter(
        gemini_path="gemini",
        workspace=tmp_path,
        **kwargs,
    )


# ── health_check ─────────────────────────────────────────────────────────────

async def test_health_check_returns_false_when_not_started(tmp_path):
    adapter = _make_adapter(tmp_path)
    result = await adapter.health_check()
    assert result is False


# ── clear_session ─────────────────────────────────────────────────────────────

async def test_clear_session_returns_false_when_no_session(tmp_path):
    adapter = _make_adapter(tmp_path)
    result = adapter.clear_session("telegram", "123")
    assert result is False


async def test_clear_session_returns_true_after_session_registered(tmp_path):
    adapter = _make_adapter(tmp_path)
    # Manually inject a session mapping
    adapter._session_map["telegram:123"] = "acp-session-abc"
    result = adapter.clear_session("telegram", "123")
    assert result is True
    assert "telegram:123" not in adapter._session_map


# ── inline_agents ────────────────────────────────────────────────────────────

def test_inline_agents_is_false(tmp_path):
    """GeminiACPAdapter has a persistent session so no inline injection needed."""
    adapter = _make_adapter(tmp_path)
    assert adapter.inline_agents is False


# ── _build_env ────────────────────────────────────────────────────────────────

def test_build_env_includes_gemini_api_key(tmp_path):
    adapter = _make_adapter(tmp_path, api_key="test-key-123")
    env = adapter._build_env()
    assert env.get("GEMINI_API_KEY") == "test-key-123"


def test_build_env_includes_google_api_key(tmp_path):
    adapter = _make_adapter(tmp_path, google_api_key="goog-key")
    env = adapter._build_env()
    assert env.get("GOOGLE_API_KEY") == "goog-key"


def test_build_env_skips_empty_keys(tmp_path):
    adapter = _make_adapter(tmp_path)  # no api_key
    env = adapter._build_env()
    assert "GEMINI_API_KEY" not in env
    assert "GOOGLE_API_KEY" not in env


# ── _build_cmd ────────────────────────────────────────────────────────────────

def test_build_cmd_includes_experimental_acp(tmp_path):
    adapter = _make_adapter(tmp_path, model="gemini-2.5-pro")
    cmd = adapter._build_cmd()
    assert "--experimental-acp" in cmd


def test_build_cmd_includes_model(tmp_path):
    adapter = _make_adapter(tmp_path, model="gemini-2.0-flash")
    cmd = adapter._build_cmd()
    assert "--model" in cmd
    assert "gemini-2.0-flash" in cmd


def test_build_cmd_includes_yolo_flag_when_enabled(tmp_path):
    adapter = _make_adapter(tmp_path, yolo=True)
    cmd = adapter._build_cmd()
    assert "--yolo" in cmd


def test_build_cmd_excludes_yolo_when_disabled(tmp_path):
    adapter = _make_adapter(tmp_path, yolo=False)
    cmd = adapter._build_cmd()
    assert "--yolo" not in cmd


def test_build_cmd_includes_sandbox_flag_when_enabled(tmp_path):
    adapter = _make_adapter(tmp_path, sandbox=True)
    cmd = adapter._build_cmd()
    assert "--sandbox" in cmd


def test_build_cmd_excludes_sandbox_when_disabled(tmp_path):
    adapter = _make_adapter(tmp_path, sandbox=False)
    cmd = adapter._build_cmd()
    assert "--sandbox" not in cmd


# ── chat_stream ───────────────────────────────────────────────────────────────

async def test_chat_stream_returns_accumulated_text(tmp_path):
    """chat_stream collects chunks from session_update and returns full text."""
    adapter = _make_adapter(tmp_path)

    # Simulate a started adapter with a mock connection
    adapter._started = True
    adapter._session_map["cli:direct"] = "session-1"

    # Mock conn.prompt to call session_update with text chunks
    from unittest.mock import AsyncMock

    async def fake_prompt(session_id, prompt):
        # Simulate two text chunks arriving via session_update
        from acp.schema import AgentMessageChunk, TextContentBlock
        chunk1 = AgentMessageChunk(
            content=TextContentBlock(type="text", text="Hello "),
            sessionUpdate="agent_message_chunk",
        )
        chunk2 = AgentMessageChunk(
            content=TextContentBlock(type="text", text="world"),
            sessionUpdate="agent_message_chunk",
        )
        await adapter._client_impl.session_update(session_id, chunk1)
        await adapter._client_impl.session_update(session_id, chunk2)

    adapter._conn = AsyncMock()
    adapter._conn.prompt.side_effect = fake_prompt

    chunks = []
    result = await adapter.chat_stream(
        "hi",
        channel="cli",
        chat_id="direct",
        on_chunk=lambda ch, cid, text: chunks.append(text),
    )

    assert result == "Hello world"
    assert chunks == ["Hello ", "world"]


async def test_chat_stream_passes_channel_and_chat_id_to_on_chunk(tmp_path):
    """on_chunk receives (channel, chat_id, text) — not just text."""
    adapter = _make_adapter(tmp_path)
    adapter._started = True
    adapter._session_map["telegram:42"] = "session-2"

    from unittest.mock import AsyncMock
    from acp.schema import AgentMessageChunk, TextContentBlock

    async def fake_prompt(session_id, prompt):
        chunk = AgentMessageChunk(
            content=TextContentBlock(type="text", text="hi"),
            sessionUpdate="agent_message_chunk",
        )
        await adapter._client_impl.session_update(session_id, chunk)

    adapter._conn = AsyncMock()
    adapter._conn.prompt.side_effect = fake_prompt

    calls = []
    await adapter.chat_stream(
        "test",
        channel="telegram",
        chat_id="42",
        on_chunk=lambda ch, cid, text: calls.append((ch, cid, text)),
    )

    assert calls == [("telegram", "42", "hi")]
