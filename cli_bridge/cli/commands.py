"""CLI commands for cli-bridge.

命令结构:
- cli-bridge gateway start   # 后台启动服务
- cli-bridge gateway run     # 前台运行（debug模式）
- cli-bridge gateway restart # 重启服务
- cli-bridge gateway stop    # 停止服务
- cli-bridge status          # 查看服务状态
- cli-bridge model <name>    # 切换模型
- cli-bridge thinking on/off # 思考模式开关
- cli-bridge iflow <args>    # iflow 命令透传
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from cli_bridge.utils.helpers import configure_logging

configure_logging()

from cli_bridge.cli._cron_commands import cron_app
from cli_bridge.cli._gateway_commands import _run_gateway, gateway_app
from cli_bridge.cli._helpers import (
    __logo__,
    __version__,
    console,
    get_config_path,
    load_config,
)
from cli_bridge.cli._model_commands import model, thinking
from cli_bridge.cli._status_commands import (
    agent_passthrough,
    commands_passthrough,
    config_cmd,
    console_run,
    iflow_passthrough,
    mcp_passthrough,
    mcp_sync,
    onboard,
    sessions,
    skill_passthrough,
    status,
    workflow_passthrough,
)

# ============================================================================
# 主命令
# ============================================================================

app = typer.Typer(
    name="cli-bridge",
    help=f"{__logo__} cli-bridge - Multi-channel AI Assistant (powered by Claude)",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool):
    if value:
        console.print(f"{__logo__} cli-bridge v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", is_eager=True, callback=_version_callback
    ),
) -> None:
    """cli-bridge - 多渠道 AI 助手（基于 iflow）。"""
    pass


@app.command()
def version():
    """查看版本信息。"""
    console.print(f"[bold cyan]{__logo__}[/bold cyan] cli-bridge [green]v{__version__}[/green]")
    console.print()
    console.print(f"  Python:     {sys.version.split()[0]}")
    console.print(f"  Platform:   {sys.platform}")
    console.print(f"  Config:     {get_config_path()}")
    console.print(f"  Workspace:  {Path.home() / '.cli-bridge' / 'workspace'}")
    console.print()


# ============================================================================
# Sub-app registrations
# ============================================================================

app.add_typer(gateway_app, name="gateway")
app.add_typer(cron_app, name="cron")


# Internal hidden command — invoked as subprocess by gateway_start
@app.command("_run_gateway", hidden=True)
def _run_gateway_cmd():
    """内部命令：运行 Gateway。"""
    config = load_config()
    asyncio.run(_run_gateway(config))


# ============================================================================
# Command registrations from sub-modules
# ============================================================================

app.command()(status)
app.command(name="console")(console_run)
app.command()(model)
app.command()(thinking)
app.command()(sessions)
app.command()(config_cmd)
app.command(name="config")(config_cmd)
app.command(name="iflow")(iflow_passthrough)
app.command(name="mcp")(mcp_passthrough)
app.command(name="agent")(agent_passthrough)
app.command(name="workflow")(workflow_passthrough)
app.command(name="skill")(skill_passthrough)
app.command(name="commands")(commands_passthrough)
app.command()(onboard)
app.command()(mcp_sync)

if __name__ == "__main__":
    app()
