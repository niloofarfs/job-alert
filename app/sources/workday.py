from __future__ import annotations

import logging
import re
from dataclasses import replace
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.sources.errors import SourceConfigError, SourceResponseError
from app.sources.http import parse_json, raise_for_status, request
from app.sources.models import CompanyRef, SourceJob
from app.sources.protocol import parse_datetime
from app.sources.textutil import html_to_text, infer_remote_type, join_locations

logger = logging.getLogger(__name__)

WORKDAY_PAGE_SIZE = 20
_MAX_PAGES = 250
_PLACEHOLDER_LOCATION = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)


class WorkdayBoard:
    def __init__(self, host: str, tenant: str, site: str) -> None:
        self.host = host
        self.tenant = tenant
        self.site = site

    @property
    def api_root(self) -> str:
        return f"https://{self.host}/wday/cxs/{quote(self.tenant, safe='')}/{self.site}"

    @property
    def public_root(self) -> str:
        return f"https://{self.host}/{self.site}"


def parse_workday_identifier(value: str) -> WorkdayBoard:
    parts = [part.strip() for part in value.strip().split("/") if part.strip()]
    if len(parts) < 3:
        raise SourceConfigError(
            "Workday source_identifier must be host/tenant/site, e.g. "
            "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"
        )
    host, tenant, site = parts[0], parts[1], "/".join(parts[2:])
    if "." not in host and host not in {"localhost"}:
        raise SourceConfigError(
            f"Workday host {host!r} does not look like a hostname; expected host/tenant/site"
        )
    return WorkdayBoard(host=host, tenant=tenant, site=site)


class _WorkdayPosting(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str
    externalPath: str
    locationsText: str | None = None
    bulletFields: list[str] | None = None


class _WorkdayList(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int | None = None
    jobPostings: list[_WorkdayPosting] | None = None


class _JobPostingInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str | None = None
    jobDescription: str | None = None
    location: str | None = None
    additionalLocations: list[str] | None = None
    timeType: str | None = None
    startDate: str | None = None
    jobReqId: str | None = None
    externalUrl: str | None = None


class WorkdayAdapter:
    source_type = "workday"

    def __init__(self, max_retries: int = 3) -> None:
        self._max_retries = max_retries

    async def fetch_jobs(self, company: CompanyRef, client: httpx.AsyncClient) -> list[SourceJob]:
        board = parse_workday_identifier(company.source_identifier)
        collected: list[SourceJob] = []
        offset = 0
        total: int | None = None
        for page in range(_MAX_PAGES):
            payload = await self._fetch_page(client, board, company.name, offset)
            listings = payload.jobPostings or []
            if page == 0:
                total = payload.total
            if not listings:
                break
            for item in listings:
                parsed = _parse_list_job(item, board)
                if parsed is not None:
                    collected.append(parsed)
            offset += WORKDAY_PAGE_SIZE
            if total is not None and offset >= total:
                break
            if len(listings) < WORKDAY_PAGE_SIZE:
                break
        else:
            logger.warning(
                "workday_pagination_capped company=%s pages=%s",
                company.name,
                _MAX_PAGES,
            )
        return collected

    async def enrich_job(
        self, company: CompanyRef, job: SourceJob, client: httpx.AsyncClient
    ) -> SourceJob:
        board = parse_workday_identifier(company.source_identifier)
        path = job.external_id if job.external_id.startswith("/") else f"/{job.external_id}"
        url = f"{board.api_root}{path}"
        response = await request(
            client,
            "GET",
            url,
            max_retries=self._max_retries,
            headers={"Accept": "application/json"},
        )
        raise_for_status(response, company=company.name, source_type=self.source_type)
        payload = parse_json(response, company=company.name, source_type=self.source_type)
        if not isinstance(payload, dict):
            raise SourceResponseError(f"Workday job detail for {company.name} is not an object")
        info_raw = payload.get("jobPostingInfo")
        if not isinstance(info_raw, dict):
            return job
        try:
            info = _JobPostingInfo.model_validate(info_raw)
        except ValidationError:
            return job
        locations = []
        if info.location:
            locations.append(info.location)
        if info.additionalLocations:
            locations.extend(info.additionalLocations)
        location = join_locations(locations) or job.location
        description = html_to_text(info.jobDescription) or job.description
        url = info.externalUrl or job.url
        return replace(
            job,
            title=(info.title or job.title).strip(),
            description=description,
            location=location,
            remote_type=infer_remote_type(location, job.remote_type),
            employment_type=info.timeType or job.employment_type,
            published_at=parse_datetime(info.startDate) or job.published_at,
            url=url,
            raw_payload={**job.raw_payload, "detail": info_raw},
        )

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        board: WorkdayBoard,
        company_name: str,
        offset: int,
    ) -> _WorkdayList:
        url = f"{board.api_root}/jobs"
        response = await request(
            client,
            "POST",
            url,
            max_retries=self._max_retries,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "appliedFacets": {},
                "limit": WORKDAY_PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            },
        )
        raise_for_status(response, company=company_name, source_type=self.source_type)
        payload = parse_json(response, company=company_name, source_type=self.source_type)
        if not isinstance(payload, dict):
            raise SourceResponseError(f"Workday list response for {company_name} is not an object")
        try:
            return _WorkdayList.model_validate(payload)
        except ValidationError as exc:
            raise SourceResponseError(
                f"Workday list response for {company_name} has an unexpected shape"
            ) from exc


def _parse_list_job(item: _WorkdayPosting, board: WorkdayBoard) -> SourceJob | None:
    path = item.externalPath.strip()
    if not path:
        return None
    if not path.startswith("/"):
        path = f"/{path}"
    location = item.locationsText or ""
    if _PLACEHOLDER_LOCATION.match(location.strip()):
        location = ""
    return SourceJob(
        external_id=path,
        title=item.title.strip(),
        url=f"{board.public_root}{path}",
        description="",
        location=location.strip(),
        remote_type=infer_remote_type(location),
        raw_payload=item.model_dump(),
    )
