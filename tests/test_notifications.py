from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification
from app.jobs.reconcile import reconcile_company_jobs
from app.matching.engine import MatchResult
from app.notifications.formatter import format_job_alert
from app.notifications.protocol import NotificationError
from app.notifications.service import ensure_pending_notification, mark_notification_result
from app.notifications.telegram import TelegramNotifier
from tests.helpers import make_company, make_source_job


def test_format_job_alert_is_concise() -> None:
    company = make_company(name="Mollie")
    from app.clock import utcnow
    from app.db.models import Job

    now = utcnow()
    job = Job(
        company_id=company.id,
        external_id="1",
        title="Senior Backend Engineer",
        description="Python",
        location="Amsterdam",
        url="https://example.com/apply",
        first_seen_at=now,
        last_seen_at=now,
        active=True,
        consecutive_misses=0,
    )
    text = format_job_alert(
        company,
        job,
        MatchResult(
            score=88,
            reasons=['title contains "backend"', 'description contains "python"'],
            qualifies=True,
        ),
    )
    assert "New matching job" in text
    assert "Mollie" in text
    assert "Senior Backend Engineer" in text
    assert "88/100" in text
    assert "https://example.com/apply" in text
    assert "• title contains" in text


async def test_notification_row_is_unique_per_job_channel(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    result = await reconcile_company_jobs(session, company, [make_source_job()], miss_threshold=2)
    job = result.new_jobs[0][0]
    first = await ensure_pending_notification(session, job, "telegram")
    second = await ensure_pending_notification(session, job, "telegram")
    await session.commit()
    assert first is not None
    assert second is not None
    assert first.id == second.id
    rows = (await session.execute(select(Notification))).scalars().all()
    assert len(rows) == 1


async def test_failed_notification_can_be_retried(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    result = await reconcile_company_jobs(session, company, [make_source_job()], miss_threshold=2)
    job = result.new_jobs[0][0]
    row = await ensure_pending_notification(session, job, "telegram")
    assert row is not None
    await mark_notification_result(session, row, success=False, error="timeout")
    await session.commit()
    assert row.status == "failed"

    again = await ensure_pending_notification(session, job, "telegram")
    assert again is not None
    assert again.status == "failed"
    await mark_notification_result(session, again, success=True)
    await session.commit()
    assert again.status == "sent"
    third = await ensure_pending_notification(session, job, "telegram")
    assert third is None


@pytest.mark.asyncio
async def test_telegram_notifier_does_not_embed_token_in_exceptions() -> None:
    async with respx.mock:
        respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
            return_value=httpx.Response(500, json={"ok": False})
        )
        async with httpx.AsyncClient() as client:
            notifier = TelegramNotifier("SECRETTOKEN", "99", client, max_retries=0)
            with pytest.raises(NotificationError) as exc:
                await notifier.send("hello")
    assert "SECRETTOKEN" not in str(exc.value)


@pytest.mark.asyncio
async def test_telegram_success() -> None:
    async with respx.mock:
        respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {}})
        )
        async with httpx.AsyncClient() as client:
            notifier = TelegramNotifier("SECRETTOKEN", "99", client, max_retries=0)
            await notifier.send("hello")
