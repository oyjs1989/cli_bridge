"""Gateway sub-commands for cli-bridge."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer
from loguru import logger

from cli_bridge.cli._helpers import (
    _OK_MARK,
    console,
    get_config_dir,
    get_data_dir,
    get_log_file,
    get_pid_file,
    init_workspace,
    load_config,
    print_banner,
    process_exists,
)
from cli_bridge.cli.health import check_backend_ready
from cli_bridge.utils.platform import (
    is_windows,
    prepare_subprocess_command,
)

# ============================================================================
# Gateway 命令组
# ============================================================================

gateway_app = typer.Typer(help="Gateway 服务管理")


@gateway_app.callback()
def gateway_callback():
    """Gateway 服务管理命令。"""
    pass


# ============================================================================
# MCP 代理辅助函数
# ============================================================================


def check_mcp_proxy_running(port: int = 8888) -> bool:
    """检查 MCP 代理是否运行。"""
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("localhost", port))
        sock.close()
        return result == 0
    except Exception:
        return False


def get_mcp_proxy_pid_file() -> Path:
    """MCP 代理 PID 文件路径（统一到 ~/.cli-bridge）。"""
    return get_config_dir() / "mcp_proxy.pid"


def get_mcp_proxy_log_file() -> Path:
    """MCP 代理日志文件路径（统一到 ~/.cli-bridge）。"""
    return get_config_dir() / "mcp_proxy.log"


def _resolve_mcp_proxy_config_file() -> Path | None:
    """解析 MCP 代理配置文件路径。"""
    env_config = os.environ.get("MCP_PROXY_CONFIG", "").strip()
    if env_config:
        env_path = Path(env_config).expanduser()
        if env_path.exists():
            return env_path

    runtime_config = get_config_dir() / "config" / ".mcp_proxy_config.json"
    if runtime_config.exists():
        return runtime_config

    project_config = Path(__file__).parent.parent.parent / "config" / ".mcp_proxy_config.json"
    if project_config.exists():
        return project_config

    return None


def stop_mcp_proxy() -> bool:
    """停止 MCP 代理（如果在运行）。"""
    pid_file = get_mcp_proxy_pid_file()
    if not pid_file.exists():
        return True

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return False

    try:
        if is_windows():
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def start_mcp_proxy(port: int = 8888) -> bool:
    """启动 MCP 代理服务器（跨平台，不依赖 bash 脚本）。"""
    try:
        config_file = _resolve_mcp_proxy_config_file()
        if not config_file:
            console.print(
                "[yellow]MCP 代理配置文件不存在: ~/.cli-bridge/config/.mcp_proxy_config.json[/yellow]"
            )
            return False

        pid_file = get_mcp_proxy_pid_file()
        log_file = get_mcp_proxy_log_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)

        # 清理失效 PID 文件
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text(encoding="utf-8").strip())
                if process_exists(old_pid):
                    return True
            except Exception:
                pass
            pid_file.unlink(missing_ok=True)

        with open(log_file, "a", encoding="utf-8") as log_f:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cli_bridge.mcp_proxy",
                    "--config",
                    str(config_file),
                    "--port",
                    str(port),
                ],
                stdout=log_f,
                stderr=log_f,
                start_new_session=not is_windows(),
            )

        pid_file.write_text(str(process.pid), encoding="utf-8")

        # 同步等待端口就绪（避免在 async 上下文中使用 asyncio.run）
        for _ in range(10):
            if check_mcp_proxy_running(port):
                return True
            time.sleep(1)
        return False
    except Exception as e:
        console.print(f"[yellow]启动 MCP 代理失败: {e}[/yellow]")
        return False


# ============================================================================
# ACP 服务辅助函数
# ============================================================================


async def _start_acp_server(port: int = 8090) -> asyncio.subprocess.Process | None:
    """启动 iflow ACP 服务。

    如果端口已被占用，则复用现有进程。

    执行: iflow --experimental-acp --stream --port {port}

    Args:
        port: ACP 服务端口

    Returns:
        成功返回进程对象，复用现有进程返回 None
    """
    import socket

    # 检查端口是否已被占用
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("localhost", port))
    sock.close()

    if result == 0:
        # 端口已被占用，复用现有进程
        print(f"ACP 服务已在运行 (端口 {port})，复用现有进程")
        return None

    try:
        prepared = prepare_subprocess_command(
            ["iflow", "--experimental-acp", "--stream", "--port", str(port)]
        )
        process = await asyncio.create_subprocess_exec(
            *prepared,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=not is_windows(),
        )

        # 等待服务启动
        await asyncio.sleep(2)

        # 检查进程是否还在运行
        if process.returncode is not None:
            stderr = await process.stderr.read()
            logger.error(f"ACP server failed to start: {stderr.decode()}")
            return None

        return process
    except FileNotFoundError:
        logger.error("iflow command not found")
        return None
    except Exception as e:
        logger.error(f"Failed to start ACP server: {e}")
        return None


async def _stop_acp_server(process: asyncio.subprocess.Process) -> None:
    """停止 ACP 服务进程。"""
    if process.returncode is None:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        except Exception as e:
            logger.warning(f"Error stopping ACP server: {e}")


# ============================================================================
# Gateway 命令
# ============================================================================


@gateway_app.command("start")
def gateway_start(
    daemon: bool = typer.Option(True, "--daemon/--no-daemon", "-d/-D", help="后台运行"),
    with_mcp: bool | None = typer.Option(
        None, "--with-mcp/--without-mcp", help="是否启动 MCP 代理（覆盖配置文件）"
    ),
) -> None:
    """后台启动 Gateway 服务。"""
    print_banner()

    # 加载配置
    config = load_config()
    _backend = config.driver.backend

    # 检查并启动 MCP 代理
    # 优先级：命令行参数 > 统一配置 > iflow 专属配置
    _unified_mcp = config.mcp_proxy.enabled
    _iflow_mcp_enabled = bool(config.driver.iflow and config.driver.iflow.mcp_proxy_enabled)
    _iflow_mcp_auto = bool(config.driver.iflow and config.driver.iflow.mcp_proxy_auto_start)

    # Conflict warning: both unified and iflow-specific configs are set
    if _unified_mcp and _iflow_mcp_enabled:
        logger.warning(
            "Both mcp_proxy (unified) and driver.iflow.mcp_proxy_enabled are set; "
            "unified config takes precedence. Remove driver.iflow.mcp_proxy_enabled to silence this warning."
        )

    should_start_mcp = (
        with_mcp
        if with_mcp is not None
        else (_unified_mcp or (_iflow_mcp_enabled and _iflow_mcp_auto))
    )
    mcp_port = (
        config.mcp_proxy.port if _unified_mcp
        else (config.driver.iflow.mcp_proxy_port if config.driver.iflow else 8888)
    )

    if _backend == "claude" and should_start_mcp:
        logger.info("Claude backend: MCP servers will be passed directly via --mcp-config (no HTTP proxy)")
    elif _backend != "claude" and should_start_mcp:
        if not check_mcp_proxy_running(mcp_port):
            console.print(f"[cyan]正在启动 MCP 代理 (端口: {mcp_port})...[/cyan]")
            if start_mcp_proxy(mcp_port):
                console.print(f"[green]{_OK_MARK}[/green] MCP 代理已启动")
            else:
                console.print("[yellow]MCP 代理启动失败，将继续运行网关[/yellow]")
        else:
            console.print(f"[green]{_OK_MARK}[/green] MCP 代理已在运行 (端口: {mcp_port})")
        console.print()

    # Startup audit log
    _mcp_enabled = _unified_mcp or _iflow_mcp_enabled
    logger.bind(
        event="gateway_startup",
        backend=config.driver.backend,
        transport=config.driver.transport,
        mcp_enabled=_mcp_enabled,
    ).info(
        f"Gateway started: backend={config.driver.backend} transport={config.driver.transport}"
    )

    # 检查后端是否就绪（根据 backend 分发到对应的检查器）
    _ready, _ready_msg = asyncio.run(check_backend_ready(_backend, config.driver))
    if not _ready:
        console.print(f"[red]{_ready_msg}[/red]")
        raise typer.Exit(1)

    config = load_config()
    workspace = Path(config.get_workspace())

    # 初始化 workspace
    init_workspace(workspace, backend=config.driver.backend)

    # 检查是否已运行
    pid_file = get_pid_file()
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if process_exists(pid):
                console.print(f"[yellow]Gateway already running (PID: {pid})[/yellow]")
                console.print("Use [cyan]cli-bridge gateway restart[/cyan] to restart")
                return
        except ValueError:
            pass

    enabled_channels = config.get_enabled_channels()
    if not enabled_channels:
        console.print("[yellow]No channels are enabled in the configuration.[/yellow]")
        console.print("Edit [cyan]~/.cli-bridge/config.json[/cyan] to enable channels.")
        return

    console.print(f"[bold]启动渠道网关:[/bold] {', '.join(enabled_channels)}")
    console.print(f"[bold]Workspace:[/bold] {workspace}")
    console.print(f"[bold]Model:[/bold] {config.get_model()}")
    console.print()

    if daemon:
        # 后台启动
        log_file = get_log_file()
        cmd = [sys.executable, "-m", "cli_bridge.cli.commands", "_run_gateway"]

        with open(log_file, "w") as log_f:
            process = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                start_new_session=True,
            )

        # 保存 PID
        pid_file.write_text(str(process.pid))

        console.print(f"[green]{_OK_MARK}[/green] Gateway started (PID: {process.pid})")
        console.print(f"[dim]Log file: {log_file}[/dim]")
    else:
        # 前台运行
        asyncio.run(_run_gateway(config))


@gateway_app.command("run")
def gateway_run(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
) -> None:
    """前台运行 Gateway 服务（debug 模式）。"""
    print_banner()

    config = load_config()
    _backend = config.driver.backend

    # Unified MCP config check (mirrors gateway_start logic)
    _unified_mcp = config.mcp_proxy.enabled
    _iflow_mcp_enabled = bool(config.driver.iflow and config.driver.iflow.mcp_proxy_enabled)

    if _unified_mcp and _iflow_mcp_enabled:
        logger.warning(
            "Both mcp_proxy (unified) and driver.iflow.mcp_proxy_enabled are set; "
            "unified config takes precedence. Remove driver.iflow.mcp_proxy_enabled to silence this warning."
        )

    if _backend == "claude" and (_unified_mcp or _iflow_mcp_enabled):
        logger.info("Claude backend: MCP servers will be passed directly via --mcp-config (no HTTP proxy)")

    # Startup audit log
    _mcp_enabled = _unified_mcp or _iflow_mcp_enabled
    logger.bind(
        event="gateway_startup",
        backend=config.driver.backend,
        transport=config.driver.transport,
        mcp_enabled=_mcp_enabled,
    ).info(
        f"Gateway started: backend={config.driver.backend} transport={config.driver.transport}"
    )

    # 检查后端是否就绪（根据 backend 分发到对应的检查器）
    _ready, _ready_msg = asyncio.run(check_backend_ready(_backend, config.driver))
    if not _ready:
        console.print(f"[red]{_ready_msg}[/red]")
        raise typer.Exit(1)

    workspace = Path(config.get_workspace())

    # 初始化 workspace
    init_workspace(workspace, backend=config.driver.backend)

    enabled_channels = config.get_enabled_channels()
    if not enabled_channels:
        console.print("[yellow]No channels are enabled in the configuration.[/yellow]")
        return

    console.print(f"[bold]启动渠道网关:[/bold] {', '.join(enabled_channels)}")
    console.print(f"[bold]Workspace:[/bold] {workspace}")
    console.print(f"[bold]Model:[/bold] {config.get_model()}")
    console.print()

    asyncio.run(_run_gateway(config, verbose=verbose))


@gateway_app.command("stop")
def gateway_stop() -> None:
    """停止 Gateway 服务。"""
    pid_file = get_pid_file()

    if not pid_file.exists():
        console.print("[yellow]Gateway is not running[/yellow]")
        return

    try:
        pid = int(pid_file.read_text().strip())
        if is_windows():
            # Windows 上使用 taskkill 终止进程
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        console.print(f"[green]{_OK_MARK}[/green] Gateway stopped (PID: {pid})")
        pid_file.unlink()
    except ProcessLookupError:
        console.print("[yellow]Gateway process not found[/yellow]")
        pid_file.unlink()
    except Exception as e:
        console.print(f"[red]Error stopping gateway: {e}[/red]")


@gateway_app.command("restart")
def gateway_restart() -> None:
    """重启 Gateway 服务。"""
    gateway_stop()
    console.print()
    gateway_start()


# ============================================================================
# 网关运行核心逻辑
# ============================================================================


async def _run_gateway(config, verbose: bool = False) -> None:
    """运行网关服务。"""
    if verbose:
        import sys as _sys

        from loguru import logger as _logger
        _logger.remove()
        _logger.add(
            _sys.stderr,
            level="DEBUG",
            format="<green>{time:HH:mm:ss}</green> | <level>{level:8}</level> | {message}",
        )

    from cli_bridge.bus import MessageBus
    from cli_bridge.bus.events import OutboundMessage
    from cli_bridge.channels import ChannelManager
    from cli_bridge.cron.service import CronService
    from cli_bridge.cron.types import CronJob
    from cli_bridge.engine import IFlowAdapter
    from cli_bridge.engine.loop import AgentLoop
    from cli_bridge.heartbeat.service import HeartbeatService

    workspace = config.get_workspace()

    # 获取后端和传输配置
    _backend = config.driver.backend
    _transport = config.driver.transport
    acp_port = config.driver.iflow.acp_port if config.driver.iflow else 8090

    # ACP 模式：启动 iflow ACP 服务
    acp_process = None
    if _transport == "acp":
        console.print(f"[bold cyan]启动 ACP 服务 (端口: {acp_port})...[/bold cyan]")
        result = await _start_acp_server(acp_port)
        if result is not None:
            acp_process = result
            console.print(f"[green]{_OK_MARK}[/green] ACP 服务已启动 (PID: {acp_process.pid})")
        else:
            # 检查端口是否已被占用（复用现有进程）
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            port_in_use = sock.connect_ex(("localhost", acp_port)) == 0
            sock.close()
            if port_in_use:
                console.print(f"[green]{_OK_MARK}[/green] 复用现有 ACP 服务 (端口: {acp_port})")
            else:
                console.print("[red]✗ ACP 服务启动失败，回退到 CLI 模式[/red]")
                _transport = "cli"

    # 创建适配器
    if _backend == "claude" and _transport == "stdio":
        from cli_bridge.engine.claude_stdio_adapter import ClaudeStdioAdapter

        claude_cfg = config.driver.claude
        adapter = ClaudeStdioAdapter(
            claude_path=claude_cfg.claude_path,
            model=claude_cfg.model,
            workspace=Path(workspace) if workspace else None,
            permission_mode=claude_cfg.permission_mode,
            system_prompt=claude_cfg.system_prompt,
            max_turns=config.driver.max_turns,
            timeout=config.get_timeout(),
        )
    elif _backend == "claude":
        from cli_bridge.engine.claude_adapter import ClaudeAdapter

        claude_cfg = config.driver.claude
        adapter = ClaudeAdapter(
            claude_path=claude_cfg.claude_path,
            model=claude_cfg.model,
            workspace=Path(workspace) if workspace else None,
            permission_mode=claude_cfg.permission_mode,
            system_prompt=claude_cfg.system_prompt,
            max_turns=config.driver.max_turns,
            timeout=config.get_timeout(),
            mcp_proxy_config=config.mcp_proxy,
        )
    elif _backend == "gemini":
        from cli_bridge.engine.gemini_adapter import GeminiACPAdapter

        gemini_cfg = config.driver.gemini
        adapter = GeminiACPAdapter(
            gemini_path=gemini_cfg.gemini_path,
            model=gemini_cfg.model,
            workspace=Path(workspace) if workspace else None,
            timeout=config.get_timeout(),
            api_key=gemini_cfg.api_key,
            google_api_key=gemini_cfg.google_api_key,
            yolo=gemini_cfg.yolo,
            sandbox=gemini_cfg.sandbox,
        )
    else:
        iflow_cfg = config.driver.iflow
        adapter = IFlowAdapter(
            default_model=config.get_model(),
            workspace=workspace if workspace else None,
            timeout=config.get_timeout(),
            thinking=iflow_cfg.thinking,
            transport=_transport,
            acp_port=iflow_cfg.acp_port,
            compression_trigger_tokens=iflow_cfg.compression_trigger_tokens,
            mcp_proxy_port=iflow_cfg.mcp_proxy_port,
            mcp_servers_auto_discover=iflow_cfg.mcp_servers_auto_discover,
            mcp_servers_max=iflow_cfg.mcp_servers_max,
            mcp_servers_allowlist=iflow_cfg.mcp_servers_allowlist or [],
            mcp_servers_blocklist=iflow_cfg.mcp_servers_blocklist or [],
        )

    bus = MessageBus()
    channel_manager = ChannelManager(config, bus)

    agent_loop = AgentLoop(
        bus=bus,
        adapter=adapter,
        model=config.get_model(),
        channel_manager=channel_manager,
        backend_name=config.driver.backend,
    )

    # 创建 Cron 服务
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # 设置 cron 任务回调
    async def on_cron_job(job: CronJob) -> str | None:
        """执行 cron 任务通过 agent。"""
        # 通过 agent 处理消息
        # 构建 cron 任务上下文前缀
        from datetime import datetime as _dt

        next_run_info = ""
        if job.state.next_run_at_ms:
            next_run_info = f"下次执行: {_dt.fromtimestamp(job.state.next_run_at_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')}"

        context_prefix = f"""[系统消息：这是一个定时任务触发]
任务名称: {job.name}
任务ID: {job.id}
调度类型: {job.schedule.kind}
执行时间: {_dt.now().strftime("%Y-%m-%d %H:%M:%S")}
{next_run_info}

--- 任务消息 ---
"""

        full_message = context_prefix + job.payload.message

        response = await agent_loop.process_direct(
            full_message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cron",
            chat_id=job.payload.to or "direct",
        )

        # 如果需要投递响应
        if job.payload.deliver and job.payload.to and job.payload.channel:
            await bus.publish_outbound(
                OutboundMessage(
                    channel=job.payload.channel, chat_id=job.payload.to, content=response or ""
                )
            )

        return response

    cron.on_job = on_cron_job

    # 选择心跳通知目标的函数
    def _pick_heartbeat_target() -> tuple[str, str]:
        """选择一个可用的渠道/聊天目标用于心跳触发消息。"""
        enabled = set(channel_manager.enabled_channels)
        # 优先使用最近更新的非内部会话
        # 这里简化处理，返回第一个启用的渠道
        if enabled:
            first_channel = list(enabled)[0]
            return first_channel, "heartbeat"
        return "cli", "direct"

    # 创建 Heartbeat 服务
    async def on_heartbeat(prompt: str) -> str:
        """执行心跳通过 agent。"""
        channel, chat_id = _pick_heartbeat_target()

        return await agent_loop.process_direct(
            prompt,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """投递心跳响应到用户渠道。"""
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # 没有外部渠道可用
        await bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=response)
        )

    heartbeat = HeartbeatService(
        workspace=workspace if workspace else Path.home() / ".cli-bridge" / "workspace",
        on_heartbeat=on_heartbeat,
        on_notify=on_heartbeat_notify,
        interval_s=30 * 60,  # 30 分钟
        enabled=True,
    )

    console.print("[bold green]Gateway 启动中...[/bold green]")

    try:
        # 启动服务
        await cron.start()
        await heartbeat.start()
        await channel_manager.start_all()
        await agent_loop.start_background()

        # 显示状态
        console.print(f"[bold green]{_OK_MARK} Gateway 运行中！[/bold green]")
        if channel_manager.enabled_channels:
            console.print(f"[dim]  渠道: {', '.join(channel_manager.enabled_channels)}[/dim]")

        cron_status = cron.status()
        if cron_status["jobs"] > 0:
            console.print(f"[dim]  定时任务: {cron_status['jobs']} 个[/dim]")

        console.print("[dim]  心跳: 每 30 分钟[/dim]")
        console.print(f"[dim]  模式: {_backend.upper()}/{_transport.upper()}[/dim]")
        console.print("[dim]按 Ctrl+C 停止[/dim]")

        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]正在关闭...[/yellow]")
    finally:
        heartbeat.stop()
        cron.stop()
        agent_loop.stop()
        await channel_manager.stop_all()
        await adapter.close()

        # 关闭 ACP 服务
        if acp_process:
            console.print("[dim]正在关闭 ACP 服务...[/dim]")
            await _stop_acp_server(acp_process)
