"""Tests for ClaudeStdioAdapter."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_bridge.engine.claude_stdio_adapter import ClaudeStdioAdapter

# ── identity ──────────────────────────────────────────────────────────────────

def test_transport_property(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    assert adapter.transport == "stdio"


def test_inline_agents_is_false(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    assert adapter.inline_agents is False


# ── constructor defaults ──────────────────────────────────────────────────────

def test_default_model(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    assert adapter.model == "claude-opus-4-6"


def test_workspace_resolved(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    assert adapter.workspace == tmp_path.resolve()
    assert adapter.workspace.exists()


def test_workspace_home_fallback():
    adapter = ClaudeStdioAdapter()
    assert adapter.workspace == Path.home() / ".cli-bridge" / "workspace"


# ── health_check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_false_when_no_process(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    assert await adapter.health_check() is False


@pytest.mark.asyncio
async def test_health_check_true_when_process_running(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    mock_process = MagicMock()
    mock_process.returncode = None
    adapter._process = mock_process
    assert await adapter.health_check() is True


@pytest.mark.asyncio
async def test_health_check_false_when_process_exited(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    mock_process = MagicMock()
    mock_process.returncode = 0
    adapter._process = mock_process
    assert await adapter.health_check() is False


# ── close ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_terminates_running_process(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.wait = AsyncMock(return_value=0)
    adapter._process = mock_process
    await adapter.close()
    mock_process.terminate.assert_called_once()
    assert adapter._process is None


@pytest.mark.asyncio
async def test_close_noop_when_no_process(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    # Should not raise
    await adapter.close()


# ── session management ────────────────────────────────────────────────────────

def test_clear_session_returns_false_when_no_session(tmp_path):
    from cli_bridge.engine.adapter import SessionMappingManager
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    adapter.session_mappings = SessionMappingManager(tmp_path / "mappings.json")
    result = adapter.clear_session("telegram", "123")
    assert result is False


def test_clear_session_returns_true_when_session_exists(tmp_path):
    from cli_bridge.engine.adapter import SessionMappingManager
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    adapter.session_mappings = SessionMappingManager(tmp_path / "mappings.json")
    adapter.session_mappings.set_session_id("telegram", "123", "sess-abc")
    result = adapter.clear_session("telegram", "123")
    assert result is True


# ── new_chat clears session ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_chat_clears_existing_session(tmp_path):
    adapter = ClaudeStdioAdapter(workspace=tmp_path)
    adapter.session_mappings.set_session_id("slack", "ch1", "old-sess")

    with patch.object(adapter, "chat", new=AsyncMock(return_value="response")) as mock_chat:
        await adapter.new_chat("hello", "slack", "ch1")

    assert adapter.session_mappings.get_session_id("slack", "ch1") is None
    mock_chat.assert_called_once_with("hello", "slack", "ch1", None, None)
