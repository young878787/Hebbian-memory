"""Pure, bounded multi-hop activation with complete per-path provenance."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ActivationEdge:
    source_id: UUID
    target_id: UUID
    edge_type: str
    weight: float
    provenance: str


@dataclass(frozen=True)
class ActivationResult:
    contributions: dict[UUID, float]
    paths: dict[UUID, list[dict[str, object]]]
    explored_nodes: int
    budget_violations: int


def activate(
    seeds: dict[UUID, float],
    edges: list[ActivationEdge],
    *,
    max_depth: int,
    max_neighbors_per_node: int,
    path_budget: int,
    alpha: float,
) -> ActivationResult:
    """Expand at most two hops by default, never revisiting a path node.

    Contradictions are included as zero-bonus context and never become a
    positive transit route.  Exhausting the path budget is explicit in traces.
    """
    if not 1 <= max_depth <= 3:
        raise ValueError("max_depth must be between 1 and 3")
    adjacency: dict[UUID, list[ActivationEdge]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source_id, []).append(edge)
    for options in adjacency.values():
        options.sort(key=lambda edge: (-edge.weight, str(edge.target_id), edge.edge_type))
    contributions: dict[UUID, float] = {}
    paths: dict[UUID, list[dict[str, object]]] = {}
    frontier = [(seed_id, score, (seed_id,), []) for seed_id, score in seeds.items()]
    explored = 0
    violations = 0
    for depth in range(1, max_depth + 1):
        next_frontier = []
        for current_id, current_score, visited, prior_path in frontier:
            for edge in adjacency.get(current_id, [])[:max_neighbors_per_node]:
                if explored >= path_budget:
                    violations += 1
                    continue
                explored += 1
                if edge.target_id in visited:
                    paths.setdefault(edge.target_id, []).append({"depth": depth, "edge_kind": edge.edge_type, "blocked_reason": "cycle_prevented", "provenance": edge.provenance})
                    continue
                contribution = 0.0 if edge.edge_type == "contradicts" else current_score * edge.weight * alpha
                step = {"depth": depth, "edge_kind": edge.edge_type, "edge_weight": edge.weight, "provenance": edge.provenance, "contribution": contribution}
                full_path = [*prior_path, step]
                paths.setdefault(edge.target_id, []).append({"activation_path": full_path, "path_depth": depth})
                if contribution:
                    contributions[edge.target_id] = contributions.get(edge.target_id, 0.0) + contribution
                    if depth < max_depth:
                        next_frontier.append((edge.target_id, contribution, (*visited, edge.target_id), full_path))
        frontier = next_frontier
    return ActivationResult(contributions, paths, explored, violations)


__all__ = ["ActivationEdge", "ActivationResult", "activate"]
