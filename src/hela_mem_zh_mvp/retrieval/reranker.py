"""Bounded structured reranking with deterministic fallback semantics."""

from __future__ import annotations

import json

from ..config import RetrievalConfig
from ..providers.base import StructuredProvider
from .contracts import RankedMemory, RerankResult


def apply_model_rerank(
    provider: StructuredProvider,
    query: str,
    items: list[RankedMemory],
    config: RetrievalConfig,
) -> bool:
    """Apply validated candidate-only scores; return False on provider fallback."""
    candidates = sorted(
        items,
        key=lambda item: -(item.semantic_score + item.lexical_score + item.rerank_score),
    )[: config.model_rerank_candidate_limit]
    payload = [
        {
            "external_id": item.external_id,
            "content": item.memory.content,
            "status": item.memory.status,
            "modality": item.memory.modality,
            "temporal_scope": item.memory.temporal_scope,
        }
        for item in candidates
    ]
    prompt = (
        "依 query 對 candidate memories 做語意精排，只回傳 memory-rerank-v1 JSON。"
        "ranked_external_ids 最多回傳五個，必須完全取自 candidates；"
        "複合問題應涵蓋每個明確子題，current 問題不得讓 archived/superseded 主導，"
        "question/uncertain 只能保留其不確定語義。"
        f"\nquery={query}\ncandidates={json.dumps(payload, ensure_ascii=False)}"
    )
    allowed = {item.external_id: item for item in candidates}
    result = None
    for _attempt in range(2):
        try:
            result = provider.generate_structured(prompt, RerankResult)
        except Exception:
            continue
        if result.ranked_external_ids and set(result.ranked_external_ids) <= set(allowed):
            break
        prompt += (
            "\n前次 ranked_external_ids 含空值或非 candidate ID。"
            "請只使用 candidates 中逐字相同的 external_id。"
        )
    if result is None or not result.ranked_external_ids or not set(result.ranked_external_ids) <= set(allowed):
        return False
    total = len(result.ranked_external_ids)
    for rank, external_id in enumerate(result.ranked_external_ids):
        allowed[external_id].model_rerank_score = config.model_rerank_bonus * (
            1.0 - rank / max(1, total)
        )
    return True


__all__ = ["apply_model_rerank"]
