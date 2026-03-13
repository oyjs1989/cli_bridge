"""
Cron module for scheduled task execution.

This module provides cron job management for cli-bridge,
supporting interval-based, cron expression, and one-time tasks.

Example:
    from cli_bridge.cron import CronService, CronJob, Schedule, ScheduleKind, CronPayload
    
    # Create the service
    service = CronService(Path("data/cron_jobs.json"))
    
    # Set up job handler
    async def handle_job(job: CronJob) -> str | None:
        print(f"Job executed: {job.name}")
        print(f"Message: {job.payload.message}")
        return "ok"
    
    service.on_job = handle_job
    
    # Create a job
    job = CronJob.create(
        name="Morning Greeting",
        schedule=Schedule(kind=ScheduleKind.EVERY, every_ms=86400000),  # Every 24 hours
        payload=CronPayload(
            message="Good morning!",
            channel="telegram",
            to="user123",
            deliver=True,
        ),
    )
    
    # Add and start
    await service.add_job(job)
    await service.start()
"""

from cli_bridge.cron.service import CronService
from cli_bridge.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    Schedule,
    ScheduleKind,
)

__all__ = [
    "ScheduleKind",
    "Schedule",
    "CronPayload",
    "CronJobState",
    "CronJob",
    "CronService",
]
