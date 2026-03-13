"""Shared helpers for cli-bridge CLI commands."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

from rich.console import Console

from cli_bridge.utils.platform import (
    is_windows,
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


def get_data_dir() -> Path:
    """获取数据存储目录。"""
    data_dir = get_config_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


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
# Banner
# ============================================================================


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
