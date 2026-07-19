from hela_mem_zh_mvp.embedding import EmbeddingClient
from hela_mem_zh_mvp.settings import Settings


def test_local_vllm_uses_documented_api_key_fallback_when_env_value_is_blank() -> None:
    settings = Settings(embedding_api_key="")
    client = EmbeddingClient(settings)
    assert client.client.api_key == "local"
