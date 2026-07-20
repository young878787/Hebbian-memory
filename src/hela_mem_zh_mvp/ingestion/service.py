"""Namespace-scoped transactional persistence for extracted memories."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ResolutionConfig
from ..persistence.edges import upsert_edge
from ..persistence.models import (
    Entity,
    EntityAlias,
    IngestionRun,
    Memory,
    MemoryCandidate,
    MemoryEntity,
    MemoryResolutionDecision,
)
from ..persistence.models import (
    SourceMessage as StoredSourceMessage,
)
from ..persistence.namespaces import get_namespace
from ..persistence.resolutions import ResolutionApplyError, apply_resolution
from ..providers.embedding import EmbeddingClient
from .candidates import find_scoped_candidates
from .contracts import (
    EdgeType,
    ExtractionResult,
    ResolutionAction,
    ResolutionDecision,
    SourceMessage,
)
from .input import content_hash
from .normalization import NORMALIZER_VERSION, TOPIC_VERSION, normalize_lookup, state_key, topic_key
from .resolver import (
    Resolution,
    candidate_key,
    canonical_key,
    reliable_order,
    resolve_memory,
    snapshot_hash,
)


class StoreError(ValueError):
    pass


def _merge_unique(values: list[str] | None, additions: list[str]) -> list[str]:
    return list(dict.fromkeys([*(values or []), *additions]))


def _resolution_topic_key(
    candidate_id: str,
    concepts: list[str],
    entity_candidate_ids: list[str],
    entity_by_candidate: dict[str, Entity],
) -> tuple[str | None, str | None]:
    """Return explicit topic or a safe single-entity fallback for state slots."""
    if normalized := topic_key(concepts[0] if concepts else None):
        return normalized, TOPIC_VERSION
    if len(entity_candidate_ids) == 1:
        entity = entity_by_candidate[entity_candidate_ids[0]]
        return f"entity_{normalize_lookup(entity.canonical_name)}", "entity-fallback-v1"
    return None, None


def _entity(
    session: Session,
    namespace_id: object,
    canonical_name: str,
    entity_type: str,
    aliases: list[str],
    confidence: float,
) -> Entity:
    canonical_normalized = normalize_lookup(canonical_name)
    aliases_by_key = {normalize_lookup(value): value for value in [canonical_name, *aliases]}
    alias_matches = session.scalars(
        select(EntityAlias).where(
            EntityAlias.namespace_id == namespace_id,
            EntityAlias.entity_type == entity_type,
            EntityAlias.alias_normalized.in_(list(aliases_by_key)),
            EntityAlias.status == "active",
        )
    ).all()
    matched_ids = {item.entity_id for item in alias_matches}
    if len(matched_ids) > 1:
        raise StoreError(f"entity alias collision for {canonical_name!r}")
    entity = session.scalar(
        select(Entity).where(
            Entity.namespace_id == namespace_id,
            Entity.entity_type == entity_type,
            Entity.canonical_name == canonical_name,
        )
    )
    if entity is not None and matched_ids and entity.id not in matched_ids:
        raise StoreError(f"canonical/alias entity collision for {canonical_name!r}")
    if entity is None and matched_ids:
        entity = session.get(Entity, next(iter(matched_ids)))
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
    for normalized, raw in aliases_by_key.items():
        alias = session.scalar(
            select(EntityAlias).where(
                EntityAlias.namespace_id == namespace_id,
                EntityAlias.entity_type == entity_type,
                EntityAlias.alias_normalized == normalized,
            )
        )
        if alias is not None and alias.entity_id != entity.id:
            raise StoreError(f"entity alias collision for {raw!r}")
        if alias is None:
            session.add(
                EntityAlias(
                    namespace_id=namespace_id,
                    entity_id=entity.id,
                    entity_type=entity_type,
                    alias_raw=raw,
                    alias_normalized=normalized,
                    source_type="canonical" if normalized == canonical_normalized else "extractor",
                    confidence=confidence,
                    status="active",
                    normalizer_version=NORMALIZER_VERSION,
                )
            )
    return entity


def write_ingestion(
    session: Session,
    namespace_key: str,
    messages: list[SourceMessage],
    extraction: ExtractionResult,
    embeddings: EmbeddingClient,
    *,
    extractor_model: str,
    resolution: ResolutionConfig,
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
    staged_by_candidate: dict[str, MemoryCandidate] = {}
    explicit_state_relation_ids = {
        candidate_id
        for relation in extraction.relations
        if relation.edge_type in {EdgeType.SUPERSEDES, EdgeType.CONTRADICTS}
        for candidate_id in (relation.source_candidate_id, relation.target_candidate_id)
    }
    counts = {
        "created": 0,
        "merged": 0,
        "superseded": 0,
        "contradicted": 0,
        "ignored": 0,
        "deferred": 0,
    }
    created_memories: list[Memory] = []
    for candidate in extraction.memories:
        normalized_topic, normalized_topic_version = _resolution_topic_key(
            candidate.candidate_id,
            candidate.concepts,
            candidate.entity_candidate_ids,
            entity_by_candidate,
        )
        entity_ids = [entity_by_candidate[item].id for item in candidate.entity_candidate_ids]
        embedding = embeddings.embed(candidate.content)
        scoped = find_scoped_candidates(
            session,
            namespace.id,
            embedding=embedding,
            memory_type=candidate.memory_type.value,
            topic_key=normalized_topic,
            entity_ids=entity_ids,
            limit=resolution.candidate_limit,
        )
        stage_key = candidate_key(
            namespace.id, candidate, entity_ids=entity_ids, topic_key=normalized_topic
        )
        staged = session.scalar(
            select(MemoryCandidate).where(
                MemoryCandidate.namespace_id == namespace.id,
                MemoryCandidate.candidate_key == stage_key,
            )
        )
        if staged is None:
            staged = MemoryCandidate(
                namespace_id=namespace.id,
                ingestion_run_id=run.id,
                candidate_key=stage_key,
                extraction_payload=candidate.model_dump(mode="json"),
                normalized_payload={
                    "topic_key": normalized_topic,
                    "attribute_key": candidate.attribute_key or "unknown",
                    "entity_ids": [str(value) for value in entity_ids],
                },
                embedding=embedding,
            )
            session.add(staged)
            session.flush()
        staged_by_candidate[candidate.candidate_id] = staged
        resolution_result = resolve_memory(session, namespace.id, candidate, candidates=scoped)
        before_state = {str(item.memory.id): item.memory.status for item in scoped}
        if (
            resolution_result.action is ResolutionAction.DEFER
            and candidate.candidate_id in explicit_state_relation_ids
        ):
            # The extractor supplied a relation only among this validated
            # batch.  Persist both endpoints, then let the atomic resolver
            # validate state-slot, direction, and cycle constraints below.
            resolution_result = Resolution(
                ResolutionAction.CREATE,
                candidates=resolution_result.candidates,
                reason="explicit extractor state relation pending atomic validation",
            )
        if resolution_result.action is ResolutionAction.DEFER:
            staged.status = "deferred"
            counts["deferred"] += 1
            session.add(
                MemoryResolutionDecision(
                    namespace_id=namespace.id,
                    candidate_id=staged.id,
                    resolver_kind="deterministic",
                    resolver_schema_version=resolution.resolver_schema_version,
                    prompt_version=resolution.prompt_version,
                    candidate_snapshot_hash=snapshot_hash(scoped),
                    action=ResolutionAction.DEFER.value,
                    target_memory_ids=[str(item.memory.id) for item in scoped],
                    confidence=0.0,
                    reason=resolution_result.reason,
                    evidence_quotes=[],
                    validation_status="deferred",
                    before_state=before_state,
                    after_state=before_state,
                )
            )
            continue
        if resolution_result.existing:
            memory = resolution_result.existing
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
                topic_raw=candidate.concepts[0] if candidate.concepts else None,
                topic_key=normalized_topic,
                topic_version=normalized_topic_version,
                attribute_key=candidate.attribute_key or "unknown",
                state_key=state_key(
                    entity_ids[0] if len(entity_ids) == 1 else None,
                    candidate.memory_type.value,
                    normalized_topic,
                    candidate.attribute_key,
                ),
                occurred_at=candidate.occurred_at,
                importance=candidate.importance,
                confidence=candidate.confidence,
                source_session_id=next(
                    message.session_id
                    for message in messages
                    if message.message_id == candidate.evidence_message_ids[0]
                ),
                source_message_ids=candidate.evidence_message_ids,
                embedding=embedding,
                metadata_={
                    "origin": "extractor",
                    "resolution_scope": "entity_topic"
                    if entity_ids and normalized_topic
                    else "no_entity_or_topic",
                },
            )
            session.add(memory)
            session.flush()
            created_memories.append(memory)
            counts["created"] += 1
        staged.status = "resolved"
        session.add(
            MemoryResolutionDecision(
                namespace_id=namespace.id,
                candidate_id=staged.id,
                resolver_kind="deterministic",
                resolver_schema_version=resolution.resolver_schema_version,
                prompt_version=resolution.prompt_version,
                candidate_snapshot_hash=snapshot_hash(scoped),
                action=resolution_result.action.value,
                target_memory_ids=[str(memory.id)] if resolution_result.existing else [],
                confidence=1.0,
                reason=resolution_result.reason,
                evidence_quotes=[candidate.evidence],
                validation_status="passed",
                before_state=before_state,
                after_state={str(memory.id): memory.status},
            )
        )
        memory_by_candidate[candidate.candidate_id] = memory
        for entity_id in candidate.entity_candidate_ids:
            entity = entity_by_candidate[entity_id]
            if session.get(MemoryEntity, (memory.id, entity.id)) is None:
                session.add(
                    MemoryEntity(
                        namespace_id=namespace.id,
                        memory_id=memory.id,
                        entity_id=entity.id,
                        mention_role=(
                            "subject"
                            if candidate.primary_entity_candidate_id == entity_id
                            or (
                                candidate.primary_entity_candidate_id is None
                                and entity_id == candidate.entity_candidate_ids[0]
                            )
                            else "mentioned"
                        ),
                        confidence=candidate.confidence,
                    )
                )
    for relation in extraction.relations:
        if (
            relation.source_candidate_id not in memory_by_candidate
            or relation.target_candidate_id not in memory_by_candidate
        ):
            # One endpoint is deferred; creating an edge would incorrectly
            # make an unresolved comparison visible as a resolved fact.
            continue
        source, target = (
            memory_by_candidate[relation.source_candidate_id],
            memory_by_candidate[relation.target_candidate_id],
        )
        if relation.edge_type in {EdgeType.SUPERSEDES, EdgeType.CONTRADICTS}:
            order = reliable_order(
                source.occurred_at,
                target.occurred_at,
                resolution.event_time_tolerance_seconds,
            )
            decision = ResolutionDecision(
                candidate_id=relation.source_candidate_id,
                action=(
                    ResolutionAction.SUPERSEDE
                    if relation.edge_type is EdgeType.SUPERSEDES
                    else ResolutionAction.CONTRADICT
                ),
                target_refs=[relation.target_candidate_id],
                relationship=(
                    "same_state_changed"
                    if relation.edge_type is EdgeType.SUPERSEDES
                    else "mutually_exclusive"
                ),
                effective_order=order,
                confidence=1.0,
                reason=relation.evidence,
                evidence_quotes=[relation.evidence],
            )
            try:
                before = {str(item.id): item.status for item in (source, target)}
                after = apply_resolution(session, namespace.id, source, decision, [target])
            except ResolutionApplyError as exc:
                raise StoreError(
                    f"invalid extractor {relation.edge_type.value} relation "
                    f"{relation.source_candidate_id}->{relation.target_candidate_id}: {exc}"
                ) from exc
            session.add(
                MemoryResolutionDecision(
                    namespace_id=namespace.id,
                    candidate_id=staged_by_candidate[relation.source_candidate_id].id,
                    resolver_kind="extractor",
                    resolver_schema_version=resolution.resolver_schema_version,
                    prompt_version=resolution.prompt_version,
                    candidate_snapshot_hash=snapshot_hash([]),
                    action=decision.action.value,
                    target_memory_ids=[str(target.id)],
                    confidence=decision.confidence,
                    reason=decision.reason,
                    evidence_quotes=decision.evidence_quotes,
                    validation_status="passed",
                    before_state=before,
                    after_state=after,
                )
            )
            if relation.edge_type is EdgeType.SUPERSEDES:
                counts["superseded"] += 1
            else:
                counts["contradicted"] += 1
            continue
        upsert_edge(
            session,
            namespace.id,
            source.id,
            target.id,
            relation.edge_type,
            1.0,
            {"origin": "extractor", "evidence": relation.evidence},
        )
    for memory in created_memories:
        entity_ids = session.scalars(
            select(MemoryEntity.entity_id).where(
                MemoryEntity.namespace_id == namespace.id, MemoryEntity.memory_id == memory.id
            )
        ).all()
        for neighbor in find_scoped_candidates(
            session,
            namespace.id,
            embedding=memory.embedding,
            memory_type=memory.memory_type,
            topic_key=memory.topic_key,
            entity_ids=entity_ids,
            limit=resolution.candidate_limit,
        ):
            if (
                neighbor.memory.id != memory.id
                and neighbor.similarity >= resolution.ambiguous_similarity_min
            ):
                upsert_edge(
                    session,
                    namespace.id,
                    memory.id,
                    neighbor.memory.id,
                    EdgeType.SEMANTIC,
                    neighbor.similarity,
                    {"origin": "scoped_pgvector"},
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
