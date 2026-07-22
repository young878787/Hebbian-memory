from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from hela_mem_zh_mvp.config import load_config
from hela_mem_zh_mvp.retrieval.candidates import (
    apply_content_rerank,
    fuse_candidates,
    select_seeds,
)
from hela_mem_zh_mvp.retrieval.contracts import QueryScope, RankedMemory, RetrievalMode, RunMode
from hela_mem_zh_mvp.retrieval.ranking import apply_time_decay, mark_selected, score
from hela_mem_zh_mvp.retrieval.reranker import apply_model_rerank
from hela_mem_zh_mvp.retrieval.service import Retriever


def _item(external_id: str, score: float, hebbian: float = 0.0) -> RankedMemory:
    memory = SimpleNamespace(
        id=uuid4(),
        external_id=external_id,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="active",
        memory_type="preference",
        temporal_scope="current",
        modality="asserted",
        content=external_id,
    )
    return RankedMemory(
        memory=memory, semantic_score=score, final_score=score + hebbian, hebbian_score=hebbian
    )


def test_hebbian_keeps_seeds_prefers_positive_bonus_then_fills_direct_hits() -> None:
    seeds = [_item("M1", 0.9), _item("M2", 0.8), _item("M3", 0.7)]
    bonus = _item("M4", 0.1, hebbian=0.4)
    no_bonus = _item("M5", 0.6)
    all_items = [*seeds, bonus, no_bonus]

    mark_selected(all_items, seeds, RetrievalMode.HEBBIAN, final_top_k=5)

    selected = {item.external_id for item in all_items if item.selected}
    assert selected == {"M1", "M2", "M3", "M4", "M5"}


def test_hebbian_selects_contradiction_context_without_positive_bonus() -> None:
    seeds = [_item("M1", 0.9), _item("M2", 0.8), _item("M3", 0.7)]
    contradiction = _item("M4", 0.1)
    contradiction.activation_path = [{"edge_type": "contradicts"}]
    bonus = _item("M5", 0.1, hebbian=0.4)

    mark_selected([*seeds, contradiction, bonus], seeds, RetrievalMode.HEBBIAN, 5)

    assert contradiction.selected
    assert contradiction.hebbian_score == 0
    assert bonus.selected


def test_retrieve_returns_trace_without_database_persistence(monkeypatch) -> None:
    session = Mock()
    item = _item("M1", 0.9)
    monkeypatch.setattr("hela_mem_zh_mvp.retrieval.service.semantic_candidates", lambda *args: [item])
    monkeypatch.setattr("hela_mem_zh_mvp.retrieval.service.lexical_candidates", lambda *args: [])

    result = Retriever(
        session,
        load_config(),
        SimpleNamespace(embed=lambda query: [0.0]),
    ).retrieve(
        uuid4(),
        "測試查詢",
        RetrievalMode.EMBEDDING_ONLY,
        QueryScope.GENERAL,
        RunMode.EVALUATION,
    )

    session.commit.assert_not_called()
    assert result.run_id is not None


def test_lexical_candidate_fusion_reserves_a_bounded_seed_slot() -> None:
    semantic = [_item("M1", 0.9), _item("M2", 0.8), _item("M3", 0.7)]
    lexical = _item("M4", 0.0)
    lexical.lexical_score = 0.15
    lexical.lexical_sources = ["alias"]
    retriever = Retriever(Mock(), load_config(), Mock())

    candidates = fuse_candidates(semantic, [lexical])
    seeds = select_seeds(retriever.config.retrieval, semantic, [lexical])

    assert set(candidates) == {item.memory.id for item in [*semantic, lexical]}
    assert [item.external_id for item in seeds] == ["M1", "M2", "M4"]
    assert lexical.source == "alias"


def test_lexical_candidate_deduplicates_semantic_candidate_and_keeps_sources() -> None:
    semantic = _item("M1", 0.9)
    lexical = RankedMemory(
        semantic.memory,
        semantic_score=0.0,
        lexical_score=0.15,
        lexical_sources=["entity", "topic"],
    )

    candidates = fuse_candidates([semantic], [lexical])

    assert list(candidates.values()) == [semantic]
    assert semantic.lexical_score == 0.15
    assert semantic.lexical_sources == ["entity", "topic"]
    assert semantic.source == "semantic+entity+topic"


def test_time_decay_only_affects_configured_memory_types_with_frozen_clock() -> None:
    now = datetime(2026, 7, 1, tzinfo=UTC)
    event = _item("event", 0.8)
    event.memory.memory_type = "event"
    event.memory.occurred_at = datetime(2026, 4, 2, tzinfo=UTC)
    preference = _item("preference", 0.8)
    preference.memory.occurred_at = datetime(2020, 1, 1, tzinfo=UTC)
    missing_time = _item("missing", 0.8)
    missing_time.memory.memory_type = "decision"
    missing_time.memory.occurred_at = None
    retriever = Retriever(Mock(), load_config(), Mock())

    apply_time_decay([event, preference, missing_time], retriever.config.retrieval, now=now)

    assert event.time_decay_factor == pytest.approx(0.5)
    assert preference.time_decay_factor == 1.0
    assert missing_time.time_decay_factor == 1.0


def test_service_preserves_activation_threshold_block_reason() -> None:
    namespace_id = uuid4()
    seed = _item("seed", 0.5)
    target = _item("target", 0.0)
    relation = SimpleNamespace(
        source_id=seed.memory.id,
        target_id=target.memory.id,
        relation_type="supports",
        weight=0.01,
        origin="test",
    )
    session = Mock()
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [relation]),
        SimpleNamespace(all=lambda: []),
        SimpleNamespace(all=lambda: [target.memory]),
    ]
    candidates = {seed.memory.id: seed}

    Retriever(session, load_config(), Mock())._spread(
        namespace_id,
        [seed],
        candidates,
        max_depth=2,
    )

    trace = candidates[target.memory.id].activation_path[0]
    assert trace["blocked_reason"] == "below_activation_threshold"
    assert trace["path_depth"] == 1
    assert candidates[target.memory.id].hebbian_score == 0.0


def test_historical_memory_remains_retrievable_but_does_not_lead_current_scope() -> None:
    current = _item("current", 0.8)
    historical = _item("historical", 0.8)
    historical.memory.temporal_scope = "historical"
    items = [historical, current]

    score(items, load_config(), QueryScope.CURRENT, RetrievalMode.EMBEDDING_ONLY)
    mark_selected(items, [], RetrievalMode.EMBEDDING_ONLY, 2, QueryScope.CURRENT)

    assert current.final_rank == 1
    assert historical.selected
    assert historical.final_rank == 2


def test_content_rerank_is_bounded_and_rewards_query_overlap() -> None:
    relevant = _item("relevant", 0.4)
    relevant.memory.content = "角色閱讀時習慣播放輕音樂"
    unrelated = _item("unrelated", 0.4)
    unrelated.memory.content = "使用者正在研究資料庫 migration"
    config = load_config().retrieval

    apply_content_rerank([relevant, unrelated], config, "閱讀時會播放什麼音樂？")

    assert 0 < relevant.rerank_score <= config.content_rerank_bonus
    assert unrelated.rerank_score == 0.0


def test_model_rerank_only_scores_valid_candidate_ids() -> None:
    first, second = _item("first", 0.8), _item("second", 0.7)
    provider = SimpleNamespace(
        generate_structured=lambda prompt, model: model.model_validate(
            {
                "schema_version": "memory-rerank-v1",
                "ranked_external_ids": ["second", "first"],
            }
        )
    )

    assert apply_model_rerank(provider, "query", [first, second], load_config().retrieval)
    assert second.model_rerank_score > first.model_rerank_score > 0

    invalid = SimpleNamespace(
        generate_structured=lambda prompt, model: model.model_validate(
            {
                "schema_version": "memory-rerank-v1",
                "ranked_external_ids": ["outside"],
            }
        )
    )
    first.model_rerank_score = second.model_rerank_score = 0.0
    assert not apply_model_rerank(invalid, "query", [first, second], load_config().retrieval)
    assert first.model_rerank_score == second.model_rerank_score == 0.0
