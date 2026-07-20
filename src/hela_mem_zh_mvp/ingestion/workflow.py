"""Ingestion use case; provider calls stay outside the caller-owned transaction."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ..config import load_config
from ..providers.base import StructuredProvider
from ..providers.embedding import EmbeddingClient
from .extractor import extract_messages
from .input import INPUT_PATH, load_input_messages
from .service import write_ingestion


def ingest(
    session: Session,
    namespace_key: str,
    provider: StructuredProvider,
    embeddings: EmbeddingClient,
    *,
    input_path: Path = INPUT_PATH,
    extractor_model: str,
) -> dict[str, int]:
    messages = load_input_messages(input_path)
    extraction = extract_messages(provider, messages)
    with session.begin():
        return write_ingestion(
            session,
            namespace_key,
            messages,
            extraction,
            embeddings,
            extractor_model=extractor_model,
            resolution=load_config().resolution,
        )
