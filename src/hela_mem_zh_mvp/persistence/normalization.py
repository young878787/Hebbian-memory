"""Pure, versioned normalization for persisted lookup keys."""

from __future__ import annotations

import hashlib
import re
import unicodedata

NORMALIZER_VERSION = "normalizer-zh-v1"
TOPIC_VERSION = "topic-taxonomy-v1"

_SEPARATORS = re.compile(r"[\s\-_/,.，。、:：;；!?！？()（）\[\]【】]+")
_TOPIC_OVERRIDES = {
    "rtx3090": "rtx_3090",
    "cmp170hx": "cmp_170hx",
    "postgresql": "postgresql",
    "pgvector": "pgvector",
    "rag": "rag",
}


def normalize_lookup(value: str) -> str:
    """Produce a conservative lookup key; it deliberately does not transliterate."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = _SEPARATORS.sub("", normalized)
    if not normalized:
        raise ValueError("normalized value cannot be empty")
    return normalized


def topic_key(topic_raw: str | None) -> str | None:
    if topic_raw is None or not topic_raw.strip():
        return None
    key = normalize_lookup(topic_raw)
    return _TOPIC_OVERRIDES.get(
        key, re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", key).strip("_") or key
    )


def state_key(
    entity_id: object | None,
    memory_type: str,
    normalized_topic: str | None,
    attribute_key: str | None,
) -> str | None:
    """Only explicit state slots receive uniqueness/state-transition semantics."""
    if (
        entity_id is None
        or normalized_topic is None
        or not attribute_key
        or attribute_key == "unknown"
    ):
        return None
    raw = f"{entity_id}:{memory_type}:{normalized_topic}:{normalize_lookup(attribute_key)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]
