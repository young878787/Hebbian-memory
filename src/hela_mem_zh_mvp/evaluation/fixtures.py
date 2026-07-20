"""Strict fixture validation and additive loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.edges import edge_weight, upsert_edge
from ..persistence.models import Memory, MemoryNamespace
from ..providers.embedding import EmbeddingClient
from .contracts import FixtureEdge, FixtureMemory, FixtureQuery


class FixtureError(ValueError):
    pass


@dataclass(frozen=True)
class FixtureBundle:
    memories: list[FixtureMemory]
    edges: list[FixtureEdge]
    queries: list[FixtureQuery]
    aliases: dict[str, list[str]]


def _load_jsonl(
    path: Path, model: type[FixtureMemory] | type[FixtureEdge] | type[FixtureQuery]
) -> list:
    values: list = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            values.append(model.model_validate_json(line))
        except Exception as exc:
            raise FixtureError(f"{path.name}:{number}: {exc}") from exc
    return values


def load_fixture_bundle(directory: str | Path) -> FixtureBundle:
    path = Path(directory)
    try:
        memories = _load_jsonl(path / "character_memories.jsonl", FixtureMemory)
        edges = _load_jsonl(path / "memory_edges.jsonl", FixtureEdge)
        queries = _load_jsonl(path / "test_queries.jsonl", FixtureQuery)
        aliases = json.loads((path / "aliases.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FixtureError(f"missing fixture file: {exc.filename}") from exc
    memory_ids = [item.external_id for item in memories]
    query_ids = [item.query_id for item in queries]
    if len(memory_ids) != len(set(memory_ids)) or len(query_ids) != len(set(query_ids)):
        raise FixtureError("memory external_id and query_id must be unique")
    known = set(memory_ids)
    for edge in edges:
        if edge.source_external_id not in known or edge.target_external_id not in known:
            raise FixtureError("edge endpoint does not exist in character_memories.jsonl")
    for query in queries:
        if not set(query.must_include + query.nice_to_have + query.must_not_primary) <= known:
            raise FixtureError(f"query {query.query_id} has an unknown oracle ID")
    if not isinstance(aliases, dict):
        raise FixtureError("aliases.json must be an object")
    return FixtureBundle(memories, edges, queries, aliases)


def seed_fixtures(
    session: Session,
    bundle: FixtureBundle,
    client: EmbeddingClient,
    *,
    namespace_key: str = "legacy-mvp-v0.4",
    replace_fixtures: bool = False,
) -> int:
    namespace = session.scalar(
        select(MemoryNamespace).where(MemoryNamespace.namespace_key == namespace_key)
    )
    if namespace is None:
        namespace = MemoryNamespace(namespace_key=namespace_key, display_name=namespace_key)
        session.add(namespace)
        session.flush()
    existing = {
        memory.external_id: memory
        for memory in session.scalars(
            select(Memory).where(Memory.namespace_id == namespace.id)
        ).all()
    }
    collisions = set(existing) & {item.external_id for item in bundle.memories}
    if collisions and not replace_fixtures:
        raise FixtureError(
            "fixture external_id already exists; pass --replace-fixtures to update fixture records"
        )
    records: dict[str, Memory] = {}
    for item in bundle.memories:
        record = existing.get(item.external_id)
        if record is None:
            record = Memory(
                namespace_id=namespace.id,
                external_id=item.external_id,
                canonical_key=item.external_id,
                extraction_schema_version="fixture-v0.4",
                embedding=client.embed(item.content),
            )
            session.add(record)
        else:
            record.embedding = client.embed(item.content)
        record.content = item.content
        record.memory_type = item.memory_type.value
        record.topic = item.topic
        record.occurred_at = item.occurred_at
        record.status = item.status.value
        record.importance = item.importance
        record.confidence = item.confidence
        record.source_session_id = item.source_session_id
        record.source_message_ids = item.source_message_ids
        record.metadata_ = {**item.metadata, "fixture_owned": True}
        records[item.external_id] = record
    session.flush()
    for edge in bundle.edges:
        upsert_edge(
            session,
            namespace.id,
            records[edge.source_external_id].id,
            records[edge.target_external_id].id,
            edge.edge_type,
            edge_weight(edge.edge_type, edge.weight),
            edge.metadata,
        )
    return len(records)
