from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.sources.errors import SourceConfigError, SourceResponseError
from app.sources.http import parse_json, raise_for_status, request
from app.sources.models import CompanyRef, SourceJob
from app.sources.protocol import PassthroughEnrichment, parse_datetime
from app.sources.textutil import html_to_text, infer_remote_type


class _Location(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None


class _GreenhouseJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | str
    title: str
    absolute_url: str
    location: _Location | None = None
    content: str | None = None
    first_published: str | None = None
    updated_at: str | None = None


class GreenhouseAdapter(PassthroughEnrichment):
    source_type = "greenhouse"

    def __init__(self, max_retries: int = 3) -> None:
        self._max_retries = max_retries

    async def fetch_jobs(self, company: CompanyRef, client: httpx.AsyncClient) -> list[SourceJob]:
        token = company.source_identifier.strip()
        if not token:
            raise SourceConfigError("Greenhouse source_identifier (board token) is empty")

        url = (
            f"https://boards-api.greenhouse.io/v1/boards/{quote(token, safe='')}/jobs?content=true"
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
            raise SourceResponseError(
                f"Greenhouse response for {company.name} is missing a jobs array"
            )
        jobs_raw = payload.get("jobs")
        if jobs_raw is None:
            return []
        if not isinstance(jobs_raw, list):
            raise SourceResponseError(f"Greenhouse jobs field for {company.name} is not a list")

        results: list[SourceJob] = []
        for item in jobs_raw:
            parsed = _parse_job(item)
            if parsed is not None:
                results.append(parsed)
        return results


def _parse_job(item: Any) -> SourceJob | None:
    if not isinstance(item, dict):
        return None
    try:
        job = _GreenhouseJob.model_validate(item)
    except ValidationError:
        return None
    location = job.location.name if job.location and job.location.name else ""
    description = html_to_text(job.content, unescape_passes=2)
    return SourceJob(
        external_id=str(job.id),
        title=job.title.strip(),
        url=job.absolute_url,
        description=description,
        location=location.strip(),
        remote_type=infer_remote_type(location),
        published_at=parse_datetime(job.first_published),
        source_updated_at=parse_datetime(job.updated_at),
        raw_payload=item,
    )
