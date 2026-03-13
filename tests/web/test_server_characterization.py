"""Characterization tests for web/server.py.

Captures current behavior of:
1. The session browser endpoint (/api/chat/history) — returns expected JSON shape
2. ConsoleService.get_gateway_status() — returns {"running": bool, "pid": ...}
3. The dashboard endpoint (/) — returns 200 HTML

These tests must be GREEN on unmodified server.py.
After refactoring, they must still be GREEN (behavior preserved).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(tmp_path: Path):
    """Create a ConsoleService with internal dirs wired to tmp_path."""
    from cli_bridge.web.server import ConsoleService

    service = ConsoleService.__new__(ConsoleService)
    service.home_dir = tmp_path
    service.channel_dir = tmp_path / "workspace" / "channel"
    service.channel_dir.mkdir(parents=True, exist_ok=True)
    service.config_path = tmp_path / "config.json"
    service.pid_file = tmp_path / "gateway.pid"
    service.log_file = tmp_path / "gateway.log"
    service._chat_adapter = None
    service._chat_lock = asyncio.Lock()
    service._web_chat_messages = {}
    service._web_log_seq = 0
    from collections import deque
    service._web_logs = deque(maxlen=3000)
    service._session_mapping_file = lambda: tmp_path / "session_mappings.json"
    service._acp_session_file = lambda sid: tmp_path / "sessions" / f"{sid}.json"
    service._chat_targets_meta_file = lambda: tmp_path / "chat_targets_meta.json"
    return service


def _make_client(token: str | None = None) -> TestClient:
    """Create a TestClient for the web app (no token by default)."""
    from cli_bridge.web.server import create_app
    app = create_app(token=token)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# ConsoleService.get_gateway_status() — shape characterization
# ---------------------------------------------------------------------------


def test_gateway_status_shape_when_no_pid_file(tmp_path):
    """get_gateway_status() returns dict with 'running' and 'pid' keys even when pid file absent."""
    service = _make_service(tmp_path)

    result = service.get_gateway_status()

    assert isinstance(result, dict), "should return a dict"
    assert "running" in result, "should have 'running' key"
    assert "pid" in result, "should have 'pid' key"
    assert result["running"] is False, "running should be False when no pid file"
    assert result["pid"] is None, "pid should be None when no pid file"


def test_gateway_status_running_false_when_pid_file_has_dead_process(tmp_path):
    """get_gateway_status() returns running=False for non-existent PID."""
    service = _make_service(tmp_path)
    # PID 1 always exists on Linux; use PID 0 which is invalid
    pid_file = tmp_path / "gateway.pid"
    pid_file.write_text("999999999", encoding="utf-8")  # Very unlikely to exist

    result = service.get_gateway_status()

    assert isinstance(result, dict)
    assert "running" in result
    assert "pid" in result
    assert result["pid"] == 999999999


# ---------------------------------------------------------------------------
# HTTP: /api/chat/history — session browser endpoint JSON shape
# ---------------------------------------------------------------------------


def test_chat_history_endpoint_returns_expected_json_shape():
    """/api/chat/history returns JSON with ok, channel, chat_id, messages keys."""
    client = _make_client()

    response = client.get("/api/chat/history?channel=web&chat_id=test-session-abc")

    assert response.status_code == 200
    data = response.json()
    assert "ok" in data, "response must have 'ok' key"
    assert data["ok"] is True, "'ok' should be True on success"
    assert "channel" in data, "response must have 'channel' key"
    assert "chat_id" in data, "response must have 'chat_id' key"
    assert "messages" in data, "response must have 'messages' key"
    assert isinstance(data["messages"], list), "'messages' should be a list"


def test_chat_history_endpoint_returns_correct_channel_and_chat_id():
    """/api/chat/history echoes back the channel and chat_id parameters."""
    client = _make_client()

    response = client.get("/api/chat/history?channel=telegram&chat_id=12345")

    assert response.status_code == 200
    data = response.json()
    assert data["channel"] == "telegram"
    assert data["chat_id"] == "12345"


def test_chat_history_endpoint_defaults_channel_to_web():
    """/api/chat/history defaults channel to 'web' when not specified."""
    client = _make_client()

    response = client.get("/api/chat/history")

    assert response.status_code == 200
    data = response.json()
    assert data["channel"] == "web"


def test_chat_history_endpoint_returns_empty_messages_for_unknown_session():
    """/api/chat/history returns empty messages list for a new/unknown session."""
    client = _make_client()

    response = client.get("/api/chat/history?channel=web&chat_id=nonexistent-session-xyz")

    assert response.status_code == 200
    data = response.json()
    assert data["messages"] == []


# ---------------------------------------------------------------------------
# HTTP: / (dashboard) — returns 200
# ---------------------------------------------------------------------------


def test_dashboard_returns_200():
    """GET / returns HTTP 200."""
    client = _make_client()

    response = client.get("/")

    assert response.status_code == 200


def test_dashboard_returns_html():
    """GET / returns HTML content."""
    client = _make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# HTTP: Token protection
# ---------------------------------------------------------------------------


def test_token_protected_endpoint_rejects_without_token():
    """Endpoints reject requests without token when token is configured."""
    client = _make_client(token="secret123")

    response = client.get("/api/chat/history")

    assert response.status_code == 401


def test_token_protected_endpoint_accepts_correct_token():
    """Endpoints accept requests with correct token in header."""
    client = _make_client(token="secret123")

    response = client.get(
        "/api/chat/history",
        headers={"x-cli-bridge-console-token": "secret123"},
    )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# HTTP: /api/mcp/status — returns expected JSON shape
# ---------------------------------------------------------------------------


def test_mcp_status_endpoint_returns_json_with_ok_key():
    """/api/mcp/status returns JSON with 'ok' key."""
    client = _make_client()

    response = client.get("/api/mcp/status")

    assert response.status_code == 200
    data = response.json()
    assert "ok" in data, "mcp status must have 'ok' key"
    assert data["ok"] is True
    assert "data" in data, "mcp status must have 'data' key"
