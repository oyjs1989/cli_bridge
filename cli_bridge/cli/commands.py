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
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from cli_bridge.cli.health import check_backend_ready
from cli_bridge.utils.platform import (
    is_windows,
    prepare_subprocess_command,
    resolve_command,
    run_command,
)

console = Console()


def _can_encode_in_stdout(text: str) -> bool:
    """Return True if current stdout encoding can represent the given text."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except Exception:
        return False


_UNICODE_CONSOLE = _can_encode_in_stdout("✓🤖")
_OK_MARK = "✓" if _UNICODE_CONSOLE else "OK"
__logo__ = "🤖" if _UNICODE_CONSOLE else "cli-bridge"


def _read_version_from_pyproject() -> str:
    """Read project version from pyproject.toml."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        content = pyproject.read_text(encoding="utf-8")
    except Exception:
        return "0.0.0"

    in_project_section = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project_section = line == "[project]"
            continue
        if not in_project_section:
            continue
        match = re.match(r'^version\s*=\s*"([^"]+)"\s*$', line)
        if match:
            return match.group(1)

    return "0.0.0"


def _resolve_version() -> str:
    """Resolve version for both installed and source-run scenarios."""
    try:
        return pkg_version("cli-bridge")
    except PackageNotFoundError:
        pass
    except Exception:
        pass

    return _read_version_from_pyproject()


__version__ = _resolve_version()


def process_exists(pid: int) -> bool:
    """检查进程是否存在（跨平台）。

    Args:
        pid: 进程 ID

    Returns:
        True 如果进程存在，False 否则
    """
    if is_windows():
        try:
            result = run_command(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


# ============================================================================
# 路径配置
# ============================================================================


def get_config_dir() -> Path:
    return Path.home() / ".cli-bridge"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_pid_file() -> Path:
    return get_config_dir() / "gateway.pid"


def get_log_file() -> Path:
    return get_config_dir() / "gateway.log"


def get_templates_dir() -> Path:
    """获取项目模板目录。"""
    return Path(__file__).parent.parent / "templates"


# ============================================================================
# iflow 检查
# ============================================================================


def _prepend_to_path(path: str) -> None:
    current_path = os.environ.get("PATH", "")
    parts = current_path.split(os.pathsep) if current_path else []
    normalized = {os.path.normcase(os.path.normpath(p)) for p in parts if p}
    candidate = os.path.normcase(os.path.normpath(path))
    if candidate not in normalized:
        os.environ["PATH"] = path + (os.pathsep + current_path if current_path else "")


def _ensure_windows_npm_path() -> None:
    """确保 Windows 下 npm/global shim 路径在 PATH 中。"""
    if not is_windows():
        return

    candidates: list[str] = []

    for probe in ("npm", "npm.cmd", "npm.exe"):
        resolved = resolve_command(probe)
        if resolved:
            npm_dir = str(Path(resolved).parent)
            candidates.append(npm_dir)

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(str(Path(appdata) / "npm"))

    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(str(Path(local_appdata) / "Programs" / "npm"))

    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            _prepend_to_path(candidate)

    try:
        result = run_command(
            ["npm", "root", "-g"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        npm_global_root = (result.stdout or "").strip()
        if npm_global_root and os.path.isdir(npm_global_root):
            _prepend_to_path(npm_global_root)
            bin_path = os.path.join(npm_global_root, "bin")
            if os.path.isdir(bin_path):
                _prepend_to_path(bin_path)
    except Exception:
        pass


# 模块加载时先刷新 Windows PATH
_ensure_windows_npm_path()


def check_iflow_installed() -> bool:
    """检查 iflow 是否已安装。

    Returns:
        True if installed, False otherwise
    """
    try:
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 10,
        }
        result = run_command(["iflow", "--version"], **kwargs)
        if result.returncode == 0:
            return True
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    return False


def check_iflow_logged_in() -> bool:
    """检查 iflow 是否已登录。

    Returns:
        True if logged in, False otherwise
    """
    try:
        # 检查 iflow 配置目录是否存在登录信息
        iflow_config_dir = Path.home() / ".iflow"
        if not iflow_config_dir.exists():
            return False

        # 检查是否有项目配置（说明已登录）
        projects_dir = iflow_config_dir / "projects"
        if projects_dir.exists() and list(projects_dir.iterdir()):
            return True

        # 尝试运行 iflow 看是否需要登录
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 10,
        }
        result = run_command(["iflow", "-p", "test"], **kwargs)
        # 如果返回 "Please login first" 或类似提示，说明未登录
        output = result.stdout + result.stderr
        if "login" in output.lower() or "please login" in output.lower():
            return False

        return True
    except Exception:
        return False


def ensure_iflow_ready() -> bool:
    """确保 iflow 已安装并登录。

    Returns:
        True if ready, False otherwise
    """
    system = platform.system().lower()

    # Windows: 先刷新 PATH，确保能找到 iflow
    _ensure_windows_npm_path()

    # 检查是否已安装
    if check_iflow_installed():
        if not check_iflow_logged_in():
            console.print("[red]Error: iflow is not logged in.[/red]")
            console.print()
            console.print("Please login first:")
            console.print("  [cyan]iflow login[/cyan]")
            return False
        return True

    # 未安装，触发自动安装
    console.print("[yellow]iflow 未安装，正在自动安装...[/yellow]")
    console.print()

    install_cmd = ["npm", "install", "-g", "@iflow-ai/iflow-cli@latest"]

    console.print("[cyan]自动安装依赖中...[/cyan]")
    try:
        result = run_command(install_cmd)
    except FileNotFoundError:
        result = None

    if result is None or result.returncode != 0:
        if system == "windows":
            console.print("[red]自动安装失败，请手动执行以下步骤:[/red]")
            console.print()
            console.print("  1. 访问 https://nodejs.org/zh-cn/download 下载最新的 Node.js 安装程序")
            console.print("  2. 运行安装程序来安装 Node.js")
            console.print("  3. 重启终端：CMD(Windows + r 输入cmd) 或 PowerShell")
            console.print(
                "  4. 运行 [cyan]npm install -g @iflow-ai/iflow-cli@latest[/cyan] 来安装 iFlow CLI"
            )
            console.print("  5. 运行 [cyan]iflow[/cyan] 来启动 iFlow CLI")
        else:
            console.print(
                "[red]自动安装失败，请先确保 Node.js 与 npm 可用，然后手动执行以下命令:[/red]"
            )
            console.print("  [cyan]npm install -g @iflow-ai/iflow-cli@latest[/cyan]")
        return False

    # 安装后再次刷新 PATH 并检查
    _ensure_windows_npm_path()
    if not check_iflow_installed():
        console.print("[red]安装后仍检测不到 iflow，请检查安装过程[/red]")
        return False
    console.print(f"[green]{_OK_MARK} iflow 安装成功![/green]")

    # 检查是否登录
    if not check_iflow_logged_in():
        console.print("[red]Error: iflow is not logged in.[/red]")
        console.print()
        console.print("Please login first:")
        console.print("  [cyan]iflow login[/cyan]")
        return False

    return True


# ============================================================================
# 配置管理
# ============================================================================


def load_config():
    """加载配置。"""
    from cli_bridge.config.loader import load_config as _load_config

    return _load_config(auto_create=False)


def save_config(config) -> None:
    """保存配置。"""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(config, "model_dump"):
        data = config.model_dump()
    elif hasattr(config, "dict"):
        data = config.dict()
    else:
        data = dict(config)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================================
# Workspace 初始化
# ============================================================================


def init_workspace(workspace: Path, backend: str = "iflow") -> None:
    """初始化 workspace 目录，从模板目录复制文件。

    逻辑：
    - 如果 workspace 已存在 AGENTS.md 或 BOOT.md，说明已初始化，跳过模板复制
    - 只有全新的 workspace 才复制所有模板（包括 BOOTSTRAP.md）
    - claude 后端不需要 .iflow/settings.json
    """
    # 展开波浪号路径
    workspace = Path(str(workspace).replace("~", str(Path.home())))
    workspace.mkdir(parents=True, exist_ok=True)

    # 创建 .iflow 目录和 settings.json（仅 iflow 后端需要）
    if backend == "iflow":
        iflow_dir = workspace / ".iflow"
        iflow_dir.mkdir(exist_ok=True)

        settings_path = iflow_dir / "settings.json"
        if not settings_path.exists():
            default_settings = {
                "contextFileName": [
                    "AGENTS.md",
                    "BOOT.md",
                    "BOOTSTRAP.md",
                    "HEARTBEAT.md",
                    "IDENTITY.md",
                    "SOUL.md",
                    "TOOLS.md",
                    "USER.md",
                ],
                "approvalMode": "yolo",
                "language": "zh-CN",
            }
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(default_settings, f, indent=2, ensure_ascii=False)
            console.print(f"[green]{_OK_MARK}[/green] Created {settings_path}")

    # 检查 workspace 是否已经初始化（通过检查核心文件是否存在）
    core_files = ["AGENTS.md", "BOOT.md", "SOUL.md"]
    is_initialized = any((workspace / f).exists() for f in core_files)

    if is_initialized:
        console.print("[dim]Workspace already initialized, skipping template copy[/dim]")
        return

    # 从模板目录复制文件（仅首次初始化）
    templates_dir = get_templates_dir()

    # 需要复制的模板文件
    template_files = [
        "AGENTS.md",
        "BOOT.md",
        "BOOTSTRAP.md",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "SOUL.md",
        "TOOLS.md",
        "USER.md",
    ]

    for filename in template_files:
        src = templates_dir / filename
        dst = workspace / filename
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            console.print(f"[green]{_OK_MARK}[/green] Created {dst}")

    # 创建 memory 目录并复制 MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)

    memory_src = templates_dir / "memory" / "MEMORY.md"
    memory_dst = memory_dir / "MEMORY.md"
    if memory_src.exists() and not memory_dst.exists():
        shutil.copy2(memory_src, memory_dst)
        console.print(f"[green]{_OK_MARK}[/green] Created {memory_dst}")

    # 创建 channel 目录（用于记录各渠道对话）
    channel_dir = workspace / "channel"
    channel_dir.mkdir(exist_ok=True)


# ============================================================================
# 主命令
# ============================================================================

app = typer.Typer(
    name="cli-bridge",
    help=f"{__logo__} cli-bridge - Multi-channel AI Assistant (powered by Claude)",
    no_args_is_help=True,
    add_completion=False,
)


def print_banner() -> None:
    console.print(r"""
         _   _           _              _       _                
   ___  | | (_)         | |__    _ __  (_)   __| |   __ _    ___ 
  / __| | | | |  _____  | '_ \  | '__| | |  / _` |  / _` |  / _ \
 | (__  | | | | |_____| | |_) | | |    | | | (_| | | (_| | |  __/
  \___| |_| |_|         |_.__/  |_|    |_|  \__,_|  \__, |  \___|
                                                    |___/                                      
  Multi-channel AI Assistant (powered by Claude)
""")


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
# Gateway 命令组
# ============================================================================

gateway_app = typer.Typer(help="Gateway 服务管理")
app.add_typer(gateway_app, name="gateway")


@gateway_app.callback()
def gateway_callback():
    """Gateway 服务管理命令。"""
    pass


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


def get_data_dir() -> Path:
    """获取数据存储目录。"""
    data_dir = get_config_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


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


# 内部命令 - 用于后台启动
@app.command("_run_gateway", hidden=True)
def _run_gateway_cmd():
    """内部命令：运行 Gateway。"""
    config = load_config()
    asyncio.run(_run_gateway(config))


async def _run_gateway(config, verbose: bool = False) -> None:
    """运行网关服务。"""
    if verbose:
        logger.remove()
        logger.add(
            sys.stderr,
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

        async def _silent(*_args, **_kwargs):
            pass

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


# ============================================================================
# Status 命令
# ============================================================================


@app.command()
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
    if config.driver.iflow:
        console.print(f"  Thinking: [cyan]{'启用' if config.driver.iflow.thinking else '禁用'}[/cyan]")
        console.print(f"  MCP Proxy: [cyan]{'启用' if config.driver.iflow.mcp_proxy_enabled else '禁用'}[/cyan]")
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


@app.command(name="console")
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
# 模型切换命令
# ============================================================================


@app.command()
def model(
    name: str = typer.Argument(..., help="模型名称 (如: glm-5, kimi-k2.5)"),
) -> None:
    """切换默认模型。"""
    config = load_config()
    if not config.driver:
        from cli_bridge.config.schema import DriverConfig

        config.driver = DriverConfig()
    if config.driver.backend == "claude" and config.driver.claude:
        config.driver.claude.model = name
    elif config.driver.iflow:
        config.driver.iflow.model = name
    save_config(config)

    console.print(f"[green]{_OK_MARK}[/green] Model set to: [cyan]{name}[/cyan]")
    console.print("[dim]Restart gateway to apply: cli-bridge gateway restart[/dim]")


# ============================================================================
# 思考模式命令
# ============================================================================


@app.command()
def thinking(
    mode: str = typer.Argument(..., help="on 或 off"),
) -> None:
    """开启/关闭思考模式。"""
    if mode.lower() not in ("on", "off", "true", "false"):
        console.print("[red]Error: mode must be 'on' or 'off'[/red]")
        raise typer.Exit(1)

    enabled = mode.lower() in ("on", "true")

    config = load_config()
    if not config.driver:
        from cli_bridge.config.schema import DriverConfig

        config.driver = DriverConfig()
    config.driver.thinking = enabled
    save_config(config)

    status = "启用" if enabled else "禁用"
    console.print(f"[green]{_OK_MARK}[/green] Thinking mode: [cyan]{status}[/cyan]")
    console.print("[dim]Restart gateway to apply: cli-bridge gateway restart[/dim]")


# ============================================================================
# Sessions 命令
# ============================================================================


@app.command()
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


@app.command()
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
        thinking = cfg.driver.thinking if hasattr(cfg, "driver") and cfg.driver else False
        console.print(f"Thinking: [cyan]{'启用' if thinking else '禁用'}[/cyan]")


app.command(name="config")(config_cmd)


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


@app.command(name="iflow")
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


# ============================================================================
# 其他 iflow 命令透传
# ============================================================================


@app.command(name="mcp")
def mcp_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow mcp 命令。Use 'mcp' directly instead."""
    _warn_passthrough_deprecated("mcp")
    cmd = ["iflow", "mcp"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


@app.command(name="agent")
def agent_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow agent 命令。Use 'agent' directly instead."""
    _warn_passthrough_deprecated("agent")
    cmd = ["iflow", "agent"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


@app.command(name="workflow")
def workflow_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow workflow 命令。Use 'workflow' directly instead."""
    _warn_passthrough_deprecated("workflow")
    cmd = ["iflow", "workflow"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


@app.command(name="skill")
def skill_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow skill 命令。Use 'skill' directly instead."""
    _warn_passthrough_deprecated("skill")
    cmd = ["iflow", "skill"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


@app.command(name="commands")
def commands_passthrough(args: list[str] = typer.Argument(None)) -> None:
    """[DEPRECATED v1.0.0] 透传到 iflow commands 命令。Use 'commands' directly instead."""
    _warn_passthrough_deprecated("commands")
    cmd = ["iflow", "commands"] + (args or [])
    returncode = _run_iflow_cmd(cmd)
    raise typer.Exit(returncode)


# ============================================================================
# Onboard 命令
# ============================================================================


@app.command()
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
# Cron 命令
# ============================================================================

cron_app = typer.Typer(help="管理定时任务")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="包含已禁用的任务"),
):
    """列出定时任务。"""
    from cli_bridge.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("没有定时任务。")
        console.print(
            '\n添加任务: [cyan]cli-bridge cron add --name "任务名" --message "消息" --every 60[/cyan]'
        )
        return

    table = Table(title="定时任务")
    table.add_column("ID", style="cyan")
    table.add_column("名称")
    table.add_column("调度")
    table.add_column("投递", style="yellow")
    table.add_column("状态")
    table.add_column("下次运行")

    from datetime import datetime as _dt

    for job in jobs:
        # 格式化调度信息
        if job.schedule.kind == "every":
            seconds = (job.schedule.every_ms or 0) // 1000
            if seconds >= 86400:
                sched = f"每 {seconds // 86400} 天"
            elif seconds >= 3600:
                sched = f"每 {seconds // 3600} 小时"
            elif seconds >= 60:
                sched = f"每 {seconds // 60} 分钟"
            else:
                sched = f"每 {seconds} 秒"
        elif job.schedule.kind == "cron":
            sched = f"cron: {job.schedule.expr}"
            if job.schedule.tz:
                sched += f" ({job.schedule.tz})"
        elif job.schedule.kind == "at":
            sched = "一次性"
        else:
            sched = "未知"

        # 格式化下次运行时间
        next_run = ""
        if job.state.next_run_at_ms:
            ts = job.state.next_run_at_ms / 1000
            next_run = _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

        # 格式化投递信息
        if job.payload.deliver and job.payload.channel:
            deliver_info = f"[green]{job.payload.channel}[/green]"
            if job.payload.to:
                deliver_info += f":{job.payload.to[:8]}..."
        else:
            deliver_info = "[dim]无[/dim]"

        status = "[green]启用[/green]" if job.enabled else "[dim]禁用[/dim]"
        if job.state.last_status == "error":
            status += " [red](错误)[/red]"
        elif job.state.last_status == "ok":
            status += f" [green]({_OK_MARK})[/green]"

        table.add_row(job.id, job.name, sched, deliver_info, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="任务名称"),
    message: str = typer.Option(..., "--message", "-m", help="提醒消息内容"),
    every: int = typer.Option(None, "--every", "-e", help="每隔 N 秒执行"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron 表达式 (如 '0 9 * * *')"),
    at: str | None = typer.Option(
        None, "--at", "-a", help="一次性任务，指定执行时间 (ISO格式: 'YYYY-MM-DDTHH:MM:SS')"
    ),
    tz: str | None = typer.Option(None, "--tz", help="时区 (如 'Asia/Shanghai')"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="投递响应到渠道"),
    to: str | None = typer.Option(None, "--to", help="投递目标 (如用户ID或群组ID)"),
    channel: str | None = typer.Option(None, "--channel", help="投递渠道 (如 telegram, discord)"),
    delete_after_run: bool = typer.Option(False, "--delete-after-run", help="执行后自动删除任务"),
    silent: bool = typer.Option(False, "--silent", "-s", help="静默模式：不投递通知，仅执行任务"),
):
    """添加定时任务。"""
    from datetime import datetime as _dt

    from cli_bridge.cron.service import CronService
    from cli_bridge.cron.types import CronSchedule

    # 检查参数冲突
    schedule_count = sum(1 for x in [every, cron_expr, at] if x)
    if schedule_count == 0:
        console.print("[red]错误: 必须指定 --every, --cron 或 --at 其中之一[/red]")
        raise typer.Exit(1)

    if schedule_count > 1:
        console.print("[red]错误: --every, --cron 和 --at 不能同时使用[/red]")
        raise typer.Exit(1)

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    # 解析调度类型
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    elif at:
        # 解析 ISO 格式时间
        try:
            # 尝试解析 ISO 格式
            if "T" in at:
                target_dt = _dt.fromisoformat(at.replace("Z", "+00:00"))
            else:
                # 只有日期，默认为当天 00:00:00
                target_dt = _dt.fromisoformat(at)

            at_ms = int(target_dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
        except ValueError:
            console.print(
                f"[red]错误: 无效的时间格式 '{at}'，请使用 ISO 格式 (如 '2024-12-25T09:00:00')[/red]"
            )
            raise typer.Exit(1)

    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
            delete_after_run=delete_after_run,
        )

        console.print(f"[green]{_OK_MARK}[/green] 已添加定时任务: {job.name} (ID: {job.id})")

        if job.state.next_run_at_ms:
            next_run = _dt.fromtimestamp(job.state.next_run_at_ms / 1000)
            console.print(f"[dim]执行时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")

        if at:
            console.print("[dim]类型: 一次性任务（执行后自动禁用）[/dim]")

        if silent:
            console.print("[dim]模式: 静默模式（不发送通知）[/dim]")
        elif deliver and channel and to:
            console.print(f"[dim]投递: {channel}:{to[:8]}...[/dim]")

        console.print("\n[dim]提示: 无需重启 Gateway，任务会自动加载[/dim]")

    except ValueError as e:
        console.print(f"[red]错误: {e}[/red]")
        raise typer.Exit(1)


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="任务 ID"),
):
    """移除定时任务。"""
    from cli_bridge.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"[green]{_OK_MARK}[/green] 已移除任务: {job_id}")
    else:
        console.print(f"[red]错误: 未找到任务 {job_id}[/red]")
        raise typer.Exit(1)


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="任务 ID"),
):
    """启用定时任务。"""
    from cli_bridge.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=True)
    if job:
        console.print(f"[green]{_OK_MARK}[/green] 已启用任务: {job.name} ({job_id})")
    else:
        console.print(f"[red]错误: 未找到任务 {job_id}[/red]")
        raise typer.Exit(1)


@cron_app.command("disable")
def cron_disable(
    job_id: str = typer.Argument(..., help="任务 ID"),
):
    """禁用定时任务。"""
    from cli_bridge.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=False)
    if job:
        console.print(f"[green]{_OK_MARK}[/green] 已禁用任务: {job.name} ({job_id})")
    else:
        console.print(f"[red]错误: 未找到任务 {job_id}[/red]")
        raise typer.Exit(1)


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="任务 ID"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行（即使已禁用）"),
):
    """立即执行定时任务。"""
    import asyncio

    from cli_bridge.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.get_job(job_id)
    if not job:
        console.print(f"[red]错误: 未找到任务 {job_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[yellow]正在执行任务: {job.name}[/yellow]")
    console.print(f"[dim]消息: {job.payload.message}[/dim]")

    async def run_job():
        success = await service.run_job(job_id, force=force)
        if success:
            console.print(f"[green]{_OK_MARK} 任务执行完成[/green]")
        else:
            console.print("[red]✗ 任务未执行（可能已禁用，使用 --force 强制执行）[/red]")

    asyncio.run(run_job())


if __name__ == "__main__":
    app()


# ============================================================================
# MCP 配置同步
# ============================================================================


@app.command()
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
