"""Tests for ConsoleService.list_chat_targets() ChannelRecorder fallback."""

import json
from pathlib import Path


def _make_service(tmp_path: Path):
    """Create a ConsoleService with its internal directories pointing to tmp_path."""
    from cli_bridge.web.server import ConsoleService

    service = ConsoleService.__new__(ConsoleService)
    service.home_dir = tmp_path
    service.channel_dir = tmp_path / "workspace" / "channel"
    service.channel_dir.mkdir(parents=True, exist_ok=True)
    service.config_path = tmp_path / "config.json"
    service.pid_file = tmp_path / "gateway.pid"
    service.log_file = tmp_path / "gateway.log"
    service._chat_adapter = None
    service._web_chat_messages = {}

    # Patch _session_mapping_file and _acp_session_file to use tmp_path
    service._session_mapping_file = lambda: tmp_path / "session_mappings.json"
    service._acp_session_file = lambda sid: tmp_path / "sessions" / f"{sid}.json"
    service._chat_targets_meta_file = lambda: tmp_path / "chat_targets_meta.json"
    return service


def _write_session_mappings(tmp_path: Path, mappings: dict) -> None:
    p = tmp_path / "session_mappings.json"
    p.write_text(json.dumps(mappings), encoding="utf-8")


def _write_acp_session(tmp_path: Path, session_id: str, messages: list) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    session_file = sessions_dir / f"{session_id}.json"
    history = [
        {"role": "user", "parts": [{"text": m}]} if i % 2 == 0
        else {"role": "assistant", "parts": [{"text": m}]}
        for i, m in enumerate(messages)
    ]
    session_file.write_text(json.dumps({"chatHistory": history}), encoding="utf-8")


def _write_recorder_file(channel_dir: Path, channel: str, chat_id: str, messages: list) -> Path:
    ch_dir = channel_dir / channel
    ch_dir.mkdir(parents=True, exist_ok=True)
    rec_file = ch_dir / f"{chat_id}-2026-03-11.json"
    msg_data = [
        {"direction": "inbound" if i % 2 == 0 else "outbound", "content": m}
        for i, m in enumerate(messages)
    ]
    rec_file.write_text(json.dumps({"messages": msg_data}), encoding="utf-8")
    return rec_file


# ── Existing behavior (ACP session file present) ──────────────────────────────


def test_list_chat_targets_acp_file_exists(tmp_path):
    """Returns updated_at from ACP session file when it exists."""
    service = _make_service(tmp_path)
    _write_session_mappings(tmp_path, {"telegram:100": "sess-acp"})
    _write_acp_session(tmp_path, "sess-acp", ["Hello", "Hi there"])

    rows = service.list_chat_targets()
    assert len(rows) == 1
    row = rows[0]
    assert row["channel"] == "telegram"
    assert row["chat_id"] == "100"
    assert row["session_id"] == "sess-acp"
    assert row["updated_at"] != ""
    assert "Hi there" in row["preview"]


# ── ChannelRecorder fallback when ACP file is absent (Claude backend) ─────────


def test_list_chat_targets_updated_at_fallback_from_recorder(tmp_path):
    """updated_at comes from recorder file mtime when ACP session file is absent."""
    service = _make_service(tmp_path)
    _write_session_mappings(tmp_path, {"slack:200": "sess-claude"})
    # No ACP session file — sessions dir is empty
    _write_recorder_file(service.channel_dir, "slack", "200", ["Question", "Answer"])

    rows = service.list_chat_targets()
    assert len(rows) == 1
    row = rows[0]
    assert row["channel"] == "slack"
    assert row["updated_at"] != "", "updated_at should come from recorder mtime"


def test_list_chat_targets_preview_fallback_from_recorder(tmp_path):
    """preview comes from last recorder message when ACP session file is absent."""
    service = _make_service(tmp_path)
    _write_session_mappings(tmp_path, {"slack:200": "sess-claude"})
    _write_recorder_file(service.channel_dir, "slack", "200", ["Question", "The final answer"])

    rows = service.list_chat_targets()
    assert len(rows) == 1
    row = rows[0]
    assert "final answer" in row["preview"], f"Expected recorder preview, got: {row['preview']}"


# ── Neither file exists ───────────────────────────────────────────────────────


def test_list_chat_targets_empty_when_neither_file_exists(tmp_path):
    """updated_at is empty and preview is '(empty)' when neither ACP nor recorder files exist."""
    service = _make_service(tmp_path)
    _write_session_mappings(tmp_path, {"discord:300": "sess-none"})
    # No ACP session file, no recorder files

    rows = service.list_chat_targets()
    assert len(rows) == 1
    row = rows[0]
    assert row["updated_at"] == ""
    assert row["preview"] == "(empty)"


def test_list_chat_targets_empty_session_mappings(tmp_path):
    """Returns empty list when session_mappings.json is absent."""
    service = _make_service(tmp_path)
    # Don't write session_mappings.json

    rows = service.list_chat_targets()
    assert rows == []
