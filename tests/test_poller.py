from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Company, Job, Notification
from app.notifications.protocol import NotificationError
from app.scheduler.poller import Poller
from app.sources.errors import SourceNetworkError
from app.sources.models import CompanyRef, SourceJob
from tests.helpers import make_company, make_source_job

HC_PING_URL = "https://hc-ping.com/11111111-1111-1111-1111-111111111111"


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


def _enable_healthchecks(settings: Settings) -> None:
    settings.healthchecks_ping_url = HC_PING_URL


async def test_zero_new_jobs_sends_healthchecks_success(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _enable_healthchecks(settings)
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    poller = await _poller(settings, session_factory, adapter, None)
    async with respx.mock(assert_all_called=False) as router:
        start = router.get(f"{HC_PING_URL}/start").mock(return_value=httpx.Response(200, text="OK"))
        success = router.get(HC_PING_URL).mock(return_value=httpx.Response(200, text="OK"))
        fail = router.get(f"{HC_PING_URL}/fail").mock(return_value=httpx.Response(200, text="OK"))
        summary = await poller.run()
    assert summary.companies_ok == 1
    assert summary.new_jobs == 0
    assert start.call_count == 1
    assert success.call_count == 1
    assert fail.call_count == 0


async def test_all_sources_failing_sends_healthchecks_fail(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _enable_healthchecks(settings)
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.errors["acme"] = SourceNetworkError("timeout")
    poller = await _poller(settings, session_factory, adapter, None)
    async with respx.mock(assert_all_called=False) as router:
        start = router.get(f"{HC_PING_URL}/start").mock(return_value=httpx.Response(200, text="OK"))
        success = router.get(HC_PING_URL).mock(return_value=httpx.Response(200, text="OK"))
        fail = router.get(f"{HC_PING_URL}/fail").mock(return_value=httpx.Response(200, text="OK"))
        summary = await poller.run()
    assert summary.companies_ok == 0
    assert summary.companies_failed == 1
    assert start.call_count == 1
    assert success.call_count == 0
    assert fail.call_count == 1


async def test_partial_source_failure_sends_healthchecks_success(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _enable_healthchecks(settings)
    await _add_company(session_factory, source_identifier="ok")
    await _add_company(
        session_factory, name="Broken", source_identifier="broken", source_type="greenhouse"
    )
    adapter = ScriptedAdapter()
    adapter.payloads["ok"] = [make_source_job()]
    adapter.errors["broken"] = SourceNetworkError("timeout")
    poller = await _poller(settings, session_factory, adapter, None)
    async with respx.mock(assert_all_called=False) as router:
        start = router.get(f"{HC_PING_URL}/start").mock(return_value=httpx.Response(200, text="OK"))
        success = router.get(HC_PING_URL).mock(return_value=httpx.Response(200, text="OK"))
        fail = router.get(f"{HC_PING_URL}/fail").mock(return_value=httpx.Response(200, text="OK"))
        summary = await poller.run()
    assert summary.companies_ok == 1
    assert summary.companies_failed == 1
    assert start.call_count == 1
    assert success.call_count == 1
    assert fail.call_count == 0


async def test_unhandled_poll_error_sends_healthchecks_fail(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _enable_healthchecks(settings)
    settings.matching_config_path = Path("/nonexistent/matching.yaml")
    poller = await _poller(settings, session_factory, ScriptedAdapter(), None)
    async with respx.mock(assert_all_called=False) as router:
        start = router.get(f"{HC_PING_URL}/start").mock(return_value=httpx.Response(200, text="OK"))
        success = router.get(HC_PING_URL).mock(return_value=httpx.Response(200, text="OK"))
        fail = router.get(f"{HC_PING_URL}/fail").mock(return_value=httpx.Response(200, text="OK"))
        with pytest.raises(FileNotFoundError):
            await poller.run()
    assert start.call_count == 1
    assert success.call_count == 0
    assert fail.call_count == 1


async def test_overlapping_poll_does_not_ping_healthchecks(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _enable_healthchecks(settings)
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job()]
    adapter._hold = asyncio.Event()
    adapter._started = asyncio.Event()
    poller = await _poller(settings, session_factory, adapter, None)
    async with respx.mock(assert_all_called=False) as router:
        start = router.get(f"{HC_PING_URL}/start").mock(return_value=httpx.Response(200, text="OK"))
        success = router.get(HC_PING_URL).mock(return_value=httpx.Response(200, text="OK"))
        fail = router.get(f"{HC_PING_URL}/fail").mock(return_value=httpx.Response(200, text="OK"))
        first = asyncio.create_task(poller.run())
        await adapter._started.wait()
        skipped = await poller.run()
        assert skipped.skipped is True
        adapter._hold.set()
        await first
    assert start.call_count == 1
    assert success.call_count == 1
    assert fail.call_count == 0


async def test_healthchecks_disabled_makes_no_http(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    poller = await _poller(settings, session_factory, adapter, None)
    async with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=AssertionError("unexpected HTTP"))
        summary = await poller.run()
    assert summary.companies_ok == 1
    assert settings.healthchecks_ping_url is None


async def test_healthchecks_failure_does_not_drop_jobs(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _enable_healthchecks(settings)
    caplog.set_level(logging.WARNING)
    await _add_company(session_factory)
    adapter = ScriptedAdapter()
    adapter.payloads["acme"] = [make_source_job()]
    poller = await _poller(settings, session_factory, adapter, None)
    async with respx.mock(assert_all_called=False) as router:
        router.get(f"{HC_PING_URL}/start").mock(side_effect=httpx.TimeoutException("timeout"))
        router.get(HC_PING_URL).mock(return_value=httpx.Response(503, text="down"))
        summary = await poller.run()
    assert summary.companies_ok == 1
    async with session_factory() as session:
        jobs = (await session.execute(select(Job))).scalars().all()
        company = (await session.execute(select(Company))).scalar_one()
        assert len(jobs) == 1
        assert company.baseline_completed_at is not None
    assert "11111111-1111-1111-1111-111111111111" not in caplog.text
    assert HC_PING_URL not in caplog.text
