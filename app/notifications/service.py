from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.db.models import Job, Notification


async def _load_notification(
    session: AsyncSession, job_id: UUID, channel: str
) -> Notification | None:
    result = await session.execute(
        select(Notification).where(
            Notification.job_id == job_id,
            Notification.channel == channel,
        )
    )
    return result.scalar_one_or_none()


async def ensure_pending_notification(
    session: AsyncSession,
    job: Job,
    channel: str,
) -> Notification | None:
    """Insert a pending row if this job/channel has never been notified.

    Returns the row to send, or None when a successful notification already exists.
    """
    existing = await _load_notification(session, job.id, channel)
    if existing is not None:
        if existing.status == "sent":
            return None
        return existing

    now = utcnow()
    row = Notification(
        job_id=job.id,
        channel=channel,
        status="pending",
        error=None,
        sent_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        existing = await _load_notification(session, job.id, channel)
        if existing is None or existing.status == "sent":
            return None
        return existing
    return row


async def mark_notification_result(
    session: AsyncSession,
    notification: Notification,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    now = utcnow()
    notification.updated_at = now
    if success:
        notification.status = "sent"
        notification.sent_at = now
        notification.error = None
    else:
        notification.status = "failed"
        notification.error = (error or "unknown error")[:2000]
