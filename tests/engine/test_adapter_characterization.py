"""Characterization tests for cli_bridge.engine.adapter.SessionMappingManager.

These tests lock the observable behavior of SessionMappingManager BEFORE any
refactoring. They must pass GREEN on unmodified source code.

Invariants captured:
- Default persistence file is ~/.cli-bridge/session_mappings.json
- get_session_id returns None when no mapping exists for a key
- set_session_id + get_session_id round-trips correctly for the same key
- Calling get_session_id twice with the same key returns the same stable ID
- Mapping persists across SessionMappingManager instances (file-backed)
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_bridge.engine.adapter import SessionMappingManager


def test_default_mapping_file_is_under_cli_bridge():
    """SessionMappingManager default file is ~/.cli-bridge/session_mappings.json."""
    mgr = SessionMappingManager()
    expected = Path.home() / ".cli-bridge" / "session_mappings.json"
    assert mgr.mapping_file == expected


def test_get_session_id_returns_none_for_unknown_key(tmp_path):
    """get_session_id returns None when no mapping exists for the given key."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    result = mgr.get_session_id("telegram", "12345")

    assert result is None


def test_set_then_get_returns_same_session_id(tmp_path):
    """After set_session_id, get_session_id returns the same session ID."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    mgr.set_session_id("telegram", "12345", "session-abc123")

    result = mgr.get_session_id("telegram", "12345")
    assert result == "session-abc123"


def test_get_session_id_is_stable_across_multiple_calls(tmp_path):
    """Calling get_session_id twice with the same key returns the same stable ID."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    mgr.set_session_id("telegram", "12345", "session-stable-xyz")

    first = mgr.get_session_id("telegram", "12345")
    second = mgr.get_session_id("telegram", "12345")
    assert first == second == "session-stable-xyz"


def test_mapping_persists_across_manager_instances(tmp_path):
    """Session mappings survive re-creating SessionMappingManager from same file."""
    mapping_file = tmp_path / "session_mappings.json"

    mgr1 = SessionMappingManager(mapping_file=mapping_file)
    mgr1.set_session_id("telegram", "12345", "session-persistent")

    # Create a fresh instance pointing to the same file
    mgr2 = SessionMappingManager(mapping_file=mapping_file)

    result = mgr2.get_session_id("telegram", "12345")
    assert result == "session-persistent"


def test_mapping_file_is_valid_json(tmp_path):
    """The persistence file contains valid JSON after set_session_id."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    mgr.set_session_id("discord", "789012", "session-def456")

    content = mapping_file.read_text(encoding="utf-8")
    data = json.loads(content)
    assert data.get("discord:789012") == "session-def456"


def test_different_channel_chat_id_pairs_are_independent(tmp_path):
    """Different channel:chat_id pairs maintain independent session IDs."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    mgr.set_session_id("telegram", "111", "session-A")
    mgr.set_session_id("discord", "111", "session-B")
    mgr.set_session_id("telegram", "222", "session-C")

    assert mgr.get_session_id("telegram", "111") == "session-A"
    assert mgr.get_session_id("discord", "111") == "session-B"
    assert mgr.get_session_id("telegram", "222") == "session-C"


def test_clear_session_removes_mapping(tmp_path):
    """clear_session removes the mapping and returns True if it existed."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    mgr.set_session_id("telegram", "12345", "session-to-clear")
    cleared = mgr.clear_session("telegram", "12345")

    assert cleared is True
    assert mgr.get_session_id("telegram", "12345") is None


def test_clear_session_returns_false_for_nonexistent(tmp_path):
    """clear_session returns False when no mapping exists for the key."""
    mapping_file = tmp_path / "session_mappings.json"
    mgr = SessionMappingManager(mapping_file=mapping_file)

    result = mgr.clear_session("telegram", "99999")

    assert result is False
