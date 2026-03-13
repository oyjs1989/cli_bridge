"""Status, session, config, passthrough and onboard commands for cli-bridge."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import typer
from loguru import logger
from rich.table import Table

from cli_bridge.cli._helpers import (
    _OK_MARK,
    console,
    get_config_dir,
    get_config_path,
    get_pid_file,
    init_workspace,
    load_config,
    print_banner,
    process_exists,
)
from cli_bridge.cli.health import check_backend_ready
from cli_bridge.utils.platform import run_command

# ============================================================================
# Status 命令
# ============================================================================


def status() -> None:
    """显示 cli-bridge 状态。"""
    print_banner()

    config = load_config()
    config_path = get_config_path()
    pid_file = get_pid_file()

    # 后端状态（根据 backend 分发到对应的检查器）
    _status_backend = config.driver.backend
    _backend_ready, _backend_msg = asyncio.run(check_backend_ready(_status_backend, config.driver))
    if _status_backend == "claude":
        console.print("[bold]Claude 状态:[/bold]")
        if _backend_ready:
            console.print("  claude CLI: [green]可用[/green]")
        else:
            console.print(f"  claude CLI: [red]不可用[/red] ({_backend_msg})")
    else:
        console.print("[bold]iflow 状态:[/bold]")
        if _backend_ready:
            console.print("  iflow: [green]已安装并已登录[/green]")
        else:
            console.print(f"  iflow: [red]未就绪[/red] ({_backend_msg})")
    console.print()

    # 服务状态
    console.print("[bold]服务状态:[/bold]")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if process_exists(pid):
                console.print(f"  Gateway: [green]运行中[/green] (PID: {pid})")
            else:
                console.print("  Gateway: [red]已停止[/red] (进程不存在)")
        except ValueError:
            console.print("  Gateway: [red]已停止[/red] (无效 PID)")
    else:
        console.print("  Gateway: [dim]未启动[/dim]")

    console.print()

    # 配置信息
    console.print("[bold]配置信息:[/bold]")
    console.print(f"  Config: [cyan]{config_path}[/cyan]")
    console.print(f"  Workspace: [cyan]{config.get_workspace() or 'Not set'}[/cyan]")
    console.print(f"  Backend: [cyan]{config.driver.backend}[/cyan]  Transport: [cyan]{config.driver.transport}[/cyan]")
    console.print(f"  Model: [cyan]{config.get_model()}[/cyan]")
    _mcp_status = config.mcp_proxy.enabled or (config.driver.iflow and config.driver.iflow.mcp_proxy_enabled)
    console.print(f"  MCP Proxy: [cyan]{'启用' if _mcp_status else '禁用'}[/cyan]")
    if config.driver.iflow:
        console.print(f"  Thinking: [cyan]{'启用' if config.driver.iflow.thinking else '禁用'}[/cyan]")
    elif config.driver.claude:
        console.print(f"  Permission Mode: [cyan]{config.driver.claude.permission_mode}[/cyan]")
    console.print()

    # 渠道状态
    enabled_channels = config.get_enabled_channels()
    console.print(f"[bold]启用渠道:[/bold] {', '.join(enabled_channels) or 'None'}")

    # 会话映射
    from cli_bridge.engine.adapter import SessionMappingManager

    mappings = SessionMappingManager().list_all()
    if mappings:
        console.print(f"[bold]会话映射:[/bold] {len(mappings)} 个用户")


# ============================================================================
# Web Console 命令
# ============================================================================


def console_run(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8787, "--port", help="监听端口"),
    token: str = typer.Option("", "--token", help="访问令牌（可选）"),
) -> None:
    """启动 Web 控制台（纯 Python）。"""
    try:
        from cli_bridge.web.server import run_console
    except Exception as e:
        console.print(f"[red]Failed to load web console dependencies: {e}[/red]")
        console.print("[yellow]请安装依赖后重试：pip install fastapi uvicorn jinja2[/yellow]")
        raise typer.Exit(1)

    access_url = f"http://{host}:{port}"
    if token:
        access_url += f"/?token={token}"

    console.print(f"[green]{_OK_MARK}[/green] Web 控制台启动中: [cyan]{access_url}[/cyan]")
    run_console(host=host, port=port, token=token or None)


# ============================================================================
# Sessions 命令
# ============================================================================


def sessions(
    channel: str | None = typer.Option(None, "--channel", "-c", help="过滤渠道"),
    chat_id: str | None = typer.Option(None, "--chat-id", help="过滤聊天ID"),
    clear: bool = typer.Option(False, "--clear", help="清除会话映射"),
) -> None:
    """管理会话映射。"""
    from cli_bridge.engine.adapter import IFlowAdapter

    config = load_config()
    workspace = config.get_workspace()

    iflow_cfg = config.driver.iflow
    adapter = IFlowAdapter(
        default_model=config.get_model(),
        workspace=workspace if workspace else None,
        compression_trigger_tokens=iflow_cfg.compression_trigger_tokens if iflow_cfg else 60000,
        mcp_proxy_port=iflow_cfg.mcp_proxy_port if iflow_cfg else 8888,
        mcp_servers_auto_discover=iflow_cfg.mcp_servers_auto_discover if iflow_cfg else True,
        mcp_servers_max=iflow_cfg.mcp_servers_max if iflow_cfg else 10,
    )
    mappings = adapter.session_mappings

    if clear and channel and chat_id:
        if mappings.clear_session(channel, chat_id):
            console.print(f"[green]{_OK_MARK}[/green] Cleared session for {channel}:{chat_id}")
        else:
            console.print(f"[yellow]No session mapping found for {channel}:{chat_id}[/yellow]")
        return

    # 显示会话映射
    console.print("[bold]会话映射:[/bold]")
    all_mappings = mappings.list_all()

    if not all_mappings:
        console.print("[dim]暂无会话映射[/dim]")
    else:
        table = Table()
        table.add_column("Channel:ChatID", style="cyan")
        table.add_column("Session ID", style="green")

        for key, session_id in all_mappings.items():
            if channel and not key.startswith(f"{channel}:"):
                continue
            if chat_id and chat_id not in key:
                continue
            table.add_row(key, session_id[:30] + "...")

        console.print(table)


# ============================================================================
# Config 命令
# ============================================================================


def config_cmd(
    show: bool = typer.Option(False, "--show", help="显示配置"),
    edit: bool = typer.Option(False, "--edit", "-e", help="编辑配置"),
) -> None:
    """管理配置。"""
    config_path = get_config_path()

    if show:
        if config_path.exists():
            console.print(f"[dim]Config file: {config_path}[/dim]")
            console.print(config_path.read_text())
        else:
            console.print("[yellow]No config file found.[/yellow]")
        return

    if edit:
        editor = os.environ.get("EDITOR", "vim")
        subprocess.run([editor, str(config_path)])
        return

    console.print(f"Config file: [cyan]{config_path}[/cyan]")
    if config_path.exists():
        cfg = load_config()
        console.print(f"Model: [cyan]{cfg.get_model()}[/cyan]")
        console.print(f"Workspace: [cyan]{cfg.get_workspace() or 'Not set'}[/cyan]")
        thinking = cfg.driver.iflow.thinking if cfg.driver and cfg.driver.iflow else False
        console.print(f"Thinking: [cyan]{'启用' if thinking else '禁用'}[/cyan]")


# ============================================================================
# iflow 命令透传
# ============================================================================


def _run_iflow_cmd(cmd: list[str], cwd: Path | None = None) -> int:
    """执行 iflow 命令（跨平台）。"""
    kwargs = {"cwd": str(cwd) if cwd else None}
    result = run_command(cmd, **kwargs)
    return result.returncode


def _warn_passthrough_deprecated(cmd: str) -> None:
    """Emit deprecation warning for a passthrough command (log + console)."""
    msg = (
        f"The 'cli-bridge {cmd}' passthrough command is deprecated and will be "
        f"removed in v1.0.0. Use '{cmd}' directly instead."
    )
    logger.warning(msg)
    console.print(
        f"[yellow]⚠ Deprecated: 'cli-bridge {cmd}' will be removed in v1.0.0. "
        f"Use '{cmd}' directly instead.[/yellow]"
    )


def iflow_passthrough(
    args: list[str] = typer.Argument(None, help="iflow 命令参数"),
) -> None:
    """[DEPRECATED v1.0.0] 透传命令到 iflow CLI。Use 'iflow' directly instead."""
    _warn_passthrough_deprecated("iflow")
    config = load_config()
    workspace = config.get_workspace()

    cmd = ["iflow"] + (args or [])

    cwd = Path(workspace) if workspace else None
    returncode = _run_iflow_cmd(cmd, cwd)
    raise typer.Exit(returncode)


def mcp_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow mcp 命令。Use 'mcp' directly instead."""
    _warn_passthrough_deprecated("mcp")
    cmd = ["iflow", "mcp"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


def agent_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow agent 命令。Use 'agent' directly instead."""
    _warn_passthrough_deprecated("agent")
    cmd = ["iflow", "agent"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


def workflow_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow workflow 命令。Use 'workflow' directly instead."""
    _warn_passthrough_deprecated("workflow")
    cmd = ["iflow", "workflow"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


def skill_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow skill 命令。Use 'skill' directly instead."""
    _warn_passthrough_deprecated("skill")
    cmd = ["iflow", "skill"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


def commands_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow commands 命令。Use 'commands' directly instead."""
    _warn_passthrough_deprecated("commands")
    cmd = ["iflow", "commands"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


# ============================================================================
# Onboard 命令
# ============================================================================


def onboard(
    force: bool = typer.Option(False, "--force", "-f", help="覆盖现有配置"),
) -> None:
    """初始化 cli-bridge 配置。"""
    print_banner()

    config_path = get_config_path()
    config_dir = get_config_dir()

    if config_path.exists() and not force:
        console.print(f"[yellow]配置已存在: {config_path}[/yellow]")
        console.print("使用 [bold]--force[/bold] 覆盖")
        return

    config_dir.mkdir(parents=True, exist_ok=True)

    # 使用 loader 模块中的统一函数创建默认配置
    from cli_bridge.config.loader import _create_default_config

    _create_default_config(config_path)

    # 初始化 workspace
    workspace = Path.home() / ".cli-bridge" / "workspace"
    init_workspace(workspace)

    console.print()
    console.print(f"[green]{_OK_MARK}[/green] 初始化完成!")
    console.print()
    console.print("[bold]配置文件位置:[/bold]")
    console.print(f"  {config_path}")
    console.print()
    console.print("[bold]工作空间位置:[/bold]")
    console.print(f"  {workspace}")
    console.print()
    console.print("[bold]下一步操作:[/bold]")
    console.print()
    console.print("  [yellow]1.[/yellow] 编辑配置文件启用需要的渠道:")
    console.print("     [cyan]~/.cli-bridge/config.json[/cyan]")
    console.print()
    console.print("  [yellow]2.[/yellow] 配置渠道参数:")
    console.print("     • Telegram: 设置 bot token (从 @BotFather 获取)")
    console.print("     • Discord: 设置 bot token (从 Discord Developer Portal 获取)")
    console.print("     • Slack: 设置 bot_token 和 app_token")
    console.print("     • Feishu: 设置 app_id 和 app_secret")
    console.print("     • DingTalk: 设置 client_id 和 client_secret")
    console.print("     • QQ: 设置 app_id 和 secret")
    console.print("     • Email: 设置 IMAP/SMTP 服务器和凭据")
    console.print("     • WhatsApp: 配置 bridge 服务地址")
    console.print("     • Mochat: 设置 claw_token 和 agent_user_id")
    console.print()
    console.print("  [yellow]3.[/yellow] 确保 iflow CLI 已安装并登录:")
    console.print("     [cyan]iflow --version[/cyan]")
    console.print("     [cyan]iflow auth status[/cyan]")
    console.print()
    console.print("  [yellow]4.[/yellow] 启动网关服务:")
    console.print("     [cyan]cli-bridge gateway start[/cyan]")
    console.print()
    console.print("  [yellow]5.[/yellow] 或使用前台模式运行(便于调试):")
    console.print("     [cyan]cli-bridge gateway run[/cyan]")
    console.print()
    console.print("[bold]常用命令:[/bold]")
    console.print("  • [cyan]cli-bridge config[/cyan]    - 查看当前配置")
    console.print("  • [cyan]cli-bridge channels[/cyan]  - 查看渠道状态")
    console.print("  • [cyan]cli-bridge cron list[/cyan] - 查看定时任务")
    console.print("  • [cyan]cli-bridge version[/cyan]   - 查看版本信息")
    console.print()
    console.print("[dim]提示: 使用 --help 查看每个命令的详细用法[/dim]")


# ============================================================================
# MCP 配置同步
# ============================================================================


def mcp_sync(
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="覆盖现有配置"),
) -> None:
    """从 iflow CLI 同步 MCP 服务器配置。

    读取 iflow 的 settings.json，将 MCP 服务器配置复制到 cli-bridge。
    配置会被保存到 ~/.cli-bridge/config/.mcp_proxy_config.json
    """
    from cli_bridge.utils.helpers import sync_mcp_from_iflow

    console.print("[cyan]正在从 iflow CLI 同步 MCP 配置...[/cyan]")

    if sync_mcp_from_iflow(overwrite=overwrite):
        console.print(f"[green]{_OK_MARK}[/green] MCP 配置同步成功")
        console.print("[dim]配置文件：~/.cli-bridge/config/.mcp_proxy_config.json[/dim]")
        console.print("[dim]重启网关使配置生效：cli-bridge gateway restart[/dim]")
    else:
        console.print("[yellow]MCP 配置同步失败或无需同步[/yellow]")
