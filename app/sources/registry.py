from __future__ import annotations

from app.sources.ashby import AshbyAdapter
from app.sources.errors import SourceConfigError
from app.sources.greenhouse import GreenhouseAdapter
from app.sources.lever import LeverAdapter
from app.sources.protocol import SourceAdapter
from app.sources.workday import WorkdayAdapter

SUPPORTED_SOURCE_TYPES = ("greenhouse", "lever", "ashby", "workday")


def default_adapters(max_retries: int = 3) -> dict[str, SourceAdapter]:
    return {
        "greenhouse": GreenhouseAdapter(max_retries=max_retries),
        "lever": LeverAdapter(max_retries=max_retries),
        "ashby": AshbyAdapter(max_retries=max_retries),
        "workday": WorkdayAdapter(max_retries=max_retries),
    }


def get_adapter(
    source_type: str, adapters: dict[str, SourceAdapter] | None = None
) -> SourceAdapter:
    registry = adapters if adapters is not None else default_adapters()
    key = source_type.strip().lower()
    try:
        return registry[key]
    except KeyError as exc:
        raise SourceConfigError(
            f"Unknown source_type {source_type!r}. Supported: {', '.join(SUPPORTED_SOURCE_TYPES)}"
        ) from exc
