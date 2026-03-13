"""Characterization tests for StdioACPAdapter.

These tests lock the public interface and session management behavior
of StdioACPAdapter BEFORE Phase C refactoring begins.
They must be GREEN on unmodified source code.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli_bridge.engine.stdio_acp import (
    StdioACPAdapter,
    StdioACPConnectionError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_adapter(tmp_path: Path) -> StdioACPAdapter:
    """Create a StdioACPAdapter that writes session maps to tmp_path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = StdioACPAdapter(workspace=workspace)
    # Override session map file so tests don't touch ~/.cli-bridge/
    adapter._session_map_file = tmp_path / "session_mappings.json"
    adapter._session_map.clear()
    return adapter


# ---------------------------------------------------------------------------
# Session key format
# ---------------------------------------------------------------------------


def test_get_session_key_format(tmp_path):
    """_get_session_key returns 'channel:chat_id' string."""
    adapter = make_adapter(tmp_path)
    assert adapter._get_session_key("telegram", "12345") == "telegram:12345"
    assert adapter._get_session_key("feishu", "ou_abc") == "feishu:ou_abc"


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def test_clear_session_returns_false_for_unknown(tmp_path):
    """clear_session returns False when session does not exist."""
    adapter = make_adapter(tmp_path)
    result = adapter.clear_session("telegram", "99999")
    assert result is False


def test_clear_session_returns_true_for_known_session(tmp_path):
    """clear_session returns True when session existed and was removed."""
    adapter = make_adapter(tmp_path)
    key = "telegram:12345"
    adapter._session_map[key] = "session-abc"
    adapter._save_session_map()

    result = adapter.clear_session("telegram", "12345")

    assert result is True
    assert key not in adapter._session_map


def test_clear_session_removes_loaded_sessions_entry(tmp_path):
    """clear_session also removes the session from _loaded_sessions."""
    adapter = make_adapter(tmp_path)
    key = "feishu:ou_test"
    session_id = "session-xyz"
    adapter._session_map[key] = session_id
    adapter._loaded_sessions.add(session_id)

    adapter.clear_session("feishu", "ou_test")

    assert session_id not in adapter._loaded_sessions


def test_clear_session_removes_rehydrate_history(tmp_path):
    """clear_session also clears pending rehydrate history for that key."""
    adapter = make_adapter(tmp_path)
    key = "feishu:ou_test"
    adapter._session_map[key] = "session-abc"
    adapter._rehydrate_history[key] = "<history_context>old</history_context>"
    adapter._save_session_map()

    adapter.clear_session("feishu", "ou_test")

    assert key not in adapter._rehydrate_history


def test_list_sessions_returns_session_map_copy(tmp_path):
    """list_sessions returns a copy of the current session map dict."""
    adapter = make_adapter(tmp_path)
    adapter._session_map["telegram:1"] = "session-a"
    adapter._session_map["discord:2"] = "session-b"

    sessions = adapter.list_sessions()

    assert sessions == {"telegram:1": "session-a", "discord:2": "session-b"}
    # Confirm it's a copy, not the same object
    sessions["extra"] = "x"
    assert "extra" not in adapter._session_map


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def test_session_map_persists_across_adapter_instances(tmp_path):
    """Session mappings survive an adapter restart via the JSON file."""
    a1 = make_adapter(tmp_path)
    mapping_file = a1._session_map_file
    a1._session_map["telegram:10"] = "session-persist"
    a1._save_session_map()

    # Create a new adapter pointing at the same file
    a2 = StdioACPAdapter(workspace=tmp_path / "workspace")
    a2._session_map_file = mapping_file
    a2._session_map.clear()
    a2._load_session_map()

    assert a2._session_map.get("telegram:10") == "session-persist"


# ---------------------------------------------------------------------------
# Error on missing connection
# ---------------------------------------------------------------------------


async def test_chat_raises_when_not_connected(tmp_path):
    """chat() raises StdioACPConnectionError when client is None."""
    adapter = make_adapter(tmp_path)
    assert adapter._client is None

    with pytest.raises(StdioACPConnectionError):
        await adapter.chat(message="hello", channel="telegram", chat_id="1")


# ---------------------------------------------------------------------------
# Utility methods
# ---------------------------------------------------------------------------


def test_estimate_tokens_zero_for_empty(tmp_path):
    """_estimate_tokens returns 0 for empty string."""
    adapter = make_adapter(tmp_path)
    assert adapter._estimate_tokens("") == 0


def test_estimate_tokens_positive_for_text(tmp_path):
    """_estimate_tokens returns positive integer for non-empty text."""
    adapter = make_adapter(tmp_path)
    result = adapter._estimate_tokens("hello world")
    assert result > 0


def test_clip_text_returns_full_when_under_limit(tmp_path):
    """_clip_text returns original text when under max_chars."""
    adapter = make_adapter(tmp_path)
    assert adapter._clip_text("hello", 10) == "hello"


def test_clip_text_truncates_and_adds_ellipsis(tmp_path):
    """_clip_text truncates to max_chars and appends '...'."""
    adapter = make_adapter(tmp_path)
    result = adapter._clip_text("hello world", 5)
    assert result.endswith("...")
    assert len(result) <= 8  # 5 chars + "..."


def test_is_context_overflow_error_detects_keywords(tmp_path):
    """_is_context_overflow_error returns True for known overflow keywords."""
    adapter = make_adapter(tmp_path)
    assert adapter._is_context_overflow_error("context window too long")
    assert adapter._is_context_overflow_error("TOKEN limit exceeded")
    assert adapter._is_context_overflow_error("max_tokens reached")


def test_is_context_overflow_error_false_for_normal_error(tmp_path):
    """_is_context_overflow_error returns False for unrelated errors."""
    adapter = make_adapter(tmp_path)
    assert not adapter._is_context_overflow_error("network error")
    assert not adapter._is_context_overflow_error("")
