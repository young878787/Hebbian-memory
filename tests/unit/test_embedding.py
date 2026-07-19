import math

import pytest

from hela_mem_zh_mvp.embedding import EMBEDDING_DIMENSION, EmbeddingError, normalize_embedding


def test_normalize_embedding_returns_unit_vector() -> None:
    vector = normalize_embedding([1.0] * EMBEDDING_DIMENSION)
    assert len(vector) == EMBEDDING_DIMENSION
    assert math.isclose(sum(value * value for value in vector), 1.0)


def test_normalize_embedding_rejects_wrong_dimension() -> None:
    with pytest.raises(EmbeddingError, match="dimension"):
        normalize_embedding([1.0])
