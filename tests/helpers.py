from __future__ import annotations

from app.clock import utcnow
from app.db.models import Company
from app.sources.models import CompanyRef, SourceJob


def make_company(**overrides: object) -> Company:
    now = utcnow()
    values: dict[str, object] = {
        "name": "Acme",
        "website": "https://acme.example",
        "careers_url": "https://boards.greenhouse.io/acme",
        "source_type": "greenhouse",
        "source_identifier": "acme",
        "enabled": True,
        "priority": 20,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Company(**values)  # type: ignore[arg-type]


def make_ref(company: Company | None = None, **overrides: object) -> CompanyRef:
    if company is not None:
        return CompanyRef(
            id=company.id,
            name=company.name,
            source_type=company.source_type,
            source_identifier=company.source_identifier,
            careers_url=company.careers_url,
        )
    from uuid import uuid4

    values: dict[str, object] = {
        "id": uuid4(),
        "name": "Acme",
        "source_type": "greenhouse",
        "source_identifier": "acme",
        "careers_url": "https://boards.greenhouse.io/acme",
    }
    values.update(overrides)
    return CompanyRef(**values)  # type: ignore[arg-type]


def make_source_job(**overrides: object) -> SourceJob:
    values: dict[str, object] = {
        "external_id": "job-1",
        "title": "Senior Backend Engineer",
        "url": "https://example.com/jobs/job-1",
        "description": "Build APIs with Python and FastAPI.",
        "location": "Amsterdam, Netherlands",
        "remote_type": "hybrid",
        "employment_type": "Full-time",
    }
    values.update(overrides)
    return SourceJob(**values)  # type: ignore[arg-type]
