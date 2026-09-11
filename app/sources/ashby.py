from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.sources.errors import SourceConfigError, SourceResponseError
from app.sources.http import parse_json, raise_for_status, request
from app.sources.models import CompanyRef, SourceJob
from app.sources.protocol import PassthroughEnrichment, parse_datetime
from app.sources.textutil import html_to_text, infer_remote_type, join_locations


class _SecondaryLocation(BaseModel):
    model_config = ConfigDict(extra="allow")
    location: str | None = None


class _Compensation(BaseModel):
    model_config = ConfigDict(extra="allow")
    compensationTierSummary: str | None = None
    scrapeableCompensationSalarySummary: str | None = None


class _AshbyJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    title: str
    location: str | None = None
    secondaryLocations: list[_SecondaryLocation] | None = None
    isListed: bool | None = True
    isRemote: bool | None = None
    workplaceType: str | None = None
    descriptionPlain: str | None = None
    descriptionHtml: str | None = None
    publishedAt: str | None = None
    employmentType: str | None = None
    jobUrl: str | None = None
    applyUrl: str | None = None
    compensation: _Compensation | None = None


class AshbyAdapter(PassthroughEnrichment):
    source_type = "ashby"

    def __init__(self, max_retries: int = 3) -> None:
        self._max_retries = max_retries

    async def fetch_jobs(self, company: CompanyRef, client: httpx.AsyncClient) -> list[SourceJob]:
        slug = company.source_identifier.strip()
        if not slug:
            raise SourceConfigError("Ashby source_identifier (job board name) is empty")

        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug, safe='')}"
            "?includeCompensation=true"
        )
        response = await request(
            client,
            "GET",
            url,
            max_retries=self._max_retries,
            headers={"Accept": "application/json"},
        )
        raise_for_status(response, company=company.name, source_type=self.source_type)
        payload = parse_json(response, company=company.name, source_type=self.source_type)
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise SourceResponseError(f"Ashby response for {company.name} is missing a jobs array")
        jobs_raw = payload.get("jobs")
        if jobs_raw is None:
            return []
        if not isinstance(jobs_raw, list):
            raise SourceResponseError(f"Ashby jobs field for {company.name} is not a list")

        results: list[SourceJob] = []
        for item in jobs_raw:
            parsed = _parse_job(item)
            if parsed is not None:
                results.append(parsed)
        return results


def _external_id(job: _AshbyJob) -> str | None:
    if job.id:
        return job.id
    url = job.jobUrl or job.applyUrl
    if not url:
        return None
    path = urlparse(url).path.rstrip("/")
    if not path:
        return None
    return path.rsplit("/", 1)[-1] or None


def _salary_text(job: _AshbyJob) -> str | None:
    if not job.compensation:
        return None
    return (
        job.compensation.scrapeableCompensationSalarySummary
        or job.compensation.compensationTierSummary
    )


def _parse_job(item: Any) -> SourceJob | None:
    if not isinstance(item, dict):
        return None
    try:
        job = _AshbyJob.model_validate(item)
    except ValidationError:
        return None
    if job.isListed is False:
        return None
    external_id = _external_id(job)
    url = job.jobUrl or job.applyUrl
    if not external_id or not url:
        return None
    locations = [job.location or ""]
    if job.secondaryLocations:
        locations.extend(loc.location or "" for loc in job.secondaryLocations)
    location = join_locations(locations)
    remote_explicit = job.workplaceType
    if job.isRemote and not remote_explicit:
        remote_explicit = "remote"
    description = (job.descriptionPlain or "").strip() or html_to_text(job.descriptionHtml)
    return SourceJob(
        external_id=external_id,
        title=job.title.strip(),
        url=url,
        description=description,
        location=location,
        remote_type=infer_remote_type(location, remote_explicit),
        employment_type=job.employmentType,
        salary_text=_salary_text(job),
        published_at=parse_datetime(job.publishedAt),
        raw_payload=item,
    )
