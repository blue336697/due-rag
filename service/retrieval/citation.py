"""Citation Builder — RAG Service 核心输出。

按 RAG高级检索能力开发指南 §9:
  - index 从 1 开始按最终 rerank 顺序生成
  - quote 只保留短摘录，用于定位证据
  - heading_path/chunk_id/parent_id/content_hash 完整
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from service.schemas.rag import Citation

_logger = logging.getLogger(__name__)


def build_citations(
    results: List[Dict[str, Any]],
    max_quote_chars: int = 160,
) -> List[Citation]:
    """从检索结果构建 Citation 列表。index 按顺序从 1 递增。"""
    citations: List[Citation] = []
    for i, r in enumerate(results):
        if not r.get("chunk_id") or not r.get("content_hash"):
            _logger.warning(
                "Citation trace fields missing: source=%s chunk_id_present=%s content_hash_present=%s",
                r.get("source", ""),
                bool(r.get("chunk_id")),
                bool(r.get("content_hash")),
            )
        quote = _extract_quote(r.get("content", ""), max_quote_chars)
        citations.append(Citation(
            index=i + 1,
            source=r.get("source", ""),
            heading=r.get("heading"),
            heading_path=r.get("heading_path", []),
            chunk_id=r.get("chunk_id", ""),
            parent_id=r.get("parent_id"),
            content_hash=r.get("content_hash", ""),
            quote=quote,
        ))
    return citations


def _extract_quote(content: str, max_chars: int) -> Optional[str]:
    """从 content 中提取短摘录。取前 max_chars 个字符。"""
    if not content:
        return None
    text = content.strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # 回退到词边界
    for i in range(len(truncated) - 1, max_chars // 2, -1):
        if truncated[i] in (" ", "。", "！", "？", "，", "\n"):
            return truncated[: i + 1].rstrip()
    return truncated + "…"
