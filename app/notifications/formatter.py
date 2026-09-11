from __future__ import annotations

from app.db.models import Company, Job
from app.matching.engine import MatchResult

_MAX_REASONS = 8
_MAX_MESSAGE = 3500


def format_job_alert(company: Company, job: Job, match: MatchResult) -> str:
    reasons = "\n".join(f"• {reason}" for reason in match.reasons[:_MAX_REASONS])
    location = job.location or "n/a"
    message = (
        "🚀 New matching job\n"
        "\n"
        f"Company: {company.name}\n"
        f"Role: {job.title}\n"
        f"Location: {location}\n"
        f"Match: {match.score}/100\n"
        "\n"
        "Why:\n"
        f"{reasons}\n"
        "\n"
        "Apply:\n"
        f"{job.url}"
    )
    if len(message) <= _MAX_MESSAGE:
        return message
    return message[: _MAX_MESSAGE - 1] + "…"
