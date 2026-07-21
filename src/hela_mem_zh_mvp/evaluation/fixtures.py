"""Strict fixture validation and additive loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ingestion.contracts import SourceMessage
from ..persistence.edges import edge_weight, upsert_edge
from ..persistence.models import Memory, MemoryNamespace
from ..providers.embedding import EmbeddingClient
from .contracts import FixtureEdge, FixtureMemory, FixtureQuery, LiveExtractionExpectation


class FixtureError(ValueError):
    pass


@dataclass(frozen=True)
class FixtureBundle:
    memories: list[FixtureMemory]
    edges: list[FixtureEdge]
    queries: list[FixtureQuery]
    aliases: dict[str, list[str]]


def query_reference_conversations(
    query: FixtureQuery, source_messages: list[SourceMessage]
) -> list[dict[str, str]]:
    """Materialize one query's answer oracle from immutable primary input."""
    if not query.reference_message_ids:
        return []
    by_id = {message.message_id: message for message in source_messages}
    unknown = set(query.reference_message_ids) - set(by_id)
    if unknown:
        raise FixtureError(
            f"query {query.query_id} references unknown input message IDs: {sorted(unknown)}"
        )
    return [
        {
            "message_id": message.message_id,
            "session_id": message.session_id,
            "role": message.role,
            "content": message.content,
            "occurred_at": message.occurred_at.isoformat(),
        }
        for message_id in query.reference_message_ids
        for message in [by_id[message_id]]
    ]


def query_reference_answer(
    query: FixtureQuery, source_messages: list[SourceMessage]
) -> dict[str, object]:
    """Build the judge's canonical answer facts from the primary source."""
    conversations = query_reference_conversations(query, source_messages)
    return {
        "answerable": query.expect_answerable,
        "source_message_ids": list(query.reference_message_ids),
        "facts": conversations,
    }


def validate_query_answer_oracles(
    queries: list[FixtureQuery], source_messages: list[SourceMessage]
) -> None:
    """Fail closed when any evaluation question lacks a primary-source oracle."""
    for query in queries:
        reference = query_reference_answer(query, source_messages)
        if not reference["facts"]:
            raise FixtureError(
                f"query {query.query_id} has no primary-source reference answer"
            )


def _load_jsonl(
    path: Path,
    model: type[FixtureMemory]
    | type[FixtureEdge]
    | type[FixtureQuery]
    | type[LiveExtractionExpectation],
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


def load_live_extraction_expectations(
    input_path: str | Path, expectation_path: str | Path
) -> list[LiveExtractionExpectation]:
    """Load a source-centred live oracle without conflating it with M1..Mn fixtures."""
    from ..ingestion.input import load_input_messages

    expectations = _load_jsonl(Path(expectation_path), LiveExtractionExpectation)
    source_ids = {message.message_id for message in load_input_messages(Path(input_path))}
    expected_ids = [item.source_message_id for item in expectations]
    if len(expected_ids) != len(set(expected_ids)):
        raise FixtureError("live extraction expectation source_message_ids must be unique")
    if set(expected_ids) != source_ids:
        raise FixtureError("live extraction expectations must cover exactly the input messages")
    return expectations


def load_key_extraction_expectations(path: str | Path) -> list[LiveExtractionExpectation]:
    """Load a deliberately partial canary oracle without weakening full coverage loaders."""
    return _load_jsonl(Path(path), LiveExtractionExpectation)


def load_fixture_bundle(directory: str | Path, *, include_edges: bool = False) -> FixtureBundle:
    """Load the fixture corpus without requiring legacy edge projections.

    ``memory_edges.jsonl`` is retained as an optional research/reference
    projection.  The default evaluation and seed path is memory-only so that
    Hebbian retrieval does not depend on hand-authored fixture edges.
    """
    path = Path(directory)
    try:
        memories = _load_jsonl(path / "character_memories.jsonl", FixtureMemory)
        edges = _load_jsonl(path / "memory_edges.jsonl", FixtureEdge) if include_edges else []
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
    seed_edges: bool = False,
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
    if seed_edges:
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
