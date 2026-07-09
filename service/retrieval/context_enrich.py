"""上下文增强 — 命中 child 后补 parent + 前后邻居。

按 RAG高级检索能力开发指南 §7.4:
  执行顺序: child 命中 → 条件拉取 parent → 拉取 prev/next neighbors → 去重
  - 评分单位永远是命中的 child chunk
  - 邻居必须同一 source + 同一 parent，不跨文档
  - 去重 key 为 chunk_id
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from service.schemas.rag import Chunk


def enrich_context(
    hit_chunks: List[Dict[str, Any]],
    chunks_by_id: Mapping[str, Chunk],
    small_to_big_enabled: bool = True,
    context_enrich_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """对命中 child chunk 补充 parent + neighbor 上下文。

    enriched text 写入 hit["_enriched_text"] 供 reranker 使用，
    enriched chunk_ids 写入 hit["_enriched_chunk_ids"] 供 citation 使用。

    不产生新的独立候选，评分单位仍为命中 child。
    """
    if not context_enrich_enabled or not hit_chunks:
        return hit_chunks

    seen_ids: set[str] = set()

    for hit in hit_chunks:
        chunk_id = hit.get("chunk_id", "")
        if not chunk_id:
            continue

        ids_to_fetch: List[str] = [chunk_id]

        if small_to_big_enabled:
            parent_id = hit.get("parent_id")
            if parent_id:
                ids_to_fetch.append(parent_id)

        chunk = chunks_by_id.get(chunk_id)
        if chunk:
            if chunk.prev_chunk_id:
                ids_to_fetch.append(chunk.prev_chunk_id)
            if chunk.next_chunk_id:
                ids_to_fetch.append(chunk.next_chunk_id)

        fetched: List[Chunk] = []
        for cid in ids_to_fetch:
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            c = chunks_by_id.get(cid)
            if c:
                fetched.append(c)

        if len(fetched) <= 1:
            continue

        # 组装: child 原文优先, parent 摘要其次, neighbors 最后
        parts: List[str] = []
        for c in fetched:
            if c.chunk_id == chunk_id:
                parts.append(c.content)
        for c in fetched:
            if c.chunk_type == "parent" and c.chunk_id != chunk_id:
                heading = " > ".join(c.heading_path) if c.heading_path else ""
                parts.append(f"[{heading}]\n{c.content[:600]}" if heading else c.content[:600])
        for c in fetched:
            if c.chunk_type == "child" and c.chunk_id != chunk_id:
                parts.append(c.content[:400])

        hit["_enriched_text"] = "\n---\n".join(parts)
        hit["_enriched_chunk_ids"] = [c.chunk_id for c in fetched]

    return hit_chunks
