"""邻域链接器 — 为 child chunk 设置 prev/next_chunk_id。

按 RAG高级检索能力开发指南 §7.4:
  - 邻居必须属于同一 source 和同一 parent
  - 不跨文档拼接
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from service.schemas.rag import Chunk


def link_neighbors(child_chunks: List[Chunk]) -> List[Chunk]:
    """为同一 parent 内的 child chunk 按 ordinal 排序后设置 prev/next 链接。

    原地修改 Chunk 对象，返回同一个 list。
    """
    by_parent: Dict[str, List[Chunk]] = defaultdict(list)
    for c in child_chunks:
        pid = c.parent_id or ""
        by_parent[pid].append(c)

    for children in by_parent.values():
        children.sort(key=lambda c: c.ordinal)
        for i, chunk in enumerate(children):
            chunk.prev_chunk_id = children[i - 1].chunk_id if i > 0 else None
            chunk.next_chunk_id = children[i + 1].chunk_id if i < len(children) - 1 else None

    return child_chunks
