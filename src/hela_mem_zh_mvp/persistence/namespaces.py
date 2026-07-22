"""Namespace lookup, reset, and true teardown for the converged schema."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Entity,
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

FIXTURE_NAMESPACE = "fixture-pipeline-v1"


def get_namespace(session: Session, namespace_key: str, *, create: bool = True) -> MemoryNamespace:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == namespace_key)
    )
    if namespace is None:
        if not create:
            raise ValueError(f"namespace {namespace_key!r} does not exist")
        namespace = MemoryNamespace(namespace_key=namespace_key, display_name=namespace_key)
        session.add(namespace)
        session.flush()
    return namespace


def reset_namespace(session: Session, namespace_key: str) -> None:
    namespace = get_namespace(session, namespace_key, create=False)
    namespace_id = namespace.id
    candidate_ids = select(MemoryCandidate.id).where(MemoryCandidate.namespace_id == namespace_id)
    session.query(MemoryAssociation).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryRelation).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryEvidence).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryEntity).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(MemoryCandidate).filter(MemoryCandidate.id.in_(candidate_ids)).delete(synchronize_session=False)
    session.query(Memory).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(Entity).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(SourceMessage).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(namespace_id=namespace_id).delete(synchronize_session=False)


def reset_test_namespace(session: Session) -> None:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == FIXTURE_NAMESPACE)
    )
    if namespace is not None:
        reset_namespace(session, FIXTURE_NAMESPACE)


def purge_namespace(session: Session, namespace_key: str) -> bool:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == namespace_key)
    )
    if namespace is None:
        return False
    reset_namespace(session, namespace_key)
    session.delete(namespace)
    session.flush()
    return True


__all__ = [
    "FIXTURE_NAMESPACE",
    "get_namespace",
    "purge_namespace",
    "reset_namespace",
    "reset_test_namespace",
]
