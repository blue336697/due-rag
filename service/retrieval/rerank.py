"""CrossEncoder 精排模块。

按 RAG高级检索能力开发指南 §8.5:
  - 方案 A: 以命中 child 为候选单位，enriched_chunk_text 送入 reranker
  - 失败时显式抛错，不静默降级
  - allow_rerank_fallback 默认 false
  - 禁止用 "hybrid(rerank_failed)" 伪装成功结果
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

_logger = logging.getLogger(__name__)


class RerankerError(Exception):
    """CrossEncoder 精排失败 — 模型不可用或推理失败。"""
    pass


def rerank_candidates(
    candidates: List[Dict[str, Any]],
    query: str,
    reranker_model: Any,
    top_k: int,
    allow_rerank_fallback: bool = False,
    enriched_texts: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """CrossEncoder 精排入口。

    Raises:
        RerankerError: reranker 不可用且 allow_rerank_fallback=false
    """
    if not candidates:
        return []

    if reranker_model is None:
        if allow_rerank_fallback:
            _logger.warning("reranker 不可用，allow_rerank_fallback=true 降级跳过精排")
            for r in candidates[:top_k]:
                r.setdefault("score", r.get("rrf_score", 0.0))
                sb = r.setdefault("score_breakdown", {})
                sb["rerank_score"] = None
            return candidates[:top_k]
        raise RerankerError("CrossEncoder reranker 不可用，且 allow_rerank_fallback=false")

    texts: List[str] = []
    for i, c in enumerate(candidates):
        if enriched_texts and i < len(enriched_texts):
            texts.append(enriched_texts[i])
        else:
            texts.append(c.get("content", ""))

    try:
        reranked = reranker_model.rerank(query, texts, candidates, top_k)
        for r in reranked:
            sb = r.setdefault("score_breakdown", {})
            if "rerank_score" not in sb:
                sb["rerank_score"] = r.get("score", 0.0)
            r["retriever"] = "hybrid"
        return reranked
    except Exception as e:
        if allow_rerank_fallback:
            _logger.warning("reranker 推理失败，降级跳过: %s", e)
            for r in candidates[:top_k]:
                r.setdefault("score", r.get("rrf_score", 0.0))
                sb = r.setdefault("score_breakdown", {})
                sb["rerank_score"] = None
            return candidates[:top_k]
        raise RerankerError(f"CrossEncoder 精排失败: {e}") from e
