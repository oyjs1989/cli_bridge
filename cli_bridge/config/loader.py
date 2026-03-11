"""Configuration loader for cli-bridge."""

import json
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from cli_bridge.config.schema import Config

# 统一的超时常量定义
DEFAULT_TIMEOUT = 180  # 默认超时时间（秒）
LEGACY_DEFAULT_TIMEOUT = 300  # 旧版默认值，用于迁移检测

# v1 遗留的扁平化字段（现已移入嵌套的 iflow/claude 配置块）
_LEGACY_FLAT_FIELDS = {
    "iflow_path", "yolo", "compression_trigger_tokens",
    "claude_path", "claude_model", "mcp_proxy_enabled",
    "acp_port", "acp_host", "extra_args", "disable_mcp",
}


class ConfigMigrationError(ValueError):
    """当检测到 v1 扁平配置格式时抛出。"""
    pass


def _check_for_legacy_config(raw_driver: dict) -> None:
    """检查 driver 字典中是否存在 v1 遗留字段，若有则抛出 ConfigMigrationError。"""
    found = [f for f in _LEGACY_FLAT_FIELDS if f in raw_driver]
    if found:
        raise ConfigMigrationError(
            f"Legacy config format detected (v1).\n"
            f"Found legacy fields: {', '.join(sorted(found))}\n"
            f"Please migrate your config to v2 format.\n"
            f"Migration guide: ~/.cli-bridge/docs/config-migration-v2.md"
        )


def get_config_dir() -> Path:
    """Get the configuration directory."""
    return Path.home() / ".cli-bridge"


def get_config_path() -> Path:
    """Get the configuration file path."""
    return get_config_dir() / "config.json"


def get_data_dir() -> Path:
    """Get the data directory for cli-bridge."""
    data_dir = get_config_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_workspace_path() -> Path:
    """Get the default workspace path."""
    workspace = get_config_dir() / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def load_config(config_path: Path | None = None, auto_create: bool = True) -> Config:
    """
    Load configuration from file.

    Args:
        config_path: Optional path to config file. If not provided,
                     uses the default path.
        auto_create: If True, create default config file when not exists.

    Returns:
        Config object.
    """
    if config_path is None:
        config_path = get_config_path()

    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            raw_driver = data.get("driver", {})
            if isinstance(raw_driver, dict):
                _check_for_legacy_config(raw_driver)
            data, migrated = _migrate_legacy_driver_timeout(data)
            if migrated:
                _save_raw_config_data(data, config_path)
                logger.info(
                    f"Migrated config timeout to {DEFAULT_TIMEOUT}s for existing install: {config_path}"
                )
            config = Config(**data)
            logger.info(f"Loaded config from {config_path}")
            return config
        except ConfigMigrationError:
            raise
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Invalid config file: {e}. Using defaults.")
    else:
        logger.info("No config file found. Creating default config.")
        config = Config()
        if auto_create:
            # 创建默认配置文件（根据环境变量中可能的 mode 偏好生成对应格式）
            _create_default_config(config_path, mode=config.driver.mode)
        return config

    return Config()


def _migrate_legacy_driver_timeout(data: dict) -> tuple[dict, bool]:
    """Migrate legacy default timeout to new default for upgraded users.

    Rules:
    - If `driver.timeout` is missing, set it to DEFAULT_TIMEOUT.
    - If `driver.timeout` equals legacy default 300 (int or string), set to DEFAULT_TIMEOUT.
    - Keep all other custom timeout values unchanged.
    """
    migrated = False
    driver = data.get("driver")
    if not isinstance(driver, dict):
        return data, migrated

    timeout = driver.get("timeout")
    if timeout is None:
        driver["timeout"] = DEFAULT_TIMEOUT
        migrated = True
    elif timeout == LEGACY_DEFAULT_TIMEOUT or timeout == str(LEGACY_DEFAULT_TIMEOUT):
        driver["timeout"] = DEFAULT_TIMEOUT
        migrated = True

    return data, migrated


def _save_raw_config_data(data: dict, config_path: Path) -> None:
    """Persist raw config dictionary to disk."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _build_iflow_driver_block(mode: str = "stdio") -> dict:
    """Build the driver config block for iflow-based modes (cli/stdio/acp)."""
    return {
        "mode": mode,
        "max_turns": 40,
        "timeout": DEFAULT_TIMEOUT,
        "workspace": str(Path.home() / ".cli-bridge" / "workspace"),
        "iflow": {
            "iflow_path": "iflow",
            "model": "minimax-m2.5",
            "yolo": True,
            "thinking": False,
            "extra_args": [],
            "compression_trigger_tokens": 60000,
            "acp_host": "localhost",
            "acp_port": 8090,
            "disable_mcp": False,
            "mcp_proxy_enabled": True,
            "mcp_proxy_port": 8888,
            "mcp_proxy_auto_start": True,
            "mcp_servers_auto_discover": True,
            "mcp_servers_max": 10,
            "mcp_servers_allowlist": [],
            "mcp_servers_blocklist": [],
        },
    }


def _build_claude_driver_block() -> dict:
    """Build the driver config block for claude mode (no iflow fields)."""
    return {
        "mode": "claude",
        "max_turns": 40,
        "timeout": DEFAULT_TIMEOUT,
        "workspace": str(Path.home() / ".cli-bridge" / "workspace"),
        "claude": {
            "claude_path": "claude",
            "model": "claude-opus-4-6",
            "system_prompt": "",
            "permission_mode": "bypassPermissions",
        },
    }


def _create_default_config(config_path: Path, mode: str = "stdio") -> None:
    """创建默认配置文件。

    根据 mode 生成对应的后端配置块：
    - mode == "claude": 仅包含 driver.claude，不含 driver.iflow
    - 其他 (cli/stdio/acp): 仅包含 driver.iflow，不含 driver.claude
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "claude":
        driver_block = _build_claude_driver_block()
    else:
        driver_block = _build_iflow_driver_block(mode)

    default_config = {
        "driver": driver_block,
        "channels": {
            "telegram": {
                "enabled": False,
                "token": "",
                "allow_from": []
            },
            "discord": {
                "enabled": False,
                "token": "",
                "allow_from": []
            },
            "slack": {
                "enabled": False,
                "bot_token": "",
                "app_token": "",
                "allow_from": [],
                "group_policy": "mention",
                "group_allow_from": [],
                "reply_in_thread": True,
                "react_emoji": "eyes",
                "dm": {
                    "enabled": True,
                    "policy": "open",
                    "allow_from": []
                }
            },
            "feishu": {
                "enabled": False,
                "app_id": "",
                "app_secret": "",
                "encrypt_key": "",
                "verification_token": "",
                "allow_from": []
            },
            "dingtalk": {
                "enabled": False,
                "client_id": "",
                "client_secret": "",
                "allow_from": []
            },
            "qq": {
                "enabled": False,
                "app_id": "",
                "secret": "",
                "allow_from": [],
                "split_threshold": 3
            },
            "whatsapp": {
                "enabled": False,
                "bridge_url": "http://localhost:3001",
                "bridge_token": "",
                "allow_from": []
            },
            "email": {
                "enabled": False,
                "consent_granted": False,
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
                "imap_username": "",
                "imap_password": "",
                "imap_use_ssl": True,
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_username": "",
                "smtp_password": "",
                "smtp_use_tls": True,
                "from_address": "",
                "allow_from": [],
                "auto_reply_enabled": True,
                "poll_interval_seconds": 30,
                "max_body_chars": 10000,
                "mark_seen": True,
                "subject_prefix": "Re: "
            },
            "mochat": {
                "enabled": False,
                "base_url": "https://mochat.io",
                "socket_url": "https://mochat.io",
                "socket_path": "/socket.io",
                "claw_token": "",
                "agent_user_id": "",
                "sessions": ["*"],
                "panels": ["*"],
                "watch_timeout_ms": 30000,
                "watch_limit": 50,
                "refresh_interval_ms": 60000,
                "reply_delay_mode": "non-mention",
                "reply_delay_ms": 120000,
                "socket_connect_timeout_ms": 10000,
                "socket_reconnect_delay_ms": 1000,
                "socket_max_reconnect_delay_ms": 5000,
                "max_retry_attempts": 5,
                "retry_delay_ms": 5000
            }
        },
        "heartbeat": {
            "enabled": True,
            "interval_s": 1800
        },
        "log_level": "INFO",
        "log_file": ""
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=2, ensure_ascii=False)

    logger.info(f"Created default config at {config_path}")


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Config object to save.
        config_path: Optional path to config file. If not provided,
                     uses the default path.
    """
    if config_path is None:
        config_path = get_config_path()

    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)

    logger.info(f"Saved config to {config_path}")


def get_session_dir() -> Path:
    """Get the sessions directory."""
    session_dir = get_data_dir() / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir
