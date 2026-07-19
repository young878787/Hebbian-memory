"""Namespace-scoped transactional persistence for extracted memories."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .edges import upsert_edge
from .embedding import EmbeddingClient
from .ingestion import content_hash
from .models import (
    Entity,
    IngestionRun,
    Memory,
    MemoryEdge,
    MemoryEntity,
    MemoryNamespace,
    RetrievalItem,
    RetrievalRun,
)
from .models import (
    SourceMessage as StoredSourceMessage,
)
from .resolver import canonical_key, resolve_memory
from .schemas import EdgeType, ExtractionResult, SourceMessage

FIXTURE_NAMESPACE = "fixture-pipeline-v1"


class StoreError(ValueError):
    pass


def get_namespace(session: Session, namespace_key: str, *, create: bool = True) -> MemoryNamespace:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == namespace_key)
    )
    if namespace is None:
        if not create:
            raise StoreError(f"unknown namespace {namespace_key!r}")
        namespace = MemoryNamespace(namespace_key=namespace_key, display_name=namespace_key)
        session.add(namespace)
        session.flush()
    return namespace


def _merge_unique(values: list[str] | None, additions: list[str]) -> list[str]:
    return list(dict.fromkeys([*(values or []), *additions]))


def _entity(
    session: Session,
    namespace_id: object,
    canonical_name: str,
    entity_type: str,
    aliases: list[str],
    confidence: float,
) -> Entity:
    entity = session.scalar(
        select(Entity).where(
            Entity.namespace_id == namespace_id,
            Entity.entity_type == entity_type,
            Entity.canonical_name == canonical_name,
        )
    )
    if entity is None:
        entity = Entity(
            namespace_id=namespace_id,
            canonical_name=canonical_name,
            entity_type=entity_type,
            aliases=list(dict.fromkeys(aliases)),
            confidence=confidence,
        )
        session.add(entity)
        session.flush()
    else:
        entity.aliases = _merge_unique(entity.aliases, aliases)
        entity.confidence = max(entity.confidence, confidence)
    return entity


def write_ingestion(
    session: Session,
    namespace_key: str,
    messages: list[SourceMessage],
    extraction: ExtractionResult,
    embeddings: EmbeddingClient,
    *,
    extractor_model: str,
    external_id_by_canonical_key: dict[str, str] | None = None,
) -> dict[str, int]:
    """Write one fully validated extraction atomically; caller owns commit/rollback."""
    namespace = get_namespace(session, namespace_key)
    for message in messages:
        existing = session.get(StoredSourceMessage, (namespace.id, message.message_id))
        digest = content_hash(message.content)
        if existing and existing.content_hash != digest:
            raise StoreError(f"message_id conflict for {message.message_id!r}")
        if existing is None:
            session.add(
                StoredSourceMessage(
                    namespace_id=namespace.id,
                    message_id=message.message_id,
                    session_id=message.session_id,
                    role=message.role,
                    content=message.content,
                    content_hash=digest,
                    occurred_at=message.occurred_at,
                    metadata_=message.metadata,
                )
            )
    session.flush()
    run = IngestionRun(
        namespace_id=namespace.id,
        status="running",
        extractor_model=extractor_model,
        extractor_schema_version=extraction.schema_version,
        message_count=len(messages),
    )
    session.add(run)
    entity_by_candidate = {
        item.candidate_id: _entity(
            session,
            namespace.id,
            item.canonical_name,
            item.entity_type.value,
            item.aliases_seen,
            item.confidence,
        )
        for item in extraction.entities
    }
    memory_by_candidate: dict[str, Memory] = {}
    counts = {"created": 0, "merged": 0, "superseded": 0, "contradicted": 0, "ignored": 0}
    created_memories: list[Memory] = []
    for candidate in extraction.memories:
        resolution = resolve_memory(session, namespace.id, candidate)
        if resolution.existing:
            memory = resolution.existing
            memory.source_message_ids = _merge_unique(
                memory.source_message_ids, candidate.evidence_message_ids
            )
            memory.confidence = max(memory.confidence, candidate.confidence)
            counts["merged"] += 1
        else:
            key = canonical_key(candidate.content, candidate.memory_type.value)
            memory = Memory(
                namespace_id=namespace.id,
                external_id=(external_id_by_canonical_key or {}).get(
                    key, f"mem-{uuid4().hex[:12]}"
                ),
                canonical_key=key,
                extraction_schema_version=extraction.schema_version,
                content=candidate.content,
                memory_type=candidate.memory_type.value,
                topic=candidate.concepts[0] if candidate.concepts else None,
                occurred_at=candidate.occurred_at,
                importance=candidate.importance,
                confidence=candidate.confidence,
                source_session_id=next(
                    message.session_id
                    for message in messages
                    if message.message_id == candidate.evidence_message_ids[0]
                ),
                source_message_ids=candidate.evidence_message_ids,
                embedding=embeddings.embed(candidate.content),
                metadata_={"origin": "extractor"},
            )
            session.add(memory)
            session.flush()
            created_memories.append(memory)
            counts["created"] += 1
        memory_by_candidate[candidate.candidate_id] = memory
        for entity_id in candidate.entity_candidate_ids:
            entity = entity_by_candidate[entity_id]
            if session.get(MemoryEntity, (namespace.id, memory.id, entity.id)) is None:
                session.add(
                    MemoryEntity(
                        namespace_id=namespace.id,
                        memory_id=memory.id,
                        entity_id=entity.id,
                        confidence=candidate.confidence,
                    )
                )
    for relation in extraction.relations:
        source, target = (
            memory_by_candidate[relation.source_candidate_id],
            memory_by_candidate[relation.target_candidate_id],
        )
        upsert_edge(
            session,
            namespace.id,
            source.id,
            target.id,
            relation.edge_type,
            1.0,
            {"origin": "extractor", "evidence": relation.evidence},
        )
        if relation.edge_type is EdgeType.SUPERSEDES:
            target.status = "superseded"
            counts["superseded"] += 1
        elif relation.edge_type is EdgeType.CONTRADICTS:
            counts["contradicted"] += 1
    for memory in created_memories:
        neighbors = session.scalars(
            select(Memory)
            .where(Memory.namespace_id == namespace.id, Memory.id != memory.id)
            .order_by(Memory.created_at.desc())
            .limit(5)
        ).all()
        for neighbor in neighbors:
            similarity = sum(
                a * b for a, b in zip(memory.embedding, neighbor.embedding, strict=True)
            )
            if similarity >= 0.72:
                upsert_edge(
                    session,
                    namespace.id,
                    memory.id,
                    neighbor.id,
                    EdgeType.SEMANTIC,
                    similarity,
                    {"origin": "resolver"},
                )
        prior = session.scalar(
            select(Memory)
            .where(
                Memory.namespace_id == namespace.id,
                Memory.id != memory.id,
                Memory.source_session_id == memory.source_session_id,
                Memory.occurred_at <= memory.occurred_at,
            )
            .order_by(Memory.occurred_at.desc())
        )
        if prior is not None:
            upsert_edge(
                session,
                namespace.id,
                memory.id,
                prior.id,
                EdgeType.TEMPORAL,
                0.25,
                {"origin": "resolver"},
            )
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.created_count, run.merged_count = counts["created"], counts["merged"]
    run.superseded_count, run.contradicted_count, run.ignored_count = (
        counts["superseded"],
        counts["contradicted"],
        counts["ignored"],
    )
    return {"messages": len(messages), **counts}


def reset_test_namespace(session: Session) -> None:
    """The only destructive reset. The key is deliberately not caller configurable."""
    namespace = get_namespace(session, FIXTURE_NAMESPACE, create=False)
    namespace_id = namespace.id
    run_ids = select(IngestionRun.id).where(IngestionRun.namespace_id == namespace_id)
    retrieval_ids = select(RetrievalRun.id).where(RetrievalRun.namespace_id == namespace_id)
    session.query(RetrievalItem).filter(RetrievalItem.run_id.in_(retrieval_ids)).delete(
        synchronize_session=False
    )
    session.query(RetrievalRun).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(MemoryEdge).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryEntity).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(Memory).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(Entity).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(StoredSourceMessage).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(IngestionRun).filter(IngestionRun.id.in_(run_ids)).delete(
        synchronize_session=False
    )
