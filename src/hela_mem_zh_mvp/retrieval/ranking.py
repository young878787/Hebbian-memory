"""Pure retrieval scoring, decay, ordering, and final selection."""

from __future__ import annotations

from datetime import datetime

from ..config import AppConfig, RetrievalConfig
from ..lifecycle.policy import time_decay_factor
from .contracts import QueryScope, RankedMemory, RetrievalMode

CONTRADICTS_EDGE_TYPE = "contradicts"


def tie_key(item: RankedMemory) -> tuple[float, float, float, str]:
    occurred = item.memory.occurred_at.timestamp() if item.memory.occurred_at else float("-inf")
    created = item.memory.created_at.timestamp() if item.memory.created_at else float("-inf")
    return (-item.final_score, -occurred, -created, item.external_id)


def apply_time_decay(
    items: list[RankedMemory], config: RetrievalConfig, *, now: datetime
) -> None:
    decaying_types = set(config.time_decay_memory_types)
    for item in items:
        if item.memory.memory_type not in decaying_types:
            continue
        item.time_decay_factor = time_decay_factor(
            occurred_at=item.memory.occurred_at,
            now=now,
            half_life_days=config.time_decay_half_life_days,
            min_factor=config.time_decay_min_factor,
        )


def score(items: list[RankedMemory], config: AppConfig, scope: QueryScope, mode: RetrievalMode) -> None:
    adjustments = config.status_adjustments[scope.value]
    for item in items:
        item.status_adjustment = adjustments[item.memory.status]
        temporal_scope = getattr(item.memory, "temporal_scope", "unknown") or "unknown"
        if temporal_scope == "historical":
            if scope is QueryScope.CURRENT:
                item.status_adjustment -= 0.20
            elif scope is QueryScope.GENERAL:
                item.status_adjustment -= 0.05
        item.final_score = (
            item.semantic_score
            + item.lexical_score
            + item.rerank_score
            + item.model_rerank_score
        ) * item.time_decay_factor + item.status_adjustment
        if mode is RetrievalMode.HEBBIAN:
            item.final_score += item.hebbian_score


def mark_selected(
    items: list[RankedMemory],
    seeds: list[RankedMemory],
    mode: RetrievalMode,
    final_top_k: int,
    scope: QueryScope = QueryScope.GENERAL,
) -> None:
    if mode is RetrievalMode.EMBEDDING_ONLY:
        chosen = sorted(items, key=tie_key)[:final_top_k]
    else:
        seed_ids = {item.memory.id for item in seeds}
        contradictions = sorted(
            (
                item
                for item in items
                if item.memory.id not in seed_ids
                and any(
                    path["edge_type"] == CONTRADICTS_EDGE_TYPE for path in item.activation_path
                )
            ),
            key=tie_key,
        )
        bonus = sorted(
            (
                item
                for item in items
                if item.memory.id not in seed_ids
                and item.hebbian_score > 0
                and item not in contradictions
            ),
            key=tie_key,
        )
        direct = [
            item
            for item in items
            if item.memory.id not in seed_ids
            and item not in contradictions
            and item not in bonus
        ]
        remaining_candidates = sorted(
            (
                item for item in [*bonus, *direct]
            ),
            key=tie_key,
        )
        remaining = max(0, final_top_k - len(seeds))
        chosen = sorted([*seeds, *contradictions[:remaining]], key=tie_key)
        chosen.extend(remaining_candidates[: max(0, final_top_k - len(chosen))])
        chosen = sorted(chosen, key=tie_key)[:final_top_k]
    if scope is QueryScope.CURRENT and chosen and chosen[0].memory.status == "archived":
        replacement = next(
            (item for item in sorted(items, key=tie_key) if item.memory.status != "archived"),
            None,
        )
        if replacement is not None:
            chosen = [replacement, *[item for item in chosen if item is not replacement]]
            chosen = chosen[:final_top_k]
    for rank, item in enumerate(chosen, start=1):
        item.selected = True
        item.final_rank = rank


__all__ = ["apply_time_decay", "mark_selected", "score", "tie_key"]
