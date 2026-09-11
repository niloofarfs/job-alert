from __future__ import annotations

import httpx
import pytest
import respx

from app.sources.errors import SourceConfigError, SourceNetworkError
from app.sources.workday import WORKDAY_PAGE_SIZE, WorkdayAdapter, parse_workday_identifier
from tests.helpers import make_ref, make_source_job

LIST_URL = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"


def _posting(i: int) -> dict[str, object]:
    return {
        "title": f"Software Engineer {i}",
        "externalPath": f"/job/US-CA/Software-Engineer-{i}_JR{i}",
        "locationsText": "Santa Clara, California",
        "postedOn": "Posted Yesterday",
    }


@pytest.mark.asyncio
async def test_workday_normal_and_pagination() -> None:
    identifier = "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"
    ref = make_ref(source_type="workday", source_identifier=identifier)
    first_page = [_posting(i) for i in range(WORKDAY_PAGE_SIZE)]
    async with respx.mock:
        respx.post(LIST_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json={"total": WORKDAY_PAGE_SIZE + 1, "jobPostings": first_page}
                ),
                httpx.Response(200, json={"total": 0, "jobPostings": [_posting(99)]}),
            ]
        )
        async with httpx.AsyncClient() as client:
            jobs = await WorkdayAdapter(max_retries=0).fetch_jobs(ref, client)
    assert len(jobs) == WORKDAY_PAGE_SIZE + 1
    assert jobs[0].description == ""
    assert jobs[0].published_at is None
    assert jobs[0].external_id.startswith("/job/")
    assert "NVIDIAExternalCareerSite" in jobs[0].url


@pytest.mark.asyncio
async def test_workday_empty() -> None:
    identifier = "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"
    ref = make_ref(source_type="workday", source_identifier=identifier)
    async with respx.mock:
        respx.post(LIST_URL).mock(
            return_value=httpx.Response(200, json={"total": 0, "jobPostings": []})
        )
        async with httpx.AsyncClient() as client:
            jobs = await WorkdayAdapter(max_retries=0).fetch_jobs(ref, client)
    assert jobs == []


@pytest.mark.asyncio
async def test_workday_placeholder_location_cleared() -> None:
    identifier = "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"
    ref = make_ref(source_type="workday", source_identifier=identifier)
    async with respx.mock:
        respx.post(LIST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "total": 1,
                    "jobPostings": [
                        {
                            "title": "Engineer",
                            "externalPath": "/job/x_JR1",
                            "locationsText": "2 Locations",
                        }
                    ],
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jobs = await WorkdayAdapter(max_retries=0).fetch_jobs(ref, client)
    assert jobs[0].location == ""


@pytest.mark.asyncio
async def test_workday_enrich_job() -> None:
    identifier = "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"
    ref = make_ref(source_type="workday", source_identifier=identifier)
    source = make_source_job(
        external_id="/job/US-CA/Software-Engineer_JR1",
        description="",
        location="",
    )
    detail_url = (
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/"
        "NVIDIAExternalCareerSite/job/US-CA/Software-Engineer_JR1"
    )
    async with respx.mock:
        respx.get(detail_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Software Engineer",
                        "jobDescription": "<p>Python platform work</p>",
                        "location": "Amsterdam",
                        "additionalLocations": ["Remote"],
                        "timeType": "Full time",
                        "startDate": "2026-07-01",
                    }
                },
            )
        )
        async with httpx.AsyncClient() as client:
            enriched = await WorkdayAdapter(max_retries=0).enrich_job(ref, source, client)
    assert "Python" in enriched.description
    assert "Amsterdam" in enriched.location
    assert enriched.published_at is not None


@pytest.mark.asyncio
async def test_workday_bad_identifier() -> None:
    with pytest.raises(SourceConfigError):
        parse_workday_identifier("nvidia")


@pytest.mark.asyncio
async def test_workday_http_failure() -> None:
    identifier = "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"
    ref = make_ref(source_type="workday", source_identifier=identifier)
    async with respx.mock:
        respx.post(LIST_URL).mock(return_value=httpx.Response(500, text="nope"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(SourceNetworkError):
                await WorkdayAdapter(max_retries=0).fetch_jobs(ref, client)
