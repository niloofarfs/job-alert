from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx

from app.companies.import_companies import load_company_file, sync_companies
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import setup_logging
from app.notifications.telegram import TelegramNotifier
from app.scheduler.poller import Poller
from app.sources.registry import default_adapters


async def _import_companies(path: Path) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    seeds = load_company_file(path)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            async with session.begin():
                summary = await sync_companies(session, seeds)
        print(
            f"Imported companies from {path}: "
            f"created={summary['created']} updated={summary['updated']} total={summary['total']}"
        )
    finally:
        await engine.dispose()


async def _poll_once() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    async with httpx.AsyncClient(
        headers={
            "User-Agent": settings.http_user_agent,
            "Accept": "application/json",
        },
        timeout=httpx.Timeout(
            settings.http_timeout_seconds,
            connect=settings.http_connect_timeout_seconds,
        ),
        follow_redirects=True,
    ) as client:
        notifier = None
        if settings.notifications_enabled:
            assert settings.telegram_bot_token is not None
            assert settings.telegram_chat_id is not None
            notifier = TelegramNotifier(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                client,
                max_retries=settings.http_max_retries,
            )
        poller = Poller(
            settings=settings,
            session_factory=factory,
            http_client=client,
            adapters=default_adapters(max_retries=settings.http_max_retries),
            notifier=notifier,
        )
        try:
            summary = await poller.run()
            print(
                "Poll complete: "
                f"ok={summary.companies_ok} failed={summary.companies_failed} "
                f"new={summary.new_jobs} notified={summary.notified} skipped={summary.skipped}"
            )
        finally:
            await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Job search alert CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    import_parser = sub.add_parser("import-companies", help="Upsert companies from YAML")
    import_parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to companies.yaml (defaults to COMPANIES_CONFIG_PATH)",
    )

    sub.add_parser("poll", help="Run a single poll cycle and exit")

    args = parser.parse_args(argv)
    if args.command == "import-companies":
        settings = get_settings()
        path = args.file or settings.companies_config_path
        asyncio.run(_import_companies(path))
        return
    if args.command == "poll":
        asyncio.run(_poll_once())
        return
    parser.error(f"unknown command {args.command}")


if __name__ == "__main__":
    main()
