from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.import_companies import load_company_file, sync_companies
from app.db.models import Company

YAML = """
companies:
  - name: Example
    website: https://example.com
    careers_url: https://boards.greenhouse.io/example
    source_type: greenhouse
    source_identifier: example
    priority: high
  - name: Example EU
    website: https://example.com
    careers_url: https://jobs.lever.co/example
    source_type: lever
    source_identifier: eu/example
    priority: medium
"""


async def test_import_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    path = tmp_path / "companies.yaml"
    path.write_text(YAML, encoding="utf-8")
    seeds = load_company_file(path)
    first = await sync_companies(session, seeds)
    await session.commit()
    assert first["created"] == 2
    second = await sync_companies(session, seeds)
    await session.commit()
    assert second["created"] == 0
    assert second["updated"] == 2
    companies = (await session.execute(select(Company))).scalars().all()
    assert len(companies) == 2
    by_id = {(c.source_type, c.source_identifier): c for c in companies}
    assert by_id[("greenhouse", "example")].priority == 30
    assert by_id[("lever", "eu/example")].name == "Example EU"
