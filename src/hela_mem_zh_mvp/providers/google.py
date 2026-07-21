"""Google Gemini structured-output provider adapter."""

from __future__ import annotations

import json
import time

from google import genai
from google.genai import types
from pydantic import ValidationError

from ..settings import Settings
from .base import ProviderError, ProviderProbe, StructuredModel


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
    _STRUCTURED_MAX_ATTEMPTS = 2

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
        for attempt in range(1, self._STRUCTURED_MAX_ATTEMPTS + 1):
            attempt_prompt = prompt
            if attempt > 1:
                attempt_prompt += (
                    "\n前一次回覆未通過 JSON 驗證。請重新產生完整且合法的 JSON，"
                    "只能輸出符合 schema 的 JSON，不要 Markdown 或額外文字。"
                )
            try:
                return self._generate_structured_once(attempt_prompt, response_model)
            except Exception as exc:
                if attempt == self._STRUCTURED_MAX_ATTEMPTS or not self._should_retry_structured(exc):
                    raise ProviderError(
                        "structured provider response failed after "
                        f"{attempt} attempt(s): {type(exc).__name__}: {exc}"
                    ) from exc
                time.sleep(min(2 ** (attempt - 1), 10))

        raise AssertionError("structured generation retry loop exhausted unexpectedly")

    def _generate_structured_once(
        self, prompt: str, response_model: type[StructuredModel]
    ) -> StructuredModel:
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
        except (json.JSONDecodeError, ValidationError):
            raise

    @staticmethod
    def _should_retry_structured(exc: Exception) -> bool:
        if isinstance(exc, (json.JSONDecodeError, ValidationError, TimeoutError)):
            return True
        status_code = getattr(exc, "status_code", getattr(exc, "code", None))
        if status_code is None:
            return False
        if status_code == 429 and "quota" not in str(exc).lower():
            return True
        return isinstance(status_code, int) and 500 <= status_code < 600

    def smoke(self) -> ProviderProbe:
        started = time.perf_counter()
        text = self.generate("請只回覆：OK")
        return ProviderProbe(
            self.settings.google_model, (time.perf_counter() - started) * 1000, text
        )
