"""Validated, immutable algorithm configuration."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_limit: int = Field(ge=3)
    seed_top_k: int = Field(ge=1)
    final_top_k: int = Field(ge=1)
    semantic_top_k: int = Field(ge=1)
    similarity_threshold: float = Field(ge=0, le=1)
    activation_alpha: float = Field(ge=0)
    spread_depth: int = Field(ge=1, le=3)
    max_neighbors_per_seed: int = Field(ge=1)
    path_budget: int = Field(default=40, ge=1)
    lexical_candidate_limit: int = Field(default=5, ge=1)
    lexical_seed_limit: int = Field(default=1, ge=0)
    lexical_bonus: float = Field(default=0.15, gt=0, le=1)
    content_rerank_bonus: float = Field(default=0.50, ge=0, le=1)
    content_rerank_min_overlap: float = Field(default=0.05, ge=0, le=1)
    model_rerank_enabled: bool = True
    model_rerank_candidate_limit: int = Field(default=40, ge=1)
    model_rerank_bonus: float = Field(default=1.0, gt=0, le=2)
    min_lexical_term_length: int = Field(default=2, ge=1)
    min_activation_contribution: float = Field(default=0.01, ge=0, le=1)
    time_decay_memory_types: tuple[str, ...] = ("event", "decision")
    time_decay_half_life_days: float = Field(default=90, gt=0)
    time_decay_min_factor: float = Field(default=0.5, gt=0, le=1)
    tie_score_tolerance: float = Field(gt=0)

    @model_validator(mode="after")
    def check_budget(self) -> RetrievalConfig:
        if self.seed_top_k > self.final_top_k:
            raise ValueError("seed_top_k cannot exceed final_top_k")
        if self.lexical_seed_limit > self.seed_top_k:
            raise ValueError("lexical_seed_limit cannot exceed seed_top_k")
        allowed_types = {"character_fact", "event", "preference", "decision"}
        if not set(self.time_decay_memory_types) <= allowed_types:
            raise ValueError("time_decay_memory_types contains an unknown memory type")
        return self


class LifecycleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relevance_threshold: float = Field(default=0.08, ge=0, le=1)
    half_life_days: float = Field(default=30, gt=0)
    recently_cited_days: int = Field(default=30, ge=0)
    min_idle_days: int = Field(default=30, ge=0)
    max_edge_weight: float = Field(default=0.2, ge=0, le=1)
    max_activation_count: int = Field(default=2, ge=0)


class LearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    co_retrieval_increment: float = Field(gt=0, le=1)
    max_edge_weight: float = Field(gt=0, le=1)


class ResolutionConfig(BaseModel):
    """Versioned, fail-closed thresholds for memory state resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_limit: int = Field(ge=1)
    ambiguous_similarity_min: float = Field(ge=0, le=1)
    exact_merge_similarity: float = Field(ge=0, le=1)
    deterministic_confidence_min: float = Field(ge=0, le=1)
    ai_decision_confidence_min: float = Field(ge=0, le=1)
    supersede_confidence_min: float = Field(ge=0, le=1)
    event_time_tolerance_seconds: int = Field(ge=0)
    max_ai_attempts: int = Field(ge=1, le=3)
    resolver_schema_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validates_similarity_band(self) -> ResolutionConfig:
        if self.ambiguous_similarity_min >= self.exact_merge_similarity:
            raise ValueError("ambiguous_similarity_min must be below exact_merge_similarity")
        return self


class EvaluationConfig(BaseModel):
    """Live-provider pacing used only by the fixed evaluation workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    answer_request_interval_seconds: float = Field(ge=0, le=10)
    structured_request_interval_seconds: float = Field(ge=0, le=10)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    retrieval: RetrievalConfig
    learning: LearningConfig
    lifecycle: LifecycleConfig
    resolution: ResolutionConfig
    evaluation: EvaluationConfig
    status_adjustments: dict[str, dict[str, float]]

    def snapshot(self) -> dict[str, object]:
        return json.loads(self.model_dump_json())


@lru_cache
def load_config(path: str = "config.yaml") -> AppConfig:
    with Path(path).open(encoding="utf-8") as handle:
        return AppConfig.model_validate(yaml.safe_load(handle))
