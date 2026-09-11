from __future__ import annotations

import html
import re
from urllib.parse import urlparse

_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def html_to_text(value: str | None, *, unescape_passes: int = 1) -> str:
    if not value:
        return ""
    text = value
    for _ in range(max(unescape_passes, 1)):
        text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return collapse_whitespace(text)


def infer_remote_type(location: str, explicit: str | None = None) -> str | None:
    if explicit:
        normalized = explicit.lower().replace("_", "").replace("-", "").replace(" ", "")
        if normalized in {"remote"}:
            return "remote"
        if normalized in {"hybrid"}:
            return "hybrid"
        if normalized in {"onsite", "onsiteonly"}:
            return "onsite"
    lowered = location.lower()
    if "remote" in lowered:
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    return None


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def join_locations(parts: list[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        cleaned = collapse_whitespace(part)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return "; ".join(ordered)
