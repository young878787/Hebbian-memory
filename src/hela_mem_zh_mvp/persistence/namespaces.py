"""Namespace lifecycle primitives; callers own transaction boundaries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Entity,
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
    namespace = get_namespace(session, namespace_key, create=False)
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
    candidate_ids = select(MemoryCandidate.id).where(MemoryCandidate.namespace_id == namespace_id)
    session.query(MemoryResolutionDecision).filter(
        MemoryResolutionDecision.candidate_id.in_(candidate_ids)
    ).delete(synchronize_session=False)
    session.query(MemoryCandidate).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(MemoryEntity).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(Memory).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(Entity).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(SourceMessage).filter_by(namespace_id=namespace_id).delete(
        synchronize_session=False
    )
    session.query(IngestionRun).filter(IngestionRun.id.in_(run_ids)).delete(
        synchronize_session=False
    )


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
