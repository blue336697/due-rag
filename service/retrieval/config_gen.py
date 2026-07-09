"""配置生成 RAG 检索器 (experimental)。

从 config_gen_retriever.py 迁移。当前生产链路未接入 (three_agent_config_gen.py 不调用此模块)。
仅保留能力，标注 experimental，直到明确接入生产。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set

_logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{1,3} (.+)$", re.MULTILINE)


def _chunk_markdown(text: str) -> List[Dict[str, Any]]:
    """按标题切分 Markdown 文本。"""
    headings: List[tuple] = []
    for m in _HEADING_RE.finditer(text):
        headings.append((m.start(), m.group(1)))
    if not headings:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        return [{"heading": "", "content": p} for p in paragraphs]
    chunks: List[Dict[str, Any]] = []
    for i, (pos, heading) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        content = text[pos:end].strip()
        chunks.append({"heading": heading, "content": content})
    return chunks


def _load_chunks(knowledge_dir: str) -> List[Dict[str, Any]]:
    root = Path(knowledge_dir)
    if not root.exists():
        return []
    chunks: List[Dict[str, Any]] = []
    for md_file in sorted(root.rglob("*.md")):
        if md_file.name.startswith("."):
            continue
        text = md_file.read_text(encoding="utf-8")
        rel_path = str(md_file.relative_to(root))
        for chunk in _chunk_markdown(text):
            chunk["source"] = rel_path
            chunks.append(chunk)
    return chunks


def _keyword_score(chunk: Dict[str, Any], keywords: List[str]) -> float:
    text = chunk.get("heading", "") + " " + chunk.get("content", "")
    hits = sum(1 for kw in keywords if kw in text)
    if hits == 0:
        return 0.0
    heading_hits = sum(1 for kw in keywords if kw in chunk.get("heading", ""))
    return hits + heading_hits * 2.0


def _jaccard(text_a: str, text_b: str) -> float:
    def bigrams(s: str) -> Set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)}
    set_a = bigrams(text_a)
    set_b = bigrams(text_b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def retrieve_repair_strategies(
    error_codes: List[str],
    source_nodes: List[str] | None = None,
    top_k: int = 5,
    knowledge_dir: str = "",
) -> List[Dict[str, Any]]:
    """根据错误码和 source_node 检索修复策略 (experimental)。"""
    chunks = _load_chunks(knowledge_dir)
    if not chunks:
        return []
    keywords = list(error_codes)
    if source_nodes:
        keywords.extend(source_nodes)
    scored = []
    for chunk in chunks:
        score = _keyword_score(chunk, keywords)
        if score > 0:
            scored.append({
                "source": chunk["source"],
                "heading": chunk["heading"],
                "content": chunk["content"],
                "score": round(score, 2),
            })
    scored.sort(key=lambda c: c["score"], reverse=True)
    result: List[Dict[str, Any]] = []
    seen_texts: List[str] = []
    for r in scored:
        is_dup = any(_jaccard(r["content"], s) > 0.8 for s in seen_texts)
        if not is_dup:
            seen_texts.append(r["content"])
            result.append(r)
    return result[:top_k]


def retrieve_generation_guide(
    questions: List[str],
    top_k: int = 5,
    knowledge_dir: str = "",
) -> List[Dict[str, Any]]:
    """首次生成场景：根据问题列表检索配置指南 (experimental)。"""
    chunks = _load_chunks(knowledge_dir)
    if not chunks:
        return []
    scored = []
    for chunk in chunks:
        score = _keyword_score(chunk, questions)
        if score > 0:
            scored.append({
                "source": chunk["source"],
                "heading": chunk["heading"],
                "content": chunk["content"],
                "score": round(score, 2),
            })
    scored.sort(key=lambda c: c["score"], reverse=True)
    result: List[Dict[str, Any]] = []
    seen_texts: List[str] = []
    for r in scored:
        is_dup = any(_jaccard(r["content"], s) > 0.8 for s in seen_texts)
        if not is_dup:
            seen_texts.append(r["content"])
            result.append(r)
    return result[:top_k]
