from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PollStatus:
    in_progress: bool = False
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    last_companies_ok: int = 0
    last_companies_failed: int = 0
    last_new_jobs: int = 0
    last_notified: int = 0
    last_skipped: bool = False


@dataclass
class CompanyPollOutcome:
    company_id: str
    company_name: str
    ok: bool
    fetched: int = 0
    new_jobs: int = 0
    notified: int = 0
    baseline: bool = False
    error: str | None = None


@dataclass
class PollSummary:
    skipped: bool = False
    companies_ok: int = 0
    companies_failed: int = 0
    new_jobs: int = 0
    notified: int = 0
    outcomes: list[CompanyPollOutcome] = field(default_factory=list)
