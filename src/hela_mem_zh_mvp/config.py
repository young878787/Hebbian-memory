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
    spread_depth: int = Field(ge=1, le=1)
    max_neighbors_per_seed: int = Field(ge=1)
    tie_score_tolerance: float = Field(gt=0)

    @model_validator(mode="after")
    def check_budget(self) -> RetrievalConfig:
        if self.seed_top_k > self.final_top_k:
            raise ValueError("seed_top_k cannot exceed final_top_k")
        return self


class LearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    co_retrieval_increment: float = Field(gt=0, le=1)
    max_edge_weight: float = Field(gt=0, le=1)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    retrieval: RetrievalConfig
    learning: LearningConfig
    status_adjustments: dict[str, dict[str, float]]

    def snapshot(self) -> dict[str, object]:
        return json.loads(self.model_dump_json())


@lru_cache
def load_config(path: str = "config.yaml") -> AppConfig:
    with Path(path).open(encoding="utf-8") as handle:
        return AppConfig.model_validate(yaml.safe_load(handle))
