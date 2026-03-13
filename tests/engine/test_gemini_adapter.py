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
