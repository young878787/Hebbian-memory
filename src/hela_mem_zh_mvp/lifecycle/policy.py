"""Frozen-clock relevance and conservative archive eligibility."""

from __future__ import annotations

import math
from datetime import datetime


def time_decay_factor(
    *, occurred_at: datetime | None, now: datetime, half_life_days: float, min_factor: float
) -> float:
    """Return a bounded frozen-clock decay; missing timestamps stay neutral."""
    if occurred_at is None:
        return 1.0
    age_days = max(0.0, (now - occurred_at).total_seconds() / 86400)
    return max(min_factor, math.pow(0.5, age_days / half_life_days))


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


def is_forgetting_candidate(
    *,
    status: str,
    importance: float,
    recently_cited: bool,
    relevance: float,
    relevance_threshold: float,
    idle_days: int | None,
    min_idle_days: int,
    max_edge_weight: float,
    edge_weight_limit: float,
    activation_count: int,
    activation_count_limit: int,
) -> bool:
    """Fail closed unless lifecycle, recency, edge, and usage gates all agree."""
    return (
        may_archive(
            status=status,
            importance=importance,
            recently_cited=recently_cited,
            relevance=relevance,
            threshold=relevance_threshold,
        )
        and idle_days is not None
        and idle_days >= min_idle_days
        and max_edge_weight <= edge_weight_limit
        and activation_count <= activation_count_limit
    )
