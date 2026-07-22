"""Build and atomically publish a disposable graph from canonical tables."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import Memory, MemoryEvidence, MemoryRelation

PROJECTION_VERSION = "memory-graph-v2"
RESULT_PATH = Path("results/graph/projection.json")


def rebuild_projection(
    session: Session, namespace_id: object, config_snapshot: dict[str, object]
) -> dict[str, object]:
    memories = session.scalars(
        select(Memory).where(Memory.namespace_id == namespace_id)
    ).all()
    evidence_memory_ids = set(
        session.scalars(
            select(MemoryEvidence.memory_id).where(MemoryEvidence.namespace_id == namespace_id)
        ).all()
    )
    included = [
        memory
        for memory in memories
        if memory.id in evidence_memory_ids and memory.status != "archived"
    ]
    included_ids = {memory.id for memory in included}
    relations = session.scalars(
        select(MemoryRelation).where(
            MemoryRelation.namespace_id == namespace_id,
            MemoryRelation.source_id.in_(included_ids),
            MemoryRelation.target_id.in_(included_ids),
            MemoryRelation.status == "active",
        )
    ).all()
    nodes = [
        {
            "node_key": str(memory.id),
            "node_type": memory.memory_type,
            "memory_ids": [str(memory.id)],
        }
        for memory in sorted(included, key=lambda item: str(item.id))
    ]
    edges = [
        {
            "source_key": str(relation.source_id),
            "target_key": str(relation.target_id),
            "relation_type": relation.relation_type,
            "memory_ids": [str(relation.source_id), str(relation.target_id)],
            "origin": relation.origin,
            "evidence_refs": relation.evidence_refs,
        }
        for relation in sorted(
            relations,
            key=lambda item: (str(item.source_id), str(item.target_id), item.relation_type),
        )
    ]
    source = {
        "nodes": nodes,
        "edges": edges,
        "config": config_snapshot,
    }
    snapshot = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    payload = {
        "projection_version": PROJECTION_VERSION,
        "projection_run_id": snapshot,
        "snapshot_hash": snapshot,
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "included_memories": len(included),
            "excluded_missing_evidence": sum(
                memory.id not in evidence_memory_ids for memory in memories
            ),
            "excluded_archived": sum(memory.status == "archived" for memory in memories),
            "nodes": len(nodes),
            "edges": len(edges),
        },
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_name(f".{RESULT_PATH.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(RESULT_PATH)
    return payload


__all__ = ["PROJECTION_VERSION", "RESULT_PATH", "rebuild_projection"]
