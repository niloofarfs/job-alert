from __future__ import annotations

import re

import httpx
import pytest
import respx

from app.sources.errors import SourceNetworkError, SourceResponseError
from app.sources.lever import LeverAdapter
from tests.helpers import make_ref

LEVER_RE = re.compile(r"https://api\.lever\.co/v0/postings/acme")
LEVER_EU_RE = re.compile(r"https://api\.eu\.lever\.co/v0/postings/acme")


def _job(i: int) -> dict[str, object]:
    return {
        "id": f"id-{i}",
        "text": f"Software Engineer {i}",
        "hostedUrl": f"https://jobs.lever.co/acme/id-{i}",
        "categories": {"location": "Remote", "commitment": "Full-time"},
        "workplaceType": "remote",
        "createdAt": 1753564800000,
        "descriptionPlain": "Python backend",
    }


@pytest.mark.asyncio
async def test_lever_normal_response() -> None:
    ref = make_ref(source_type="lever", source_identifier="acme")
    async with respx.mock:
        respx.get(url__regex=LEVER_RE).mock(return_value=httpx.Response(200, json=[_job(1)]))
        async with httpx.AsyncClient() as client:
            jobs = await LeverAdapter(max_retries=0).fetch_jobs(ref, client)
    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineer 1"
    assert jobs[0].remote_type == "remote"
    assert jobs[0].published_at is not None
    assert jobs[0].description == "Python backend"


@pytest.mark.asyncio
async def test_lever_empty_array() -> None:
    ref = make_ref(source_type="lever", source_identifier="acme")
    async with respx.mock:
        respx.get(url__regex=LEVER_RE).mock(return_value=httpx.Response(200, json=[]))
        async with httpx.AsyncClient() as client:
            jobs = await LeverAdapter(max_retries=0).fetch_jobs(ref, client)
    assert jobs == []


@pytest.mark.asyncio
async def test_lever_pagination() -> None:
    ref = make_ref(source_type="lever", source_identifier="acme")
    adapter = LeverAdapter(max_retries=0, page_size=2)
    async with respx.mock:
        respx.get(url__regex=LEVER_RE).mock(
            side_effect=lambda request: httpx.Response(
                200,
                json=[_job(1), _job(2)] if request.url.params.get("skip") == "0" else [_job(3)],
            )
        )
        async with httpx.AsyncClient() as client:
            jobs = await adapter.fetch_jobs(ref, client)
    assert [job.external_id for job in jobs] == ["id-1", "id-2", "id-3"]


@pytest.mark.asyncio
async def test_lever_eu_host() -> None:
    ref = make_ref(source_type="lever", source_identifier="eu/acme")
    async with respx.mock:
        respx.get(url__regex=LEVER_EU_RE).mock(return_value=httpx.Response(200, json=[_job(1)]))
        async with httpx.AsyncClient() as client:
            jobs = await LeverAdapter(max_retries=0).fetch_jobs(ref, client)
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_lever_object_payload_is_invalid() -> None:
    ref = make_ref(source_type="lever", source_identifier="acme")
    async with respx.mock:
        respx.get(url__regex=LEVER_RE).mock(return_value=httpx.Response(200, json={"jobs": []}))
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceResponseError):
                await LeverAdapter(max_retries=0).fetch_jobs(ref, client)


@pytest.mark.asyncio
async def test_lever_http_failure() -> None:
    ref = make_ref(source_type="lever", source_identifier="acme")
    async with respx.mock:
        respx.get(url__regex=LEVER_RE).mock(return_value=httpx.Response(503, text="down"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceNetworkError):
                await LeverAdapter(max_retries=0).fetch_jobs(ref, client)
