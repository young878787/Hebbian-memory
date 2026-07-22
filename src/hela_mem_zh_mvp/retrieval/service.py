"""Retrieval orchestration across candidates, activation, ranking, and selection."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..persistence.models import Memory, MemoryAssociation, MemoryRelation
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient
from .activation import ActivationEdge, activate
from .candidates import (
    apply_content_rerank,
    fuse_candidates,
    lexical_candidates,
    select_seeds,
    semantic_candidates,
)
from .contracts import QueryScope, RankedMemory, RetrievalMode, RetrievalResult, RunMode
from .ranking import apply_time_decay, mark_selected, score, tie_key
from .reranker import apply_model_rerank


class Retriever:
    def __init__(
        self,
        session: Session,
        config: AppConfig,
        embeddings: EmbeddingClient,
        reranker: StructuredProvider | None = None,
    ):
        self.session = session
        self.config = config
        self.embeddings = embeddings
        self.reranker = reranker

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
        relation_rows = self.session.scalars(
            select(MemoryRelation).where(
                MemoryRelation.namespace_id == namespace_id,
                MemoryRelation.status == "active",
            )
        ).all()
        association_rows = self.session.scalars(
            select(MemoryAssociation).where(
                MemoryAssociation.namespace_id == namespace_id,
                MemoryAssociation.effective_weight > 0,
            )
        ).all()
        activation_edges = []
        for relation in relation_rows:
            activation_edges.append(
                ActivationEdge(
                    source_id=relation.source_id,
                    target_id=relation.target_id,
                    edge_type=relation.relation_type,
                    weight=relation.weight,
                    provenance=relation.origin,
                )
            )
            if relation.relation_type in {"semantic", "contradicts", "temporal"}:
                activation_edges.append(
                    ActivationEdge(
                        source_id=relation.target_id,
                        target_id=relation.source_id,
                        edge_type=relation.relation_type,
                        weight=relation.weight,
                        provenance=relation.origin,
                    )
                )
        for association in association_rows:
            activation_edges.extend(
                (
                    ActivationEdge(
                        association.source_memory_id,
                        association.target_memory_id,
                        "association",
                        association.effective_weight,
                        "memory_associations",
                    ),
                    ActivationEdge(
                        association.target_memory_id,
                        association.source_memory_id,
                        "association",
                        association.effective_weight,
                        "memory_associations",
                    ),
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
            {
                seed.memory.id: (seed.semantic_score + seed.lexical_score)
                * seed.time_decay_factor
                for seed in seeds
            },
            activation_edges,
            max_depth=max_depth,
            max_neighbors_per_node=self.config.retrieval.max_neighbors_per_seed,
            path_budget=self.config.retrieval.path_budget,
            alpha=self.config.retrieval.activation_alpha,
            min_contribution=self.config.retrieval.min_activation_contribution,
        )
        for target_id, traces in result.paths.items():
            target = targets.get(target_id)
            if target is None:
                continue
            item = candidates.setdefault(
                target.id, RankedMemory(memory=target, semantic_score=0.0, source="hebbian")
            )
            item.hebbian_score += result.contributions.get(target_id, 0.0)
            for trace in traces:
                path = trace.get("activation_path", [])
                if not path:
                    item.activation_path.append(trace)
                    continue
                flattened_trace = {
                    **path[-1],
                    "edge_type": path[-1]["edge_kind"],
                    "path_depth": trace["path_depth"],
                    "explored_nodes": result.explored_nodes,
                    "path_budget_violations": result.budget_violations,
                }
                if blocked_reason := trace.get("blocked_reason"):
                    flattened_trace["blocked_reason"] = blocked_reason
                item.activation_path.append(flattened_trace)

    def retrieve(
        self,
        namespace_id: uuid.UUID,
        query: str,
        mode: RetrievalMode,
        scope: QueryScope,
        run_mode: RunMode = RunMode.EVALUATION,
        *,
        activation_depth: int | None = None,
        now: datetime | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        retrieval_config = self.config.retrieval
        semantic = semantic_candidates(
            self.session, retrieval_config, namespace_id, self.embeddings.embed(query)
        )
        lexical = lexical_candidates(self.session, retrieval_config, namespace_id, query)
        apply_content_rerank(semantic, retrieval_config, query)
        apply_content_rerank(lexical, retrieval_config, query)
        candidates = fuse_candidates(semantic, lexical)
        if retrieval_config.model_rerank_enabled and self.reranker is not None:
            apply_model_rerank(
                self.reranker,
                query,
                list(candidates.values()),
                retrieval_config,
            )
        seeds = select_seeds(retrieval_config, semantic, lexical)
        retrieval_now = now or datetime.now(UTC)
        apply_time_decay(seeds, retrieval_config, now=retrieval_now)
        if mode is RetrievalMode.HEBBIAN:
            self._spread(
                namespace_id,
                seeds,
                candidates,
                max_depth=activation_depth or retrieval_config.spread_depth,
            )
        items = list(candidates.values())
        apply_time_decay(items, retrieval_config, now=retrieval_now)
        score(items, self.config, scope, mode)
        ordered_candidates = sorted(items, key=tie_key)
        for rank, item in enumerate(ordered_candidates, start=1):
            item.candidate_rank = rank
        mark_selected(items, seeds, mode, retrieval_config.final_top_k, scope)
        latency_ms = (time.perf_counter() - started) * 1000
        return RetrievalResult(uuid.uuid4(), mode, scope, latency_ms, ordered_candidates)


__all__ = ["Retriever"]
