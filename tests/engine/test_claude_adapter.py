"""Tests for ClaudeAdapter using claude-agent-sdk."""

from pathlib import Path
from unittest.mock import patch

import pytest

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from cli_bridge.engine.claude_adapter import ClaudeAdapter, ClaudeAdapterError


# ── helpers ──────────────────────────────────────────────────────────────────

async def _make_query(*messages):
    """Return an async generator function that yields given messages."""
    async def _query(*, prompt, options=None, transport=None):
        for msg in messages:
            yield msg
    return _query


# ── chat() ───────────────────────────────────────────────────────────────────

async def test_chat_returns_result_text(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    mock_q = await _make_query(
        AssistantMessage(content=[TextBlock(text="Hello")], model="claude-opus-4-6"),
        ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                      is_error=False, num_turns=1, session_id="sess-abc", result="Hello"),
    )
    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        result = await adapter.chat("hi", "telegram", "123")
    assert result == "Hello"


async def test_chat_saves_session_id(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    mock_q = await _make_query(
        ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                      is_error=False, num_turns=1, session_id="sess-xyz", result="ok"),
    )
    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        await adapter.chat("hi", "telegram", "123")
    assert adapter.session_mappings.get_session_id("telegram", "123") == "sess-xyz"


async def test_chat_resumes_existing_session(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    adapter.session_mappings.set_session_id("telegram", "123", "existing-sess")

    captured_options = {}

    async def mock_q(*, prompt, options=None, transport=None):
        captured_options["resume"] = options.resume if options else None
        yield ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                            is_error=False, num_turns=1, session_id="existing-sess", result="ok")

    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        await adapter.chat("hi", "telegram", "123")
    assert captured_options["resume"] == "existing-sess"


async def test_chat_ignores_model_param(tmp_path):
    """model= from AgentLoop (iflow model name) must be ignored."""
    adapter = ClaudeAdapter(workspace=tmp_path, model="claude-opus-4-6")
    captured_options = {}

    async def mock_q(*, prompt, options=None, transport=None):
        captured_options["model"] = options.model if options else None
        yield ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                            is_error=False, num_turns=1, session_id="s", result="ok")

    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        await adapter.chat("hi", "telegram", "123", model="minimax-m2.5")  # iflow model
    assert captured_options["model"] == "claude-opus-4-6"  # adapter's own model used


# ── chat_stream() ─────────────────────────────────────────────────────────────

async def test_chat_stream_calls_on_chunk(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    mock_q = await _make_query(
        AssistantMessage(
            content=[TextBlock(text="Hello"), TextBlock(text=" world")],
            model="claude-opus-4-6",
        ),
        ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                      is_error=False, num_turns=1, session_id="s", result="Hello world"),
    )
    chunks = []

    async def on_chunk(channel, chat_id, text):
        chunks.append(text)

    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        result = await adapter.chat_stream("hi", "telegram", "123", on_chunk=on_chunk)

    assert chunks == ["Hello", " world"]
    assert result == "Hello world"


async def test_chat_stream_calls_on_tool_call(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    mock_q = await _make_query(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
            model="claude-opus-4-6",
        ),
        ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                      is_error=False, num_turns=1, session_id="s", result="done"),
    )
    tool_calls = []

    async def on_tool_call(channel, chat_id, name):
        tool_calls.append(name)

    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        await adapter.chat_stream("hi", "telegram", "123", on_tool_call=on_tool_call)

    assert tool_calls == ["Bash"]


async def test_chat_stream_todowrite_fires_plan_event(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    todos = [{"content": "step 1", "status": "pending"}]
    mock_q = await _make_query(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="TodoWrite", input={"todos": todos})],
            model="claude-opus-4-6",
        ),
        ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                      is_error=False, num_turns=1, session_id="s", result="ok"),
    )
    events = []

    async def on_event(event):
        events.append(event)

    tool_calls = []
    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        await adapter.chat_stream(
            "hi", "telegram", "123",
            on_tool_call=lambda c, ci, n: tool_calls.append(n),
            on_event=on_event,
        )

    assert events == [{"type": "plan", "entries": todos}]
    assert tool_calls == []  # TodoWrite must NOT appear as tool_call


# ── clear_session() / new_chat() ─────────────────────────────────────────────

async def test_clear_session_removes_mapping(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    adapter.session_mappings.set_session_id("telegram", "123", "old-sess")
    cleared = adapter.clear_session("telegram", "123")
    assert cleared is True
    assert adapter.session_mappings.get_session_id("telegram", "123") is None


async def test_clear_session_returns_false_when_not_found(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    assert adapter.clear_session("telegram", "999") is False


async def test_new_chat_clears_then_sends(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    adapter.session_mappings.set_session_id("telegram", "123", "old-sess")

    async def mock_q(*, prompt, options=None, transport=None):
        # new_chat should clear first → no resume
        assert options.resume is None
        yield ResultMessage(subtype="success", duration_ms=100, duration_api_ms=50,
                            is_error=False, num_turns=1, session_id="new-sess", result="fresh")

    with patch("cli_bridge.engine.claude_adapter.query", new=mock_q):
        result = await adapter.new_chat("start fresh", "telegram", "123")

    assert result == "fresh"
    assert adapter.session_mappings.get_session_id("telegram", "123") == "new-sess"


# ── health_check() ────────────────────────────────────────────────────────────

async def test_health_check_returns_true_when_claude_found(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    # Uses real claude binary if available, otherwise mock
    result = await adapter.health_check()
    assert isinstance(result, bool)


# ── mode attribute ────────────────────────────────────────────────────────────

def test_mode_is_claude(tmp_path):
    adapter = ClaudeAdapter(workspace=tmp_path)
    assert adapter.mode == "claude"
