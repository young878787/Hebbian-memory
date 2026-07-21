import pytest

from hela_mem_zh_mvp.evaluation.contracts import FixtureQuery
from hela_mem_zh_mvp.evaluation.fixtures import (
    FixtureError,
    load_fixture_bundle,
    query_reference_answer,
    query_reference_conversations,
    validate_query_answer_oracles,
)
from hela_mem_zh_mvp.ingestion.input import load_input_messages


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


def test_judge_oracle_is_directly_mapped_to_input_conversations() -> None:
    bundle = load_fixture_bundle("data/fixtures")
    query = next(item for item in bundle.queries if item.query_id == "Q_GPU_ASSOC_02")
    references_by_query = {
        item.query_id: query_reference_conversations(item, load_input_messages())
        for item in bundle.queries
    }

    references = references_by_query[query.query_id]

    assert query.expect_answerable is True
    assert all(references_by_query.values())
    validate_query_answer_oracles(bundle.queries, load_input_messages())
    assert query_reference_answer(query, load_input_messages())["source_message_ids"] == [
        "msg-001",
        "msg-003",
        "msg-002",
    ]
    assert [item["message_id"] for item in references] == ["msg-001", "msg-003", "msg-002"]
    assert any("CMP 170HX" in item["content"] for item in references)
    assert any("沒有買 RTX 3090" in item["content"] for item in references)


def test_judge_oracle_rejects_unknown_input_message_id() -> None:
    query = FixtureQuery.model_validate(
        {
            "query_id": "unknown-source",
            "query": "問題",
            "scope": "general",
            "expect_answerable": False,
            "category": "test",
            "reference_message_ids": ["missing"],
        }
    )
    with pytest.raises(FixtureError, match="unknown input message IDs"):
        query_reference_conversations(query, load_input_messages())


def test_fixture_memory_provenance_matches_the_correct_input_messages() -> None:
    bundle = load_fixture_bundle("data/fixtures")
    by_external_id = {memory.external_id: memory for memory in bundle.memories}

    assert {
        external_id: by_external_id[external_id].source_message_ids
        for external_id in (
            "M1",
            "M2",
            "M3",
            "M4",
            "M5",
            "M6",
            "M7",
            "M8",
            "M9",
            "M10",
            "M11",
            "M12",
            "M13",
            "M14",
            "M15",
        )
    } == {
        "M1": ["msg-001"],
        "M2": ["msg-001"],
        "M3": ["msg-002"],
        "M4": ["msg-003"],
        "M5": ["msg-004"],
        "M6": ["msg-002"],
        "M7": ["msg-004"],
        "M8": ["msg-003"],
        "M9": ["msg-005"],
        "M10": ["msg-005"],
        "M11": ["msg-006"],
        "M12": ["msg-006"],
        "M13": ["msg-006"],
        "M14": ["msg-007"],
        "M15": ["msg-007"],
    }
