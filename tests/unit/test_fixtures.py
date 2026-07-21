import pytest

from hela_mem_zh_mvp.evaluation.contracts import FixtureQuery
from hela_mem_zh_mvp.evaluation.fixtures import (
    load_fixture_bundle,
    load_live_extraction_expectations,
)


def test_fixture_bundle_matches_mvp_contract() -> None:
    bundle = load_fixture_bundle("data/fixtures")
    assert len(bundle.memories) == 60
    assert len(bundle.queries) == 60
    assert {
        "alias_normalization",
        "scoped_candidate",
        "ambiguous_resolver",
        "supersedes",
        "contradicts",
    } <= {query.category for query in bundle.queries}
    assert bundle.memories[0].external_id == "M1"
    assert bundle.edges == []
    reference_bundle = load_fixture_bundle("data/fixtures", include_edges=True)
    assert any(edge.edge_type.value == "supersedes" for edge in reference_bundle.edges)


def test_fixture_suite_covers_architecture_targets_and_complexity() -> None:
    bundle = load_fixture_bundle("data/fixtures")
    architecture = [query for query in bundle.queries if query.suite == "architecture_v1"]
    assert len(architecture) == 28
    assert all(query.architecture_targets for query in architecture)
    targets = {target for query in architecture for target in query.architecture_targets}
    assert {
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
    } <= targets
    counts = {
        level: sum(query.complexity == level for query in bundle.queries)
        for level in ("basic", "intermediate", "advanced", "adversarial")
    }
    assert all(counts.values())
    assert counts["advanced"] + counts["adversarial"] >= 20
    assert sum(query.required_hops >= 2 for query in architecture) >= 5
    assert sum(memory.topic == "lifestyle" for memory in bundle.memories) == 20
    assert sum(query.query_id.startswith("Q_LIFESTYLE_") for query in architecture) == 28


def test_fixture_query_requires_explicit_architecture_labels() -> None:
    base = {
        "query_id": "case-1",
        "query": "問題",
        "scope": "general",
        "expect_answerable": True,
        "category": "test",
    }
    with pytest.raises(ValueError, match="architecture_v1 queries require"):
        FixtureQuery.model_validate({**base, "suite": "architecture_v1"})
    with pytest.raises(ValueError, match="multi-hop queries must target"):
        FixtureQuery.model_validate(
            {
                **base,
                "suite": "architecture_v1",
                "architecture_targets": ["graph_projection"],
                "required_hops": 2,
            }
        )


def test_live_extraction_oracle_is_source_centred_and_complete() -> None:
    expectations = load_live_extraction_expectations(
        "data/fixtures/preference_resolution_scenarios.jsonl",
        "data/fixtures/preference_resolution_expectations.jsonl",
    )
    assert len(expectations) == 10
    assert expectations[-1].expected_outcome == "NO_MEMORY"
    assert all(
        claim.required_evidence
        for expectation in expectations
        for claim in expectation.expected_claims
    )
