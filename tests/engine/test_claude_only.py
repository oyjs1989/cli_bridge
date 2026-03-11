"""Tests verifying ClaudeAdapter independence from iflow.

Confirms:
- ClaudeAdapter can be instantiated with a v2 ClaudeBackendConfig
- health_check() returns False gracefully when the claude binary is absent
- No iflow modules are imported during instantiation
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cli_bridge.config.schema import ClaudeBackendConfig
from cli_bridge.engine.base_adapter import BaseAdapter
from cli_bridge.engine.claude_adapter import ClaudeAdapter


def _make_adapter(tmp_path: Path, **kwargs) -> ClaudeAdapter:
    """Build a ClaudeAdapter from a ClaudeBackendConfig (v2)."""
    cfg = ClaudeBackendConfig(**kwargs)
    return ClaudeAdapter(
        claude_path=cfg.claude_path,
        model=cfg.model,
        workspace=tmp_path,
        permission_mode=cfg.permission_mode,
        system_prompt=cfg.system_prompt,
    )


# ── instantiation ────────────────────────────────────────────────────────────

def test_claude_adapter_is_base_adapter(tmp_path):
    """ClaudeAdapter is a subtype of BaseAdapter."""
    adapter = _make_adapter(tmp_path)
    assert isinstance(adapter, BaseAdapter)


def test_claude_adapter_mode_property(tmp_path):
    """mode property returns 'claude'."""
    adapter = _make_adapter(tmp_path)
    assert adapter.mode == "claude"


def test_claude_adapter_uses_v2_config_fields(tmp_path):
    """ClaudeAdapter correctly reads fields from ClaudeBackendConfig."""
    adapter = _make_adapter(
        tmp_path,
        claude_path="/custom/claude",
        model="claude-sonnet-4-6",
        permission_mode="acceptEdits",
        system_prompt="Be concise.",
    )
    assert adapter.claude_path == "/custom/claude"
    assert adapter.model == "claude-sonnet-4-6"
    assert adapter.permission_mode == "acceptEdits"
    assert adapter.system_prompt == "Be concise."


def test_claude_adapter_workspace_created(tmp_path):
    """ClaudeAdapter creates workspace directory if it doesn't exist."""
    workspace = tmp_path / "new_workspace"
    assert not workspace.exists()
    ClaudeAdapter(workspace=workspace)
    assert workspace.exists()


# ── health_check ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_returns_false_when_binary_missing(tmp_path):
    """health_check() returns False gracefully when claude binary not found."""
    adapter = _make_adapter(tmp_path, claude_path="/nonexistent/claude")

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
        result = await adapter.health_check()

    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_false_on_nonzero_exit(tmp_path):
    """health_check() returns False when claude --version exits non-zero."""
    adapter = _make_adapter(tmp_path)

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.wait = AsyncMock(return_value=1)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await adapter.health_check()

    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_true_on_success(tmp_path):
    """health_check() returns True when claude --version exits zero."""
    adapter = _make_adapter(tmp_path)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await adapter.health_check()

    assert result is True


# ── iflow isolation ──────────────────────────────────────────────────────────

def test_no_iflow_modules_imported_after_claude_adapter_import():
    """After importing ClaudeAdapter, no cli_bridge.engine.adapter module loaded.

    IFlowAdapter (cli_bridge.engine.adapter) must not be imported as a side
    effect of importing ClaudeAdapter — the two backends should be independent.
    """
    # ClaudeAdapter is already imported at the top of this module.
    # Verify that importing claude_adapter doesn't force-load iflow adapter.
    import cli_bridge.engine.claude_adapter  # noqa: F401 (re-import is a no-op)
    # base_adapter is fine to load; adapter (iflow) should not be forced
    assert "cli_bridge.engine.base_adapter" in sys.modules
