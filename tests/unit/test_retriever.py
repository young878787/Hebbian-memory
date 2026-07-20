from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from hela_mem_zh_mvp.config import load_config
from hela_mem_zh_mvp.retrieval import service as retrieval_service
from hela_mem_zh_mvp.retrieval.contracts import QueryScope, RankedMemory, RetrievalMode, RunMode
from hela_mem_zh_mvp.retrieval.service import Retriever


def _item(external_id: str, score: float, hebbian: float = 0.0) -> RankedMemory:
    memory = SimpleNamespace(
        id=uuid4(),
        external_id=external_id,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="active",
    )
    return RankedMemory(
        memory=memory, semantic_score=score, final_score=score + hebbian, hebbian_score=hebbian
    )


def test_hebbian_keeps_seeds_and_only_adds_positive_bonus() -> None:
    seeds = [_item("M1", 0.9), _item("M2", 0.8), _item("M3", 0.7)]
    bonus = _item("M4", 0.1, hebbian=0.4)
    no_bonus = _item("M5", 0.6)
    all_items = [*seeds, bonus, no_bonus]

    Retriever._mark_selected(all_items, seeds, RetrievalMode.HEBBIAN, final_top_k=5)

    selected = {item.external_id for item in all_items if item.selected}
    assert selected == {"M1", "M2", "M3", "M4"}
    assert no_bonus.final_rank is None


def test_hebbian_selects_contradiction_context_without_positive_bonus() -> None:
    seeds = [_item("M1", 0.9), _item("M2", 0.8), _item("M3", 0.7)]
    contradiction = _item("M4", 0.1)
    contradiction.activation_path = [{"edge_type": "contradicts"}]
    bonus = _item("M5", 0.1, hebbian=0.4)

    Retriever._mark_selected([*seeds, contradiction, bonus], seeds, RetrievalMode.HEBBIAN, 5)

    assert contradiction.selected
    assert contradiction.hebbian_score == 0
    assert bonus.selected


def test_retrieve_commits_after_persisting_run_trace(monkeypatch) -> None:
    session = Mock()
    item = _item("M1", 0.9)
    run = SimpleNamespace(id=uuid4())
    events: list[str] = []

    def create_run(*args, **kwargs):
        events.append("run")
        return run

    def create_items(*args, **kwargs):
        assert not session.commit.called
        events.append("items")

    class StubRetriever(Retriever):
        def _semantic_candidates(self, namespace_id, query_embedding):
            return [item]

    monkeypatch.setattr(retrieval_service, "create_retrieval_run", create_run)
    monkeypatch.setattr(retrieval_service, "create_retrieval_items", create_items)
    result = StubRetriever(
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

    assert events == ["run", "items"]
    session.commit.assert_called_once_with()
    assert result.run_id == run.id
