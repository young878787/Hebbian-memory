from hela_mem_zh_mvp.config import load_config


def test_canonical_config_is_valid() -> None:
    config = load_config()
    assert config.retrieval.seed_top_k == 3
    assert config.retrieval.final_top_k == 5
    assert config.status_adjustments["current"]["superseded"] == -0.35
