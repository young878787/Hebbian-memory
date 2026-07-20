from hela_mem_zh_mvp.evaluation.fixtures import load_fixture_bundle


def test_fixture_bundle_matches_mvp_contract() -> None:
    bundle = load_fixture_bundle("data/fixtures")
    assert len(bundle.memories) == 40
    assert len(bundle.queries) == 32
    assert {
        "alias_normalization",
        "scoped_candidate",
        "ambiguous_resolver",
        "supersedes",
        "contradicts",
    } <= {query.category for query in bundle.queries}
    assert bundle.memories[0].external_id == "M1"
    assert any(edge.edge_type.value == "supersedes" for edge in bundle.edges)
