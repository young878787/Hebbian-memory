"""External provider adapters and their stable contracts."""

from .base import ProviderError, ProviderProbe, StructuredModel, StructuredProvider
from .embedding import (
    EMBEDDING_DIMENSION,
    EmbeddingClient,
    EmbeddingError,
    EmbeddingProbe,
    normalize_embedding,
)
from .google import GoogleProvider

__all__ = [
    "EMBEDDING_DIMENSION",
    "EmbeddingClient",
    "EmbeddingError",
    "EmbeddingProbe",
    "GoogleProvider",
    "ProviderError",
    "ProviderProbe",
    "StructuredModel",
    "StructuredProvider",
    "normalize_embedding",
]
