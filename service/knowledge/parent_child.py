"""父子块构建器 — parent chunk 按 Markdown 章节，child chunk 由语义分块产生。

按 RAG高级检索能力开发指南 §7.3:
  - parent chunk 通常对应 Markdown 一个自然章节
  - child chunk 由 parent chunk 语义切分产生（semantic_chunker）
  - Qdrant 存 child chunk 向量，BM25 索引 child chunk（含 parent heading_path）
  - BM25 child 索引文本必须强制拼入 parent heading_path 和 parent title
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

from service.knowledge.markdown_parser import ParsedMarkdown, extract_heading_path
from service.knowledge.metadata import extract_metadata
from service.knowledge.semantic_chunker import semantic_chunk
from service.schemas.rag import Chunk
from service.core.hashing import compute_chunk_id, compute_content_hash

_logger = logging.getLogger(__name__)


def _find_section_ranges(
    content: str,
    heading_events: List[Tuple[int, int, str]],
) -> List[Tuple[int, int]]:
    """找到父块边界：以 ## 及以上标题作为章节分割点。"""
    section_breaks = [(pos, level) for pos, level, _title in heading_events if level >= 2]

    if not section_breaks:
        return [(0, len(content))]

    ranges: List[Tuple[int, int]] = []
    for i, (pos, _) in enumerate(section_breaks):
        end = section_breaks[i + 1][0] if i + 1 < len(section_breaks) else len(content)
        ranges.append((pos, end))

    if section_breaks[0][0] > 0:
        pre_text = content[: section_breaks[0][0]].strip()
        if pre_text:
            ranges.insert(0, (0, section_breaks[0][0]))

    return ranges


def build_parent_child_chunks(
    relative_path: str,
    parsed: ParsedMarkdown,
    token_counter: Callable[[str], int],
    embedding_model: Any,
    parent_max_tokens: int = 1200,
    child_max_tokens: int = 350,
    child_min_tokens: int = 80,
    overlap_tokens: int = 40,
    similarity_threshold: float = 0.62,
    chunk_version: str = "v3",
) -> Tuple[List[Chunk], List[Chunk]]:
    """从 ParsedMarkdown 构建父子块。

    Returns:
        (parent_chunks, child_chunks) — child.parent_id 指向 parent.chunk_id
    """
    content = parsed.content
    if not content.strip():
        return [], []

    section_ranges = _find_section_ranges(content, parsed.heading_events)
    parent_chunks: List[Chunk] = []
    child_chunks: List[Chunk] = []

    for section_start, section_end in section_ranges:
        section_text = content[section_start:section_end]
        if not section_text.strip():
            continue

        parent_heading_path = extract_heading_path(parsed.heading_events, section_start)
        parent_meta = extract_metadata(relative_path, parsed, section_start)
        parent_content = section_text.strip()
        parent_hash = compute_content_hash(parent_content)
        parent_id = compute_chunk_id(
            source=relative_path,
            heading_path=parent_heading_path,
            ordinal=0,
            content_hash=parent_hash,
        )

        parent = Chunk(
            chunk_id=parent_id,
            source=relative_path,
            heading_path=parent_heading_path,
            title=parent_meta.title,
            content=parent_content,
            content_hash=parent_hash,
            chunk_type="parent",
            ordinal=0,
            metadata={
                "category": parent_meta.category,
                "statement_type": parent_meta.statement_type,
                "entities": parent_meta.entities,
                "frontmatter": parent_meta.frontmatter,
                "chunk_version": chunk_version,
            },
        )
        parent_chunks.append(parent)

        # 父块 token 数不够 child_max → 整个作为 child
        if token_counter(parent_content) <= child_max_tokens:
            child = _make_child(parent, parent_content, ordinal=1, chunk_version=chunk_version)
            child_chunks.append(child)
            continue

        # 语义分块
        child_texts = semantic_chunk(
            text=parent_content,
            token_counter=token_counter,
            embedding_model=embedding_model,
            similarity_threshold=similarity_threshold,
            min_tokens=child_min_tokens,
            max_tokens=child_max_tokens,
            overlap_tokens=overlap_tokens,
            protected_ranges=parsed.table_ranges + parsed.code_block_ranges,
        )

        for i, child_text in enumerate(child_texts, start=1):
            if not child_text.strip():
                continue
            child = _make_child(parent, child_text.strip(), ordinal=i, chunk_version=chunk_version)
            child_chunks.append(child)

    _logger.info(
        "父子块构建完成: %s → %d parents, %d children",
        relative_path, len(parent_chunks), len(child_chunks),
    )
    return parent_chunks, child_chunks


def _make_child(parent: Chunk, content: str, ordinal: int, chunk_version: str) -> Chunk:
    """创建 child chunk，继承 parent 的 metadata 和 heading_path。"""
    child_hash = compute_content_hash(content)
    child_id = compute_chunk_id(
        source=parent.source,
        heading_path=parent.heading_path,
        ordinal=ordinal,
        content_hash=child_hash,
    )

    # embedding_text 格式按 §7.1: 标题路径 + 来源 + 正文
    heading_line = f"标题路径: {' > '.join(parent.heading_path)}" if parent.heading_path else ""
    source_line = f"来源: {parent.source}"
    parts = [p for p in [heading_line, source_line, "正文:", content] if p]
    embedding_text = "\n".join(parts)

    return Chunk(
        chunk_id=child_id,
        parent_id=parent.chunk_id,
        source=parent.source,
        heading_path=parent.heading_path,
        title=parent.title,
        content=content,
        embedding_text=embedding_text,
        content_hash=child_hash,
        chunk_type="child",
        ordinal=ordinal,
        metadata={**parent.metadata, "chunk_version": chunk_version},
    )
