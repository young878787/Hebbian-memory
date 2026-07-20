"""Stable contracts shared by structured-output providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderProbe:
    model: str
    latency_ms: float
    text: str


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class StructuredProvider(Protocol):
    def generate_structured(
        self, prompt: str, response_model: type[StructuredModel]
    ) -> StructuredModel: ...
