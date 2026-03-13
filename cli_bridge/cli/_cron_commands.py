"""Cron sub-commands for cli-bridge."""

from __future__ import annotations

import typer

from cli_bridge.cli._helpers import _OK_MARK, console, get_data_dir

cron_app = typer.Typer(help="管理定时任务")


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

    from rich.table import Table

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
