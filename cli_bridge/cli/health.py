"""Backend health-check dispatcher.

Decouples health checking from the main commands module so that only
the checks relevant to the active mode are executed.

Usage::

    from cli_bridge.cli.health import check_backend_ready

    ready, message = await check_backend_ready(config.driver.mode, config.driver)
    if not ready:
        console.print(f"[red]{message}[/red]")
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from cli_bridge.config.schema import DriverConfig


async def _check_claude_ready(claude_path: str) -> tuple[bool, str]:
    """Check that the claude CLI binary is findable on PATH.

    Uses shutil.which() instead of running --version to avoid hanging in
    environments where claude performs network requests on startup.
    """
    # Resolve absolute path or search PATH
    resolved = shutil.which(claude_path)
    if resolved:
        return True, f"claude CLI is available at {resolved}."
    # Also accept if claude_path is an absolute path that exists
    if Path(claude_path).is_file():
        return True, f"claude CLI is available at {claude_path}."
    return False, (
        f"claude CLI not found (searched PATH for '{claude_path}'). "
        "Install Claude Code: https://claude.ai/code"
    )


def _check_iflow_installed(iflow_path: str) -> bool:
    """Return True if iflow binary exits zero on --version."""
    try:
        result = subprocess.run(
            [iflow_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _check_iflow_logged_in() -> bool:
    """Heuristic: check if iflow config dir contains login artifacts."""
    iflow_config_dir = Path.home() / ".iflow"
    if not iflow_config_dir.exists():
        return False
    projects_dir = iflow_config_dir / "projects"
    if projects_dir.exists() and list(projects_dir.iterdir()):
        return True
    try:
        result = subprocess.run(
            ["iflow", "-p", "test"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout + result.stderr).lower()
        if "login" in output or "please login" in output:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


async def _check_iflow_ready(iflow_path: str) -> tuple[bool, str]:
    """Check that iflow is installed and logged in."""
    if not _check_iflow_installed(iflow_path):
        return False, (
            f"iflow not found at '{iflow_path}'. "
            "Install: npm install -g @iflow-ai/iflow-cli@latest"
        )
    if not _check_iflow_logged_in():
        return False, "iflow is installed but not logged in. Run: iflow login"
    return True, "iflow is available and logged in."


async def check_backend_ready(
    mode: str,
    driver: DriverConfig | None = None,
) -> tuple[bool, str]:
    """Dispatch to the appropriate backend health check.

    Args:
        mode: Active driver mode ('claude', 'cli', 'stdio', 'acp').
        driver: DriverConfig instance (used to resolve binary paths).

    Returns:
        Tuple of (is_ready: bool, message: str).
    """
    if mode == "claude":
        claude_path = "claude"
        if driver and driver.claude:
            claude_path = driver.claude.claude_path
        logger.debug(f"Health check: claude mode, binary={claude_path}")
        return await _check_claude_ready(claude_path)
    else:
        iflow_path = "iflow"
        if driver and driver.iflow:
            iflow_path = driver.iflow.iflow_path
        logger.debug(f"Health check: iflow mode={mode}, binary={iflow_path}")
        return await _check_iflow_ready(iflow_path)
