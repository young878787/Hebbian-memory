"""Frozen-clock relevance and conservative archive eligibility."""

from __future__ import annotations

import math
from datetime import datetime


def effective_relevance(
    *, importance: float, confidence: float, last_activated_at: datetime | None,
    now: datetime, positive_strength: float, negative_strength: float,
    half_life_days: float,
) -> float:
    age_days = 0.0 if last_activated_at is None else max(0.0, (now - last_activated_at).total_seconds() / 86400)
    recency = math.pow(0.5, age_days / half_life_days)
    usage = min(1.0, positive_strength)
    feedback = max(0.0, 1.0 - negative_strength)
    return importance * confidence * recency * (0.5 + 0.5 * usage) * feedback


def may_archive(*, status: str, importance: float, recently_cited: bool, relevance: float, threshold: float) -> bool:
    """Current/high-importance claims never become an automatic archive proposal."""
    return status != "active" and importance < 0.8 and not recently_cited and relevance < threshold
