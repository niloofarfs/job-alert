from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import setup_logging
from app.notifications.telegram import TelegramNotifier
from app.scheduler.poller import Poller
from app.scheduler.scheduler import build_scheduler
from app.sources.registry import default_adapters


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    http_client = httpx.AsyncClient(
        headers={
            "User-Agent": settings.http_user_agent,
            "Accept": "application/json",
        },
        timeout=httpx.Timeout(
            settings.http_timeout_seconds,
            connect=settings.http_connect_timeout_seconds,
        ),
        follow_redirects=True,
    )
    notifier = None
    if settings.notifications_enabled:
        assert settings.telegram_bot_token is not None
        assert settings.telegram_chat_id is not None
        notifier = TelegramNotifier(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            http_client,
            max_retries=settings.http_max_retries,
        )
    poller = Poller(
        settings=settings,
        session_factory=session_factory,
        http_client=http_client,
        adapters=default_adapters(max_retries=settings.http_max_retries),
        notifier=notifier,
    )
    scheduler = build_scheduler(poller, settings.poll_interval_seconds)

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.http_client = http_client
    app.state.poller = poller
    app.state.scheduler = scheduler

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await http_client.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Job Search Alert",
        description="Self-hosted monitor for new engineering jobs at target companies.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
