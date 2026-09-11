from __future__ import annotations

import re
from dataclasses import dataclass

from app.matching.config import MatchingRules
from app.sources.models import SourceJob


@dataclass(frozen=True, slots=True)
class MatchResult:
    score: int
    reasons: list[str]
    qualifies: bool


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])",
        re.IGNORECASE,
    )


def contains_keyword(text: str, keyword: str) -> bool:
    if not text or not keyword:
        return False
    return _keyword_pattern(keyword).search(text) is not None


def _unique_hits(text: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        key = keyword.lower()
        if key in seen:
            continue
        if contains_keyword(text, keyword):
            seen.add(key)
            hits.append(keyword)
    return hits


def _location_gate(location: str, rules: MatchingRules) -> str | None:
    lowered = location.lower()
    for keyword in rules.location.exclude:
        if contains_keyword(location, keyword) or keyword.lower() in lowered:
            return f'location excluded by "{keyword}"'
    if rules.location.include and location.strip():
        if not any(
            contains_keyword(location, keyword) or keyword.lower() in lowered
            for keyword in rules.location.include
        ):
            return "location did not match include rules"
    return None


def score_job(job: SourceJob, rules: MatchingRules) -> MatchResult:
    reasons: list[str] = []
    gate = _location_gate(job.location, rules)
    if gate:
        return MatchResult(score=0, reasons=[gate], qualifies=False)

    score = 0
    title_hits = _unique_hits(job.title, rules.title_keywords)
    for keyword in title_hits:
        score += rules.title_weight
        reasons.append(f'title contains "{keyword}"')

    description_hits = _unique_hits(job.description, rules.description_keywords)
    for keyword in description_hits:
        score += rules.description_weight
        reasons.append(f'description contains "{keyword}"')

    seniority_hits = _unique_hits(job.title, rules.seniority_keywords)
    if seniority_hits:
        score += rules.seniority_bonus
        reasons.append(f'seniority match "{seniority_hits[0]}"')

    negative_hits = _unique_hits(f"{job.title}\n{job.description}", rules.negative_keywords)
    if negative_hits and rules.hard_reject_on_negative:
        listed = ", ".join(f'"{item}"' for item in negative_hits)
        return MatchResult(
            score=0,
            reasons=[f"rejected by negative keyword {listed}"],
            qualifies=False,
        )
    for keyword in negative_hits:
        score -= rules.negative_weight
        reasons.append(f'negative keyword "{keyword}"')

    score = max(0, min(100, score))
    qualifies = score >= rules.alert_threshold and bool(reasons)
    if not reasons:
        reasons.append("no matching keywords")
    return MatchResult(score=score, reasons=reasons, qualifies=qualifies)
