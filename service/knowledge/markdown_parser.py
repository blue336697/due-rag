"""Markdown 解析器 — frontmatter、1-6级标题、表格/代码块边界。

按 RAG高级检索能力开发指南 §5 调用链:
  loader → markdown_parser → metadata → parent_child / semantic_chunker / neighbor

输入: raw_text (文件原始文本)
输出: ParsedMarkdown (结构化解析结果，不含业务 metadata)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ParsedMarkdown:
    """Markdown 结构化解析结果 — 不含业务 metadata。"""
    frontmatter_raw: Optional[str] = None
    frontmatter: dict = field(default_factory=dict)
    content: str = ""  # 剥离 frontmatter 后的正文
    heading_events: List[Tuple[int, int, str]] = field(default_factory=list)
    # (position_in_content, level, title)
    table_ranges: List[Tuple[int, int]] = field(default_factory=list)
    # (start, end) in content
    code_block_ranges: List[Tuple[int, int]] = field(default_factory=list)
    # (start, end) in content
    list_ranges: List[Tuple[int, int]] = field(default_factory=list)
    # (start, end) in content


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_TABLE_LINE_RE = re.compile(r"^\|.+\|.*\|$")
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")


def parse_markdown(raw_text: str) -> ParsedMarkdown:
    """解析 Markdown 原始文本，返回结构化 ParsedMarkdown。

    步骤:
      1. 提取 frontmatter (---...---)
      2. 剥离 frontmatter 得到 content
      3. 识别 1-6 级标题事件 (position, level, title)
      4. 识别表格边界 (连续的 |...|...| 行)
      5. 识别代码块边界 (``` 对)
    """
    result = ParsedMarkdown()

    # 1. 提取 frontmatter
    fm_match = _FRONTMATTER_RE.match(raw_text)
    content_start = 0
    if fm_match:
        result.frontmatter_raw = fm_match.group(1)
        content_start = fm_match.end()
        result.frontmatter = _parse_frontmatter_yaml(result.frontmatter_raw)

    # 2. content = 剥离 frontmatter 后的正文
    result.content = raw_text[content_start:]

    # 3. 识别标题事件
    for m in _HEADING_RE.finditer(result.content):
        level = len(m.group(1))
        title = m.group(2).strip()
        result.heading_events.append((m.start(), level, title))

    # 4. 识别表格范围
    result.table_ranges = _find_table_ranges(result.content)

    # 5. 识别代码块范围
    result.code_block_ranges = _find_code_block_ranges(result.content)

    # 6. 识别连续列表范围
    result.list_ranges = _find_list_ranges(result.content)

    return result


def _parse_frontmatter_yaml(raw: str) -> dict:
    """尝试解析 frontmatter YAML，失败返回空 dict。"""
    try:
        import yaml
        parsed = yaml.safe_load(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _find_table_ranges(content: str) -> List[Tuple[int, int]]:
    """识别连续的表格行范围。表格行以 | 开头和结尾。"""
    lines = content.split("\n")
    ranges: List[Tuple[int, int]] = []
    in_table = False
    table_start = 0
    pos = 0

    for line in lines:
        if _TABLE_LINE_RE.match(line):
            if not in_table:
                table_start = pos
                in_table = True
        else:
            if in_table:
                ranges.append((table_start, pos - 1 if pos > 0 else pos))
                in_table = False
        pos += len(line) + 1  # +1 for \n

    if in_table:
        ranges.append((table_start, len(content)))

    return ranges


def _find_code_block_ranges(content: str) -> List[Tuple[int, int]]:
    """识别 ``` 包围的代码块范围。"""
    fence_positions = [m.start() for m in _CODE_FENCE_RE.finditer(content)]
    ranges: List[Tuple[int, int]] = []

    for i in range(0, len(fence_positions) - 1, 2):
        start = fence_positions[i]
        end_fence = fence_positions[i + 1]
        end_line_end = content.find("\n", end_fence)
        if end_line_end == -1:
            end_line_end = len(content)
        ranges.append((start, end_line_end))

    return ranges


def _find_list_ranges(content: str) -> List[Tuple[int, int]]:
    """识别连续 Markdown 列表，避免从单个列表项中间切块。"""
    lines = content.splitlines(keepends=True)
    ranges: List[Tuple[int, int]] = []
    start: Optional[int] = None
    pos = 0

    for line in lines:
        is_item = bool(_LIST_LINE_RE.match(line))
        is_continuation = start is not None and bool(line.strip()) and line[:1].isspace()
        if is_item or is_continuation:
            if start is None:
                start = pos
        elif start is not None:
            ranges.append((start, pos))
            start = None
        pos += len(line)

    if start is not None:
        ranges.append((start, len(content)))
    return ranges


def extract_heading_path(
    heading_events: List[Tuple[int, int, str]],
    position: int,
) -> List[str]:
    """根据 chunk 在正文中的位置，确定其 heading_path。

    position 之前所有活动标题按层级累加。
    例如: # A (pos=0) > ## B (pos=50), chunk at pos=80 → ["A", "B"]
    """
    paths: List[List[str]] = [[] for _ in range(7)]  # index 1-6

    for evt_pos, level, title in heading_events:
        if evt_pos > position:
            break
        for l in range(level, 7):
            paths[l] = []
        paths[level] = paths[level - 1][:] if level > 1 else []
        paths[level].append(title)

    for l in range(6, 0, -1):
        if paths[l]:
            return paths[l]
    return []


def is_position_in_range(
    pos: int,
    ranges: List[Tuple[int, int]],
) -> bool:
    """检查位置是否在任意范围内。"""
    return any(start <= pos <= end for start, end in ranges)
