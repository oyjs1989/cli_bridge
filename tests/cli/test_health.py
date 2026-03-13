"""Tests for backend health checks."""
import pytest
from unittest.mock import patch
from cli_bridge.cli.health import check_backend_ready
from cli_bridge.config.schema import DriverConfig, GeminiBackendConfig


async def test_check_backend_ready_gemini_not_found():
    with patch("cli_bridge.cli.health.shutil.which", return_value=None):
        ok, msg = await check_backend_ready(
            "gemini", DriverConfig(backend="gemini")
        )
    assert ok is False
    assert "gemini" in msg.lower()


async def test_check_backend_ready_gemini_found():
    with patch("cli_bridge.cli.health.shutil.which", return_value="/usr/local/bin/gemini"):
        ok, msg = await check_backend_ready(
            "gemini", DriverConfig(backend="gemini")
        )
    assert ok is True
