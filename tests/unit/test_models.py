import pytest

from hela_mem_zh_mvp.persistence.models import (
    Entity,
    EntityAlias,
    IngestionRun,
    Memory,
    MemoryCandidate,
    MemoryEdge,
    MemoryEntity,
    MemoryNamespace,
    MemoryResolutionDecision,
    RetrievalItem,
    RetrievalRun,
    SourceMessage,
)


def test_memory_entity_lookup_uses_its_declared_primary_key() -> None:
    assert [column.name for column in MemoryEntity.__table__.primary_key.columns] == [
        "memory_id",
        "entity_id",
    ]


@pytest.mark.parametrize(
    "model",
    [
        MemoryNamespace,
        SourceMessage,
        IngestionRun,
        Entity,
        EntityAlias,
        Memory,
        MemoryEntity,
        MemoryCandidate,
        MemoryResolutionDecision,
        MemoryEdge,
        RetrievalRun,
        RetrievalItem,
    ],
)
def test_pipeline_models_have_database_backed_audit_timestamps(model: type[object]) -> None:
    table = model.__table__  # type: ignore[attr-defined]
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False
    assert table.c.created_at.server_default is not None
    assert table.c.updated_at.server_default is not None
    assert table.c.updated_at.onupdate is not None
