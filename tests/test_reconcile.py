from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.jobs.reconcile import reconcile_company_jobs
from tests.helpers import make_company, make_source_job


async def test_baseline_does_not_treat_jobs_as_alertable(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()

    result = await reconcile_company_jobs(
        session,
        company,
        [make_source_job(external_id="a"), make_source_job(external_id="b", title="Two")],
        miss_threshold=2,
    )
    await session.commit()

    assert result.is_baseline is True
    assert len(result.new_jobs) == 2
    assert company.baseline_completed_at is not None
    stored = (await session.execute(select(Job))).scalars().all()
    assert len(stored) == 2


async def test_existing_jobs_are_not_new_on_second_fetch(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    await reconcile_company_jobs(
        session, company, [make_source_job(external_id="a")], miss_threshold=2
    )
    await session.commit()

    result = await reconcile_company_jobs(
        session, company, [make_source_job(external_id="a")], miss_threshold=2
    )
    await session.commit()

    assert result.is_baseline is False
    assert result.new_jobs == []
    assert result.updated_count == 1


async def test_newly_appearing_job_is_recognized(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    await reconcile_company_jobs(
        session, company, [make_source_job(external_id="a")], miss_threshold=2
    )
    await session.commit()

    result = await reconcile_company_jobs(
        session,
        company,
        [make_source_job(external_id="a"), make_source_job(external_id="new")],
        miss_threshold=2,
    )
    await session.commit()

    assert result.is_baseline is False
    assert [job.external_id for job, _ in result.new_jobs] == ["new"]


async def test_duplicate_source_result_does_not_insert_twice(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    first = make_source_job(external_id="dup", title="One")
    second = make_source_job(external_id="dup", title="Two")
    result = await reconcile_company_jobs(session, company, [first, second], miss_threshold=2)
    await session.commit()
    rows = (await session.execute(select(Job))).scalars().all()
    assert len(rows) == 1
    assert rows[0].title == "One"
    assert len(result.new_jobs) == 1


async def test_job_not_inactivated_after_single_miss(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    await reconcile_company_jobs(
        session, company, [make_source_job(external_id="a")], miss_threshold=2
    )
    await session.commit()

    result = await reconcile_company_jobs(session, company, [], miss_threshold=2)
    await session.commit()
    job = (await session.execute(select(Job))).scalar_one()
    assert job.active is True
    assert job.consecutive_misses == 1
    assert result.inactivated_count == 0

    result = await reconcile_company_jobs(session, company, [], miss_threshold=2)
    await session.commit()
    await session.refresh(job)
    assert job.active is False
    assert job.consecutive_misses == 2
    assert result.inactivated_count == 1


async def test_reappearance_reactivates_job(session: AsyncSession) -> None:
    company = make_company()
    session.add(company)
    await session.flush()
    await reconcile_company_jobs(
        session, company, [make_source_job(external_id="a")], miss_threshold=2
    )
    await reconcile_company_jobs(session, company, [], miss_threshold=2)
    await reconcile_company_jobs(session, company, [], miss_threshold=2)
    await session.commit()
    job = (await session.execute(select(Job))).scalar_one()
    assert job.active is False

    result = await reconcile_company_jobs(
        session, company, [make_source_job(external_id="a")], miss_threshold=2
    )
    await session.commit()
    await session.refresh(job)
    assert job.active is True
    assert job.consecutive_misses == 0
    assert result.reactivated_count == 1
    assert result.new_jobs == []
