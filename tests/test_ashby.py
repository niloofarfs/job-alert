from __future__ import annotations

import httpx
import pytest
import respx

from app.sources.ashby import AshbyAdapter
from app.sources.errors import SourceResponseError
from tests.helpers import make_ref


@pytest.mark.asyncio
async def test_ashby_normal_response() -> None:
    ref = make_ref(source_type="ashby", source_identifier="acme")
    async with respx.mock:
        respx.get(
            "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "34413f8d-26bf-4bbc-8ade-eb309a0e2245",
                            "title": " Software Engineer, Backend",
                            "location": "New York, NY (HQ)",
                            "secondaryLocations": [{"location": "Remote (EU)"}],
                            "isListed": True,
                            "isRemote": True,
                            "workplaceType": "Hybrid",
                            "descriptionPlain": "Python and Postgres",
                            "publishedAt": "2026-04-07T17:12:35.753+00:00",
                            "employmentType": "FullTime",
                            "jobUrl": "https://jobs.ashbyhq.com/acme/34413f8d/",
                            "compensation": {
                                "scrapeableCompensationSalarySummary": "$150K - $180K"
                            },
                        }
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jobs = await AshbyAdapter(max_retries=0).fetch_jobs(ref, client)
    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineer, Backend"
    assert "Remote (EU)" in jobs[0].location
    assert jobs[0].salary_text == "$150K - $180K"
    assert jobs[0].remote_type == "hybrid"


@pytest.mark.asyncio
async def test_ashby_skips_unlisted_and_uses_url_id() -> None:
    ref = make_ref(source_type="ashby", source_identifier="acme")
    async with respx.mock:
        respx.get(
            "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "title": "Hidden",
                            "isListed": False,
                            "jobUrl": "https://jobs.ashbyhq.com/acme/hidden",
                        },
                        {
                            "title": "Visible",
                            "jobUrl": "https://jobs.ashbyhq.com/acme/from-url",
                            "descriptionHtml": "<p>Hello</p>",
                        },
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jobs = await AshbyAdapter(max_retries=0).fetch_jobs(ref, client)
    assert [job.external_id for job in jobs] == ["from-url"]
    assert jobs[0].description == "Hello"


@pytest.mark.asyncio
async def test_ashby_empty() -> None:
    ref = make_ref(source_type="ashby", source_identifier="acme")
    async with respx.mock:
        respx.get(
            "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
        ).mock(return_value=httpx.Response(200, json={"jobs": []}))
        async with httpx.AsyncClient() as client:
            jobs = await AshbyAdapter(max_retries=0).fetch_jobs(ref, client)
    assert jobs == []


@pytest.mark.asyncio
async def test_ashby_malformed() -> None:
    ref = make_ref(source_type="ashby", source_identifier="acme")
    async with respx.mock:
        respx.get(
            "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
        ).mock(return_value=httpx.Response(200, json=["nope"]))
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceResponseError):
                await AshbyAdapter(max_retries=0).fetch_jobs(ref, client)
