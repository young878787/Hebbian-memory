"""Fixed-path source message loading and pre-database extraction preparation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .schemas import SourceMessage

INPUT_PATH = Path("data/input/conversations.jsonl")


class IngestionError(ValueError):
    pass


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_input_messages(path: Path = INPUT_PATH) -> list[SourceMessage]:
    """Read and fully validate the fixed JSONL input before any database change."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise IngestionError(f"missing input file: {path}") from exc
    messages: list[SourceMessage] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            message = SourceMessage.model_validate_json(line)
        except Exception as exc:
            raise IngestionError(f"{path}:{line_number}: {exc}") from exc
        if message.message_id in seen:
            raise IngestionError(
                f"{path}:{line_number}: duplicate message_id {message.message_id!r}"
            )
        seen.add(message.message_id)
        messages.append(message)
    if not messages:
        raise IngestionError(f"{path}: requires at least one message")
    return messages
