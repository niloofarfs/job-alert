from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.sources.errors import SourceConfigError, SourceResponseError
from app.sources.http import parse_json, raise_for_status, request
from app.sources.models import CompanyRef, SourceJob
from app.sources.protocol import PassthroughEnrichment, parse_datetime
from app.sources.textutil import html_to_text, infer_remote_type, join_locations

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
_MAX_PAGES = 50


class _LeverCategories(BaseModel):
    model_config = ConfigDict(extra="allow")
    location: str | None = None
    allLocations: list[str] | None = None
    commitment: str | None = None


class _LeverSalary(BaseModel):
    model_config = ConfigDict(extra="allow")
    currency: str | None = None
    interval: str | None = None
    min: int | float | None = None
    max: int | float | None = None


class _LeverJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    text: str
    hostedUrl: str | None = None
    applyUrl: str | None = None
    categories: _LeverCategories | None = None
    workplaceType: str | None = None
    createdAt: int | float | None = None
    descriptionPlain: str | None = None
    description: str | None = None
    salaryRange: _LeverSalary | None = None
    salaryDescriptionPlain: str | None = None


class LeverAdapter(PassthroughEnrichment):
    source_type = "lever"

    def __init__(self, max_retries: int = 3, page_size: int = _PAGE_SIZE) -> None:
        self._max_retries = max_retries
        self._page_size = page_size

    async def fetch_jobs(self, company: CompanyRef, client: httpx.AsyncClient) -> list[SourceJob]:
        base, site = parse_lever_identifier(company.source_identifier)
        if not site:
            raise SourceConfigError("Lever source_identifier (site slug) is empty")

        collected: list[SourceJob] = []
        skip = 0
        for _page in range(_MAX_PAGES):
            url = f"{base}/{quote(site, safe='')}?mode=json&limit={self._page_size}&skip={skip}"
            response = await request(
                client,
                "GET",
                url,
                max_retries=self._max_retries,
                headers={"Accept": "application/json"},
            )
            raise_for_status(response, company=company.name, source_type=self.source_type)
            payload = parse_json(response, company=company.name, source_type=self.source_type)
            if not isinstance(payload, list):
                raise SourceResponseError(f"Lever response for {company.name} is not a JSON array")
            if not payload:
                break
            for item in payload:
                parsed = _parse_job(item)
                if parsed is not None:
                    collected.append(parsed)
            if len(payload) < self._page_size:
                break
            skip += self._page_size
        else:
            logger.warning(
                "lever_pagination_capped company=%s pages=%s",
                company.name,
                _MAX_PAGES,
            )
        return collected


def parse_lever_identifier(value: str) -> tuple[str, str]:
    identifier = value.strip()
    if identifier.startswith("eu/"):
        return "https://api.eu.lever.co/v0/postings", identifier[3:].strip()
    return "https://api.lever.co/v0/postings", identifier


def _salary_text(job: _LeverJob) -> str | None:
    if job.salaryDescriptionPlain:
        return job.salaryDescriptionPlain.strip() or None
    rng = job.salaryRange
    if not rng:
        return None
    parts: list[str] = []
    if rng.min is not None and rng.max is not None:
        parts.append(f"{rng.min}-{rng.max}")
    elif rng.min is not None:
        parts.append(f"{rng.min}+")
    elif rng.max is not None:
        parts.append(f"up to {rng.max}")
    if rng.currency:
        parts.append(rng.currency)
    if rng.interval:
        parts.append(f"/{rng.interval}")
    text = " ".join(parts).strip()
    return text or None


def _parse_job(item: Any) -> SourceJob | None:
    if not isinstance(item, dict):
        return None
    try:
        job = _LeverJob.model_validate(item)
    except ValidationError:
        return None
    locations: list[str] = []
    if job.categories:
        if job.categories.allLocations:
            locations.extend(job.categories.allLocations)
        elif job.categories.location:
            locations.append(job.categories.location)
    location = join_locations(locations)
    description = job.descriptionPlain or html_to_text(job.description)
    url = job.hostedUrl or job.applyUrl or ""
    if not url:
        return None
    employment = job.categories.commitment if job.categories else None
    return SourceJob(
        external_id=job.id,
        title=job.text.strip(),
        url=url,
        description=description.strip(),
        location=location,
        remote_type=infer_remote_type(location, job.workplaceType),
        employment_type=employment,
        salary_text=_salary_text(job),
        published_at=parse_datetime(job.createdAt),
        raw_payload=item,
    )
