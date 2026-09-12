from __future__ import annotations

import logging

import httpx
import pytest
import respx

from app.healthchecks import (
    fetch_cycle_is_healthy,
    ping_url_for_signal,
    send_healthcheck,
)

PING_URL = "https://hc-ping.com/11111111-1111-1111-1111-111111111111"


def test_ping_url_suffixes() -> None:
    assert ping_url_for_signal(PING_URL, "success") == PING_URL
    assert ping_url_for_signal(PING_URL + "/", "success") == PING_URL
    assert ping_url_for_signal(PING_URL, "start") == f"{PING_URL}/start"
    assert ping_url_for_signal(PING_URL, "fail") == f"{PING_URL}/fail"


def test_fetch_cycle_health_rules() -> None:
    assert fetch_cycle_is_healthy(companies_ok=0, companies_failed=0) is True
    assert fetch_cycle_is_healthy(companies_ok=2, companies_failed=0) is True
    assert fetch_cycle_is_healthy(companies_ok=1, companies_failed=3) is True
    assert fetch_cycle_is_healthy(companies_ok=0, companies_failed=1) is False


async def test_send_healthcheck_noop_when_url_missing() -> None:
    async with httpx.AsyncClient() as client:
        async with respx.mock(assert_all_called=False) as router:
            router.route().mock(side_effect=AssertionError("unexpected HTTP"))
            await send_healthcheck(client, None, "success")
            await send_healthcheck(client, "", "fail")


async def test_send_healthcheck_hits_signal_urls() -> None:
    async with httpx.AsyncClient() as client:
        async with respx.mock(assert_all_called=True) as router:
            start = router.get(f"{PING_URL}/start").mock(
                return_value=httpx.Response(200, text="OK")
            )
            success = router.get(PING_URL).mock(return_value=httpx.Response(200, text="OK"))
            fail = router.get(f"{PING_URL}/fail").mock(return_value=httpx.Response(200, text="OK"))
            await send_healthcheck(client, PING_URL, "start")
            await send_healthcheck(client, PING_URL, "success")
            await send_healthcheck(client, PING_URL, "fail")
    assert start.call_count == 1
    assert success.call_count == 1
    assert fail.call_count == 1


async def test_send_healthcheck_swallows_http_errors() -> None:
    async with httpx.AsyncClient() as client:
        async with respx.mock:
            respx.get(PING_URL).mock(return_value=httpx.Response(503, text="down"))
            await send_healthcheck(client, PING_URL, "success")
            respx.get(f"{PING_URL}/fail").mock(side_effect=httpx.TimeoutException("timeout"))
            await send_healthcheck(client, PING_URL, "fail")


async def test_send_healthcheck_does_not_log_url(caplog: pytest.LogCaptureFixture) -> None:
    secret = "https://hc-ping.com/secret-uuid-do-not-log"
    caplog.set_level(logging.WARNING)
    async with httpx.AsyncClient() as client:
        async with respx.mock:
            respx.get(secret).mock(side_effect=httpx.ConnectError(f"Failed to connect to {secret}"))
            await send_healthcheck(client, secret, "success")
    assert "healthchecks_ping_failed" in caplog.text
    assert "ConnectError" in caplog.text
    assert "secret-uuid-do-not-log" not in caplog.text
    assert secret not in caplog.text
