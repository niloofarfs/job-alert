from __future__ import annotations

import httpx
import pytest
import respx

from app.sources.errors import SourceConfigError, SourceNetworkError, SourceResponseError
from app.sources.greenhouse import GreenhouseAdapter
from tests.helpers import make_ref


def _adapter() -> GreenhouseAdapter:
    return GreenhouseAdapter(max_retries=0)


@pytest.mark.asyncio
async def test_greenhouse_normal_response() -> None:
    ref = make_ref()
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 8023928,
                            "title": "Backend Engineer",
                            "location": {"name": "Amsterdam"},
                            "absolute_url": "https://example.com/jobs/8023928",
                            "first_published": "2026-07-30T06:59:38-04:00",
                            "updated_at": "2026-08-04T07:02:31-04:00",
                            "content": (
                                "&amp;lt;p&amp;gt;We use Python and FastAPI&amp;lt;/p&amp;gt;"
                            ),
                        }
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jobs = await _adapter().fetch_jobs(ref, client)
    assert len(jobs) == 1
    assert jobs[0].external_id == "8023928"
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].location == "Amsterdam"
    assert "Python" in jobs[0].description
    assert "<" not in jobs[0].description
    assert jobs[0].published_at is not None


@pytest.mark.asyncio
async def test_greenhouse_empty_results() -> None:
    ref = make_ref()
    async with respx.mock:
        respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
            return_value=httpx.Response(200, json={"jobs": []})
        )
        async with httpx.AsyncClient() as client:
            jobs = await _adapter().fetch_jobs(ref, client)
    assert jobs == []


@pytest.mark.asyncio
async def test_greenhouse_skips_malformed_job() -> None:
    ref = make_ref()
    async with respx.mock:
        respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {"id": 1, "title": "Good", "absolute_url": "https://example.com/1"},
                        {"title": "Missing id and url"},
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jobs = await _adapter().fetch_jobs(ref, client)
    assert [job.external_id for job in jobs] == ["1"]


@pytest.mark.asyncio
async def test_greenhouse_malformed_payload() -> None:
    ref = make_ref()
    async with respx.mock:
        respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
            return_value=httpx.Response(200, json={"nope": True})
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceResponseError):
                await _adapter().fetch_jobs(ref, client)


@pytest.mark.asyncio
async def test_greenhouse_http_404() -> None:
    ref = make_ref()
    async with respx.mock:
        respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceConfigError):
                await _adapter().fetch_jobs(ref, client)


@pytest.mark.asyncio
async def test_greenhouse_http_500() -> None:
    ref = make_ref()
    async with respx.mock:
        respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
            return_value=httpx.Response(500, text="nope")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceNetworkError):
                await _adapter().fetch_jobs(ref, client)
