"""关键词召回模块 — 从 hybrid_retriever.py 拆分。

调用方: retrieval/service.py (hybrid 检索流程)
"""
from __future__ import annotations

from typing import Any, Dict, List

from service.storage.bm25 import keyword_recall


def retrieve_keyword(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """纯关键词检索入口。

    Returns:
        [{"source": str, "heading": str, "content": str, "score": float, "retriever": "keyword"}, ...]
    """
    results = keyword_recall(query, chunks, top_k)
    for r in results:
        r.setdefault("chunk_id", "")
        r.setdefault("heading_path", [r.get("heading", "")] if r.get("heading") else [])
        r.setdefault("parent_id", None)
        r.setdefault("content_hash", "")
        r["score"] = r.get("keyword_score", 0.0)
        r["retriever"] = "keyword"
    return results
