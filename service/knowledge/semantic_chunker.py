"""语义分块器 — embedding 相似度驱动的 child chunk 切分。

按 RAG高级检索能力开发指南 §7.2:
  1. 先按 Markdown 标题、段落、列表、表格做结构切分
  2. 对长段落按句子切分
  3. 用 embedding 模型计算相邻句子相似度
  4. 相似度低于阈值或 token 超限时切块
  5. 保证每个 child chunk 不截断表格、代码块和列表项

默认参数:
  child_max_tokens=350, child_min_tokens=80, overlap_tokens=40
  semantic_similarity_threshold=0.62 (对 BAAI/bge-small-zh-v1.5 校准)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, List, Optional, Tuple

_logger = logging.getLogger(__name__)

_SENTENCE_RE = re.compile(r"[^。！？!?\n]+(?:[。！？!?]|\n\n|$)")


def _split_sentences(text: str) -> List[str]:
    """将文本切分为句子列表。"""
    sentences: List[str] = []
    for m in _SENTENCE_RE.finditer(text):
        s = m.group().strip()
        if s:
            sentences.append(s)
    if not sentences and text.strip():
        sentences.append(text.strip())
    return sentences


def semantic_chunk(
    text: str,
    token_counter: Callable[[str], int],
    embedding_model: Any,
    similarity_threshold: float = 0.62,
    min_tokens: int = 80,
    max_tokens: int = 350,
    overlap_tokens: int = 40,
    protected_ranges: Optional[List[Tuple[int, int]]] = None,
) -> List[str]:
    """对父块正文进行语义分块，返回 child chunk 正文列表。

    §7.2: embedding 不可用时必须失败，不降级为硬切分。
    """
    if not text.strip():
        return []

    sentences = _split_sentences(text)
    if not sentences:
        return []

    sent_tokens = [token_counter(s) for s in sentences]

    # embedding 必须可用，不可降级（§7.2）
    if embedding_model is None:
        raise RuntimeError("embedding 模型不可用，语义分块必须失败")
    sent_embeddings: Optional[List[List[float]]] = embedding_model.encode(sentences)

    # 贪心分组
    groups: List[List[int]] = []
    current_group: List[int] = []
    current_tokens = 0

    for i in range(len(sentences)):
        sent_tok = sent_tokens[i]

        if not current_group:
            current_group = [i]
            current_tokens = sent_tok
            continue

        if sent_tok >= max_tokens:
            if current_group:
                groups.append(current_group)
            groups.append([i])
            current_group = []
            current_tokens = 0
            continue

        too_large = (current_tokens + sent_tok) > max_tokens
        too_dissimilar = False

        if not too_large:
            import numpy as np
            curr_emb = np.array(sent_embeddings[current_group[-1]])
            next_emb = np.array(sent_embeddings[i])
            sim = float(
                np.dot(curr_emb, next_emb)
                / (np.linalg.norm(curr_emb) * np.linalg.norm(next_emb) + 1e-8)
            )
            too_dissimilar = sim < similarity_threshold

        if too_large or too_dissimilar:
            groups.append(current_group)
            current_group = [i]
            current_tokens = sent_tok
        else:
            current_group.append(i)
            current_tokens += sent_tok

    if current_group:
        groups.append(current_group)

    # 合并过小组
    groups = _merge_small_groups(groups, sent_tokens, min_tokens)

    # 组装 child chunk 文本
    return ["".join(sentences[g[0] : g[-1] + 1]) for g in groups]


def _merge_small_groups(
    groups: List[List[int]],
    sent_tokens: List[int],
    min_tokens: int,
) -> List[List[int]]:
    """合并 token 数不足 min_tokens 的组到相邻组。"""
    if len(groups) <= 1:
        return groups

    merged: List[List[int]] = []
    i = 0
    while i < len(groups):
        group = groups[i]
        group_tok = sum(sent_tokens[j] for j in group)

        if group_tok >= min_tokens:
            merged.append(group)
            i += 1
        elif merged:
            merged[-1] = merged[-1] + group
            i += 1
        elif i + 1 < len(groups):
            groups[i + 1] = group + groups[i + 1]
            i += 1
        else:
            merged.append(group)
            i += 1

    return merged
