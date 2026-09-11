from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.db.models import Company
from app.sources.registry import SUPPORTED_SOURCE_TYPES
from app.sources.workday import parse_workday_identifier

PRIORITY_MAP = {"low": 10, "medium": 20, "high": 30}


class CompanySeed(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    website: str | None = None
    careers_url: str | None = None
    source_type: str
    source_identifier: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    priority: str | int = "medium"

    @field_validator("name", "source_identifier")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_type")
    @classmethod
    def normalize_source_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {', '.join(SUPPORTED_SOURCE_TYPES)}")
        return normalized

    @field_validator("website", "careers_url")
    @classmethod
    def validate_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        parsed = urlparse(stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be http(s) with a host")
        return stripped

    def priority_value(self) -> int:
        if isinstance(self.priority, int):
            if self.priority < 0 or self.priority > 100:
                raise ValueError("priority int must be between 0 and 100")
            return self.priority
        key = str(self.priority).strip().lower()
        if key not in PRIORITY_MAP:
            raise ValueError("priority must be low, medium, high, or an integer")
        return PRIORITY_MAP[key]

    def validate_source_shape(self) -> None:
        if self.source_type == "workday":
            parse_workday_identifier(self.source_identifier)


class CompanyFile(BaseModel):
    companies: list[CompanySeed]


def load_company_file(path: Path) -> list[CompanySeed]:
    if not path.exists():
        raise FileNotFoundError(f"Company config not found: {path}")
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    parsed = CompanyFile.model_validate(raw)
    for company in parsed.companies:
        company.validate_source_shape()
    return parsed.companies


async def sync_companies(session: AsyncSession, seeds: list[CompanySeed]) -> dict[str, int]:
    created = 0
    updated = 0
    now = utcnow()
    for seed in seeds:
        result = await session.execute(
            select(Company).where(
                Company.source_type == seed.source_type,
                Company.source_identifier == seed.source_identifier,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            session.add(
                Company(
                    name=seed.name,
                    website=seed.website,
                    careers_url=seed.careers_url,
                    source_type=seed.source_type,
                    source_identifier=seed.source_identifier,
                    enabled=seed.enabled,
                    priority=seed.priority_value(),
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            continue
        existing.name = seed.name
        existing.website = seed.website
        existing.careers_url = seed.careers_url
        existing.enabled = seed.enabled
        existing.priority = seed.priority_value()
        existing.updated_at = now
        updated += 1
    return {"created": created, "updated": updated, "total": len(seeds)}
