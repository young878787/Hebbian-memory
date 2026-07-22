"""Pydantic contracts owned by evaluation workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..ingestion.contracts import (
    EdgeType,
    MemoryStatus,
    MemoryType,
)
from ..retrieval.contracts import QueryScope


class AIJudgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    verdict: Literal["PASS", "FAIL", "UNSURE"]
    correctness: int = Field(ge=0, le=2)
    groundedness: int = Field(ge=0, le=2)
    association_completeness: int = Field(ge=0, le=2)
    contradiction_correctness: int = Field(ge=0, le=2)
    no_answer_safety: int | None = Field(default=None, ge=0, le=2)
    unsupported_claims: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)


class AIJudgeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["answer-judge-summary-v1"]
    cases: list[AIJudgeCase]
    summary: dict[str, Any]


class FixtureMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    memory_type: MemoryType
    topic: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_session_id: str | None = Field(default=None, max_length=100)
    source_message_ids: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FixtureEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_external_id: str = Field(min_length=1, max_length=64)
    target_external_id: str = Field(min_length=1, max_length=64)
    edge_type: EdgeType
    weight: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_self_loop(self) -> FixtureEdge:
        if self.source_external_id == self.target_external_id:
            raise ValueError("memory edges cannot have self loops")
        return self


class FixtureQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    scope: QueryScope
    must_include: list[str] = Field(default_factory=list)
    # Final top-k oracle. Every group represents one required semantic facet;
    # selecting any one memory in each group satisfies that facet. When absent,
    # must_include remains an exact singleton-per-ID contract.
    selected_evidence_groups: list[list[str]] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    must_not_primary: list[str] = Field(default_factory=list)
    # Primary-source oracle used by the answer judge. Fixture memory IDs only
    # describe retrieval expectations and are not a correctness reference.
    reference_message_ids: list[str] = Field(default_factory=list)
    expect_answerable: bool
    category: str = Field(min_length=1)
    suite: Literal["baseline", "architecture_v1"] = "baseline"
    complexity: Literal["basic", "intermediate", "advanced", "adversarial"] = "basic"
    architecture_targets: list[
        Literal[
            "direct_recall",
            "alias_resolution",
            "association_growth",
            "adaptive_forgetting",
            "graph_projection",
            "multi_hop_activation",
            "temporal_reasoning",
            "state_resolution",
            "contradiction_safety",
            "provenance",
            "noise_resistance",
            "consolidation",
            "abstention",
        ]
    ] = Field(default_factory=list)
    required_hops: int = Field(default=0, ge=0, le=3)

    @field_validator(
        "must_include", "nice_to_have", "must_not_primary", "reference_message_ids"
    )
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("oracle IDs must be unique")
        return value

    @field_validator("selected_evidence_groups")
    @classmethod
    def valid_selected_evidence_groups(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not group for group in value):
            raise ValueError("selected evidence groups cannot be empty")
        if any(len(group) != len(set(group)) for group in value):
            raise ValueError("IDs within a selected evidence group must be unique")
        return value

    @field_validator("architecture_targets")
    @classmethod
    def unique_architecture_targets(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("architecture_targets must be unique")
        return value

    @model_validator(mode="after")
    def architecture_suite_has_explicit_targets(self) -> FixtureQuery:
        if self.suite == "architecture_v1" and not self.architecture_targets:
            raise ValueError("architecture_v1 queries require architecture_targets")
        if self.required_hops >= 2 and "multi_hop_activation" not in self.architecture_targets:
            raise ValueError("multi-hop queries must target multi_hop_activation")
        if self.selected_evidence_groups and not self.expect_answerable:
            raise ValueError("unanswerable queries cannot require selected evidence groups")
        return self

    def selected_evidence_matches(self, selected: list[str]) -> bool:
        """Require every semantic facet while allowing declared equivalent evidence."""
        groups = self.selected_evidence_groups or [[item] for item in self.must_include]
        selected_ids = set(selected)
        return all(selected_ids.intersection(group) for group in groups)
