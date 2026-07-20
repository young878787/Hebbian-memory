"""Deterministic exact-cosine and one-hop Hebbian retrieval."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..persistence.models import Memory, MemoryEdge
from ..persistence.retrieval_runs import create_retrieval_items, create_retrieval_run
from ..providers.embedding import EmbeddingClient
from .contracts import QueryScope, RankedMemory, RetrievalMode, RetrievalResult, RunMode

CONTRADICTS_EDGE_TYPE = "contradicts"


def _tie_key(item: RankedMemory) -> tuple[float, float, float, str]:
    occurred = item.memory.occurred_at.timestamp() if item.memory.occurred_at else float("-inf")
    created = item.memory.created_at.timestamp() if item.memory.created_at else float("-inf")
    return (-item.final_score, -occurred, -created, item.external_id)


class Retriever:
    def __init__(self, session: Session, config: AppConfig, embeddings: EmbeddingClient):
        self.session = session
        self.config = config
        self.embeddings = embeddings

    def _semantic_candidates(
        self, namespace_id: uuid.UUID, query_embedding: list[float]
    ) -> list[RankedMemory]:
        distance = Memory.embedding.cosine_distance(query_embedding)
        rows = self.session.execute(
            select(Memory, (1 - distance).label("semantic_score"))
            .where(Memory.namespace_id == namespace_id)
            .order_by(distance)
            .limit(self.config.retrieval.candidate_limit)
        ).all()
        return [
            RankedMemory(
                memory=row.Memory, semantic_score=max(0.0, min(1.0, float(row.semantic_score)))
            )
            for row in rows
        ]

    def _spread(
        self,
        namespace_id: uuid.UUID,
        seeds: list[RankedMemory],
        candidates: dict[uuid.UUID, RankedMemory],
    ) -> None:
        if not seeds:
            return
        seed_ids = [seed.memory.id for seed in seeds]
        edge_rows = self.session.scalars(
            select(MemoryEdge).where(
                MemoryEdge.namespace_id == namespace_id, MemoryEdge.source_id.in_(seed_ids)
            )
        ).all()
        target_ids = {edge.target_id for edge in edge_rows}
        targets = {
            memory.id: memory
            for memory in self.session.scalars(
                select(Memory).where(Memory.namespace_id == namespace_id, Memory.id.in_(target_ids))
            ).all()
        }
        grouped: dict[uuid.UUID, list[MemoryEdge]] = {seed_id: [] for seed_id in seed_ids}
        for edge in edge_rows:
            grouped[edge.source_id].append(edge)
        for seed in seeds:
            ordered_edges = sorted(
                grouped[seed.memory.id],
                key=lambda edge: (-edge.weight, targets[edge.target_id].external_id),
            )[: self.config.retrieval.max_neighbors_per_seed]
            for edge in ordered_edges:
                target = targets[edge.target_id]
                item = candidates.setdefault(
                    target.id, RankedMemory(memory=target, semantic_score=0.0, source="hebbian")
                )
                if edge.edge_type == CONTRADICTS_EDGE_TYPE:
                    item.activation_path.append(
                        {
                            "source_external_id": seed.external_id,
                            "target_external_id": target.external_id,
                            "edge_type": edge.edge_type,
                            "edge_weight": edge.weight,
                            "source_semantic_score": seed.semantic_score,
                            "activation_alpha": self.config.retrieval.activation_alpha,
                            "contribution": 0.0,
                        }
                    )
                    continue
                contribution = (
                    seed.semantic_score * edge.weight * self.config.retrieval.activation_alpha
                )
                item.hebbian_score += contribution
                item.activation_path.append(
                    {
                        "source_external_id": seed.external_id,
                        "target_external_id": target.external_id,
                        "edge_type": edge.edge_type,
                        "edge_weight": edge.weight,
                        "source_semantic_score": seed.semantic_score,
                        "activation_alpha": self.config.retrieval.activation_alpha,
                        "contribution": contribution,
                    }
                )

    def _score(self, items: list[RankedMemory], scope: QueryScope, mode: RetrievalMode) -> None:
        adjustments = self.config.status_adjustments[scope.value]
        for item in items:
            item.status_adjustment = adjustments[item.memory.status]
            item.final_score = item.semantic_score + item.status_adjustment
            if mode is RetrievalMode.HEBBIAN:
                item.final_score += item.hebbian_score

    @staticmethod
    def _mark_selected(
        items: list[RankedMemory], seeds: list[RankedMemory], mode: RetrievalMode, final_top_k: int
    ) -> None:
        if mode is RetrievalMode.EMBEDDING_ONLY:
            chosen = sorted(items, key=_tie_key)[:final_top_k]
        else:
            seed_ids = {item.memory.id for item in seeds}
            contradictions = sorted(
                (
                    item
                    for item in items
                    if item.memory.id not in seed_ids
                    and any(
                        path["edge_type"] == CONTRADICTS_EDGE_TYPE for path in item.activation_path
                    )
                ),
                key=_tie_key,
            )
            bonus = sorted(
                (
                    item
                    for item in items
                    if item.memory.id not in seed_ids
                    and item.hebbian_score > 0
                    and item not in contradictions
                ),
                key=_tie_key,
            )
            # Contradictions are selected as traceable context, not because
            # they receive a positive association score.
            remaining = max(0, final_top_k - len(seeds))
            chosen = sorted([*seeds, *contradictions[:remaining]], key=_tie_key)
            chosen.extend(bonus[: max(0, final_top_k - len(chosen))])
            chosen = sorted(chosen, key=_tie_key)[:final_top_k]
        for rank, item in enumerate(chosen, start=1):
            item.selected = True
            item.final_rank = rank

    def retrieve(
        self,
        namespace_id: uuid.UUID,
        query: str,
        mode: RetrievalMode,
        scope: QueryScope,
        run_mode: RunMode = RunMode.EVALUATION,
    ) -> RetrievalResult:
        started = time.perf_counter()
        semantic = self._semantic_candidates(namespace_id, self.embeddings.embed(query))
        candidates = {item.memory.id: item for item in semantic}
        seeds = semantic[: self.config.retrieval.seed_top_k]
        if mode is RetrievalMode.HEBBIAN:
            self._spread(namespace_id, seeds, candidates)
        items = list(candidates.values())
        self._score(items, scope, mode)
        ordered_candidates = sorted(items, key=_tie_key)
        for rank, item in enumerate(ordered_candidates, start=1):
            item.candidate_rank = rank
        self._mark_selected(items, seeds, mode, self.config.retrieval.final_top_k)
        latency_ms = (time.perf_counter() - started) * 1000
        run = create_retrieval_run(
            self.session,
            namespace_id,
            query,
            mode,
            scope,
            run_mode,
            latency_ms,
            self.config.snapshot(),
        )
        create_retrieval_items(self.session, namespace_id, run.id, items)
        self.session.commit()
        return RetrievalResult(run.id, mode, scope, latency_ms, ordered_candidates)


__all__ = ["Retriever"]
