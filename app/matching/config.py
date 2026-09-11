from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class LocationRules(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MatchingRules(BaseModel):
    alert_threshold: int = Field(default=50, ge=0, le=100)
    hard_reject_on_negative: bool = False
    title_weight: int = Field(default=20, ge=0)
    description_weight: int = Field(default=8, ge=0)
    seniority_bonus: int = Field(default=10, ge=0)
    negative_weight: int = Field(default=15, ge=0)
    title_keywords: list[str] = Field(default_factory=list)
    description_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    seniority_keywords: list[str] = Field(default_factory=list)
    location: LocationRules = Field(default_factory=LocationRules)

    @field_validator(
        "title_keywords",
        "description_keywords",
        "negative_keywords",
        "seniority_keywords",
        mode="before",
    )
    @classmethod
    def strip_keywords(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [str(item).strip() for item in value if str(item).strip()]


def load_matching_rules(path: Path) -> MatchingRules:
    if not path.exists():
        raise FileNotFoundError(f"Matching config not found: {path}")
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Matching config {path} must be a YAML mapping")
    return MatchingRules.model_validate(raw)
