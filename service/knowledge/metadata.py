"""知识库 chunk metadata 提取 — 从 ParsedMarkdown 生成结构化元数据。

按 RAG高级检索能力开发指南 §5 调用链:
  markdown_parser → metadata → parent_child / semantic_chunker / neighbor

metadata.py 不读文件，只接收 relative_path + ParsedMarkdown。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from service.knowledge.markdown_parser import ParsedMarkdown, extract_heading_path


@dataclass
class ChunkMetadata:
    """知识库 chunk 的结构化元数据。"""
    source: str
    heading_path: List[str] = field(default_factory=list)
    title: Optional[str] = None
    category: Optional[str] = None
    statement_type: Optional[str] = None
    entities: List[str] = field(default_factory=list)
    frontmatter: Dict[str, Any] = field(default_factory=dict)


def extract_metadata(
    relative_path: str,
    parsed: ParsedMarkdown,
    position: int,
) -> ChunkMetadata:
    """从文件路径和 Markdown 解析结果中提取结构化 metadata。

    Args:
        relative_path: 知识库内的相对路径，如 "字段定义/amount.md"
        parsed: markdown_parser 解析结果
        position: chunk 在 parsed.content 中的起始位置
    """
    heading_path = extract_heading_path(parsed.heading_events, position)

    # title: 优先 frontmatter.title → heading_path[-1] → 文件名 stem
    title: Optional[str] = None
    fm_title = parsed.frontmatter.get("title")
    if fm_title and isinstance(fm_title, str):
        title = fm_title
    elif heading_path:
        title = heading_path[-1]
    else:
        title = Path(relative_path).stem

    # category: 从 frontmatter 或路径推断
    category: Optional[str] = None
    fm_category = parsed.frontmatter.get("category")
    if fm_category and isinstance(fm_category, str):
        category = fm_category
    else:
        parts = Path(relative_path).parts
        if len(parts) > 1:
            category = parts[0]

    # statement_type: 从 frontmatter
    statement_type: Optional[str] = None
    fm_st = parsed.frontmatter.get("statement_type")
    if fm_st and isinstance(fm_st, str):
        statement_type = fm_st

    # entities: 从 frontmatter
    entities: List[str] = []
    fm_entities = parsed.frontmatter.get("entities")
    if isinstance(fm_entities, list):
        entities = [str(e) for e in fm_entities]
    elif isinstance(fm_entities, str):
        entities = [fm_entities]

    return ChunkMetadata(
        source=relative_path,
        heading_path=heading_path,
        title=title,
        category=category,
        statement_type=statement_type,
        entities=entities,
        frontmatter=parsed.frontmatter,
    )


def build_heading_path_fallback(relative_path: str) -> List[str]:
    """当文件没有 Markdown 标题时，从文件路径生成 heading_path fallback。

    规则（§8.3.1）:
      用相对路径去掉扩展名后的所有路径段。
      例如: "字段定义/微信/个人/amount.md" → ["字段定义", "微信", "个人"]
    """
    p = Path(relative_path)
    parts = list(p.parts[:-1])
    stem = p.stem
    if stem.lower() not in ("index", "readme", ""):
        parts.append(stem)
    return parts
