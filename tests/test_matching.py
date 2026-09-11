from __future__ import annotations

from app.matching.config import MatchingRules
from app.matching.engine import score_job
from tests.helpers import make_source_job


def _rules(**overrides: object) -> MatchingRules:
    data: dict[str, object] = {
        "alert_threshold": 40,
        "hard_reject_on_negative": False,
        "title_keywords": ["backend", "software engineer", "python"],
        "description_keywords": ["python", "fastapi", "postgres"],
        "negative_keywords": ["frontend", "intern"],
        "seniority_keywords": ["senior", "staff"],
        "location": {
            "include": ["Netherlands", "Remote", "Europe"],
            "exclude": ["United States only"],
        },
    }
    data.update(overrides)
    return MatchingRules.model_validate(data)


def test_positive_title_and_description_match() -> None:
    job = make_source_job(
        title="Senior Backend Engineer",
        description="We use Python, FastAPI and Postgres.",
        location="Amsterdam, Netherlands",
    )
    result = score_job(job, _rules())
    assert result.qualifies is True
    assert result.score >= 50
    assert any("backend" in reason for reason in result.reasons)
    assert any("python" in reason.lower() for reason in result.reasons)
    assert any("seniority" in reason for reason in result.reasons)


def test_negative_keywords_reduce_score_but_do_not_auto_reject() -> None:
    job = make_source_job(
        title="Backend Engineer",
        description="Python APIs. Some frontend work.",
        location="Remote",
    )
    result = score_job(job, _rules())
    assert any("frontend" in reason for reason in result.reasons)
    assert (
        result.score
        < score_job(
            make_source_job(
                title="Backend Engineer",
                description="Python APIs.",
                location="Remote",
            ),
            _rules(),
        ).score
    )


def test_hard_reject_on_negative() -> None:
    job = make_source_job(title="Frontend Engineer", description="React", location="Remote")
    result = score_job(job, _rules(hard_reject_on_negative=True))
    assert result.qualifies is False
    assert result.score == 0
    assert "rejected" in result.reasons[0]


def test_threshold_behavior() -> None:
    job = make_source_job(
        title="Accountant",
        description="Python scripts",
        location="Remote",
    )
    low = score_job(job, _rules(alert_threshold=5))
    high = score_job(job, _rules(alert_threshold=90))
    assert low.qualifies is True
    assert high.qualifies is False


def test_location_include_and_exclude() -> None:
    included = score_job(
        make_source_job(location="Berlin, Europe"),
        _rules(),
    )
    missing = score_job(
        make_source_job(location="Tokyo, Japan"),
        _rules(),
    )
    excluded = score_job(
        make_source_job(location="United States only"),
        _rules(),
    )
    empty = score_job(make_source_job(location=""), _rules())
    assert included.qualifies is True
    assert missing.qualifies is False
    assert "include" in missing.reasons[0]
    assert excluded.qualifies is False
    assert "excluded" in excluded.reasons[0]
    assert empty.qualifies is True


def test_reasons_are_human_readable() -> None:
    result = score_job(
        make_source_job(title="Python Software Engineer", description="FastAPI"),
        _rules(),
    )
    assert all(isinstance(reason, str) and reason for reason in result.reasons)
    assert any(reason.startswith("title contains") for reason in result.reasons)
