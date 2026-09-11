from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    website: str | None
    careers_url: str | None
    source_type: str
    source_identifier: str
    enabled: bool
    priority: int
    baseline_completed_at: datetime | None


class JobListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    company_name: str | None = None
    external_id: str
    title: str
    location: str
    remote_type: str | None
    employment_type: str | None
    url: str
    published_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool


class JobDetailOut(JobListOut):
    description: str
    salary_text: str | None
    source_updated_at: datetime | None
    consecutive_misses: int


class HealthOut(BaseModel):
    status: str
    database: str
    last_poll_started_at: datetime | None = None
    last_poll_finished_at: datetime | None = None
    poll_in_progress: bool = False


class StatsOut(BaseModel):
    companies: int
    companies_enabled: int
    jobs_active: int
    jobs_inactive: int
    notifications_sent: int
    notifications_failed: int
    last_poll_started_at: datetime | None = None
    last_poll_finished_at: datetime | None = None
    last_poll_companies_ok: int = 0
    last_poll_companies_failed: int = 0
    last_poll_new_jobs: int = 0
    last_poll_notified: int = 0


class PollTriggerOut(BaseModel):
    status: str
    detail: str | None = None
