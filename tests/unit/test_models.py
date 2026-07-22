import pytest

from hela_mem_zh_mvp.persistence.models import (
    Entity,
    EntityAlias,
    IngestionRun,
    Memory,
    MemoryAssociation,
    MemoryCandidate,
    MemoryEntity,
    MemoryEvidence,
    MemoryNamespace,
    MemoryRelation,
    SourceMessage,
)


def test_converged_schema_has_exactly_eleven_application_tables() -> None:
    assert set(Memory.metadata.tables) == {
        "memory_namespaces",
        "source_messages",
        "ingestion_runs",
        "entities",
        "entity_aliases",
        "memories",
        "memory_evidence",
        "memory_entities",
        "memory_candidates",
        "memory_relations",
        "memory_associations",
    }


def test_memory_entity_lookup_uses_its_declared_primary_key() -> None:
    assert [column.name for column in MemoryEntity.__table__.primary_key.columns] == [
        "memory_id",
        "entity_id",
    ]


def test_run_and_candidate_own_latest_operational_state() -> None:
    assert IngestionRun.__table__.c.extraction_outcomes.nullable is False
    assert MemoryCandidate.__table__.c.latest_decision.nullable is False


def test_evidence_and_relation_reference_canonical_memory_directly() -> None:
    evidence_fks = {fk.target_fullname for fk in MemoryEvidence.__table__.foreign_keys}
    relation_fks = {fk.target_fullname for fk in MemoryRelation.__table__.foreign_keys}
    assert "memories.id" in evidence_fks
    assert relation_fks == {"memories.namespace_id", "memories.id"}


@pytest.mark.parametrize(
    "model",
    [
        MemoryNamespace,
        SourceMessage,
        IngestionRun,
        Entity,
        EntityAlias,
        Memory,
        MemoryEvidence,
        MemoryEntity,
        MemoryCandidate,
        MemoryRelation,
        MemoryAssociation,
    ],
)
def test_pipeline_models_have_database_backed_audit_timestamps(model: type[object]) -> None:
    table = model.__table__  # type: ignore[attr-defined]
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False
    assert table.c.created_at.server_default is not None
    assert table.c.updated_at.server_default is not None
    assert table.c.updated_at.onupdate is not None
