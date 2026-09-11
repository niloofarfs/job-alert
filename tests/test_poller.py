from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Company, Notification
from app.notifications.protocol import NotificationError
from app.scheduler.poller import Poller
from app.sources.errors import SourceNetworkError
from app.sources.models import CompanyRef, SourceJob
from tests.helpers import make_company, make_source_job


class ScriptedAdapter:
    source_type = "greenhouse"

    def __init__(self) -> None:
        self.payloads: dict[str, list[SourceJob]] = {}
        self.errors: dict[str, Exception] = {}
        self.calls: list[str] = []
        self._hold: asyncio.Event | None = None
        self._started: asyncio.Event | None = None

    async def fetch_jobs(self, company: CompanyRef, client: httpx.AsyncClient) -> list[SourceJob]:
        self.calls.append(company.source_identifier)
        if self._started is not None:
            self._started.set()
        if self._hold is not None:
            await self._hold.wait()
        error = self.errors.get(company.source_identifier)
        if error is not None:
            raise error
        return list(self.payloads.get(company.source_identifier, []))

    async def enrich_job(
        self, company: CompanyRef, job: SourceJob, client: httpx.AsyncClient
    ) -> SourceJob:
        return job


class RecordingNotifier:
    channel = "telegram"

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.failures_remaining = 0

    async def send(self, text: str) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise NotificationError("telegram down")
        self.messages.append(text)


async def _add_company(factory: async_sessionmaker[AsyncSession], **overrides: object) -> UUID:
    async with factory() as session:
        async with session.begin():
            company = make_company(**overrides)
            session.add(company)
            await session.flush()
            return company.id


async def _poller(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    adapter: ScriptedAdapter,
    notifier: RecordingNotifier | None,
) -> Poller:
    return Poller(
        settings=settings,
        session_factory=factory,
        http_client=httpx.AsyncClient(),
        adapters={"greenhouse": adapter},
        notifier=notifier,
    )


async def test_baseline_does_not_notify(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job()]
    notifier = RecordingNotifier()
    poller = await _poller(settings, session_factory, adapter, notifier)
    summary = await poller.run()
    assert summary.companies_ok == 1
    assert summary.new_jobs == 1
    assert notifier.messages == []
    async with session_factory() as session:
        company = (await session.execute(select(Company))).scalar_one()
        assert company.baseline_completed_at is not None


async def test_only_new_matching_jobs_are_notified(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job(external_id="old")]
    notifier = RecordingNotifier()
    poller = await _poller(settings, session_factory, adapter, notifier)
    await poller.run()
    assert notifier.messages == []

    adapter.payloads["acme"] = [
        make_source_job(external_id="old"),
        make_source_job(external_id="new", title="Senior Backend Engineer"),
    ]
    await poller.run()
    assert len(notifier.messages) == 1
    assert "Senior Backend Engineer" in notifier.messages[0]

    await poller.run()
    assert len(notifier.messages) == 1


async def test_one_company_failure_does_not_stop_others(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory, source_identifier="ok")
    await _add_company(
        session_factory, name="Broken", source_identifier="broken", source_type="greenhouse"
    )
    adapter = ScriptedAdapter()
    adapter.payloads["ok"] = [make_source_job()]
    adapter.errors["broken"] = SourceNetworkError("timeout")
    notifier = RecordingNotifier()
    poller = await _poller(settings, session_factory, adapter, notifier)
    summary = await poller.run()
    assert summary.companies_ok == 1
    assert summary.companies_failed == 1
    assert "broken" in adapter.calls and "ok" in adapter.calls


async def test_restart_does_not_renotify(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job()]
    notifier = RecordingNotifier()
    poller = await _poller(settings, session_factory, adapter, notifier)
    await poller.run()
    adapter.payloads["acme"] = [
        make_source_job(),
        make_source_job(external_id="fresh", title="Python Software Engineer"),
    ]
    await poller.run()
    assert len(notifier.messages) == 1

    poller2 = await _poller(settings, session_factory, adapter, notifier)
    await poller2.run()
    assert len(notifier.messages) == 1
    async with session_factory() as session:
        sent = (await session.execute(select(Notification))).scalars().all()
        assert len(sent) == 1
        assert sent[0].status == "sent"


async def test_telegram_failure_does_not_drop_job(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job()]
    notifier = RecordingNotifier()
    poller = await _poller(settings, session_factory, adapter, notifier)
    await poller.run()
    notifier.failures_remaining = 2
    adapter.payloads["acme"] = [
        make_source_job(),
        make_source_job(external_id="fresh"),
    ]
    await poller.run()
    async with session_factory() as session:
        from app.db.models import Job

        jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 2
        rows = (await session.execute(select(Notification))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
    await poller.run()
    assert len(notifier.messages) == 1


async def test_overlapping_poll_is_skipped(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job()]
    adapter._hold = asyncio.Event()
    adapter._started = asyncio.Event()
    poller = await _poller(settings, session_factory, adapter, RecordingNotifier())
    first = asyncio.create_task(poller.run())
    await adapter._started.wait()
    skipped = await poller.run()
    assert skipped.skipped is True
    adapter._hold.set()
    await first
