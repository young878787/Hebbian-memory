"""OpenAI-compatible local vLLM embedding client."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from openai import OpenAI

from .settings import Settings

EMBEDDING_DIMENSION = 2560


class EmbeddingError(RuntimeError):
    pass


def normalize_embedding(values: list[float]) -> list[float]:
    if len(values) != EMBEDDING_DIMENSION:
        raise EmbeddingError(
            f"embedding dimension must be {EMBEDDING_DIMENSION}, got {len(values)}"
        )
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingError("embedding contains non-finite values")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise EmbeddingError("embedding norm cannot be zero")
    return [value / norm for value in values]


@dataclass(frozen=True)
class EmbeddingProbe:
    served_model_id: str
    dimension: int
    latency_ms: float


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value() or "local",
            timeout=30.0,
            max_retries=1,
        )
        self._served_model_id: str | None = None

    def served_model_id(self) -> str:
        if self._served_model_id is None:
            models = self.client.models.list().data
            matching = next(
                (item.id for item in models if item.id == self.settings.embedding_model), None
            )
            if not matching:
                available = ", ".join(sorted(item.id for item in models))
                raise EmbeddingError(
                    f"EMBEDDING_MODEL {self.settings.embedding_model!r} was not served; available: {available}"
                )
            self._served_model_id = matching
        return self._served_model_id

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingError("embedding text cannot be empty")
        try:
            response = self.client.embeddings.create(model=self.served_model_id(), input=text)
            if not response.data:
                raise EmbeddingError("embedding response was empty")
            return normalize_embedding(list(response.data[0].embedding))
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"embedding request failed: {type(exc).__name__}") from exc

    def smoke(self) -> EmbeddingProbe:
        started = time.perf_counter()
        vector = self.embed("這是一段中文 embedding smoke test。")
        return EmbeddingProbe(
            served_model_id=self.served_model_id(),
            dimension=len(vector),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
