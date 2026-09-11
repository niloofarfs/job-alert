from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ["NOTIFICATIONS_ENABLED"] = "false"
os.environ["APP_ENV"] = "test"

from app.config import Settings
from app.db.base import Base
from app.db.models import Company, Job, Notification  # noqa: F401

MATCHING_YAML = """
alert_threshold: 40
hard_reject_on_negative: false
title_keywords:
  - backend
  - software engineer
  - python
description_keywords:
  - python
  - fastapi
negative_keywords:
  - frontend
  - intern
seniority_keywords:
  - senior
  - staff
location:
  include:
    - Netherlands
    - Remote
    - Europe
  exclude:
    - United States only
"""


@pytest.fixture
def matching_file(tmp_path: Path) -> Path:
    path = tmp_path / "matching.yaml"
    path.write_text(MATCHING_YAML, encoding="utf-8")
    return path


@pytest.fixture
def settings(matching_file: Path) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        notifications_enabled=False,
        matching_config_path=matching_file,
        miss_threshold=2,
        company_concurrency=4,
        http_max_retries=1,
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db_session:
        yield db_session
        await db_session.rollback()
    await engine.dispose()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()
