from types import SimpleNamespace

import pytest

from hela_mem_zh_mvp.config import ResolutionConfig
from hela_mem_zh_mvp.ingestion.contracts import (
    EffectiveOrder,
    ResolutionAction,
    ResolutionDecision,
)
from hela_mem_zh_mvp.ingestion.normalization import normalize_lookup, state_key, topic_key
from hela_mem_zh_mvp.ingestion.resolver import validate_ai_decision
from hela_mem_zh_mvp.ingestion.service import _resolution_topic_key
from hela_mem_zh_mvp.persistence.models import Entity


def _config() -> ResolutionConfig:
    return ResolutionConfig(
        candidate_limit=8,
        ambiguous_similarity_min=0.72,
        exact_merge_similarity=0.96,
        deterministic_confidence_min=0.95,
        ai_decision_confidence_min=0.85,
        supersede_confidence_min=0.90,
        event_time_tolerance_seconds=60,
        max_ai_attempts=2,
        resolver_schema_version="memory-resolution-v2",
        prompt_version="resolver-zh-v1",
    )


def test_normalization_is_conservative_and_deterministic() -> None:
    assert normalize_lookup(" ＣＭＰ 170HX！ ") == "cmp170hx"
    assert topic_key("CMP 170HX") == "cmp_170hx"
    assert state_key("entity-1", "decision", "gpu_purchase", "owned_item") == state_key(
        "entity-1", "decision", "gpu_purchase", "owned_item"
    )
    assert state_key("entity-1", "decision", "gpu_purchase", "unknown") is None


def test_single_entity_fallback_topic_is_only_used_when_scope_is_unambiguous() -> None:
    entity = Entity(canonical_name="RTX 3090", entity_type="concept", confidence=1.0)
    assert _resolution_topic_key("m-1", [], ["rtx"], {"rtx": entity}) == (
        "entity_rtx3090",
        "entity-fallback-v1",
    )
    assert _resolution_topic_key("m-1", [], [], {}) == (None, None)


def test_resolution_schema_rejects_unknown_supersede_order() -> None:
    with pytest.raises(ValueError, match="known effective_order"):
        ResolutionDecision(
            candidate_id="new-1",
            action=ResolutionAction.SUPERSEDE,
            target_refs=["old-1"],
            confidence=0.99,
            reason="x",
        )


def test_ai_post_validation_rejects_target_outside_snapshot() -> None:
    decision = ResolutionDecision(
        candidate_id="new-1",
        action=ResolutionAction.CONTRADICT,
        target_refs=["missing"],
        confidence=0.95,
        reason="x",
        evidence_quotes=["沒有買"],
        effective_order=EffectiveOrder.UNKNOWN,
    )
    assert (
        validate_ai_decision(
            decision,
            candidate_ref="new-1",
            target_by_ref={"old-1": SimpleNamespace(content="舊資料")},
            candidate_evidence="沒有買",
            config=_config(),
        )
        == "target outside supplied snapshot"
    )


def test_extractor_resolver_kind_fits_the_persisted_contract() -> None:
    assert len("extractor") <= 16
