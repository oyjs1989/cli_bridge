"""Model and thinking sub-commands for cli-bridge."""

from __future__ import annotations

import typer

from cli_bridge.cli._helpers import _OK_MARK, console, load_config, save_config


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
    if config.driver.iflow is None:
        from cli_bridge.config.schema import IFlowBackendConfig
        config.driver.iflow = IFlowBackendConfig()
    config.driver.iflow.thinking = enabled
    save_config(config)

    status = "启用" if enabled else "禁用"
    console.print(f"[green]{_OK_MARK}[/green] Thinking mode: [cyan]{status}[/cyan]")
    console.print("[dim]Restart gateway to apply: cli-bridge gateway restart[/dim]")
