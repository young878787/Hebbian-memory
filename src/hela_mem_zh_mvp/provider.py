"""Optional Google provider integration, isolated from retrieval scoring."""

from __future__ import annotations

import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from .settings import Settings


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderProbe:
    model: str
    latency_ms: float
    text: str


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
                    thinking_config=types.ThinkingConfig(thinking_level=self.settings.google_thinking_level),
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

    def smoke(self) -> ProviderProbe:
        started = time.perf_counter()
        text = self.generate("請只回覆：OK")
        return ProviderProbe(self.settings.google_model, (time.perf_counter() - started) * 1000, text)
