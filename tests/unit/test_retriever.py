from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from hela_mem_zh_mvp.retriever import RankedMemory, Retriever
from hela_mem_zh_mvp.schemas import RetrievalMode


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
