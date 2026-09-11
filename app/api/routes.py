from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CompanyOut,
    HealthOut,
    JobDetailOut,
    JobListOut,
    PollTriggerOut,
    StatsOut,
)
from app.db.models import Company, Job, Notification
from app.scheduler.poller import Poller

router = APIRouter()


def get_poller(request: Request) -> Poller:
    poller: Poller = request.app.state.poller
    return poller


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(db_session)]
PollerDep = Annotated[Poller, Depends(get_poller)]


@router.get("/health", response_model=HealthOut)
async def health(session: SessionDep, poller: PollerDep) -> HealthOut:
    try:
        await session.execute(select(1))
        database = "ok"
        status = "ok"
    except Exception:
        database = "error"
        status = "degraded"
    return HealthOut(
        status=status,
        database=database,
        last_poll_started_at=poller.status.last_started_at,
        last_poll_finished_at=poller.status.last_finished_at,
        poll_in_progress=poller.status.in_progress,
    )


@router.get("/companies", response_model=list[CompanyOut])
async def list_companies(session: SessionDep) -> list[Company]:
    result = await session.execute(
        select(Company).order_by(Company.priority.desc(), Company.name.asc())
    )
    return list(result.scalars().all())


@router.get("/jobs", response_model=list[JobListOut])
async def list_jobs(
    session: SessionDep,
    active: bool | None = None,
    company_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[JobListOut]:
    stmt = select(Job, Company.name).join(Company, Job.company_id == Company.id)
    if active is not None:
        stmt = stmt.where(Job.active.is_(active))
    if company_id is not None:
        stmt = stmt.where(Job.company_id == company_id)
    stmt = stmt.order_by(Job.first_seen_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).all()
    items: list[JobListOut] = []
    for job, company_name in rows:
        item = JobListOut.model_validate(job)
        item.company_name = company_name
        items.append(item)
    return items


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
async def get_job(job_id: UUID, session: SessionDep) -> JobDetailOut:
    stmt = (
        select(Job, Company.name)
        .join(Company, Job.company_id == Company.id)
        .where(Job.id == job_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job, company_name = row
    item = JobDetailOut.model_validate(job)
    item.company_name = company_name
    return item


@router.get("/stats", response_model=StatsOut)
async def stats(session: SessionDep, poller: PollerDep) -> StatsOut:
    companies = await session.scalar(select(func.count()).select_from(Company)) or 0
    enabled = (
        await session.scalar(
            select(func.count()).select_from(Company).where(Company.enabled.is_(True))
        )
        or 0
    )
    active_jobs = (
        await session.scalar(select(func.count()).select_from(Job).where(Job.active.is_(True))) or 0
    )
    inactive_jobs = (
        await session.scalar(select(func.count()).select_from(Job).where(Job.active.is_(False)))
        or 0
    )
    sent = (
        await session.scalar(
            select(func.count()).select_from(Notification).where(Notification.status == "sent")
        )
        or 0
    )
    failed = (
        await session.scalar(
            select(func.count()).select_from(Notification).where(Notification.status == "failed")
        )
        or 0
    )
    return StatsOut(
        companies=companies,
        companies_enabled=enabled,
        jobs_active=active_jobs,
        jobs_inactive=inactive_jobs,
        notifications_sent=sent,
        notifications_failed=failed,
        last_poll_started_at=poller.status.last_started_at,
        last_poll_finished_at=poller.status.last_finished_at,
        last_poll_companies_ok=poller.status.last_companies_ok,
        last_poll_companies_failed=poller.status.last_companies_failed,
        last_poll_new_jobs=poller.status.last_new_jobs,
        last_poll_notified=poller.status.last_notified,
    )


@router.post("/poll", response_model=PollTriggerOut)
async def trigger_poll(request: Request, poller: PollerDep) -> PollTriggerOut:
    if poller.is_running():
        return PollTriggerOut(status="already_running", detail="A poll cycle is in progress")
    request.app.state.poll_task = asyncio.create_task(poller.run())
    return PollTriggerOut(status="started")
