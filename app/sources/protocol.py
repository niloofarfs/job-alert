from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from httpx import AsyncClient

from app.sources.models import CompanyRef, SourceJob


class SourceAdapter(Protocol):
    source_type: str

    async def fetch_jobs(self, company: CompanyRef, client: AsyncClient) -> list[SourceJob]:
        """Return currently published jobs for the company."""

    async def enrich_job(
        self, company: CompanyRef, job: SourceJob, client: AsyncClient
    ) -> SourceJob:
        """Optionally fill in description/location for a newly discovered job."""
        return job


class PassthroughEnrichment:
    """Mixin for adapters that already return full job payloads from fetch_jobs."""

    async def enrich_job(
        self, company: CompanyRef, job: SourceJob, client: AsyncClient
    ) -> SourceJob:
        return job


def parse_datetime(value: str | int | float | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
