from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.scheduler.poller import Poller


def build_scheduler(poller: Poller, interval_seconds: int) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        poller.run,
        trigger="interval",
        seconds=interval_seconds,
        id="poll-companies",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
        replace_existing=True,
    )
    return scheduler
