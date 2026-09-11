from __future__ import annotations

from app.sources.models import SourceJob
from app.sources.textutil import collapse_whitespace, infer_remote_type, is_http_url


def normalize_source_job(job: SourceJob) -> SourceJob | None:
    external_id = collapse_whitespace(job.external_id)
    title = collapse_whitespace(job.title)
    url = collapse_whitespace(job.url)
    if not external_id or not title or not url or not is_http_url(url):
        return None
    location = collapse_whitespace(job.location)
    description = collapse_whitespace(job.description)
    remote_type = infer_remote_type(location, job.remote_type)
    employment = collapse_whitespace(job.employment_type or "") or None
    salary = collapse_whitespace(job.salary_text or "") or None
    return SourceJob(
        external_id=external_id[:512],
        title=title[:512],
        url=url[:2048],
        description=description,
        location=location[:1024],
        remote_type=remote_type,
        employment_type=employment[:64] if employment else None,
        salary_text=salary[:512] if salary else None,
        published_at=job.published_at,
        source_updated_at=job.source_updated_at,
        raw_payload=job.raw_payload,
    )
