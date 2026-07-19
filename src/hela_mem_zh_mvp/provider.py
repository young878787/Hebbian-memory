"""Optional Google provider integration, isolated from retrieval scoring."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from .settings import Settings


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


def _google_response_schema(value: object) -> object:
    """Remove JSON Schema keywords that Gemini's response-schema endpoint rejects.

    Strictness remains enforced by the Pydantic model after the response is received.
    """
    if isinstance(value, list):
        return [_google_response_schema(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _google_response_schema(item)
            for key, item in value.items()
            if key != "additionalProperties"
        }
    return value


class GoogleProvider:
    def __init__(self, settings: Settings):
        if not settings.google_api_key or not settings.google_api_key.get_secret_value():
            raise ProviderError("GOOGLE_API_KEY is required")
        self.settings = settings
        self.client = genai.Client(api_key=settings.google_api_key.get_secret_value())

    def generate(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=self.settings.google_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=512,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=self.settings.google_thinking_level
                    ),
                    http_options=types.HttpOptions(timeout=30_000),
                ),
            )
            text = (response.text or "").strip()
            if not text:
                raise ProviderError("provider returned an empty response")
            return text
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"provider request failed: {type(exc).__name__}") from exc

    def generate_structured(
        self, prompt: str, response_model: type[StructuredModel]
    ) -> StructuredModel:
        """Generate strict JSON without exposing database actions to the provider."""
        try:
            response = self.client.models.generate_content(
                model=self.settings.google_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=_google_response_schema(response_model.model_json_schema()),
                    max_output_tokens=4096,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=self.settings.google_thinking_level
                    ),
                    http_options=types.HttpOptions(timeout=30_000),
                ),
            )
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, response_model):
                return parsed
            if parsed is not None:
                return response_model.model_validate(parsed)
            return response_model.model_validate(json.loads((response.text or "").strip()))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"structured provider response failed validation: {type(exc).__name__}: {exc}"
            ) from exc

    def smoke(self) -> ProviderProbe:
        started = time.perf_counter()
        text = self.generate("請只回覆：OK")
        return ProviderProbe(
            self.settings.google_model, (time.perf_counter() - started) * 1000, text
        )
