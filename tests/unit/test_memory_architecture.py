from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hela_mem_zh_mvp.lifecycle.policy import (
    effective_relevance,
    is_forgetting_candidate,
    may_archive,
    time_decay_factor,
)
from hela_mem_zh_mvp.retrieval.activation import ActivationEdge, activate


def test_bounded_activation_preserves_two_hop_provenance_and_blocks_cycles() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()
    result = activate(
        {
            first: 1.0,
        },
        [
            ActivationEdge(first, second, "association", 0.8, "association_stats"),
            ActivationEdge(second, third, "supports", 0.5, "relation_evidence"),
            ActivationEdge(third, first, "supports", 1.0, "relation_evidence"),
        ],
        max_depth=3,
        max_neighbors_per_node=3,
        path_budget=10,
        alpha=0.5,
    )
    assert result.contributions[third] == pytest.approx(0.1)
    assert result.paths[third][0]["path_depth"] == 2
    assert any(path.get("blocked_reason") == "cycle_prevented" for path in result.paths[first])


def test_contradiction_is_context_without_positive_bonus() -> None:
    first, second = uuid4(), uuid4()
    result = activate(
        {first: 1.0},
        [ActivationEdge(first, second, "contradicts", 1.0, "relation_evidence")],
        max_depth=2,
        max_neighbors_per_node=1,
        path_budget=3,
        alpha=0.5,
    )
    assert second not in result.contributions
    assert result.paths[second][0]["activation_path"][0]["contribution"] == 0


def test_activation_threshold_traces_weak_edge_without_propagating_it() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()
    result = activate(
        {first: 0.5},
        [
            ActivationEdge(first, second, "association", 0.1, "association_stats"),
            ActivationEdge(second, third, "supports", 1.0, "relation_evidence"),
        ],
        max_depth=2,
        max_neighbors_per_node=2,
        path_budget=5,
        alpha=0.2,
        min_contribution=0.02,
    )

    assert second not in result.contributions
    assert third not in result.paths
    assert result.paths[second][0]["blocked_reason"] == "below_activation_threshold"


def test_lifecycle_is_frozen_clock_and_protects_current_or_important_claims() -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    old = effective_relevance(
        importance=0.2,
        confidence=0.7,
        last_activated_at=now - timedelta(days=120),
        now=now,
        positive_strength=0,
        negative_strength=0,
        half_life_days=30,
    )
    assert old < 0.08
    assert not may_archive(
        status="active", importance=0.2, recently_cited=False, relevance=old, threshold=0.08
    )
    assert not may_archive(
        status="superseded", importance=0.9, recently_cited=False, relevance=old, threshold=0.08
    )
    assert may_archive(
        status="superseded", importance=0.2, recently_cited=False, relevance=old, threshold=0.08
    )


def test_time_decay_is_bounded_and_missing_timestamp_is_neutral() -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    assert time_decay_factor(
        occurred_at=now - timedelta(days=30), now=now, half_life_days=30, min_factor=0.25
    ) == pytest.approx(0.5)
    assert time_decay_factor(
        occurred_at=now - timedelta(days=365), now=now, half_life_days=30, min_factor=0.25
    ) == 0.25
    assert time_decay_factor(
        occurred_at=None, now=now, half_life_days=30, min_factor=0.25
    ) == 1.0


def test_forgetting_candidate_requires_every_dry_run_gate() -> None:
    eligible = dict(
        status="superseded",
        importance=0.2,
        recently_cited=False,
        relevance=0.03,
        relevance_threshold=0.08,
        idle_days=90,
        min_idle_days=30,
        max_edge_weight=0.1,
        edge_weight_limit=0.2,
        activation_count=1,
        activation_count_limit=2,
    )
    assert is_forgetting_candidate(**eligible)
    assert not is_forgetting_candidate(**{**eligible, "status": "active"})
    assert not is_forgetting_candidate(**{**eligible, "idle_days": None})
    assert not is_forgetting_candidate(**{**eligible, "max_edge_weight": 0.8})
    assert not is_forgetting_candidate(**{**eligible, "activation_count": 3})
