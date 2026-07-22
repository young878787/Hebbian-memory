"""Cross-message resolution orchestration with provider calls outside transactions."""

from __future__ import annotations

import time
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ResolutionConfig
from ..persistence.evidence import ensure_memory_evidence
from ..persistence.models import (
    IngestionRun,
    Memory,
    MemoryCandidate,
    MemoryEntity,
    MemoryEvidence,
)
from ..persistence.models import (
    SourceMessage as StoredSourceMessage,
)
from ..persistence.normalization import state_key
from ..persistence.relations import upsert_memory_relation
from ..persistence.resolutions import ResolutionApplyError, apply_resolution
from ..providers.base import StructuredProvider
from .candidates import ScopedCandidate, find_scoped_candidates
from .contracts import ExtractedMemory, ResolutionAction, ResolutionDecision
from .resolver import (
    canonical_key,
    resolution_prompt,
    snapshot_hash,
    validate_ai_decision,
)
from .state import STATE_POLICY_VERSION, derive_initial_state


def _candidate_context(
    session: Session, staged: MemoryCandidate, candidate: ExtractedMemory, config: ResolutionConfig
) -> list[ScopedCandidate]:
    payload = staged.normalized_payload
    entity_ids = [UUID(value) for value in payload["entity_ids"]]
    return find_scoped_candidates(
        session,
        staged.namespace_id,
        embedding=staged.embedding,
        memory_type=candidate.memory_type.value,
        topic_key=payload.get("topic_key"),
        attribute_key=candidate.attribute_key,
        entity_ids=entity_ids,
        limit=config.candidate_limit,
    )


def _candidate_snapshot(candidate: ExtractedMemory) -> dict[str, object]:
    return {
        "content": candidate.content,
        "memory_type": candidate.memory_type.value,
        "attribute_key": candidate.attribute_key,
        "occurred_at": candidate.occurred_at.isoformat(),
        "evidence": candidate.evidence,
        "modality": candidate.modality.value,
        "temporal_scope": candidate.temporal_scope.value,
    }


def _target_snapshot(
    session: Session, scoped: list[ScopedCandidate]
) -> tuple[list[dict[str, object]], dict[str, Memory]]:
    target_by_ref: dict[str, Memory] = {}
    snapshot: list[dict[str, object]] = []
    for index, item in enumerate(scoped, start=1):
        ref = f"target-{index}"
        target_by_ref[ref] = item.memory
        snapshot.append(
            {
                "ref": ref,
                "content": item.memory.content,
                "memory_type": item.memory.memory_type,
                "attribute_key": item.memory.attribute_key,
                "status": item.memory.status,
                "modality": item.memory.modality,
                "temporal_scope": item.memory.temporal_scope,
                "occurred_at": item.memory.occurred_at.isoformat()
                if item.memory.occurred_at
                else None,
                "evidence_message_ids": list(
                    session.scalars(
                        select(MemoryEvidence.source_message_id).where(
                            MemoryEvidence.memory_id == item.memory.id
                        )
                    ).all()
                ),
                "similarity": round(item.similarity, 8),
            }
        )
    return snapshot, target_by_ref


def _persist_decision(
    session: Session,
    *,
    staged: MemoryCandidate,
    config: ResolutionConfig,
    snapshot: str,
    action: ResolutionAction,
    targets: list[Memory],
    confidence: float,
    reason: str,
    evidence_quotes: list[str],
    validation_status: str,
    before_state: dict[str, object],
    after_state: dict[str, object],
    resolver_model: str | None,
) -> None:
    staged.latest_decision = {
        "resolver_kind": "ai",
        "resolver_model": resolver_model,
        "resolver_schema_version": config.resolver_schema_version,
        "prompt_version": config.prompt_version,
        "candidate_snapshot_hash": snapshot,
        "action": action.value,
        "target_memory_ids": [str(target.id) for target in targets],
        "confidence": confidence,
        "reason": reason,
        "evidence_quotes": evidence_quotes,
        "validation_status": validation_status,
        "before_state": before_state,
        "after_state": after_state,
    }


def _create_memory(session: Session, staged: MemoryCandidate, candidate: ExtractedMemory) -> Memory:
    payload = staged.normalized_payload
    entity_ids = [UUID(value) for value in payload["entity_ids"]]
    source = session.get(
        StoredSourceMessage, (staged.namespace_id, candidate.evidence_message_ids[0])
    )
    if source is None:
        raise ValueError("candidate source message is missing")
    initial_state = derive_initial_state(
        candidate.modality, candidate.temporal_scope, candidate.memory_type
    )
    memory = Memory(
        namespace_id=staged.namespace_id,
        external_id=f"mem-{uuid4().hex[:12]}",
        canonical_key=canonical_key(candidate.content, candidate.memory_type.value),
        extraction_schema_version="memory-extraction-v2",
        content=candidate.content,
        memory_type=candidate.memory_type.value,
        topic=candidate.concepts[0] if candidate.concepts else None,
        topic_raw=candidate.concepts[0] if candidate.concepts else None,
        topic_key=payload.get("topic_key"),
        topic_version=payload.get("topic_version"),
        attribute_key=candidate.attribute_key or "unknown",
        state_key=state_key(
            entity_ids[0] if len(entity_ids) == 1 else None,
            candidate.memory_type.value,
            payload.get("topic_key"),
            candidate.attribute_key,
        ),
        occurred_at=candidate.occurred_at,
        status=initial_state.status.value,
        modality=candidate.modality.value,
        temporal_scope=candidate.temporal_scope.value,
        importance=candidate.importance,
        confidence=candidate.confidence,
        source_session_id=source.session_id,
        embedding=staged.embedding,
        metadata_={
            "origin": "cross_message_resolver",
            "resolution_scope": "entity_topic",
            "state_policy_version": STATE_POLICY_VERSION,
            "initial_state_reason": initial_state.reason,
        },
    )
    session.add(memory)
    session.flush()
    for candidate_entity_id, entity_id in zip(candidate.entity_candidate_ids, entity_ids, strict=True):
        session.add(
            MemoryEntity(
                namespace_id=staged.namespace_id,
                memory_id=memory.id,
                entity_id=entity_id,
                mention_role=(
                    "subject"
                    if candidate.primary_entity_candidate_id == candidate_entity_id
                    else "mentioned"
                ),
                confidence=candidate.confidence,
            )
        )
    return memory


def _defer(
    session: Session,
    *,
    staged: MemoryCandidate,
    config: ResolutionConfig,
    snapshot: str,
    scoped: list[ScopedCandidate],
    reason: str,
    resolver_model: str | None,
    confidence: float = 0.0,
) -> dict[str, object]:
    before = {str(item.memory.id): item.memory.status for item in scoped}
    staged.status = "deferred"
    staged.attempt_count += 1
    _persist_decision(
        session,
        staged=staged,
        config=config,
        snapshot=snapshot,
        action=ResolutionAction.DEFER,
        targets=[item.memory for item in scoped],
        confidence=confidence,
        reason=reason,
        evidence_quotes=[],
        validation_status="deferred",
        before_state=before,
        after_state=before,
        resolver_model=resolver_model,
    )
    return {"candidate_id": str(staged.id), "action": "DEFER", "reason": reason}


def resolve_staged_candidates(
    session: Session,
    run: IngestionRun,
    provider: StructuredProvider,
    config: ResolutionConfig,
    *,
    resolver_model: str,
    request_interval_seconds: float = 0.0,
) -> tuple[dict[str, int], list[dict[str, object]]]:
    """Resolve only ambiguous candidates created by this run.

    Each provider invocation occurs after the immutable candidate/snapshot has
    committed and before a new transaction re-checks the snapshot.
    """
    with session.begin():
        candidate_ids = list(
            session.scalars(
                select(MemoryCandidate.id).where(
                    MemoryCandidate.ingestion_run_id == run.id,
                    MemoryCandidate.status == "deferred",
                )
            ).all()
        )
    counts = {"created": 0, "merged": 0, "superseded": 0, "contradicted": 0, "ignored": 0, "deferred": 0}
    records: list[dict[str, object]] = []
    for index, staged_id in enumerate(candidate_ids):
        if index or candidate_ids:
            time.sleep(request_interval_seconds)
        with session.begin():
            staged = session.get(MemoryCandidate, staged_id)
            if staged is None or staged.status != "deferred":
                continue
            candidate = ExtractedMemory.model_validate(staged.extraction_payload)
            scoped = _candidate_context(session, staged, candidate, config)
            snapshot = snapshot_hash(scoped)
            targets, _ = _target_snapshot(session, scoped)
            staged.status = "resolving"
        try:
            decision = provider.generate_structured(
                resolution_prompt("candidate-1", _candidate_snapshot(candidate), targets),
                ResolutionDecision,
            )
        except Exception as exc:
            with session.begin():
                staged = session.get(MemoryCandidate, staged_id)
                if staged is not None:
                    current = _candidate_context(session, staged, candidate, config)
                    records.append(
                        _defer(
                            session,
                            staged=staged,
                            config=config,
                            snapshot=snapshot_hash(current),
                            scoped=current,
                            reason=f"resolver provider failure: {type(exc).__name__}",
                            resolver_model=resolver_model,
                        )
                    )
                    counts["deferred"] += 1
            continue
        with session.begin():
            staged = session.get(MemoryCandidate, staged_id)
            if staged is None:
                continue
            current = _candidate_context(session, staged, candidate, config)
            current_snapshot = snapshot_hash(current)
            if current_snapshot != snapshot:
                records.append(
                    _defer(
                        session,
                        staged=staged,
                        config=config,
                        snapshot=current_snapshot,
                        scoped=current,
                        reason="candidate snapshot changed before apply",
                        resolver_model=resolver_model,
                    )
                )
                counts["deferred"] += 1
                continue
            _, target_by_ref = _target_snapshot(session, current)
            rejection = validate_ai_decision(
                decision,
                candidate_ref="candidate-1",
                target_by_ref=target_by_ref,
                candidate_evidence=candidate.evidence,
                config=config,
            )
            if rejection:
                records.append(
                    _defer(
                        session,
                        staged=staged,
                        config=config,
                        snapshot=current_snapshot,
                        scoped=current,
                        reason=f"AI decision rejected: {rejection}",
                        resolver_model=resolver_model,
                        confidence=decision.confidence,
                    )
                )
                counts["deferred"] += 1
                continue
            selected = [target_by_ref[ref] for ref in decision.target_refs]
            before = {str(item.memory.id): item.memory.status for item in current}
            if decision.action is ResolutionAction.DEFER:
                records.append(
                    _defer(
                        session,
                        staged=staged,
                        config=config,
                        snapshot=current_snapshot,
                        scoped=current,
                        reason=decision.reason,
                        resolver_model=resolver_model,
                        confidence=decision.confidence,
                    )
                )
                counts["deferred"] += 1
                continue
            if decision.action is ResolutionAction.IGNORE:
                staged.status = "resolved"
                staged.attempt_count += 1
                _persist_decision(
                    session, staged=staged, config=config, snapshot=current_snapshot,
                    action=decision.action, targets=[], confidence=decision.confidence,
                    reason=decision.reason, evidence_quotes=decision.evidence_quotes,
                    validation_status="passed", before_state=before, after_state=before,
                    resolver_model=resolver_model,
                )
                counts["ignored"] += 1
                records.append({"candidate_id": str(staged.id), "action": "IGNORE"})
                continue
            if decision.action is ResolutionAction.MERGE_PROVENANCE:
                memory = selected[0]
                if (
                    memory.modality != candidate.modality.value
                    or memory.temporal_scope != candidate.temporal_scope.value
                ):
                    records.append(
                        _defer(
                            session,
                            staged=staged,
                            config=config,
                            snapshot=current_snapshot,
                            scoped=current,
                            reason="MERGE_PROVENANCE cannot erase modality or temporal-scope differences",
                            resolver_model=resolver_model,
                            confidence=decision.confidence,
                        )
                    )
                    counts["deferred"] += 1
                    continue
                memory.confidence = max(memory.confidence, candidate.confidence)
                ensure_memory_evidence(
                    session, memory,
                    evidence_by_message={
                        candidate.evidence_message_ids[0]: (
                            candidate.evidence or "",
                            candidate.evidence_start or 0,
                            candidate.evidence_end or len(candidate.evidence or ""),
                        )
                    },
                    extractor_model=resolver_model, schema_version="memory-extraction-v2",
                )
                staged.status = "resolved"
                staged.attempt_count += 1
                after = {str(memory.id): memory.status}
                _persist_decision(
                    session, staged=staged, config=config, snapshot=current_snapshot,
                    action=decision.action, targets=[memory], confidence=decision.confidence,
                    reason=decision.reason, evidence_quotes=decision.evidence_quotes,
                    validation_status="passed", before_state=before, after_state=after,
                    resolver_model=resolver_model,
                )
                counts["merged"] += 1
                records.append({"candidate_id": str(staged.id), "action": "MERGE_PROVENANCE"})
                continue
            if decision.action not in {ResolutionAction.CREATE, ResolutionAction.SUPERSEDE, ResolutionAction.CONTRADICT}:
                raise ValueError(f"unsupported resolution action {decision.action.value}")
            incoming = _create_memory(session, staged, candidate)
            if decision.action is ResolutionAction.CREATE:
                after = {str(incoming.id): incoming.status}
            else:
                try:
                    with session.begin_nested():
                        after = apply_resolution(session, staged.namespace_id, incoming, decision, selected)
                except ResolutionApplyError as exc:
                    session.delete(incoming)
                    session.flush()
                    records.append(
                        _defer(
                            session, staged=staged, config=config, snapshot=current_snapshot,
                            scoped=current, reason=f"atomic apply rejected: {exc}",
                            resolver_model=resolver_model, confidence=decision.confidence,
                        )
                    )
                    counts["deferred"] += 1
                    continue
            ensure_memory_evidence(
                session, incoming,
                evidence_by_message={
                    candidate.evidence_message_ids[0]: (
                        candidate.evidence or "",
                        candidate.evidence_start or 0,
                        candidate.evidence_end or len(candidate.evidence or ""),
                    )
                },
                extractor_model=resolver_model, schema_version="memory-extraction-v2",
            )
            for target in selected:
                if decision.action in {ResolutionAction.SUPERSEDE, ResolutionAction.CONTRADICT}:
                    upsert_memory_relation(
                        session,
                        staged.namespace_id,
                        incoming.id,
                        target.id,
                        "supersedes" if decision.action is ResolutionAction.SUPERSEDE else "contradicts",
                        1.0,
                        origin="ai_resolver",
                        evidence_refs=decision.evidence_quotes,
                        confidence=decision.confidence,
                    )
            staged.status = "resolved"
            staged.attempt_count += 1
            _persist_decision(
                session, staged=staged, config=config, snapshot=current_snapshot,
                action=decision.action, targets=selected, confidence=decision.confidence,
                reason=decision.reason, evidence_quotes=decision.evidence_quotes,
                validation_status="passed", before_state=before, after_state=after,
                resolver_model=resolver_model,
            )
            key = {ResolutionAction.CREATE: "created", ResolutionAction.SUPERSEDE: "superseded", ResolutionAction.CONTRADICT: "contradicted"}[decision.action]
            counts[key] += 1
            records.append({"candidate_id": str(staged.id), "action": decision.action.value})
    return counts, records


__all__ = ["resolve_staged_candidates"]
