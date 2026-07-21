"""Namespace lifecycle primitives; callers own transaction boundaries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AssociationEvent,
    AssociationStat,
    ClaimEvidence,
    Entity,
    GraphProjectionEdge,
    GraphProjectionNode,
    GraphProjectionRun,
    IngestionRun,
    LifecycleDecision,
    Memory,
    MemoryCandidate,
    MemoryClaim,
    MemoryEdge,
    MemoryEntity,
    MemoryNamespace,
    MemoryResolutionDecision,
    MessageExtractionOutcome,
    RelationEvidence,
    RetrievalItem,
    RetrievalRun,
    SourceMessage,
)

FIXTURE_NAMESPACE = "fixture-pipeline-v1"


class NamespaceError(ValueError):
    pass


def get_namespace(session: Session, namespace_key: str, *, create: bool = True) -> MemoryNamespace:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == namespace_key)
    )
    if namespace is None:
        if not create:
            raise NamespaceError(f"unknown namespace {namespace_key!r}")
        namespace = MemoryNamespace(namespace_key=namespace_key, display_name=namespace_key)
        session.add(namespace)
        session.flush()
    return namespace


def reset_namespace(session: Session, namespace_key: str) -> None:
    """Clear an explicit disposable namespace, including all derived projections."""
    namespace = get_namespace(session, namespace_key, create=False)
    namespace_id = namespace.id
    run_ids = select(IngestionRun.id).where(IngestionRun.namespace_id == namespace_id)
    retrieval_ids = select(RetrievalRun.id).where(RetrievalRun.namespace_id == namespace_id)
    projection_ids = select(GraphProjectionRun.id).where(GraphProjectionRun.namespace_id == namespace_id)
    claim_ids = select(MemoryClaim.id).where(MemoryClaim.namespace_id == namespace_id)
    candidate_ids = select(MemoryCandidate.id).where(MemoryCandidate.namespace_id == namespace_id)
    session.query(RetrievalItem).filter(RetrievalItem.run_id.in_(retrieval_ids)).delete(synchronize_session=False)
    session.query(AssociationEvent).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(AssociationStat).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(RetrievalRun).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(GraphProjectionEdge).filter(GraphProjectionEdge.projection_run_id.in_(projection_ids)).delete(synchronize_session=False)
    session.query(GraphProjectionNode).filter(GraphProjectionNode.projection_run_id.in_(projection_ids)).delete(synchronize_session=False)
    session.query(GraphProjectionRun).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(LifecycleDecision).filter(LifecycleDecision.claim_id.in_(claim_ids)).delete(synchronize_session=False)
    session.query(RelationEvidence).filter((RelationEvidence.source_claim_id.in_(claim_ids)) | (RelationEvidence.target_claim_id.in_(claim_ids))).delete(synchronize_session=False)
    session.query(ClaimEvidence).filter(ClaimEvidence.claim_id.in_(claim_ids)).delete(synchronize_session=False)
    session.query(MemoryClaim).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryEdge).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryResolutionDecision).filter(MemoryResolutionDecision.candidate_id.in_(candidate_ids)).delete(synchronize_session=False)
    session.query(MemoryCandidate).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MessageExtractionOutcome).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(MemoryEntity).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(Memory).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(Entity).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(SourceMessage).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(IngestionRun).filter(IngestionRun.id.in_(run_ids)).delete(synchronize_session=False)


def reset_test_namespace(session: Session) -> None:
    reset_namespace(session, FIXTURE_NAMESPACE)


def purge_namespace(session: Session, namespace_key: str) -> bool:
    try:
        namespace = get_namespace(session, namespace_key, create=False)
    except NamespaceError:
        return False
    reset_namespace(session, namespace_key)
    session.delete(namespace)
    session.flush()
    return True
