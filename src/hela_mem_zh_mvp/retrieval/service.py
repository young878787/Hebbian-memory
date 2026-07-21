"""Deterministic exact-cosine and one-hop Hebbian retrieval."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..persistence.models import AssociationStat, Memory, MemoryEdge
from ..persistence.retrieval_runs import create_retrieval_items, create_retrieval_run
from ..providers.embedding import EmbeddingClient
from .activation import ActivationEdge, activate
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
        *,
        max_depth: int,
    ) -> None:
        if not seeds:
            return
        edge_rows = self.session.scalars(
            select(MemoryEdge).where(
                MemoryEdge.namespace_id == namespace_id,
                MemoryEdge.edge_type != "co_retrieval",
            )
        ).all()
        association_rows = self.session.scalars(
            select(AssociationStat).where(
                AssociationStat.namespace_id == namespace_id,
                AssociationStat.effective_weight > 0,
            )
        ).all()
        activation_edges = [
            ActivationEdge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=edge.edge_type,
                weight=edge.weight,
                provenance=str(edge.metadata_.get("origin", "memory_edge")),
            )
            for edge in edge_rows
        ]
        for stat in association_rows:
            activation_edges.extend(
                (
                    ActivationEdge(stat.source_memory_id, stat.target_memory_id, "association", stat.effective_weight, "association_stats"),
                    ActivationEdge(stat.target_memory_id, stat.source_memory_id, "association", stat.effective_weight, "association_stats"),
                )
            )
        target_ids = {edge.target_id for edge in activation_edges}
        targets = {
            memory.id: memory
            for memory in self.session.scalars(
                select(Memory).where(Memory.namespace_id == namespace_id, Memory.id.in_(target_ids))
            ).all()
        }
        result = activate(
            {seed.memory.id: seed.semantic_score for seed in seeds},
            activation_edges,
            max_depth=max_depth,
            max_neighbors_per_node=self.config.retrieval.max_neighbors_per_seed,
            path_budget=self.config.retrieval.path_budget,
            alpha=self.config.retrieval.activation_alpha,
        )
        for target_id, traces in result.paths.items():
            target = targets.get(target_id)
            if target is None:
                continue
            item = candidates.setdefault(target.id, RankedMemory(memory=target, semantic_score=0.0, source="hebbian"))
            item.hebbian_score += result.contributions.get(target_id, 0.0)
            for trace in traces:
                path = trace.get("activation_path", [])
                if path:
                    item.activation_path.append({
                        **path[-1],
                        "edge_type": path[-1]["edge_kind"],
                        "path_depth": trace["path_depth"],
                        "explored_nodes": result.explored_nodes,
                        "path_budget_violations": result.budget_violations,
                    })
                else:
                    item.activation_path.append(trace)

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
        *,
        activation_depth: int | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        semantic = self._semantic_candidates(namespace_id, self.embeddings.embed(query))
        candidates = {item.memory.id: item for item in semantic}
        seeds = semantic[: self.config.retrieval.seed_top_k]
        if mode is RetrievalMode.HEBBIAN:
            self._spread(
                namespace_id,
                seeds,
                candidates,
                max_depth=activation_depth or self.config.retrieval.spread_depth,
            )
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
