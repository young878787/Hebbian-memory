"""Deterministic source-state policy; providers never choose lifecycle states."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import MemoryModality, MemoryStatus, MemoryType, TemporalScope

STATE_POLICY_VERSION = "source-state-v1"


@dataclass(frozen=True)
class InitialState:
    status: MemoryStatus
    reason: str
    lifecycle_action: str | None = None


def derive_initial_state(
    modality: MemoryModality, temporal_scope: TemporalScope, memory_type: MemoryType
) -> InitialState:
    """Map source semantics to a conservative initial retrieval lifecycle."""
    if modality is MemoryModality.UNCERTAIN:
        return InitialState(MemoryStatus.UNCERTAIN, "source_explicit_uncertainty")
    if modality is MemoryModality.CONSIDERED and temporal_scope is TemporalScope.HISTORICAL:
        return InitialState(
            MemoryStatus.ARCHIVED,
            "source_explicit_past_consideration",
            "archive",
        )
    if modality is MemoryModality.QUESTION:
        return InitialState(MemoryStatus.ACTIVE, "source_current_research_question")
    # A reliable historical event remains a fact; history is not archival by itself.
    return InitialState(MemoryStatus.ACTIVE, "source_asserted_statement")


__all__ = ["InitialState", "STATE_POLICY_VERSION", "derive_initial_state"]
