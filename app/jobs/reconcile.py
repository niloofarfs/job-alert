from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.db.models import Company, Job
from app.sources.models import SourceJob

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReconcileResult:
    new_jobs: list[tuple[Job, SourceJob]]
    updated_count: int
    inactivated_count: int
    reactivated_count: int
    is_baseline: bool


def _apply_source_fields(row: Job, source: SourceJob) -> None:
    row.title = source.title
    row.description = source.description
    row.location = source.location
    row.remote_type = source.remote_type
    row.employment_type = source.employment_type
    row.salary_text = source.salary_text
    row.url = source.url
    if source.published_at is not None:
        row.published_at = source.published_at
    if source.source_updated_at is not None:
        row.source_updated_at = source.source_updated_at
    row.raw_payload = source.raw_payload or None


async def reconcile_company_jobs(
    session: AsyncSession,
    company: Company,
    source_jobs: list[SourceJob],
    *,
    miss_threshold: int,
) -> ReconcileResult:
    """Persist a successful fetch. Caller must commit.

    Failed fetches must not call this function — disappearance is only inferred
    from a successful ATS response.
    """
    now = utcnow()
    result = await session.execute(select(Job).where(Job.company_id == company.id))
    existing = {job.external_id: job for job in result.scalars().all()}

    incoming: dict[str, SourceJob] = {}
    for source in source_jobs:
        if source.external_id in incoming:
            logger.warning(
                "duplicate_source_job company=%s source_type=%s external_id=%s",
                company.name,
                company.source_type,
                source.external_id,
            )
            continue
        incoming[source.external_id] = source

    new_jobs: list[tuple[Job, SourceJob]] = []
    updated_count = 0
    reactivated_count = 0
    seen: set[str] = set()

    for external_id, source in incoming.items():
        seen.add(external_id)
        row = existing.get(external_id)
        if row is None:
            row = Job(
                company_id=company.id,
                external_id=external_id,
                title=source.title,
                description=source.description,
                location=source.location,
                remote_type=source.remote_type,
                employment_type=source.employment_type,
                salary_text=source.salary_text,
                url=source.url,
                published_at=source.published_at,
                first_seen_at=now,
                last_seen_at=now,
                source_updated_at=source.source_updated_at,
                active=True,
                consecutive_misses=0,
                raw_payload=source.raw_payload or None,
            )
            session.add(row)
            new_jobs.append((row, source))
            continue

        was_inactive = not row.active
        _apply_source_fields(row, source)
        row.last_seen_at = now
        row.consecutive_misses = 0
        row.active = True
        updated_count += 1
        if was_inactive:
            reactivated_count += 1

    inactivated_count = 0
    for external_id, row in existing.items():
        if external_id in seen:
            continue
        row.consecutive_misses += 1
        if row.consecutive_misses >= miss_threshold and row.active:
            row.active = False
            inactivated_count += 1

    is_baseline = company.baseline_completed_at is None
    if is_baseline:
        company.baseline_completed_at = now
        company.updated_at = now

    await session.flush()
    return ReconcileResult(
        new_jobs=new_jobs,
        updated_count=updated_count,
        inactivated_count=inactivated_count,
        reactivated_count=reactivated_count,
        is_baseline=is_baseline,
    )
