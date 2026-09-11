from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from app.sources.errors import (
    SourceAuthError,
    SourceNetworkError,
    SourceResponseError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 30.0


def safe_url(url: str) -> str:
    """Redact secrets that ATS/Telegram URLs may embed in the path."""
    parsed = urlparse(url)
    path = parsed.path
    if "/bot" in path:
        after_bot = path.split("/bot", 1)[1]
        if "/" in after_bot:
            rest = after_bot.split("/", 1)[1]
            path = f"/bot<redacted>/{rest}"
        else:
            path = "/bot<redacted>"
    host = parsed.netloc
    return f"{parsed.scheme}://{host}{path}"


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            parsed = float(header)
        except ValueError:
            pass
        else:
            return min(parsed, MAX_RETRY_AFTER_SECONDS)
    backoff = 0.5 * (2**attempt)
    return min(float(backoff), MAX_RETRY_AFTER_SECONDS)


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    json: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    last_error: Exception | None = None
    attempts = max_retries + 1
    for attempt in range(attempts):
        try:
            response = await client.request(
                method,
                url,
                json=dict(json) if json is not None else None,
                headers=dict(headers) if headers is not None else None,
            )
        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning(
                "http_timeout method=%s url=%s attempt=%s/%s",
                method,
                safe_url(url),
                attempt + 1,
                attempts,
            )
            if attempt >= max_retries:
                raise SourceNetworkError(f"timeout requesting {safe_url(url)}") from exc
            await asyncio.sleep(_retry_after_seconds(httpx.Response(408), attempt))
            continue
        except httpx.TransportError as exc:
            last_error = exc
            logger.warning(
                "http_transport_error method=%s url=%s attempt=%s/%s error=%s",
                method,
                safe_url(url),
                attempt + 1,
                attempts,
                type(exc).__name__,
            )
            if attempt >= max_retries:
                raise SourceNetworkError(f"network error requesting {safe_url(url)}") from exc
            await asyncio.sleep(min(0.5 * (2**attempt), MAX_RETRY_AFTER_SECONDS))
            continue

        if response.status_code in RETRYABLE_STATUS and attempt < max_retries:
            delay = _retry_after_seconds(response, attempt)
            logger.warning(
                "http_retryable_status method=%s url=%s status=%s attempt=%s/%s delay_s=%.1f",
                method,
                safe_url(url),
                response.status_code,
                attempt + 1,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue
        return response

    raise SourceNetworkError(f"network error requesting {safe_url(url)}") from last_error


def raise_for_status(response: httpx.Response, *, company: str, source_type: str) -> None:
    status = response.status_code
    url = safe_url(str(response.request.url)) if response.request else ""
    if status in {401, 403}:
        raise SourceAuthError(
            f"{source_type} rejected request for company={company} status={status} url={url}"
        )
    if status in {404, 422}:
        from app.sources.errors import SourceConfigError

        raise SourceConfigError(
            f"{source_type} returned {status} for company={company} url={url}; "
            "check source_identifier"
        )
    if status >= 400:
        raise SourceNetworkError(f"{source_type} HTTP {status} for company={company} url={url}")


def parse_json(response: httpx.Response, *, company: str, source_type: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SourceResponseError(f"{source_type} returned non-JSON for company={company}") from exc
