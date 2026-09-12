from __future__ import annotations

import logging
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

HealthcheckSignal = Literal["start", "success", "fail"]

PING_TIMEOUT = httpx.Timeout(10.0)


def ping_url_for_signal(ping_url: str, signal: HealthcheckSignal) -> str:
    base = ping_url.rstrip("/")
    if signal == "start":
        return f"{base}/start"
    if signal == "fail":
        return f"{base}/fail"
    return base


def fetch_cycle_is_healthy(*, companies_ok: int, companies_failed: int) -> bool:
    """A completed cycle is healthy unless every polled company failed."""
    return companies_ok > 0 or companies_failed == 0


async def send_healthcheck(
    client: httpx.AsyncClient,
    ping_url: str | None,
    signal: HealthcheckSignal,
) -> None:
    if not ping_url:
        return
    url = ping_url_for_signal(ping_url, signal)
    try:
        response = await client.get(url, timeout=PING_TIMEOUT)
    except Exception as exc:
        logger.warning(
            "healthchecks_ping_failed signal=%s error=%s",
            signal,
            type(exc).__name__,
        )
        return
    if response.status_code >= 400:
        logger.warning(
            "healthchecks_ping_failed signal=%s error=HTTP%s",
            signal,
            response.status_code,
        )
