from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CompanyRef:
    id: UUID
    name: str
    source_type: str
    source_identifier: str
    careers_url: str | None = None


@dataclass(frozen=True, slots=True)
class SourceJob:
    external_id: str
    title: str
    url: str
    description: str = ""
    location: str = ""
    remote_type: str | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
