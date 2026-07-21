import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from hela_mem_zh_mvp.providers.base import ProviderError
from hela_mem_zh_mvp.providers.google import GoogleProvider
from hela_mem_zh_mvp.settings import Settings


class _Result(BaseModel):
    value: int


class _FakeModels:
    def __init__(self, responses: list[object]):
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def generate_content(self, *, contents: str, **kwargs: object) -> SimpleNamespace:  # noqa: ARG002
        self.prompts.append(contents)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(parsed=None, text=response)


def _provider(responses: list[object]) -> tuple[GoogleProvider, _FakeModels]:
    models = _FakeModels(responses)
    provider = GoogleProvider.__new__(GoogleProvider)
    provider.settings = Settings(google_api_key="not-a-real-secret")
    provider.client = SimpleNamespace(models=models)
    return provider, models


def test_structured_generation_retries_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, models = _provider(['{"value": }', '{"value": 7}'])
    monkeypatch.setattr("hela_mem_zh_mvp.providers.google.time.sleep", lambda _: None)

    assert provider.generate_structured("請回傳 JSON", _Result) == _Result(value=7)
    assert len(models.prompts) == 2
    assert "前一次回覆未通過 JSON 驗證" in models.prompts[1]


def test_structured_generation_preserves_validation_error_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, models = _provider(['{"value": }', '{"value": }'])
    monkeypatch.setattr("hela_mem_zh_mvp.providers.google.time.sleep", lambda _: None)

    with pytest.raises(ProviderError, match="after 2 attempt\\(s\\): JSONDecodeError") as exc_info:
        provider.generate_structured("請回傳 JSON", _Result)

    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)
    assert len(models.prompts) == 2


def test_structured_generation_retries_google_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ServerError(Exception):
        code = 503

    provider, models = _provider([_ServerError("unavailable"), '{"value": 7}'])
    monkeypatch.setattr("hela_mem_zh_mvp.providers.google.time.sleep", lambda _: None)

    assert provider.generate_structured("請回傳 JSON", _Result) == _Result(value=7)
    assert len(models.prompts) == 2
