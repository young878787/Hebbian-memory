"""Deterministic exact-cosine and one-hop Hebbian retrieval."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppConfig
from .edges import reinforce_co_retrieval
from .embedding import EmbeddingClient
from .models import Memory, MemoryEdge, RetrievalItem, RetrievalRun
from .schemas import EdgeType, QueryScope, RetrievalMode, RunMode


@dataclass
class RankedMemory:
    memory: Memory
    semantic_score: float
    candidate_rank: int = 0
    hebbian_score: float = 0.0
    status_adjustment: float = 0.0
    final_score: float = 0.0
    source: str = "seed"
    activation_path: list[dict[str, Any]] = field(default_factory=list)
    selected: bool = False
    final_rank: int | None = None

    @property
    def external_id(self) -> str:
        return self.memory.external_id


@dataclass(frozen=True)
class RetrievalResult:
    run_id: uuid.UUID
    mode: RetrievalMode
    scope: QueryScope
    latency_ms: float
    items: list[RankedMemory]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id), "mode": self.mode.value, "scope": self.scope.value,
            "latency_ms": self.latency_ms,
            "items": [
                {
                    "external_id": item.external_id, "content": item.memory.content,
                    "status": item.memory.status, "semantic_score": item.semantic_score,
                    "hebbian_score": item.hebbian_score, "status_adjustment": item.status_adjustment,
                    "final_score": item.final_score, "source": item.source, "selected": item.selected,
                    "final_rank": item.final_rank, "activation_path": item.activation_path,
                }
                for item in self.items
            ],
        }


def _tie_key(item: RankedMemory) -> tuple[float, float, float, str]:
    occurred = item.memory.occurred_at.timestamp() if item.memory.occurred_at else float("-inf")
    created = item.memory.created_at.timestamp() if item.memory.created_at else float("-inf")
    return (-item.final_score, -occurred, -created, item.external_id)


class Retriever:
    def __init__(self, session: Session, config: AppConfig, embeddings: EmbeddingClient):
        self.session = session
        self.config = config
        self.embeddings = embeddings

    def _semantic_candidates(self, query_embedding: list[float]) -> list[RankedMemory]:
        distance = Memory.embedding.cosine_distance(query_embedding)
        rows = self.session.execute(
            select(Memory, (1 - distance).label("semantic_score"))
            .order_by(distance)
            .limit(self.config.retrieval.candidate_limit)
        ).all()
        return [RankedMemory(memory=row.Memory, semantic_score=max(0.0, min(1.0, float(row.semantic_score)))) for row in rows]

    def _spread(self, seeds: list[RankedMemory], candidates: dict[uuid.UUID, RankedMemory]) -> None:
        if not seeds:
            return
        seed_ids = [seed.memory.id for seed in seeds]
        edge_rows = self.session.scalars(
            select(MemoryEdge).where(MemoryEdge.source_id.in_(seed_ids))
        ).all()
        target_ids = {edge.target_id for edge in edge_rows}
        targets = {
            memory.id: memory
            for memory in self.session.scalars(select(Memory).where(Memory.id.in_(target_ids))).all()
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
                item = candidates.setdefault(target.id, RankedMemory(memory=target, semantic_score=0.0, source="hebbian"))
                if edge.edge_type == EdgeType.CONTRADICTS.value:
                    item.activation_path.append({
                        "source_external_id": seed.external_id, "target_external_id": target.external_id,
                        "edge_type": edge.edge_type, "edge_weight": edge.weight,
                        "source_semantic_score": seed.semantic_score, "activation_alpha": self.config.retrieval.activation_alpha,
                        "contribution": 0.0,
                    })
                    continue
                contribution = seed.semantic_score * edge.weight * self.config.retrieval.activation_alpha
                item.hebbian_score += contribution
                item.activation_path.append({
                    "source_external_id": seed.external_id, "target_external_id": target.external_id,
                    "edge_type": edge.edge_type, "edge_weight": edge.weight,
                    "source_semantic_score": seed.semantic_score, "activation_alpha": self.config.retrieval.activation_alpha,
                    "contribution": contribution,
                })

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
            bonus = sorted(
                (item for item in items if item.memory.id not in seed_ids and item.hebbian_score > 0),
                key=_tie_key,
            )[: max(0, final_top_k - len(seeds))]
            chosen = sorted([*seeds, *bonus], key=_tie_key)[:final_top_k]
        for rank, item in enumerate(chosen, start=1):
            item.selected = True
            item.final_rank = rank

    def retrieve(
        self, query: str, mode: RetrievalMode, scope: QueryScope, run_mode: RunMode = RunMode.EVALUATION
    ) -> RetrievalResult:
        started = time.perf_counter()
        semantic = self._semantic_candidates(self.embeddings.embed(query))
        candidates = {item.memory.id: item for item in semantic}
        seeds = semantic[: self.config.retrieval.seed_top_k]
        if mode is RetrievalMode.HEBBIAN:
            self._spread(seeds, candidates)
        items = list(candidates.values())
        self._score(items, scope, mode)
        ordered_candidates = sorted(items, key=_tie_key)
        for rank, item in enumerate(ordered_candidates, start=1):
            item.candidate_rank = rank
        self._mark_selected(items, seeds, mode, self.config.retrieval.final_top_k)
        latency_ms = (time.perf_counter() - started) * 1000
        run = RetrievalRun(
            query=query, retrieval_mode=mode.value, run_mode=run_mode.value, query_scope=scope.value,
            total_latency_ms=latency_ms, metadata_={"config": self.config.snapshot()},
        )
        self.session.add(run)
        self.session.flush()
        for item in items:
            self.session.add(RetrievalItem(
                run_id=run.id, memory_id=item.memory.id, candidate_rank=item.candidate_rank,
                final_rank=item.final_rank, selected=item.selected, semantic_score=item.semantic_score,
                hebbian_score=item.hebbian_score, status_adjustment=item.status_adjustment,
                final_score=item.final_score, retrieval_source=item.source,
                activation_path=item.activation_path,
            ))
        self.session.commit()
        if run_mode is RunMode.LEARNING:
            reinforce_co_retrieval(
                self.session, [item.memory.id for item in items if item.selected], self.config.learning
            )
            self.session.commit()
        return RetrievalResult(run.id, mode, scope, latency_ms, ordered_candidates)
