from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.clock import utcnow
from app.config import Settings
from app.db.models import Company, Job, Notification
from app.jobs.normalize import normalize_source_job
from app.jobs.reconcile import reconcile_company_jobs
from app.matching.config import MatchingRules, load_matching_rules
from app.matching.engine import MatchResult, score_job
from app.notifications.formatter import format_job_alert
from app.notifications.protocol import NotificationError, Notifier
from app.notifications.service import (
    ensure_pending_notification,
    mark_notification_result,
)
from app.scheduler.status import CompanyPollOutcome, PollStatus, PollSummary
from app.sources.errors import SourceError
from app.sources.models import CompanyRef, SourceJob
from app.sources.protocol import SourceAdapter
from app.sources.registry import get_adapter

logger = logging.getLogger(__name__)


def to_company_ref(company: Company) -> CompanyRef:
    return CompanyRef(
        id=company.id,
        name=company.name,
        source_type=company.source_type,
        source_identifier=company.source_identifier,
        careers_url=company.careers_url,
    )


class Poller:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient,
        adapters: Mapping[str, SourceAdapter],
        notifier: Notifier | None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._http = http_client
        self._adapters = adapters
        self._notifier = notifier
        self._lock = asyncio.Lock()
        self.status = PollStatus()

    async def run(self) -> PollSummary:
        if self._lock.locked():
            logger.info("poll_skipped reason=already_running")
            self.status.last_skipped = True
            return PollSummary(skipped=True)
        await self._lock.acquire()
        try:
            return await self._run_locked()
        finally:
            self._lock.release()

    def is_running(self) -> bool:
        return self.status.in_progress or self._lock.locked()

    async def _run_locked(self) -> PollSummary:
        started = utcnow()
        self.status.in_progress = True
        self.status.last_started_at = started
        self.status.last_skipped = False
        self.status.last_error = None
        logger.info("poll_started interval_s=%s", self._settings.poll_interval_seconds)
        try:
            rules = load_matching_rules(self._settings.matching_config_path)
            companies = await self._load_enabled_companies()
            semaphore = asyncio.Semaphore(self._settings.company_concurrency)
            outcomes = await asyncio.gather(
                *(self._guarded_company(company, rules, semaphore) for company in companies)
            )
            if self._notifier is not None:
                retried = await self._retry_failed_notifications(rules)
            else:
                retried = 0
            summary = PollSummary(
                companies_ok=sum(1 for item in outcomes if item.ok),
                companies_failed=sum(1 for item in outcomes if not item.ok),
                new_jobs=sum(item.new_jobs for item in outcomes),
                notified=sum(item.notified for item in outcomes) + retried,
                outcomes=list(outcomes),
            )
            self.status.last_companies_ok = summary.companies_ok
            self.status.last_companies_failed = summary.companies_failed
            self.status.last_new_jobs = summary.new_jobs
            self.status.last_notified = summary.notified
            logger.info(
                "poll_completed companies_ok=%s companies_failed=%s new_jobs=%s notified=%s",
                summary.companies_ok,
                summary.companies_failed,
                summary.new_jobs,
                summary.notified,
            )
            return summary
        except Exception as exc:
            self.status.last_error = type(exc).__name__
            logger.exception("poll_failed error=%s", type(exc).__name__)
            raise
        finally:
            self.status.in_progress = False
            self.status.last_finished_at = utcnow()

    async def _load_enabled_companies(self) -> list[Company]:
        async with self._sessions() as session:
            result = await session.execute(
                select(Company)
                .where(Company.enabled.is_(True))
                .order_by(Company.priority.desc(), Company.name.asc())
            )
            companies = list(result.scalars().all())
            session.expunge_all()
            return companies

    async def _guarded_company(
        self,
        company: Company,
        rules: MatchingRules,
        semaphore: asyncio.Semaphore,
    ) -> CompanyPollOutcome:
        async with semaphore:
            try:
                return await self._poll_company(company, rules)
            except Exception as exc:
                logger.exception(
                    "company_poll_failed company=%s source_type=%s operation=poll error=%s",
                    company.name,
                    company.source_type,
                    type(exc).__name__,
                )
                return CompanyPollOutcome(
                    company_id=str(company.id),
                    company_name=company.name,
                    ok=False,
                    error=type(exc).__name__,
                )

    async def _poll_company(self, company: Company, rules: MatchingRules) -> CompanyPollOutcome:
        logger.info(
            "company_poll_started company=%s source_type=%s",
            company.name,
            company.source_type,
        )
        try:
            adapter = get_adapter(company.source_type, dict(self._adapters))
        except SourceError as exc:
            logger.warning(
                "adapter_failure company=%s source_type=%s operation=resolve error=%s",
                company.name,
                company.source_type,
                exc,
            )
            return CompanyPollOutcome(
                company_id=str(company.id),
                company_name=company.name,
                ok=False,
                error=type(exc).__name__,
            )

        ref = to_company_ref(company)
        try:
            fetched = await adapter.fetch_jobs(ref, self._http)
        except SourceError as exc:
            logger.warning(
                "adapter_failure company=%s source_type=%s operation=fetch error=%s",
                company.name,
                company.source_type,
                type(exc).__name__,
            )
            return CompanyPollOutcome(
                company_id=str(company.id),
                company_name=company.name,
                ok=False,
                error=type(exc).__name__,
            )

        normalized: list[SourceJob] = []
        for raw in fetched:
            parsed_job = normalize_source_job(raw)
            if parsed_job is None:
                logger.warning(
                    "skipped_malformed_job company=%s source_type=%s",
                    company.name,
                    company.source_type,
                )
                continue
            normalized.append(parsed_job)

        logger.info(
            "jobs_fetched company=%s source_type=%s count=%s",
            company.name,
            company.source_type,
            len(normalized),
        )

        async with self._sessions() as session:
            async with session.begin():
                db_company = await session.get(Company, company.id)
                if db_company is None:
                    raise RuntimeError(f"Company {company.id} disappeared during poll")
                reconcile = await reconcile_company_jobs(
                    session,
                    db_company,
                    normalized,
                    miss_threshold=self._settings.miss_threshold,
                )
                new_pairs = list(reconcile.new_jobs)
                is_baseline = reconcile.is_baseline

        logger.info(
            "company_poll_completed company=%s source_type=%s fetched=%s new=%s baseline=%s",
            company.name,
            company.source_type,
            len(normalized),
            len(new_pairs),
            is_baseline,
        )

        if is_baseline:
            logger.info(
                "baseline_established company=%s jobs=%s alerts=suppressed",
                company.name,
                len(new_pairs),
            )
            return CompanyPollOutcome(
                company_id=str(company.id),
                company_name=company.name,
                ok=True,
                fetched=len(normalized),
                new_jobs=len(new_pairs),
                baseline=True,
            )

        if not new_pairs:
            return CompanyPollOutcome(
                company_id=str(company.id),
                company_name=company.name,
                ok=True,
                fetched=len(normalized),
            )

        enriched_pairs = await self._enrich_new_jobs(adapter, ref, new_pairs)
        await self._persist_enriched(enriched_pairs)

        notified = 0
        for job, source in enriched_pairs:
            logger.info(
                "new_job_detected company=%s external_id=%s title=%s",
                company.name,
                source.external_id,
                source.title,
            )
            match = score_job(source, rules)
            logger.info(
                "matching_result company=%s title=%s score=%s qualifies=%s",
                company.name,
                source.title,
                match.score,
                match.qualifies,
            )
            if not match.qualifies or self._notifier is None:
                continue
            if await self._notify(company, job, match):
                notified += 1

        return CompanyPollOutcome(
            company_id=str(company.id),
            company_name=company.name,
            ok=True,
            fetched=len(normalized),
            new_jobs=len(new_pairs),
            notified=notified,
        )

    async def _enrich_new_jobs(
        self,
        adapter: SourceAdapter,
        ref: CompanyRef,
        pairs: list[tuple[Job, SourceJob]],
    ) -> list[tuple[Job, SourceJob]]:
        semaphore = asyncio.Semaphore(3)

        async def enrich_one(job: Job, source: SourceJob) -> tuple[Job, SourceJob]:
            async with semaphore:
                try:
                    enriched = await adapter.enrich_job(ref, source, self._http)
                except SourceError:
                    logger.warning(
                        "adapter_failure company=%s source_type=%s operation=enrich external_id=%s",
                        ref.name,
                        ref.source_type,
                        source.external_id,
                    )
                    return job, source
                return enrich_into_job(job, enriched)

        results = await asyncio.gather(*(enrich_one(job, source) for job, source in pairs))
        return list(results)

    async def _persist_enriched(self, pairs: list[tuple[Job, SourceJob]]) -> None:
        async with self._sessions() as session:
            async with session.begin():
                for job, source in pairs:
                    db_job = await session.get(Job, job.id)
                    if db_job is None:
                        continue
                    db_job.description = source.description
                    db_job.location = source.location
                    db_job.remote_type = source.remote_type
                    db_job.employment_type = source.employment_type
                    db_job.salary_text = source.salary_text
                    db_job.url = source.url
                    db_job.title = source.title
                    if source.published_at is not None:
                        db_job.published_at = source.published_at
                    db_job.raw_payload = source.raw_payload or None
                    job.description = source.description
                    job.location = source.location
                    job.title = source.title
                    job.url = source.url

    async def _notify(self, company: Company, job: Job, match: MatchResult) -> bool:
        assert self._notifier is not None
        async with self._sessions() as session:
            async with session.begin():
                notification = await ensure_pending_notification(
                    session, job, self._notifier.channel
                )
                if notification is None:
                    return False
                notification_id = notification.id

        text = format_job_alert(company, job, match)
        success = True
        error: str | None = None
        try:
            await self._notifier.send(text)
        except NotificationError as exc:
            success = False
            error = str(exc)
            logger.warning(
                "notification_failed channel=%s company=%s job_id=%s error=%s",
                self._notifier.channel,
                company.name,
                job.id,
                type(exc).__name__,
            )

        async with self._sessions() as session:
            async with session.begin():
                row = await session.get(Notification, notification_id)
                if row is None or row.status == "sent":
                    return False
                await mark_notification_result(session, row, success=success, error=error)

        if success:
            logger.info(
                "notification_sent channel=%s company=%s job_id=%s title=%s score=%s",
                self._notifier.channel,
                company.name,
                job.id,
                job.title,
                match.score,
            )
        return success

    async def _retry_failed_notifications(self, rules: MatchingRules) -> int:
        assert self._notifier is not None
        async with self._sessions() as session:
            result = await session.execute(
                select(Notification)
                .options(selectinload(Notification.job).selectinload(Job.company))
                .where(
                    Notification.channel == self._notifier.channel,
                    Notification.status.in_(("pending", "failed")),
                )
            )
            rows = list(result.scalars().all())
            session.expunge_all()

        sent = 0
        for row in rows:
            job = row.job
            company = job.company
            source = job_to_source_job(job)
            match = score_job(source, rules)
            if not match.qualifies:
                continue
            if await self._notify(company, job, match):
                sent += 1
        return sent


def enrich_into_job(job: Job, source: SourceJob) -> tuple[Job, SourceJob]:
    job.title = source.title
    job.description = source.description
    job.location = source.location
    job.remote_type = source.remote_type
    job.employment_type = source.employment_type
    job.salary_text = source.salary_text
    job.url = source.url
    job.published_at = source.published_at or job.published_at
    job.raw_payload = source.raw_payload or job.raw_payload
    return job, source


def job_to_source_job(job: Job) -> SourceJob:
    return SourceJob(
        external_id=job.external_id,
        title=job.title,
        url=job.url,
        description=job.description,
        location=job.location,
        remote_type=job.remote_type,
        employment_type=job.employment_type,
        salary_text=job.salary_text,
        published_at=job.published_at,
        source_updated_at=job.source_updated_at,
        raw_payload=job.raw_payload or {},
    )
