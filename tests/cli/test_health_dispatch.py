"""Tests for check_backend_ready health dispatch."""

from unittest.mock import AsyncMock, patch

import pytest

from cli_bridge.cli.health import check_backend_ready
from cli_bridge.config.schema import DriverConfig

# ── mode dispatch ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claude_mode_calls_check_claude_ready_not_iflow():
    """check_backend_ready with mode='claude' calls _check_claude_ready only."""
    driver = DriverConfig(mode="claude")

    with (
        patch("cli_bridge.cli.health._check_claude_ready", new_callable=AsyncMock) as mock_claude,
        patch("cli_bridge.cli.health._check_iflow_ready", new_callable=AsyncMock) as mock_iflow,
    ):
        mock_claude.return_value = (True, "ok")
        await check_backend_ready("claude", driver)

    mock_claude.assert_called_once()
    mock_iflow.assert_not_called()


@pytest.mark.asyncio
async def test_cli_mode_calls_check_iflow_ready_not_claude():
    """check_backend_ready with mode='cli' calls _check_iflow_ready only."""
    driver = DriverConfig(mode="cli")

    with (
        patch("cli_bridge.cli.health._check_claude_ready", new_callable=AsyncMock) as mock_claude,
        patch("cli_bridge.cli.health._check_iflow_ready", new_callable=AsyncMock) as mock_iflow,
    ):
        mock_iflow.return_value = (True, "ok")
        await check_backend_ready("cli", driver)

    mock_iflow.assert_called_once()
    mock_claude.assert_not_called()


@pytest.mark.asyncio
async def test_stdio_mode_calls_check_iflow_ready_not_claude():
    """check_backend_ready with mode='stdio' calls _check_iflow_ready only."""
    driver = DriverConfig(mode="stdio")

    with (
        patch("cli_bridge.cli.health._check_claude_ready", new_callable=AsyncMock) as mock_claude,
        patch("cli_bridge.cli.health._check_iflow_ready", new_callable=AsyncMock) as mock_iflow,
    ):
        mock_iflow.return_value = (True, "ok")
        await check_backend_ready("stdio", driver)

    mock_iflow.assert_called_once()
    mock_claude.assert_not_called()


@pytest.mark.asyncio
async def test_acp_mode_calls_check_iflow_ready_not_claude():
    """check_backend_ready with mode='acp' calls _check_iflow_ready only."""
    driver = DriverConfig(mode="acp")

    with (
        patch("cli_bridge.cli.health._check_claude_ready", new_callable=AsyncMock) as mock_claude,
        patch("cli_bridge.cli.health._check_iflow_ready", new_callable=AsyncMock) as mock_iflow,
    ):
        mock_iflow.return_value = (True, "ok")
        await check_backend_ready("acp", driver)

    mock_iflow.assert_called_once()
    mock_claude.assert_not_called()


# ── return value propagation ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_backend_ready_propagates_failure():
    """Returns (False, message) from the underlying check on failure."""
    driver = DriverConfig(mode="claude")

    with patch("cli_bridge.cli.health._check_claude_ready", new_callable=AsyncMock) as mock_claude:
        mock_claude.return_value = (False, "binary not found")
        ready, message = await check_backend_ready("claude", driver)

    assert ready is False
    assert "binary not found" in message


@pytest.mark.asyncio
async def test_check_backend_ready_propagates_success():
    """Returns (True, message) from the underlying check on success."""
    driver = DriverConfig(mode="stdio")

    with patch("cli_bridge.cli.health._check_iflow_ready", new_callable=AsyncMock) as mock_iflow:
        mock_iflow.return_value = (True, "iflow is available and logged in.")
        ready, message = await check_backend_ready("stdio", driver)

    assert ready is True


# ── binary path forwarding ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claude_mode_passes_configured_binary_path():
    """check_backend_ready forwards claude_path from config to _check_claude_ready."""
    driver = DriverConfig(mode="claude")
    driver.claude.claude_path = "/opt/claude/bin/claude"

    with patch("cli_bridge.cli.health._check_claude_ready", new_callable=AsyncMock) as mock_claude:
        mock_claude.return_value = (True, "ok")
        await check_backend_ready("claude", driver)

    mock_claude.assert_called_once_with("/opt/claude/bin/claude")


@pytest.mark.asyncio
async def test_iflow_mode_passes_configured_binary_path():
    """check_backend_ready forwards iflow_path from config to _check_iflow_ready."""
    driver = DriverConfig(mode="cli")
    driver.iflow.iflow_path = "/usr/local/bin/iflow"

    with patch("cli_bridge.cli.health._check_iflow_ready", new_callable=AsyncMock) as mock_iflow:
        mock_iflow.return_value = (True, "ok")
        await check_backend_ready("cli", driver)

    mock_iflow.assert_called_once_with("/usr/local/bin/iflow")
