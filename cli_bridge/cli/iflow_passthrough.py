"""iflow 命令透传模块

这个模块用于透传 iflow CLI 的所有命令，确保 cli-bridge 完全兼容 iflow 的功能。

已废弃：本透传命令将在未来版本中移除，请直接使用 iflow CLI。
"""
import typer
from loguru import logger
from rich.console import Console

from cli_bridge.utils.platform import run_command

console = Console()

_DEPRECATION_MSG = (
    "The 'iflow' passthrough command is deprecated and will be removed in a future version. "
    "Use iflow directly instead."
)


def _warn_deprecation() -> None:
    """Emit a deprecation warning via loguru."""
    logger.warning(_DEPRECATION_MSG)


def create_passthrough_app():
    """创建透传 iflow 命令的 Typer 应用"""
    app = typer.Typer(
        name="iflow-passthrough",
        help="Passthrough commands to iflow CLI",
    )

    @app.command("mcp")
    def mcp_passthrough(args: list[str] = typer.Argument(None)):  # noqa: B008
        """管理 MCP 服务器 - 透传到 iflow mcp"""
        _warn_deprecation()
        _run_iflow(["mcp"] + (args or []))

    @app.command("agent")
    def agent_passthrough(args: list[str] = typer.Argument(None)):  # noqa: B008
        """管理代理 - 透传到 iflow agent"""
        _warn_deprecation()
        _run_iflow(["agent"] + (args or []))

    @app.command("workflow")
    def workflow_passthrough(args: list[str] = typer.Argument(None)):  # noqa: B008
        """管理工作流 - 透传到 iflow workflow"""
        _warn_deprecation()
        _run_iflow(["workflow"] + (args or []))

    @app.command("skill")
    def skill_passthrough(args: list[str] = typer.Argument(None)):  # noqa: B008
        """管理技能 - 透传到 iflow skill"""
        _warn_deprecation()
        _run_iflow(["skill"] + (args or []))

    @app.command("commands")
    def commands_passthrough(args: list[str] = typer.Argument(None)):  # noqa: B008
        """管理市场命令 - 透传到 iflow commands"""
        _warn_deprecation()
        _run_iflow(["commands"] + (args or []))

    return app


def _run_iflow(args: list[str]) -> int:
    """执行 iflow 命令并返回退出码"""
    cmd = ["iflow"] + args
    result = run_command(cmd)
    return result.returncode


def run_iflow_interactive() -> None:
    """运行 iflow 交互模式"""
    run_command(["iflow"])
